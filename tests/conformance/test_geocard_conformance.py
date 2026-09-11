"""GeoCard conformance tests (v1.0).

Runs the official schema directly (jsonschema) against the vector suite in
``geocard_vectors`` — independent of the SDK's pydantic models, so a
conforming implementation of the spec would pass the same vectors.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from geocard_vectors import (
    INVALID_CASE_IDS,
    INVALID_VECTORS,
    VALID_CASE_IDS,
    VALID_VECTORS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCHEMA = json.loads((REPO_ROOT / "schemas" / "geocard.schema.json").read_text("utf-8"))
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA)


@pytest.mark.parametrize("card", VALID_VECTORS, ids=VALID_CASE_IDS)
def test_geocard_valid_vectors(card: dict) -> None:
    errors = sorted(_VALIDATOR.iter_errors(card), key=lambda e: str(e.path))
    assert not errors, f"expected valid, got: {[e.message for e in errors]}"


@pytest.mark.parametrize("card", [v for _, v in INVALID_VECTORS], ids=INVALID_CASE_IDS)
def test_geocard_invalid_vectors(card: dict) -> None:
    errors = list(_VALIDATOR.iter_errors(card))
    assert errors, "expected invalid, but the card validates"


def test_geocard_conformance_suite_is_nonempty() -> None:
    assert len(VALID_VECTORS) >= 5
    assert len(INVALID_VECTORS) >= 10
