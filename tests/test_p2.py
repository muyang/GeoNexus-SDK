"""Tests for GeoKG knowledge graph and OSM adapter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from geonexus.adapters.osm import (
    OSMOverpassError,
    osm_to_geocard,
    query_osm,
    register_osm_contracts,
)
from geonexus.gaag import GAAGRegistry
from geonexus.kg import KGEntity, KnowledgeGraph

# --------------------------------------------------------------------------- #
# GeoKG
# --------------------------------------------------------------------------- #

class TestKnowledgeGraph:
    def test_seed_demo_creates_entities(self):
        kg = KnowledgeGraph("test").seed_demo()
        assert kg.entity_count() >= 6  # Regions + SDGs + Skills
        assert kg.relation_count() >= 2

    def test_search_by_type(self):
        kg = KnowledgeGraph("test").seed_demo()
        regions = kg.search_by_type("Region")
        assert len(regions) == 3
        assert all(e.type == "Region" for e in regions)

    def test_search_by_label(self):
        kg = KnowledgeGraph("test").seed_demo()
        sdgs = kg.search_by_label("sdg")
        assert len(sdgs) == 3

    def test_neighbors(self):
        kg = KnowledgeGraph("test")
        kg.add_entity(KGEntity("a", "Skill", {"name": "A"}))
        kg.add_entity(KGEntity("b", "DataProduct", {"name": "B"}))
        kg.add_relation("a", "b", "DEPENDS_ON")

        neighbors = kg.neighbors("a", "DEPENDS_ON")
        assert len(neighbors) == 1
        assert neighbors[0][0].id == "b"

    def test_search_keyword(self):
        kg = KnowledgeGraph("test").seed_demo()
        results = kg.search("flood")
        assert any("flood" in e.id for e in results)

    def test_stats(self):
        kg = KnowledgeGraph("test").seed_demo()
        stats = kg.stats()
        assert "by_type" in stats
        assert "Region" in stats["by_type"]
        assert stats["name"] == "test"

    def test_import_from_gaag(self, tmp_path):
        import numpy as np
        import rasterio
        from rasterio.transform import from_bounds

        # Create a test raster and register in GAAG
        path = str(tmp_path / "r.tif")
        data = np.full((16, 16), 0.5, np.float32)
        with rasterio.open(path, "w", driver="GTiff", dtype=np.float32, count=1,
                           width=16, height=16, crs="EPSG:4326",
                           transform=from_bounds(-10, -5, 10, 5, 16, 16)) as dst:
            dst.write(data, 1)

        registry = GAAGRegistry()
        registry.register_asset(path, description="test raster for KG import")
        assert registry.count() == 1

        kg = KnowledgeGraph("import-test")
        count = kg.import_from_gaag(registry)
        assert count == 1
        entities = kg.search_by_type("DataProduct")
        assert len(entities) == 1


# --------------------------------------------------------------------------- #
# OSM Adapter
# --------------------------------------------------------------------------- #

def _mock_osm_element(eid=123, tags=None):
    return {"id": eid, "type": "way", "tags": tags or {"building": "yes", "name": "Test Building"}}


class TestOSMToGeoCard:
    def test_converts_osm_to_geocard(self):
        element = _mock_osm_element(123, {"building": "yes", "name": "City Hall"})
        geocard = osm_to_geocard(element, "building", [-5, -2, 5, 2])
        assert geocard.id == "osm.123"
        assert "openstreetmap" in geocard.tags
        assert geocard.spatial.bbox == [-5, -2, 5, 2]
        assert geocard.access.endpoint == "https://www.openstreetmap.org/way/123"

    def test_converts_node_type(self):
        element = {"id": 456, "type": "node", "tags": {"amenity": "school"}}
        geocard = osm_to_geocard(element, "building", [0, 0, 1, 1])
        assert geocard.id == "osm.456"

    def test_no_tags_fallback(self):
        element = {"id": 789, "type": "way", "tags": {}}
        geocard = osm_to_geocard(element, "water", [10, 20, 30, 40])
        assert "water" in geocard.description


class TestOSMQuery:
    def test_mock_query(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"elements": [_mock_osm_element()]}
        mock_client.post.return_value = mock_resp

        result = query_osm([-5, -2, 5, 2], "building", client=mock_client)
        assert len(result["elements"]) == 1

    def test_query_raises_on_error(self):
        mock_client = MagicMock()
        mock_client.post.side_effect = ConnectionError("no network")
        with pytest.raises(OSMOverpassError):
            query_osm([0, 0, 1, 1], "building", client=mock_client)

    def test_invalid_query_type(self):
        with pytest.raises(ValueError, match="query_type"):
            query_osm([0, 0, 1, 1], "invalid_type")


class TestOSMRegister:
    def test_register_into_gaag(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"elements": [_mock_osm_element(123)]}
        mock_client.post.return_value = mock_resp

        registry = GAAGRegistry()
        contracts = register_osm_contracts(registry, [-5, -2, 5, 2], "building", client=mock_client)
        assert len(contracts) == 1
        assert "osm" in contracts[0].contract_id
        assert contracts[0].asset_type == "vector"
        assert registry.count() == 1