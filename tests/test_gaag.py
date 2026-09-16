"""Tests for the GAAG contract registry (scan → register → search → gate)."""

from __future__ import annotations

import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from geonexus.gaag import (
    ContractGate,
    GAAGError,
    GAAGRegistry,
    cosine_similarity,
    embed_card,
    scan_asset_to_contract,
    scan_raster,
    scan_vector,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_raster(path, width=32, height=24, val=0.5, crs="EPSG:4326"):
    data = np.full((height, width), val, dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        dtype=rasterio.float32,
        count=3,
        width=width,
        height=height,
        crs=crs,
        transform=from_bounds(-10, -5, 10, 5, width, height),
    ) as dst:
        dst.write(data, 1)
        dst.write(data * 0.5, 2)
        dst.write(data * 0.2, 3)
    return path


def _make_geojson(path):
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [101.0, 4.0]},
                "properties": {"name": "point-a"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[100, 3], [102, 3], [102, 5], [100, 5], [100, 3]]]},
                "properties": {"name": "poly-b"},
            },
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #


class TestScanner:
    def test_scan_raster_metadata(self, tmp_path):
        path = _make_raster(str(tmp_path / "forest.tif"))
        meta = scan_raster(path)

        assert meta["driver"] == "GTiff"
        assert meta["crs"] == "EPSG:4326"
        assert meta["bbox"] == [-10.0, -5.0, 10.0, 5.0]
        assert meta["count"] == 3
        assert len(meta["bands"]) == 3
        assert meta["width"] == 32
        assert meta["height"] == 24
        assert meta["resolution"] > 0

    def test_scan_geojson_metadata(self, tmp_path):
        path = _make_geojson(str(tmp_path / "regions.geojson"))
        meta = scan_vector(path)

        assert meta["driver"] == "GeoJSON"
        assert meta["crs"] == "EPSG:4326"
        assert meta["count"] == 2
        assert "Point" in meta["geometry_types"]
        assert "Polygon" in meta["geometry_types"]
        assert meta["bbox"][0] == 100.0  # west

    def test_scan_asset_to_contract(self, tmp_path):
        path = _make_raster(str(tmp_path / "landsat.tif"))
        contract = scan_asset_to_contract(path, description="Landsat sample")

        assert contract.asset_type == "raster"
        assert contract.card.id == "contract.landsat"
        assert contract.card.spatial.crs == "EPSG:4326"
        assert contract.card.spatial.bbox == [-10.0, -5.0, 10.0, 5.0]
        assert len(contract.card.bands) == 3
        assert contract.provenance.startswith("scanned:")


# --------------------------------------------------------------------------- #
# Embedding
# --------------------------------------------------------------------------- #


class TestEmbedding:
    def test_embed_text_deterministic(self):
        a = embed_card_proxy(["flood", "mekong", "water"])
        # deterministic: same tokens → same vector
        assert a == a

    def test_cosine_similarity_same(self):
        from geonexus.gaag.embed import embed_text

        v1 = embed_text("mekong flood water extent")
        v2 = embed_text("mekong flood water extent")
        assert cosine_similarity(v1, v2) > 0.99

    def test_cosine_similarity_different(self):
        from geonexus.gaag.embed import embed_text

        v1 = embed_text("urban heat island nairobi")
        v2 = embed_text("amazon rainforest ndvi")
        assert cosine_similarity(v1, v2) < 0.5

    def test_embed_dim(self):
        from geonexus.gaag.embed import embed_text

        vec = embed_text("test")
        assert len(vec) == 64


def embed_card_proxy(tokens):
    """Small helper: build a card and embed it."""
    from geonexus.geocard.builder import GeoCardBuilder

    card = (
        GeoCardBuilder(id="x", type="data", name=tokens[0], description=" ".join(tokens))
        .tag(*tokens)
        .build()
    )
    return embed_card(card)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class TestRegistry:
    def test_register_and_get(self, tmp_path):
        registry = GAAGRegistry()
        path = _make_raster(str(tmp_path / "a.tif"))
        contract = registry.register_asset(path, description="Test raster")
        assert registry.count() == 1
        assert registry.get("contract.a") is contract

    def test_duplicate_register_raises(self, tmp_path):
        registry = GAAGRegistry()
        path = _make_raster(str(tmp_path / "a.tif"))
        registry.register_asset(path)
        with pytest.raises(GAAGError):
            registry.register_asset(path)

    def test_list_by_type(self, tmp_path):
        registry = GAAGRegistry()
        registry.register_asset(_make_raster(str(tmp_path / "r.tif")))
        registry.register_asset(_make_geojson(str(tmp_path / "v.geojson")))
        assert len(registry.list("raster")) == 1
        assert len(registry.list("vector")) == 1
        assert len(registry.list()) == 2

    def test_semantic_search(self, tmp_path):
        registry = GAAGRegistry()
        flood = registry.register_asset(
            _make_raster(str(tmp_path / "flood.tif")),
            description="Mekong flood water extent map",
        )
        registry.register_asset(
            _make_raster(str(tmp_path / "urban.tif")),
            description="Nairobi urban heat island zones",
        )
        # attach embeddings
        flood.semantic_embedding = embed_flood()
        results = registry.search_semantic("mekong flood")
        assert results[0][0].contract_id == "contract.flood"
        assert results[0][1] > 0.3
        assert len(results) == 2

    def test_sqlite_persistence(self, tmp_path):
        db_path = str(tmp_path / "gaag.db")
        registry = GAAGRegistry(persist_path=db_path)
        registry.register_asset(_make_raster(str(tmp_path / "r.tif")))

        registry2 = GAAGRegistry(persist_path=db_path)
        assert registry2.count() == 1
        assert registry2.get("contract.r") is not None


def embed_flood():
    from geonexus.gaag.embed import embed_text

    return embed_text("mekong flood water extent")


# --------------------------------------------------------------------------- #
# Contract gate
# --------------------------------------------------------------------------- #


class TestContractGate:
    def test_gate_pass(self, tmp_path):
        registry = GAAGRegistry()
        contract = registry.register_asset(
            _make_raster(str(tmp_path / "flood.tif")),
            description="Mekong flood water extent map, EPSG:4326",
        )
        contract.semantic_embedding = embed_flood()

        gate = ContractGate(semantic_threshold=0.2)
        result = gate.gate(contract, "mekong flood water",
                           bbox=[-5, -2, 5, 2],
                           crs="EPSG:4326",
                           required_bands=["B01", "B02", "B03"])
        assert result.passed
        assert result.semantic_ok
        assert result.contract_ok

    def test_gate_fail_semantic(self, tmp_path):
        registry = GAAGRegistry()
        contract = registry.register_asset(
            _make_raster(str(tmp_path / "urban.tif")),
            description="urban heat island",
        )
        from geonexus.gaag.embed import embed_text

        contract.semantic_embedding = embed_text("urban heat island")

        gate = ContractGate(semantic_threshold=0.7)
        result = gate.gate(contract, "mekong flood")
        assert not result.passed
        assert not result.semantic_ok

    def test_gate_fail_contract(self, tmp_path):
        registry = GAAGRegistry()
        contract = registry.register_asset(
            _make_raster(str(tmp_path / "flood.tif")),
            description="Mekong flood water extent map",
        )
        contract.semantic_embedding = embed_flood()

        gate = ContractGate(semantic_threshold=0.2)
        # bbox far away (+200 lon) → contract not satisfied
        result = gate.gate(contract, "mekong flood",
                           bbox=[120, 30, 130, 40],
                           crs="EPSG:4326")
        assert not result.passed
        assert result.semantic_ok  # semantic passes
        assert not result.contract_ok  # contract fails

    def test_full_pipeline(self, tmp_path):
        """scan → embed → register → search → gate 完整流水线。"""
        registry = GAAGRegistry()
        contract = registry.register_asset(
            _make_raster(str(tmp_path / "crop.tif")),
            description="Punjab crop NDVI fields",
        )
        from geonexus.gaag.embed import embed_card

        contract.semantic_embedding = embed_card(contract.card)

        # search
        results = registry.search_semantic("punjab crop", k=1)
        assert results[0][0].contract_id == "contract.crop"

        # gate
        gate = ContractGate(semantic_threshold=0.1)
        result = gate.gate(contract, "punjab crop ndvi",
                           bbox=[-5, -2, 5, 2],
                           crs="EPSG:4326")
        assert result.passed


# --------------------------------------------------------------------------- #
# Register scan + registration via one-liner used by the execution plane
# --------------------------------------------------------------------------- #


class TestPipelineConvenience:
    def test_register_asset_returns_dictable(self, tmp_path):
        registry = GAAGRegistry()
        contract = registry.register_asset(
            _make_raster(str(tmp_path / "demo.tif")),
            description="demo asset",
        )
        d = contract.to_dict()
        assert d["contract_id"] == "contract.demo"
        assert d["asset_type"] == "raster"
        assert "card" in d
        assert d["card"]["spatial"]["crs"] == "EPSG:4326"