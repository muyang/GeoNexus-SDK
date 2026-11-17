"""Small transactional admission store. Host identity, ACL and execution stay outside SDK."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from ..geocard import GeoCard, compose_visibility, effective_visibility
from .models import AccessContext, Envelope, Evidence, NodeAnnouncement, Principal, digest, utcnow
from .rating import RatingPolicy, observed, rate_node, rate_resource, readiness


class AdmissionDecision(Envelope):
    allowed: bool
    node_id: str
    resource_id: str
    manifest_digest: str
    resource_digest: str
    role: str
    reasons: list[str]


class AdmissionService:
    def __init__(self, path: str | Path, policy: RatingPolicy | None = None):
        if str(path) == ":memory:":
            raise ValueError("AdmissionService requires a persistent SQLite file")
        self.path = str(path)
        self.policy = policy or RatingPolicy()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS announcements (
                  node_id TEXT PRIMARY KEY, digest TEXT NOT NULL, revision INTEGER NOT NULL,
                  submitted_by TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations (
                  id TEXT PRIMARY KEY, node_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (
                  id INTEGER PRIMARY KEY, node_id TEXT NOT NULL, subject TEXT NOT NULL,
                  digest TEXT NOT NULL, reviewer TEXT NOT NULL, status TEXT NOT NULL,
                  note TEXT NOT NULL, at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assessments (
                  id INTEGER PRIMARY KEY, node_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _load(self, db, node_id):
        row = db.execute("SELECT * FROM announcements WHERE node_id=?", (node_id,)).fetchone()
        if not row:
            raise KeyError("Unknown node")
        return row, NodeAnnouncement.model_validate_json(row["payload"])

    def submit(
        self, declaration: NodeAnnouncement, actor: Principal, *, now: datetime | None = None
    ):
        actor.require("nodes:submit")
        declaration = NodeAnnouncement.model_validate_json(declaration.model_dump_json())
        now = now or utcnow()
        if declaration.declared_at > now + timedelta(minutes=5) or declaration.expires_at <= now:
            raise ValueError("Announcement expired, too short-lived or future-dated")
        if (
            declaration.expires_at - declaration.declared_at
        ).total_seconds() > self.policy.max_lease_seconds:
            raise ValueError("Announcement lease exceeds policy")
        # A transaction protects ownership and monotonic revisions against races.
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM announcements WHERE node_id=?", (declaration.node.node_id,)
            ).fetchone()
            if old:
                previous = NodeAnnouncement.model_validate_json(old["payload"])
                if old["submitted_by"] != actor.subject:
                    raise PermissionError("A node ID cannot be taken over by another publisher")
                if declaration.revision < old["revision"] or (
                    declaration.digest != old["digest"] and declaration.revision <= old["revision"]
                ):
                    raise ValueError("Changed content requires a newer revision")
                if declaration.declared_at < previous.declared_at:
                    raise ValueError("A replay cannot replace a newer announcement")
            db.execute(
                "INSERT OR REPLACE INTO announcements VALUES (?,?,?,?,?)",
                (
                    declaration.node.node_id,
                    declaration.digest,
                    declaration.revision,
                    actor.subject,
                    declaration.model_dump_json(),
                ),
            )
        report = self.assess(declaration.node.node_id, now=now)
        with self._db() as db:
            db.execute(
                "INSERT INTO assessments(node_id,digest,payload) VALUES (?,?,?)",
                (declaration.node.node_id, declaration.digest, json.dumps(report)),
            )
        return {
            "node_id": declaration.node.node_id,
            "manifest_digest": declaration.digest,
            "status": self.status(declaration.node.node_id),
            "assessment": report,
        }

    def observe(
        self, node_id: str, entry: Evidence, actor: Principal, *, now: datetime | None = None
    ):
        actor.require("observations:write")
        now = now or utcnow()
        if actor.subject != entry.issuer or actor.subject not in self.policy.trusted_issuers:
            raise PermissionError("Evidence issuer must match a trusted authenticated inspector")
        if not entry.observed_at <= now < entry.expires_at:
            raise ValueError("Evidence is expired or future-dated")
        with self._db() as db:
            owner, announcement = self._load(db, node_id)
            if owner["submitted_by"] == actor.subject:
                raise PermissionError("Publisher cannot self-attest independent evidence")
            subjects = {announcement.digest, *(r.digest for r in announcement.resources)}
            if entry.subject_digest not in subjects:
                raise ValueError("Evidence is for a different or superseded declaration")
            db.execute(
                "INSERT INTO observations VALUES (?,?,?)",
                (uuid.uuid4().hex, node_id, entry.model_dump_json()),
            )
        return self.assess(node_id, now=now)

    def submissions(self, actor: Principal):
        actor.require("nodes:review")
        with self._db() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT node_id,digest,revision,submitted_by FROM announcements ORDER BY node_id"
                )
            ]
        return {
            "items": [{**r, "status": self.status(r["node_id"])} for r in rows],
            "total": len(rows),
        }

    def declaration(self, node_id: str, actor: Principal):
        """Return the exact snapshot a reviewer is being asked to approve."""
        actor.require("nodes:review")
        with self._db() as db:
            owner, announcement = self._load(db, node_id)
            return {
                "manifest_digest": announcement.digest,
                "submitted_by": owner["submitted_by"],
                "announcement": announcement.model_dump(mode="json"),
            }

    def _evidence(self, db, node_id):
        return [
            Evidence.model_validate_json(r["payload"])
            for r in db.execute("SELECT payload FROM observations WHERE node_id=?", (node_id,))
        ]

    def assess(self, node_id: str, *, now: datetime | None = None):
        now = now or utcnow()
        with self._db() as db:
            _, announcement = self._load(db, node_id)
            evidence = self._evidence(db, node_id)
        return {
            "manifest_digest": announcement.digest,
            "policy_version": self.policy.version,
            "node": rate_node(announcement, evidence, policy=self.policy, now=now).model_dump(
                mode="json"
            ),
            "resources": {
                r.resource_id: rate_resource(r, evidence, policy=self.policy, now=now).model_dump(
                    mode="json"
                )
                for r in announcement.resources
            },
        }

    @staticmethod
    def _status(db, node_id, subject, manifest_digest):
        row = db.execute(
            "SELECT status FROM reviews WHERE node_id=? AND subject=? AND digest=? ORDER BY id DESC LIMIT 1",
            (node_id, subject, manifest_digest),
        ).fetchone()
        return row["status"] if row else "pending"

    def status(self, node_id: str):
        with self._db() as db:
            _, announcement = self._load(db, node_id)
            return {
                "node": self._status(db, node_id, "node", announcement.digest),
                "resources": {
                    r.resource_id: self._status(
                        db, node_id, "resource:" + r.resource_id, announcement.digest
                    )
                    for r in announcement.resources
                },
            }

    def review(
        self,
        node_id: str,
        manifest_digest: str,
        actor: Principal,
        *,
        subject: str = "node",
        resource_id: str | None = None,
        decision: Literal["approved", "rejected", "revoked"],
        note: str,
        now: datetime | None = None,
    ):
        actor.require("nodes:review")
        now = now or utcnow()
        if decision not in {"approved", "rejected", "revoked"} or not note.strip():
            raise ValueError("Review requires a valid decision and a non-empty note")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row, announcement = self._load(db, node_id)
            if row["submitted_by"] == actor.subject:
                raise PermissionError("Submitter cannot review their own node or resources")
            if manifest_digest != announcement.digest:
                raise ValueError("Declaration changed: review the new immutable snapshot")
            if now >= announcement.expires_at:
                raise ValueError("Cannot review an expired announcement")
            if subject == "resource":
                resource = next(
                    (r for r in announcement.resources if r.resource_id == resource_id), None
                )
                if not resource:
                    raise KeyError("Unknown resource")
                if (
                    decision == "approved"
                    and rate_resource(
                        resource, self._evidence(db, node_id), policy=self.policy, now=now
                    ).blockers
                ):
                    raise ValueError(
                        "Blocking contract or inspection findings must be resolved before approval"
                    )
                target = "resource:" + resource.resource_id
            elif subject == "node" and resource_id is None:
                target = "node"
            else:
                raise ValueError("Invalid review subject")
            db.execute(
                "INSERT INTO reviews(node_id,subject,digest,reviewer,status,note,at) VALUES (?,?,?,?,?,?,?)",
                (node_id, target, manifest_digest, actor.subject, decision, note, now.isoformat()),
            )
        return self.status(node_id)

    def history(self, node_id: str, actor: Principal):
        actor.require("nodes:review")
        with self._db() as db:
            return [
                dict(r)
                for r in db.execute("SELECT * FROM reviews WHERE node_id=? ORDER BY id", (node_id,))
            ]

    def admitted_catalog(self, *, now: datetime | None = None):
        """Public approved view only. Restricted catalogues belong to host ACL services."""
        now = now or utcnow()
        result = []
        with self._db() as db:
            db.execute("BEGIN")
            for row in db.execute("SELECT * FROM announcements ORDER BY node_id").fetchall():
                declaration = NodeAnnouncement.model_validate_json(row["payload"])
                if (
                    now >= declaration.expires_at
                    or self._status(db, row["node_id"], "node", declaration.digest) != "approved"
                ):
                    continue
                cards = {
                    r.resource_id: GeoCard.from_dict(r.card)
                    for r in declaration.resources
                    if self._status(
                        db, row["node_id"], "resource:" + r.resource_id, declaration.digest
                    )
                    == "approved"
                }
                levels = {
                    r.resource_id: compose_visibility(
                        [r.visibility, (r.card.get("compliance") or {}).get("sensitivity")]
                    )
                    for r in declaration.resources
                }

                def lookup(identity, cards=cards, levels=levels):
                    c = cards.get(identity)
                    if c:
                        value = c.to_dict()
                        value["compliance"] = {
                            **value.get("compliance", {}),
                            "sensitivity": levels[identity],
                        }
                        return value
                    return None

                evidence = self._evidence(db, row["node_id"])
                reachability = observed(evidence, declaration.digest, self.policy, now).get(
                    "endpoint_reachable"
                )
                for resource in declaration.resources:
                    if resource.resource_id not in cards:
                        continue
                    visibility = effective_visibility(
                        cards[resource.resource_id], lookup, own=levels[resource.resource_id]
                    )
                    if not visibility.visible or not visibility.is_visible_to(["public"]):
                        continue
                    rating = rate_resource(resource, evidence, policy=self.policy, now=now)
                    binding = resource.binding
                    result.append(
                        {
                            "kind": resource.card["type"].title(),
                            "state": {
                                "mode": "Governed",
                                "node_name": declaration.node.name,
                                "approved": True,
                                "reachable": True
                                if reachability == "pass"
                                else False
                                if reachability == "fail"
                                else None,
                                "executable": bool(
                                    binding and not rating.blockers and reachability == "pass"
                                ),
                                "tool": binding.tool if binding else None,
                                "input_schema": binding.input_schema if binding else None,
                                "output_schema": binding.output_schema if binding else None,
                            },
                            "rating": {
                                k: rating.model_dump(mode="json")[k]
                                for k in [
                                    "grade",
                                    "score",
                                    "evidence_coverage",
                                    "policy_version",
                                    "policy_digest",
                                    "subject_digest",
                                ]
                            },
                            "id": "admitted." + digest([row["node_id"], resource.resource_id]),
                            "geocard": resource.card,
                            "node_ref": row["node_id"],
                            "node_url": declaration.node.endpoint,
                            "node_protocol": declaration.node.protocol,
                            "resource_version": resource.version,
                            "manifest_digest": declaration.digest,
                            "resource_digest": resource.digest,
                            "readiness": readiness(resource),
                            "binding": resource.binding.model_dump() if resource.binding else None,
                        }
                    )
        return {"items": result, "total": len(result)}

    def eligible(
        self,
        node_id: str,
        resource_id: str,
        manifest_digest: str,
        context: AccessContext,
        *,
        now: datetime | None = None,
    ) -> AdmissionDecision:
        """Same gate for official and third-party assets. Never dispatches a task."""
        now = now or utcnow()
        reasons = []
        with self._db() as db:
            db.execute("BEGIN")
            _, announcement = self._load(db, node_id)
            resource = next(
                (r for r in announcement.resources if r.resource_id == resource_id), None
            )
            if resource is None:
                raise KeyError("Unknown resource")
            if announcement.digest != manifest_digest:
                reasons.append("Declaration digest changed")
            if now >= announcement.expires_at:
                reasons.append("Announcement expired")
            for subject in ["node", "resource:" + resource_id]:
                if self._status(db, node_id, subject, announcement.digest) != "approved":
                    reasons.append(subject + " is not approved")
            for component in (resource.card.get("composition") or {}).get("components", []):
                if component.get("required", True) and (
                    not any(r.resource_id == component["id"] for r in announcement.resources)
                    or self._status(db, node_id, "resource:" + component["id"], announcement.digest)
                    != "approved"
                ):
                    reasons.append(
                        "Required component unavailable or not approved: " + component["id"]
                    )
            checks = observed(self._evidence(db, node_id), announcement.digest, self.policy, now)
            if self.policy.require_reachable and checks.get("endpoint_reachable") != "pass":
                reasons.append("Node reachability is unknown, failed or expired")
            quality = rate_resource(
                resource, self._evidence(db, node_id), policy=self.policy, now=now
            )
            reasons.extend(quality.blockers)
        role = readiness(resource)
        if role not in {"input", "tool"}:
            reasons.append("Resource has no compatible input/tool access contract")
        for name, value in context.model_dump().items():
            if value is not True:
                reasons.append(name + " is not explicitly allowed")
        return AdmissionDecision(
            allowed=not reasons,
            node_id=node_id,
            resource_id=resource_id,
            manifest_digest=announcement.digest,
            resource_digest=resource.digest,
            role=role,
            reasons=reasons,
        )
