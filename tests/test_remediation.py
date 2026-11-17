"""Offline assistance must preserve evidence, constraints and review boundaries."""

import copy
import json

import pytest

from geonexus.admission import ResourceDeclaration, ToolBinding
from geonexus.admission.models import digest
from geonexus.remediation import diagnose, draft_binding, validate_samples, write_bundle
from geonexus.remediation.__main__ import main


def snapshot():
    return {
        "result": {
            "geocards": [
                {
                    "geocard_version": "1.0",
                    "id": "skill.a",
                    "name": "Synthetic example",
                    "description": "Synthetic test only",
                    "type": "skill",
                    "license": "Not stated; ask the team",
                }
            ],
            "skills": [
                {
                    "name": "tool.a",
                    "geocard_id": "skill.a",
                    "input_schema": {
                        "type": "object",
                        "required": ["mean"],
                        "properties": {
                            "mean": {"type": "grid", "description": "Synthetic grid"},
                            "limit": {"type": "number", "minimum": 0},
                        },
                    },
                    "output_schema": {"type": "object", "properties": {"field": {"type": "grid"}}},
                }
            ],
        }
    }


def samples():
    return {"mean": [[1, 2], [3, 4]], "limit": 2}, {
        "field": {"uri": "https://example.org/private-value"}
    }


def test_draft_preserves_source_constraints_and_semantics_without_sample_values():
    source = snapshot()
    before = copy.deepcopy(source)
    result = draft_binding(source, "tool.a", *samples())
    assert source == before
    binding = result["candidate_binding"]
    assert binding["input_schema"]["required"] == ["mean"]
    assert binding["input_schema"]["properties"]["limit"]["minimum"] == 0
    assert binding["input_schema"]["properties"]["mean"]["type"] == "array"
    assert binding["input_schema"]["properties"]["mean"]["x-geonexus-logical-type"] == "grid"
    assert result["sample_check"]["all_samples_valid"]
    assert not result["sample_check"]["runtime_verified"]
    assert result["approval"] == "not_granted"
    assert "private-value" not in json.dumps(result)


def test_draft_rejects_sample_violating_existing_constraints():
    i, o = samples()
    i["limit"] = -1
    with pytest.raises(ValueError):
        draft_binding(snapshot(), "tool.a", i, o)


def test_missing_actual_field_is_not_fabricated():
    with pytest.raises(ValueError):
        draft_binding(snapshot(), "tool.a", {"limit": 1}, {})


def test_diagnosis_catches_placeholder_license_and_both_schemas():
    result = diagnose(snapshot())
    findings = result["findings"]
    assert len([f for f in findings if f["code"] == "execution_schema_invalid"]) == 2
    assert any(f["code"] == "license_unknown" for f in findings)
    assert result == diagnose(snapshot())


@pytest.mark.parametrize("value", [None, [], False, {"type": "wrong"}])
def test_malformed_schema_produces_diagnostic(value):
    data = snapshot()
    data["result"]["skills"][0]["input_schema"] = value
    assert any(f["code"] == "execution_schema_invalid" for f in diagnose(data)["findings"])


def assessment(source):
    card = source["result"]["geocards"][0]
    skill = source["result"]["skills"][0]
    declaration = ResourceDeclaration(
        card=card,
        version="snapshot-" + digest(card),
        binding=ToolBinding(
            tool=skill["name"],
            input_schema=skill["input_schema"],
            output_schema=skill["output_schema"],
        ),
    )
    return {
        "resources": [
            {
                "id": card["id"],
                "assessment_view_version": declaration.version,
                "rating": {"subject_digest": declaration.digest},
            }
        ]
    }


def test_stale_assessment_rejected_after_tool_change_even_same_card():
    source = snapshot()
    old = assessment(source)
    assert diagnose(source, old)["assessment_is_historical"]
    source["result"]["skills"][0]["input_schema"]["required"] = []
    with pytest.raises(ValueError):
        diagnose(source, old)


def test_inventory_change_rejected_and_no_duplicate_ids():
    source = snapshot()
    old = assessment(source)
    source["result"]["geocards"].append(copy.deepcopy(source["result"]["geocards"][0]))
    with pytest.raises(ValueError):
        diagnose(source, old)


def test_no_overwrite_of_source_or_existing_bundle(tmp_path):
    write_bundle(diagnose(snapshot()), tmp_path / "bundle")
    with pytest.raises(FileExistsError):
        write_bundle(diagnose(snapshot()), tmp_path / "bundle")


def test_instance_error_does_not_print_rejected_secret():
    binding = {
        "tool": "a",
        "input_schema": {"type": "object", "properties": {"key": {"type": "integer"}}},
        "output_schema": {"type": "object"},
    }
    result = validate_samples(binding, {"key": "secret-not-for-log"}, {})
    assert not result["all_samples_valid"]
    assert "secret-not-for-log" not in json.dumps(result)


def test_non_finite_json_samples_rejected():
    binding = {"tool": "a", "input_schema": {"type": "object"}, "output_schema": {"type": "number"}}
    with pytest.raises(ValueError):
        validate_samples(binding, {}, float("nan"))


def test_cli_roundtrip_and_exclusive_draft(tmp_path, capsys):
    source = tmp_path / "snapshot.json"
    source.write_text(json.dumps(snapshot()))
    i, o = samples()
    ip, op = tmp_path / "input.json", tmp_path / "output.json"
    ip.write_text(json.dumps(i))
    op.write_text(json.dumps(o))
    assert main(["inspect", str(source), "--output", str(tmp_path / "bundle")]) == 0
    path = tmp_path / "draft.json"
    args = [
        "draft",
        str(source),
        "--tool",
        "tool.a",
        "--input-sample",
        str(ip),
        "--output-sample",
        str(op),
        "--output",
        str(path),
    ]
    assert main(args) == 0
    assert main(["check", str(path), "--input-sample", str(ip), "--output-sample", str(op)]) == 0
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        main(args)
    assert path.read_bytes() == before
