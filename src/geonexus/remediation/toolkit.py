"""Deterministic diagnosis and observed-shape drafts, without network or execution."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import jsonschema

from ..admission.models import ResourceDeclaration, ToolBinding, digest
from ..geocard import validate_card_schema

TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}
NOTICE = (
    "Drafts and sample validation are not runtime verification, approval or scientific validation."
)


def body(snapshot: dict) -> dict:
    value = snapshot.get("result", snapshot)
    if not isinstance(value, dict) or not isinstance(value.get("geocards"), list):
        raise ValueError("Expected a saved geo.describe response")
    cards = value["geocards"]
    if not all(isinstance(c, dict) and isinstance(c.get("id"), str) for c in cards):
        raise ValueError("GeoCards require string IDs")
    if len({c["id"] for c in cards}) != len(cards):
        raise ValueError("Duplicate GeoCard IDs require publisher reconciliation")
    skills = value.get("skills", [])
    if not isinstance(skills, list) or not all(
        isinstance(s, dict) and isinstance(s.get("name"), str) for s in skills
    ):
        raise ValueError("Invalid skill descriptors")
    if len({s["name"] for s in skills}) != len(skills):
        raise ValueError("Ambiguous tool names")
    return value


def schema_errors(schema: Any) -> list[dict]:
    """Stable, complete schema diagnostics; do not print rejected instance values."""
    validator = jsonschema.Draft202012Validator(jsonschema.Draft202012Validator.META_SCHEMA)
    errors = []
    for error in validator.iter_errors(schema):
        errors.append({"path": list(error.absolute_path), "validator": error.validator})
    return sorted(errors, key=lambda v: json.dumps(v, sort_keys=True))


def diagnose(snapshot: dict, assessment: dict | None = None) -> dict:
    data = body(snapshot)
    findings = []

    def add(code, subject, priority, action, acceptance, **details):
        findings.append(
            dict(
                code=code,
                subject=subject,
                priority=priority,
                action=action,
                acceptance=acceptance,
                **details,
            )
        )

    ids = {c["id"] for c in data["geocards"]}
    for card in data["geocards"]:
        rid = card["id"]
        report = validate_card_schema(card)
        if not report.valid:
            add(
                "geocard_schema",
                rid,
                "P0",
                "Repair the canonical GeoCard structure",
                "Canonical GeoCard schema validation passes",
            )
        license_value = str(card.get("license") or "").strip().lower()
        if not license_value or any(
            s in license_value
            for s in ("not stated", "unknown", "tbd", "ask the team", "未声明", "待补充")
        ):
            add(
                "license_unknown",
                rid,
                "P0",
                "Provider supplies verifiable license/authorization",
                "Reviewer verifies actual terms; never substitute an invented open license",
            )
        if not card.get("version"):
            add(
                "release_version_unknown",
                rid,
                "P0",
                "Provider declares immutable release identity",
                "Published version and artifact digest verified; snapshot hashes are not releases",
            )
        if card.get("type") == "data" and not (card.get("access") or {}).get("endpoint"):
            add(
                "data_access_missing",
                rid,
                "P0",
                "Declare authorized data or snapshot access",
                "Read actual bytes, check hash, format, units and task coverage",
            )
        if card.get("type") == "model":
            add(
                "model_contract_unverified",
                rid,
                "P1",
                "Document and verify HTTP/model binding",
                "Authorized real request and result pass declared schemas; no inference from a URL",
            )
        if card.get("type") == "compute":
            add(
                "capacity_unverified",
                rid,
                "P1",
                "Independent authorized capacity verification",
                "Record hardware, concurrency and fresh evidence; no stress test by this tool",
            )
        for component in (card.get("composition") or {}).get("components", []):
            if component.get("required", True) and component.get("id") not in ids:
                add(
                    "unresolved_component",
                    rid,
                    "P0",
                    "Resolve required component in reviewed catalog",
                    "Dependency resolves with version, access and sovereignty checks",
                    component_id=component.get("id"),
                )
    for skill in data.get("skills", []):
        for direction in ("input_schema", "output_schema"):
            if direction not in skill:
                add(
                    "binding_schema_missing",
                    skill["name"],
                    "P0",
                    "Provide missing execution schema",
                    "Check real examples and runtime return shape",
                    direction=direction,
                )
                continue
            errors = schema_errors(skill[direction])
            if direction == "input_schema" and (
                not isinstance(skill[direction], dict) or skill[direction].get("type") != "object"
            ):
                errors.append({"path": ["type"], "validator": "input_root_object"})
            if errors:
                add(
                    "execution_schema_invalid",
                    skill["name"],
                    "P0",
                    "Supply real input/output samples; generate and review a draft",
                    "Both schemas and authorized real executions pass; sample checks alone insufficient",
                    direction=direction,
                    errors=errors,
                )
    imported = False
    if assessment is not None:
        rated = assessment.get("resources", [])
        if not isinstance(rated, list):
            raise ValueError("Assessment resources must be a list")
        if len(rated) != len(ids) or {r.get("id") for r in rated} != ids:
            raise ValueError("Assessment inventory differs from current snapshot")
        # Do not attach another revision's rating to current content.
        for item in rated:
            card = next((c for c in data["geocards"] if c["id"] == item.get("id")), None)
            if card is None or item.get("assessment_view_version") != "snapshot-" + digest(card):
                raise ValueError("Assessment does not match current GeoCard snapshot")
            skill = next(
                (s for s in data.get("skills", []) if s.get("geocard_id", s["name"]) == card["id"]),
                None,
            )
            current = ResourceDeclaration(
                card=card,
                version="snapshot-" + digest(card),
                binding=ToolBinding(
                    tool=skill["name"],
                    input_schema=skill.get("input_schema", {}),
                    output_schema=skill.get("output_schema"),
                )
                if skill
                else None,
            )
            if item.get("rating", {}).get("subject_digest") != current.digest:
                raise ValueError("Assessment tool binding or resource envelope differs")
        for check in assessment.get("data_access_checks", []):
            if check.get("resource_id") not in ids:
                raise ValueError("Unknown resource in assessment")
            if check.get("target_present") is False:
                add(
                    "target_period_missing",
                    check["resource_id"],
                    "P0",
                    "Freeze suitable data or explicitly revise task period/missing-data policy",
                    "Reviewer verifies actual task-period coverage, not HTTP 200",
                    target=check.get("target_period_index"),
                    last_valid_period=check.get("last_period_index"),
                    evidence_status="imported_historical",
                )
        imported = True
    add(
        "formal_announcement_required",
        "node",
        "P0",
        "Obtain provider-supplied NodeAnnouncement",
        "Identity, revision, jurisdiction and lease verified; no fabricated operator or capacity",
    )
    add(
        "scientific_replay_required",
        "workflow",
        "P1",
        "Separate snapshot replay and raw-data recompute",
        "Independent replay, cutoff/leakage checks, baseline and scientific acceptance recorded",
    )
    findings.sort(key=lambda f: (f["priority"], f["subject"], f["code"], f.get("direction", "")))
    return {
        "toolkit_version": "remediation/0.1",
        "snapshot_digest": digest(snapshot),
        "notice": NOTICE,
        "mode": "offline_review_only",
        "approval": "not_granted",
        "assessment_imported": imported,
        "assessment_is_historical": imported,
        "resources": len(ids),
        "findings": findings,
    }


def infer_shape(value: Any) -> dict:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float) and math.isfinite(value):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        shapes = {json.dumps(infer_shape(v), sort_keys=True) for v in value}
        items = [json.loads(s) for s in sorted(shapes)]
        return {
            "type": "array",
            "items": items[0] if len(items) == 1 else {"anyOf": items} if items else {},
        }
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        # Requiredness, ranges and closed-world constraints cannot be inferred from one sample.
        return {"type": "object", "properties": {k: infer_shape(v) for k, v in value.items()}}
    raise ValueError("Samples must be finite JSON")


def validate_samples(binding: dict, input_sample: Any, output_sample: Any) -> dict:
    infer_shape(input_sample)
    infer_shape(output_sample)
    parsed = ToolBinding.model_validate(binding)
    results = {}
    for key, sample in (("input_schema", input_sample), ("output_schema", output_sample)):
        schema = getattr(parsed, key)
        errors = schema_errors(schema)
        if key == "input_schema" and (
            not isinstance(sample, dict) or schema.get("type") != "object"
        ):
            errors.append({"path": [], "validator": "input_root_object"})
        if not errors:
            errors = [
                {"path": list(e.absolute_path), "validator": e.validator}
                for e in jsonschema.Draft202012Validator(schema).iter_errors(sample)
            ]
        results[key] = {"sample_valid": not errors, "errors": errors}
    return {
        "checks": results,
        "all_samples_valid": all(r["sample_valid"] for r in results.values()),
        "runtime_verified": False,
        "scientific_validation": "unknown",
        "notice": NOTICE,
    }


def draft_binding(snapshot: dict, tool: str, input_sample: Any, output_sample: Any) -> dict:
    data = body(snapshot)
    skill = next((s for s in data.get("skills", []) if s["name"] == tool), None)
    if skill is None:
        raise ValueError("Tool not found")
    candidate = {"tool": tool}
    changes = []

    def repair(schema, sample, path):
        if not isinstance(schema, dict):
            return schema
        result = copy.deepcopy(schema)
        typ = schema.get("type")
        if isinstance(typ, str) and typ not in TYPES:
            shape = infer_shape(sample)
            for k, v in shape.items():
                result.setdefault(k, v)
            result["type"] = shape["type"]
            result["x-geonexus-logical-type"] = typ
            changes.append({"path": path, "logical_type": typ, "observed_json_type": shape["type"]})
        if isinstance(sample, dict):
            for key, subschema in list(result.get("properties", {}).items()):
                if key in sample:
                    result["properties"][key] = repair(
                        subschema, sample[key], path + ["properties", key]
                    )
        if isinstance(sample, list) and sample and isinstance(result.get("items"), dict):
            # All sample elements are validated below. This is only a shape draft.
            result["items"] = repair(result["items"], sample[0], path + ["items"])
        return result

    for key, sample in (("input_schema", input_sample), ("output_schema", output_sample)):
        if key not in skill:
            raise ValueError("Missing original schema requires manual authoring")
        candidate[key] = repair(skill[key], sample, [key])
    check = validate_samples(candidate, input_sample, output_sample)
    if not check["all_samples_valid"]:
        raise ValueError("Sample does not support a valid draft; manual repair required")
    return {
        "status": "draft_requires_review",
        "source_snapshot_digest": digest(snapshot),
        "source_binding_digest": digest(skill),
        "candidate_binding": candidate,
        "sample_digests": {"input": digest(input_sample), "output": digest(output_sample)},
        "sample_origin": "caller_supplied_unverified",
        "changes": changes,
        "sample_check": check,
        "approval": "not_granted",
        "review_requirements": [
            "Actual samples versus fixtures",
            "Required fields and nulls",
            "Ranges, units, CRS and nodata",
            "Artifact shape and media type",
            "Task semantics and scientific acceptance",
            "New digest and re-review",
        ],
        "notice": NOTICE,
    }


def write_bundle(result: dict, output: Path) -> None:
    """Refuse overwrite; never mutate a node, original snapshot or existing bundle."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    (output / "diagnosis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Remediation tasks",
        "",
        NOTICE,
        "",
        "Source digest: " + result["snapshot_digest"],
        "",
    ]
    grouped = {}
    for f in result["findings"]:
        grouped.setdefault((f["priority"], f["code"]), []).append(f)
    for (priority, code), fs in sorted(grouped.items()):
        lines += [
            f"## {priority}: {code} ({len(fs)})",
            "",
            fs[0]["action"],
            "",
            "Acceptance: " + fs[0]["acceptance"],
            "",
        ]
        lines += [
            "- " + f["subject"] + (" / " + f["direction"] if "direction" in f else "") for f in fs
        ]
        lines += [""]
    (output / "TASKS.md").write_text("\n".join(lines), encoding="utf-8")
    agent = {
        "source_snapshot_digest": result["snapshot_digest"],
        "capabilities": ["read_snapshot", "draft_binding", "check_samples"],
        "forbidden": [
            "invent_license_or_provenance",
            "approve_nodes",
            "execute_remote_jobs",
            "install_code",
            "write_live_node",
            "claim_scientific_pass",
        ],
        "tasks": result["findings"],
    }
    (output / "agent-tasks.json").write_text(
        json.dumps(agent, ensure_ascii=False, indent=2), encoding="utf-8"
    )
