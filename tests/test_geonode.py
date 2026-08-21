"""Tests for the Local GeoNode: registries, runtime and the NDVI skill."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from examples.amazon_ndvi import skill as ndvi_skill

from geonexus.geocard import GeoCardBuilder, load_geocard
from geonexus.geomcp import GeoMCPClient, GeoMCPClientError
from geonexus.geonode import DuplicateEntryError, GeoNode, Skill


def _card():
    return (
        GeoCardBuilder(
            id="sentinel-2-amazon",
            type="data",
            name="Sentinel-2 Amazon",
            description="Test card.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .band("B04")
        .build()
    )


def test_geonode_registry() -> None:
    """GeoCard and Skill registries behave correctly."""
    node = GeoNode(name="registry-test")

    node.register_geocard(_card())
    assert node.geocard_registry.count() == 1
    assert node.geocard_registry.get("sentinel-2-amazon") is not None
    assert "sentinel-2-amazon" in node.geocard_registry
    assert len(node.geocard_registry.list_cards()) == 1
    assert len(node.geocard_registry.search(capability="ndvi")) == 0  # no caps on card

    with pytest.raises(DuplicateEntryError):
        node.register_geocard(_card())

    def handler(params, context):
        return {"pong": True}

    node.register_skill(
        name="ping",
        handler=handler,
        description="A ping skill",
        input_schema={"required": []},
    )
    assert node.skill_registry.has("ping")
    assert node.skill_registry.count() == 1
    assert node.skill_registry.require("ping").name == "ping"

    # register_skill_object accepts a pre-built Skill.
    node.register_skill_object(Skill(name="pong", description="another", handler=handler))
    assert node.skill_registry.count() == 2

    # Duplicate names are rejected.
    with pytest.raises(DuplicateEntryError):
        node.register_skill(name="ping", handler=handler, input_schema={"required": []})

    # Capabilities / describe / health work on a bare node.
    caps = node.capabilities()
    assert caps["node"] == "registry-test"
    assert node.health()["status"] == "ok"
    assert node.describe()["geocards"][0]["id"] == "sentinel-2-amazon"


def test_ndvi_numpy_core() -> None:
    """The numpy NDVI core is correct."""
    red = np.full((4, 4), 0.1, dtype=np.float32)
    nir = np.full((4, 4), 0.4, dtype=np.float32)
    ndvi = ndvi_skill.compute_ndvi_arrays(red, nir)
    assert np.allclose(ndvi, 0.6)

    # Zero denominator -> nodata, not NaN/Inf.
    ndvi = ndvi_skill.compute_ndvi_arrays(np.zeros((2, 2)), np.zeros((2, 2)))
    assert np.all(ndvi == ndvi_skill.NDVI_NODATA)

    # Values clip to [-1, 1].
    ndvi = ndvi_skill.compute_ndvi_arrays(np.zeros((2, 2)), np.ones((2, 2)) * 100)
    assert np.allclose(ndvi, 1.0)


def test_ndvi(tmp_path) -> None:
    """Full NDVI skill execution through a GeoNode over HTTP (synthetic data)."""
    out_dir = Path(tmp_path)
    scenes = ndvi_skill.generate_synthetic_scene(2015, out_dir, width=48, height=32, seed=7)
    assert Path(scenes["red"]).exists()
    assert Path(scenes["nir"]).exists()

    node = GeoNode(name="ndvi-node", port=0, workdir=str(out_dir))
    card = load_geocard(
        Path(__file__).resolve().parent.parent / "examples" / "amazon_ndvi" / "geocard.yaml"
    )
    node.register_geocard(card)
    node.register_skill(
        name="ndvi-analysis",
        handler=ndvi_skill.ndvi_analysis_handler,
        description="NDVI demo skill (synthetic)",
        input_schema={"required": ["red", "nir"]},
    )

    running = node.start_in_thread(port=0)
    running.wait_until_ready()
    try:
        ndvi_out = out_dir / "ndvi_2015.tif"
        with GeoMCPClient(f"http://127.0.0.1:{running.port}", timeout=15) as client:
            result = client.execute(
                skill="ndvi-analysis",
                geocards=["sentinel-2-amazon"],
                spatial={"bbox": [-73.9, -15.0, -44.0, 5.0], "crs": "EPSG:4326"},
                temporal={"start": "2015-01-01", "end": "2015-12-31"},
                params={
                    "red": scenes["red"],
                    "nir": scenes["nir"],
                    "output": str(ndvi_out),
                },
                request_id="test-ndvi",
            )
            assert result["status"] == "ok"
            assert result["skill"] == "ndvi-analysis"
            assert result["executed_by"] == "local-runtime"
            assert ndvi_out.exists()
            stats = result["outputs"]["stats"]
            assert stats["synthetic"] is True
            assert 0.0 < stats["mean"] < 1.0
            assert stats["count"] > 0

            # Missing required inputs -> GeoMCPClientError (application code).
            with pytest.raises(GeoMCPClientError) as exc_info:
                client.execute(
                    skill="ndvi-analysis",
                    geocards=["sentinel-2-amazon"],
                    params={},
                )
            assert exc_info.value.code is not None

        # NDVI GeoTIFF is readable and correctly geo-referenced.
        import rasterio

        with rasterio.open(ndvi_out) as src:
            assert src.crs.to_string() == "EPSG:4326"
            assert src.dtypes[0] == "float32"
            data = src.read(1)
        assert data.shape == (32, 48)

        # A 2025 scene has lower vegetation fraction (deforestation signal).
        scenes25 = ndvi_skill.generate_synthetic_scene(2025, out_dir, width=48, height=32, seed=8)
        ndvi25 = ndvi_skill.compute_ndvi_from_files(
            scenes25["red"], scenes25["nir"], str(out_dir / "ndvi_2025.tif")
        )
        assert ndvi25["vegetation_fraction"] < stats["vegetation_fraction"]

        # Change raster generation.
        change = ndvi_skill.compute_change(
            str(ndvi_out), str(out_dir / "ndvi_2025.tif"), str(out_dir / "ndvi_change.tif")
        )
        assert (out_dir / "ndvi_change.tif").exists()
        assert change["mean"] < 0.0  # vegetation declined
    finally:
        running.stop()
