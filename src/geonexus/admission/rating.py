"""Deterministic, explainable readiness ratings; never an automatic approval."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import jsonschema
from pydantic import Field, field_validator

from .models import Envelope, Evidence, NodeAnnouncement, ResourceDeclaration, digest, utcnow


class RatingPolicy(Envelope):
    version: str = "readiness-v1"
    trusted_issuers: frozenset[str] = frozenset()
    weights: dict[str, int] = Field(
        default_factory=lambda: {
            "metadata": 25,
            "provenance": 20,
            "publication": 15,
            "contract": 25,
            "reproducibility": 15,
        }
    )
    max_lease_seconds: int = Field(default=86400, ge=60, le=604800)
    require_reachable: bool = True
    standard_cpu_cores: int = Field(default=8, ge=1)
    standard_memory_gib: int = Field(default=16, ge=1)
    large_cpu_cores: int = Field(default=32, ge=1)
    large_memory_gib: int = Field(default=64, ge=1)

    @field_validator("weights")
    @classmethod
    def valid_weights(cls, value):
        if set(value) != {"metadata", "provenance", "publication", "contract", "reproducibility"}:
            raise ValueError("Weights must name all five resource dimensions")
        if any(type(v) is not int or v <= 0 for v in value.values()) or sum(value.values()) != 100:
            raise ValueError("Positive integer weights must sum to 100")
        return value


class Dimension(Envelope):
    score: float | None = Field(default=None, ge=0, le=100)
    basis: str


class Rating(Envelope):
    subject_digest: str
    policy_version: str
    policy_digest: str
    evaluated_at: datetime
    score: float
    grade: str
    dimensions: dict[str, Dimension]
    observed_checks: dict[str, str]
    evidence_coverage: float
    blockers: list[str]
    capacity_profile: dict[str, Any] | None = None
    notice: str = (
        "Readiness is not scientific correctness, approval, authorization or live capacity."
    )


def observed(
    evidence: list[Evidence], subject: str, policy: RatingPolicy, now: datetime
) -> dict[str, str]:
    latest: dict[str, Evidence] = {}
    for entry in evidence:
        if (
            entry.subject_digest != subject
            or entry.issuer not in policy.trusted_issuers
            or not entry.observed_at <= now < entry.expires_at
        ):
            continue
        # A failure wins ties; adding a same-time success cannot hide it.
        previous = latest.get(entry.check)
        if previous is None or (entry.observed_at, entry.outcome == "fail") > (
            previous.observed_at,
            previous.outcome == "fail",
        ):
            latest[entry.check] = entry
    return {key: value.outcome for key, value in latest.items()}


def contract_issue(resource: ResourceDeclaration) -> str | None:
    if not resource.binding:
        return None
    schema = resource.binding.input_schema
    if schema.get("type") != "object":
        return "Tool input schema must have type object"
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        if resource.binding.output_schema is not None:
            jsonschema.Draft202012Validator.check_schema(resource.binding.output_schema)
    except jsonschema.SchemaError as exc:
        return "Invalid JSON Schema: " + exc.message[:200]
    return None


def readiness(resource: ResourceDeclaration) -> str:
    if contract_issue(resource):
        return "unsupported_schema"
    if resource.binding:
        return "tool"
    access = resource.card.get("access") or {}
    from urllib.parse import urlsplit

    if resource.card["type"] == "data" and urlsplit(access.get("endpoint", "")).scheme in {
        "http",
        "https",
    }:
        return "input"
    return "catalog_only"


def _rating(
    subject: str,
    dimensions: dict[str, Dimension],
    weights: dict[str, int],
    checks: dict[str, str],
    blockers: list[str],
    policy: RatingPolicy,
    now: datetime,
) -> Rating:
    # Unknown contributes zero with a fixed denominator, never boosts a score.
    score = round(sum((dimensions[k].score or 0) * w / 100 for k, w in weights.items()), 2)
    grade = (
        "not_ready"
        if blockers
        else "A"
        if score >= 85
        else "B"
        if score >= 70
        else "C"
        if score >= 50
        else "D"
    )
    coverage = (
        round(sum(v != "unknown" for v in checks.values()) / len(checks), 3) if checks else 0.0
    )
    return Rating(
        subject_digest=subject,
        policy_version=policy.version,
        policy_digest=digest(
            {**policy.model_dump(mode="json"), "trusted_issuers": sorted(policy.trusted_issuers)}
        ),
        evaluated_at=now,
        score=score,
        grade=grade,
        dimensions=dimensions,
        observed_checks=checks,
        evidence_coverage=coverage,
        blockers=blockers,
    )


def rate_resource(
    resource: ResourceDeclaration,
    evidence: list[Evidence] = (),
    *,
    policy: RatingPolicy | None = None,
    now: datetime | None = None,
) -> Rating:
    policy = policy or RatingPolicy()
    now = now or utcnow()
    checks = observed(evidence, resource.digest, policy, now)
    required_checks = ["source_verified", "reproducibility", "scientific_validation"]
    if resource.card["type"] == "data":
        required_checks.append("data_access")
    if resource.card["type"] == "compute":
        required_checks.append("compute_verified")
    checks = {k: checks.get(k, "unknown") for k in required_checks}
    provenance = resource.card.get("provenance") or {}

    def reference(value: Any) -> bool:
        return isinstance(value, str) and value.startswith(("https://", "http://", "doi:", "urn:"))

    metadata = [
        bool(resource.card.get("description")),
        bool(resource.card.get("license")),
        bool(provenance.get("provider")),
        reference(provenance.get("source")),
        bool(resource.version),
    ]
    publication = resource.publication
    stage = {"unknown": 0, "draft": 0, "experimental": 35, "stable": 70, "deprecated": 0}[
        publication.stage
    ]
    release = min(
        100,
        stage
        + (15 if reference(publication.documentation) else 0)
        + (15 if reference(publication.reproducible_example) else 0),
    )
    issue = contract_issue(resource)
    role = readiness(resource)
    dimensions = {
        "metadata": Dimension(
            score=sum(metadata) * 20,
            basis="description, license, provider, source reference, version (20 each)",
        ),
        "provenance": Dimension(
            score=100
            if checks.get("source_verified") == "pass"
            else 0
            if checks.get("source_verified") == "fail"
            else 40
            if reference(provenance.get("source"))
            else None,
            basis="declared source reference=40; independent verification=100; failed verification=0",
        ),
        "publication": Dimension(
            score=release,
            basis="stage plus documentation/example; source-code publication recorded separately, not required for proprietary products",
        ),
        "contract": Dimension(
            score=100 if role in {"input", "tool"} else 0 if issue else 25,
            basis=issue or "standard tool binding or data access; catalogue-only=25",
        ),
        "reproducibility": Dimension(
            score=100
            if checks.get("reproducibility") == "pass"
            else 0
            if checks.get("reproducibility") == "fail"
            else None,
            basis="independent replay evidence required; example URL alone is not a verified replay",
        ),
    }
    if resource.card["type"] == "data":
        spatial = resource.card.get("spatial") or {}
        fields = [
            bool(spatial.get("crs")),
            bool(spatial.get("bbox")),
            bool(resource.card.get("temporal")),
            bool(resource.card.get("access")),
            bool(resource.card.get("bands") or resource.card.get("outputs")),
        ]
        measured = 50 if checks["data_access"] == "pass" else 0
        completeness = sum(fields) * 10 + measured
        dimensions["data_completeness"] = Dimension(
            score=completeness,
            basis="five declared data descriptors (10 each) plus trusted access check (50); not full coverage, accuracy or raster-content validation",
        )
        dimensions["metadata"] = Dimension(
            score=(dimensions["metadata"].score + completeness) / 2,
            basis="mean of generic metadata and data descriptor/access completeness",
        )
    if resource.card["type"] == "compute":
        runtime = resource.card.get("runtime") or {}
        value = (
            100
            if checks["compute_verified"] == "pass"
            else 0
            if checks["compute_verified"] == "fail"
            else 40
            if runtime.get("cpu") and runtime.get("memory")
            else None
        )
        dimensions["compute_capacity"] = Dimension(
            score=value,
            basis="declared CPU/memory=40; independent probe=100; not guaranteed free capacity",
        )
        dimensions["metadata"] = Dimension(
            score=(dimensions["metadata"].score + (value or 0)) / 2,
            basis="mean of generic metadata and compute capacity evidence",
        )
    blockers = [issue] if issue else []
    blockers.extend(
        "Independent check failed: " + name for name, outcome in checks.items() if outcome == "fail"
    )
    return _rating(resource.digest, dimensions, policy.weights, checks, blockers, policy, now)


def rate_node(
    announcement: NodeAnnouncement,
    evidence: list[Evidence] = (),
    *,
    policy: RatingPolicy | None = None,
    now: datetime | None = None,
) -> Rating:
    policy = policy or RatingPolicy()
    now = now or utcnow()
    checks = observed(evidence, announcement.digest, policy, now)
    checks = {k: checks.get(k, "unknown") for k in ["compute_verified", "endpoint_reachable"]}
    capacity = announcement.node.capacity
    dims = {
        "declaration": Dimension(
            score=100 if announcement.node.jurisdictions else 70,
            basis="validated identity/endpoint; jurisdictions declared adds 30",
        ),
        "compute": Dimension(
            score=100
            if checks.get("compute_verified") == "pass"
            else 0
            if checks.get("compute_verified") == "fail"
            else 40
            if capacity.cpu_cores and capacity.memory_gib
            else None,
            basis="capacity declaration=40; trusted compute probe=100; GPU absence is not a penalty",
        ),
        "availability": Dimension(
            score=100
            if checks.get("endpoint_reachable") == "pass"
            else 0
            if checks.get("endpoint_reachable") == "fail"
            else None,
            basis="fresh independent endpoint observation; unknown is not online",
        ),
        "inventory": Dimension(
            score=round(
                100
                * sum(readiness(r) != "unsupported_schema" for r in announcement.resources)
                / len(announcement.resources)
            )
            if announcement.resources
            else 0,
            basis="share of declared resources without incompatible binding schemas, not resource volume",
        ),
    }
    blockers = ["Announcement expired"] if now >= announcement.expires_at else []
    rating = _rating(
        announcement.digest,
        dims,
        {"declaration": 20, "compute": 25, "availability": 25, "inventory": 30},
        checks,
        blockers,
        policy,
        now,
    )

    if capacity.cpu_cores is None or capacity.memory_gib is None:
        tier = "unknown"
    elif capacity.gpu_count and capacity.gpu_count > 0:
        tier = "accelerated"
    elif (
        capacity.cpu_cores >= policy.large_cpu_cores
        and capacity.memory_gib >= policy.large_memory_gib
    ):
        tier = "large_cpu"
    elif (
        capacity.cpu_cores >= policy.standard_cpu_cores
        and capacity.memory_gib >= policy.standard_memory_gib
    ):
        tier = "standard_cpu"
    else:
        tier = "small_cpu"
    return rating.model_copy(
        update={
            "capacity_profile": {
                "class": tier,
                "declared": capacity.model_dump(mode="json"),
                "basis": "inspected_declaration"
                if checks["compute_verified"] == "pass"
                else "unverified_declaration",
                "live_free_capacity": "unknown",
            }
        }
    )
