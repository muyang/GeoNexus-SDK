"""Tests for GAAG pgvector store and backend switching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from geonexus.gaag import GAAGContract, GAAGRegistry
from geonexus.gaag.embed import embed_text
from geonexus.gaag.pgvector_store import SCHEMA_SQL, PgVectorStore


def _make_raster(path, val=0.5, w=16, h=16):
    data = np.full((h, w), val, dtype=np.float32)
    with rasterio.open(path, "w", driver="GTiff", dtype=rasterio.float32, count=1,
                       width=w, height=h, crs="EPSG:4326",
                       transform=from_bounds(-10, -5, 10, 5, w, h)) as dst:
        dst.write(data, 1)
    return str(path)


def _make_contract(contract_id, desc, bbox=None):
    from geonexus.geocard import GeoCardBuilder

    b = bbox or [-10, -5, 10, 5]
    card = (
        GeoCardBuilder(
            id=contract_id, type="data", name=contract_id, description=desc,
        )
        .spatial(bbox=b, crs="EPSG:4326")
        .tag("test")
        .build()
    )
    return GAAGContract(
        contract_id=contract_id, asset_type="raster", card=card,
        scanned_meta={"bbox": b}, semantic_embedding=embed_text(desc),
        provenance="test",
    )


# --------------------------------------------------------------------------- #
# PgVectorStore with Mock
# --------------------------------------------------------------------------- #

class TestPgVectorStore:
    def test_schema_sql_contains_extensions(self):
        assert "CREATE EXTENSION" in SCHEMA_SQL
        assert "vector" in SCHEMA_SQL
        assert "postgis" in SCHEMA_SQL

    def test_schema_has_embedding_dim(self):
        assert "vector(64)" in SCHEMA_SQL

    def test_schema_has_spatial_index(self):
        assert "USING GIST (bbox)" in SCHEMA_SQL

    def test_schema_has_ivfflat(self):
        assert "ivfflat" in SCHEMA_SQL.lower()

    @patch("geonexus.gaag.pgvector_store.psycopg2")
    def test_init_with_dsn(self, mock_psycopg2):
        store = PgVectorStore(dsn="postgresql://localhost/test")
        assert store.dsn == "postgresql://localhost/test"

    @patch("geonexus.gaag.pgvector_store.psycopg2")
    def test_from_env_empty(self, mock_psycopg2):
        store = PgVectorStore.from_env()
        assert store is None  # No DSN set

    @patch("geonexus.gaag.pgvector_store.psycopg2")
    def test_register_calls_insert(self, mock_psycopg2):
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn
        mock_psycopg2.extras.register_vector.return_value = None

        store = PgVectorStore(dsn="postgresql://localhost/test")
        contract = _make_contract("test-01", "test raster")

        result = store.register(contract)
        assert result.contract_id == "test-01"
        # Verify INSERT was called
        mock_conn.cursor.return_value.__enter__.return_value.execute.assert_called()


# --------------------------------------------------------------------------- #
# GAAGRegistry Backend Switching
# --------------------------------------------------------------------------- #

class TestRegistryBackend:
    def test_default_backend_is_memory(self, tmp_path):
        registry = GAAGRegistry()
        registry.register_asset(_make_raster(str(tmp_path / "r.tif")))
        assert registry.count() == 1

    def test_pgvector_backend_fallback_to_memory(self):
        """No POSTGIS_DSN → falls back to memory."""
        registry = GAAGRegistry(backend="pgvector")
        assert registry._backend == "memory"  # Fallback
        assert registry._pgvector is None

    @patch("geonexus.gaag.pgvector_store.psycopg2")
    def test_pgvector_backend_initializes(self, mock_psycopg2):
        """With POSTGIS_DSN set → pgvector backend active."""
        import os
        os.environ["POSTGIS_DSN"] = "postgresql://mock/mock"
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn
        mock_psycopg2.extras.register_vector.return_value = None

        registry = GAAGRegistry(backend="pgvector")
        assert registry._backend == "pgvector"
        assert registry._pgvector is not None

    def test_search_semantic_bbox_filter_memory(self, tmp_path):
        """Memory backend supports optional bbox filter in search."""
        registry = GAAGRegistry()
        c1 = _make_contract("c1", "flood mekong", bbox=[-5, -2, 5, 2])
        c2 = _make_contract("c2", "urban nairobi", bbox=[30, 0, 40, 5])
        registry.register(c1)
        registry.register(c2)

        # Search within c1's bbox → should only return c1
        results = registry.search_semantic("flood", k=5, bbox=[-3, -1, 3, 1])
        contract_ids = [c.contract_id for c, _ in results]
        assert "c1" in contract_ids
        assert "c2" not in contract_ids  # bbox too far


# --------------------------------------------------------------------------- #
# Contract serialization round-trip
# --------------------------------------------------------------------------- #

class TestContractRoundTrip:
    def test_contract_to_json_roundtrip(self, tmp_path):
        contract = _make_contract("test-round", "test description")
        d = contract.to_dict()
        assert d["contract_id"] == "test-round"
        assert "card" in d
        assert d["asset_type"] == "raster"