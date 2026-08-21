"""Tests for the STAC adapter (GeoCard import)."""

from __future__ import annotations

import json

import httpx
import pytest

from geonexus.adapters import (
    StacAdapterError,
    fetch_stac_item,
    import_stac_item,
    stac_item_to_geocard,
)


def _stac_item() -> dict:
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": "S2A_T21LUN_20200101T123456",
        "collection": "sentinel-2-l2a",
        "bbox": [-54.5, -2.5, -53.5, -1.5],
        "properties": {
            "datetime": "2020-01-01T12:34:56Z",
            "start_datetime": "2020-01-01T12:34:56Z",
            "end_datetime": "2020-01-01T12:34:56Z",
            "title": "Sentinel-2 L2A tile (test)",
            "description": "A sample STAC item for adapter tests.",
            "gsd": 10,
            "proj:epsg": 32721,
            "license": "proprietary",
        },
        "assets": {
            "B04": {
                "href": "https://example.com/B04.tif",
                "type": "image/tiff; application=geotiff",
                "eo:bands": [{"name": "B04", "data_type": "uint16", "unit": "dn"}],
            },
            "B08": {
                "href": "https://example.com/B08.tif",
                "type": "image/tiff; application=geotiff",
                "eo:bands": [{"name": "B08", "data_type": "uint16", "unit": "dn"}],
            },
        },
        "links": [{"rel": "self", "href": "https://example.com/items/S2A_T21LUN_20200101T123456"}],
    }


def test_stac_item_to_geocard() -> None:
    """STAC fields map onto the correct GeoCard sections."""
    card = stac_item_to_geocard(_stac_item())
    assert card.id == "S2A_T21LUN_20200101T123456"
    assert card.type == "data"
    assert card.name == "Sentinel-2 L2A tile (test)"
    assert "stac" in card.tags

    assert card.spatial is not None
    assert card.spatial.bbox == [-54.5, -2.5, -53.5, -1.5]
    assert card.spatial.crs == "EPSG:32721"
    assert card.spatial.resolution == 10

    assert card.temporal is not None
    assert card.temporal.start == "2020-01-01T12:34:56Z"
    assert card.temporal.end == "2020-01-01T12:34:56Z"

    assert card.band_names() == ["B04", "B08"]
    assert card.bands[0].dtype == "uint16"

    assert card.access is not None
    assert card.access.protocol == "stac"
    assert card.access.format == "image/tiff; application=geotiff"
    assert card.access.endpoint == "https://example.com/items/S2A_T21LUN_20200101T123456"

    assert card.provenance is not None
    assert "sentinel-2-l2a" in card.provenance.lineage
    assert card.license is not None and card.license.name == "proprietary"

    # The imported card conforms to the official GeoCard schema.
    card.validate()


def test_stac_node_url_override() -> None:
    card = stac_item_to_geocard(_stac_item(), node_url="http://127.0.0.1:8787")
    assert card.access is not None
    assert card.access.endpoint == "http://127.0.0.1:8787"


def test_stac_import_from_file(tmp_path) -> None:
    """import_stac_item works from a local file."""
    path = tmp_path / "item.json"
    path.write_text(json.dumps(_stac_item()), encoding="utf-8")
    card = import_stac_item(str(path))
    assert card.id == "S2A_T21LUN_20200101T123456"
    assert card.band_names() == ["B04", "B08"]


def test_stac_import_from_url(tmp_path) -> None:
    """import_stac_item works from a URL (via injected mock transport)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_stac_item())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    card = import_stac_item("https://example.test/item.json", client=client)
    assert card.id == "S2A_T21LUN_20200101T123456"


def test_stac_invalid_source() -> None:
    with pytest.raises(StacAdapterError):
        fetch_stac_item("/nonexistent/path.json")

    def not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(not_found))
    with pytest.raises(StacAdapterError):
        fetch_stac_item("https://example.test/missing.json", client=client)
