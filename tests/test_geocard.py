"""Tests for the GeoCard SDK: schema validation, builder, save/load."""

from __future__ import annotations

import pytest

from geonexus.geocard import (
    GeoCard,
    GeoCardBuilder,
    GeoCardValidationError,
    load_geocard,
    validate_card_schema,
)


def _valid_dict() -> dict:
    return {
        "geocard_version": "0.1",
        "id": "test-card",
        "type": "data",
        "name": "Test Card",
        "description": "A test card.",
        "spatial": {
            "bbox": [-73.9, -15.0, -44.0, 5.0],
            "crs": "EPSG:4326",
            "resolution": 10,
        },
        "temporal": {"start": "2015-01-01", "end": "2025-12-31"},
        "bands": [
            {"name": "B04", "dtype": "uint16", "units": "dn"},
            {"name": "B08", "dtype": "uint16", "units": "dn"},
        ],
        "capabilities": ["ndvi"],
    }


def test_geocard_schema_validation() -> None:
    """A conformant card validates; missing required fields do not."""
    report = validate_card_schema(_valid_dict())
    assert report.valid
    assert report.errors == []

    # Missing required field.
    bad = _valid_dict()
    del bad["id"]
    report = validate_card_schema(bad)
    assert not report.valid
    assert any("'id'" in e or "id" in e for e in report.errors)

    # Invalid asset type.
    bad = _valid_dict()
    bad["type"] = "not-a-real-type"
    assert not validate_card_schema(bad).valid

    # Wrong geocard version.
    bad = _valid_dict()
    bad["geocard_version"] = "9.9"
    assert not validate_card_schema(bad).valid


def test_geocard_builder() -> None:
    """The fluent builder produces a valid, correctly-populated card."""
    card = (
        GeoCardBuilder(
            id="sentinel-2-amazon",
            type="data",
            name="Sentinel-2 Amazon",
            description="Sentinel-2 imagery covering Amazon rainforest",
        )
        .tag("synthetic", "amazon")
        .spatial(
            bbox=[-73.9, -15.0, -44.0, 5.0],
            crs="EPSG:4326",
            resolution=10,
        )
        .temporal(start="2015-01-01", end="2025-12-31", interval="P5D")
        .band("B04", dtype="uint16", units="dn")
        .band("B08", dtype="uint16", units="dn")
        .capability("ndvi")
        .capability("change-detection")
        .access(protocol="geomcp", endpoint="http://127.0.0.1:8787")
        .build()
    )

    assert card.id == "sentinel-2-amazon"
    assert card.type == "data"
    assert card.spatial is not None
    assert card.spatial.crs == "EPSG:4326"
    assert card.spatial.bbox == [-73.9, -15.0, -44.0, 5.0]
    assert card.spatial.resolution == 10
    assert card.temporal is not None
    assert card.temporal.start == "2015-01-01"
    assert card.band_names() == ["B04", "B08"]
    assert card.capability_names() == ["ndvi", "change-detection"]
    assert card.tags == ["synthetic", "amazon"]
    assert card.access is not None and card.access.protocol == "geomcp"

    # Must pass the official schema.
    card.validate()


def test_geocard_save_load(tmp_path) -> None:
    """Save and load round-trips YAML and JSON files."""
    card = (
        GeoCardBuilder(
            id="roundtrip-card",
            type="data",
            name="Roundtrip",
            description="Round-trip test card.",
        )
        .spatial(bbox=[0, 0, 1, 1], crs="EPSG:4326", resolution=30)
        .band("B01", dtype="uint16")
        .build()
    )

    yaml_path = tmp_path / "card.yaml"
    card.save(str(yaml_path))
    loaded = GeoCard.load(str(yaml_path))
    assert loaded.id == card.id
    assert loaded.to_dict() == card.to_dict()

    json_path = tmp_path / "card.json"
    card.save(str(json_path))
    loaded_json = GeoCard.load(str(json_path))
    assert loaded_json.id == card.id
    assert loaded_json.spatial is not None
    assert loaded_json.spatial.resolution == 30


def test_geocard_load_rejects_invalid(tmp_path) -> None:
    """Loading a schema-invalid card raises GeoCardValidationError."""
    import json

    # Schema-invalid but structurally loadable: wrong asset type.
    bad = _valid_dict()
    bad["type"] = "not-a-real-type"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(GeoCardValidationError):
        load_geocard(str(path))
    # Non-strict load skips the schema check but still parses the structure.
    card = load_geocard(str(path), validate=False)
    assert card.id == "test-card"
    assert card.type == "not-a-real-type"

    # A card missing a required top-level field fails schema validation too.
    bad2 = _valid_dict()
    del bad2["name"]
    path2 = tmp_path / "bad2.json"
    path2.write_text(json.dumps(bad2), encoding="utf-8")
    with pytest.raises(GeoCardValidationError):
        load_geocard(str(path2))
