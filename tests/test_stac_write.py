"""Tests for the STAC write-side adapter (GeoCard -> STAC)."""

from __future__ import annotations

import json

from geonexus.adapters import (
    geocard_to_stac_catalog,
    geocard_to_stac_item,
    import_stac_item,
    save_stac_item,
)
from geonexus.geocard import GeoCardBuilder


def _card() -> GeoCardBuilder:
    return (
        GeoCardBuilder(
            id="sentinel-2-amazon",
            type="data",
            name="Sentinel-2 Amazon",
            description="Synthetic Sentinel-2 imagery.",
        )
        .spatial(
            bbox=[-73.9, -15.0, -44.0, 5.0],
            crs="EPSG:4326",
            resolution=10,
        )
        .temporal(start="2015-01-01T00:00:00Z", end="2025-12-31T00:00:00Z")
        .band("B04", dtype="uint16", units="dn")
        .band("B08", dtype="uint16", units="dn")
        .capability("ndvi")
        .license(name="CC-BY-4.0")
        .access(protocol="geomcp", endpoint="https://node.example/asset")
        .build()
    )


def test_geocard_to_stac_item() -> None:
    """GeoCard fields map onto a STAC Item."""
    card = _card()
    item = geocard_to_stac_item(card, collection="amazon")

    assert item["type"] == "Feature"
    assert item["stac_version"] == "1.0.0"
    assert item["id"] == "sentinel-2-amazon"
    assert item["collection"] == "amazon"

    assert item["bbox"] == [-73.9, -15.0, -44.0, 5.0]
    geometry = item["geometry"]
    assert geometry["type"] == "Polygon"
    assert geometry["coordinates"][0][0] == [-73.9, -15.0]

    props = item["properties"]
    assert props["title"] == "Sentinel-2 Amazon"
    assert props["description"] == "Synthetic Sentinel-2 imagery."
    assert props["license"] == "CC-BY-4.0"
    assert props["proj:epsg"] == 4326
    assert props["gsd"] == 10
    assert props["start_datetime"] == "2015-01-01T00:00:00Z"
    assert props["end_datetime"] == "2025-12-31T00:00:00Z"
    assert props["geonexus:type"] == "data"
    assert props["geonexus:capabilities"] == ["ndvi"]

    # Assets: one per band, carrying eo:bands metadata.
    assert set(item["assets"]) == {"B04", "B08"}
    assert item["assets"]["B04"]["href"] == "https://node.example/asset"
    assert item["assets"]["B04"]["eo:bands"][0]["data_type"] == "uint16"

    # Links reference the access endpoint.
    assert item["links"][0]["href"] == "https://node.example/asset"


def test_geocard_to_stac_item_id_sanitization() -> None:
    card = GeoCardBuilder(id="lakes:feature/1", type="data", name="x", description="d").build()
    item = geocard_to_stac_item(card)
    assert item["id"] == "lakes-feature-1"  # ':' and '/' replaced


def test_geocard_to_stac_catalog() -> None:
    """A catalog bundles items and links them."""
    result = geocard_to_stac_catalog([_card()], catalog_id="geo catalog", collection="amazon")
    catalog = result["catalog"]
    assert catalog["type"] == "Catalog"
    assert catalog["id"] == "geo-catalog"
    assert len(result["items"]) == 1
    item_links = [link for link in catalog["links"] if link["rel"] == "item"]
    assert item_links[0]["href"] == "items/sentinel-2-amazon.json"


def test_stac_export_import_roundtrip(tmp_path) -> None:
    """Export -> import returns a valid GeoCard with preserved fields."""
    card = _card()
    item = geocard_to_stac_item(card, collection="amazon")
    path = tmp_path / "item.json"
    save_stac_item(item, path)
    assert path.exists()

    # Import back via the read adapter; key fields survive the round trip.
    imported = import_stac_item(str(path))
    assert imported.id == "sentinel-2-amazon"
    assert imported.spatial is not None
    assert imported.spatial.bbox == [-73.9, -15.0, -44.0, 5.0]
    assert imported.name == "Sentinel-2 Amazon"
    assert imported.temporal is not None
    assert imported.temporal.start == "2015-01-01T00:00:00Z"
    assert imported.band_names() == ["B04", "B08"]


def test_stac_item_valid_json_shape(tmp_path) -> None:
    """The exported item is valid JSON with the expected structure."""
    item = geocard_to_stac_item(_card())
    raw = json.dumps(item)
    parsed = json.loads(raw)
    assert parsed["id"] == item["id"]
    assert "assets" in parsed and "properties" in parsed
