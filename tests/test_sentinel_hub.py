"""Tests for Sentinel Hub / Copernicus Data Space adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from geonexus.adapters.sentinel_hub import (
    S2_BANDS,
    SentinelHubError,
    register_sentinel2_contracts,
    search_sentinel2,
    stac_item_to_geocard,
)
from geonexus.gaag import GAAGRegistry


def _mock_stac_feature(item_id="S2A_MSIL2A_20250101", cloud=15):
    """Create a realistic STAC Feature for Sentinel-2."""
    return {
        "id": item_id,
        "bbox": [-10.5, -5.2, 10.8, 5.0],
        "type": "Feature",
        "collection": "sentinel-2-l2a",
        "properties": {
            "datetime": "2025-01-01T10:30:00Z",
            "platform": "sentinel-2a",
            "eo:cloud_cover": cloud,
            "proj:epsg": 32630,
        },
        "assets": {
            "B04": {"eo:bands": [{"name": "B04", "data_type": "uint16"}]},
            "B08": {"eo:bands": [{"name": "B08", "data_type": "uint16"}]},
            "visual": {"href": "https://example.com/visual.tif"},
        },
        "links": [],
    }


class TestStacToGeoCard:
    def test_maps_stac_to_geocard(self):
        feature = _mock_stac_feature()
        geocard = stac_item_to_geocard(feature)
        assert geocard.id == "s2.S2A_MSIL2A_20250101"
        assert geocard.spatial.bbox == [-10.5, -5.2, 10.8, 5.0]
        assert geocard.spatial.crs == "EPSG:32630"
        assert geocard.spatial.resolution == 10.0
        assert len(geocard.bands) == 2

    def test_marks_as_data_type(self):
        geocard = stac_item_to_geocard(_mock_stac_feature())
        assert geocard.type == "data"

    def test_visual_href(self):
        feature = _mock_stac_feature()
        feature["assets"]["visual"]["href"] = "https://cdn.example.com/tci.tif"
        geocard = stac_item_to_geocard(feature)
        assert geocard.access.endpoint == "https://cdn.example.com/tci.tif"


class TestSentinel2Search:
    def test_mock_search(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"features": [_mock_stac_feature()]}
        mock_client.post.return_value = mock_response

        features = search_sentinel2([-10, -5, 10, 5], client=mock_client)
        assert len(features) == 1
        assert features[0]["id"] == "S2A_MSIL2A_20250101"

    def test_search_raises_on_error(self):
        mock_client = MagicMock()
        mock_client.post.side_effect = ConnectionError("no network")
        with pytest.raises(SentinelHubError):
            search_sentinel2([-10, -5, 10, 5], client=mock_client)


class TestRegisterContracts:
    def test_registers_into_gaag(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"features": [_mock_stac_feature()]}
        mock_client.post.return_value = mock_response

        registry = GAAGRegistry()
        contracts = register_sentinel2_contracts(registry, [-10, -5, 10, 5], client=mock_client)
        assert len(contracts) == 1
        assert "S2A" in contracts[0].card.id  # GeoCard id has s2. prefix
        assert contracts[0].asset_type == "raster"
        assert registry.count() == 1


class TestS2Bands:
    def test_key_bands_defined(self):
        assert "B04" in S2_BANDS  # Red
        assert "B08" in S2_BANDS  # NIR
        assert "B11" in S2_BANDS  # SWIR
        assert S2_BANDS["B08"]["resolution_m"] == 10