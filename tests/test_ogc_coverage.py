"""Tests for the OGC API - Coverages adapter (V1.0)."""

from __future__ import annotations

import httpx
import pytest
from examples.ogc_coverage.run_demo import (
    _build_mock_coverages_app,
    make_coverage_ndvi_skill,
)
from test_registry import _start_server

from geonexus.adapters import (
    OgcCoverageError,
    coverage_to_geotiff,
    fetch_coverage_range,
    fetch_ogc_coverage_metadata,
    ogc_coverage_metadata_to_geocard,
    parse_coveragejson,
)
from geonexus.geomcp import GeoMCPClient
from geonexus.geonode import GeoNode

API = "https://cov.example/v1"

_COLLECTION = {
    "id": "amazon",
    "title": "Amazon coverage",
    "description": "Test coverage.",
    "extent": {
        "spatial": {"bbox": [[-73.9, -15.0, -44.0, 5.0]], "crs": "EPSG:4326"},
        "temporal": {"interval": [["2020-01-01T00:00:00Z", "2020-12-31T00:00:00Z"]]},
    },
}

_RANGES_DOC = {
    "ranges": [
        {"name": "red", "dataType": "uint16", "unit": "dn"},
        {"name": "nir", "dataType": "uint16", "unit": "dn"},
    ]
}

_CJ_DOC = {
    "type": "Coverage",
    "domain": {"domainType": "Grid", "referencing": [{"crs": {"type": "EPSG:4326"}}]},
    "ranges": {
        "red": {
            "type": "NdArray",
            "dataType": "uint16",
            "axisNames": ["y", "x"],
            "shape": [2, 3],
            "values": [1000, 2000, 3000, 4000, 5000, 6000],
        }
    },
}


def _mock_client(routes: dict[str, dict | object]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for key, value in routes.items():
            if path.endswith(key):
                return httpx.Response(200, json=value)
        return httpx.Response(404, json={"code": "NotFound"})

    return httpx.Client(base_url=API, transport=httpx.MockTransport(handler))


def test_parse_coveragejson() -> None:
    """CoverageJSON ranges become correctly-shaped numpy arrays."""
    import numpy as np

    array, metadata = parse_coveragejson(_CJ_DOC)
    assert array.shape == (2, 3)
    assert array.dtype == np.uint16
    assert array[1, 2] == 6000
    assert metadata["name"] == "red"
    assert metadata["axis_names"] == ["y", "x"]
    assert metadata["crs"] == "EPSG:4326"

    with pytest.raises(OgcCoverageError, match="no ranges"):
        parse_coveragejson({"type": "Coverage"})


def test_coverage_metadata_to_geocard() -> None:
    """Ranges map onto GeoCard bands; extent onto spatial/temporal."""
    card = ogc_coverage_metadata_to_geocard(_COLLECTION, _RANGES_DOC, API)
    assert card.id == "amazon"
    assert card.type == "data"
    assert "ogcapi-coverages" in card.tags
    assert card.band_names() == ["red", "nir"]
    assert card.bands[0].dtype == "uint16"
    assert card.bands[0].units == "dn"
    assert card.spatial is not None
    assert card.spatial.bbox == [-73.9, -15.0, -44.0, 5.0]
    assert card.spatial.crs == "EPSG:4326"
    assert card.temporal is not None and card.temporal.start == "2020-01-01T00:00:00Z"
    assert card.access is not None and card.access.protocol == "ogcapi"
    card.validate()


def test_fetch_metadata() -> None:
    client = _mock_client(
        {
            "/collections/amazon": _COLLECTION,
            "/collections/amazon/coverage/ranges": _RANGES_DOC,
        }
    )
    card = fetch_ogc_coverage_metadata(API, "amazon", client=client)
    assert card.id == "amazon"
    assert card.band_names() == ["red", "nir"]


def test_fetch_range() -> None:
    import numpy as np

    client = _mock_client({"/coverage/ranges/red": _CJ_DOC})
    array, metadata = fetch_coverage_range(API, "amazon", "red", client=client)
    assert array.shape == (2, 3)
    assert np.all(array.flatten() == [1000, 2000, 3000, 4000, 5000, 6000])
    assert metadata["name"] == "red"


def test_coverage_to_geotiff(tmp_path) -> None:
    """GeoTIFF output is a readable, correctly geo-referenced raster."""
    import numpy as np

    array = np.arange(12, dtype=np.float32).reshape(3, 4)
    out = tmp_path / "cov.tif"
    coverage_to_geotiff(array, str(out), crs="EPSG:4326")
    import rasterio

    with rasterio.open(out) as src:
        assert src.crs.to_string() == "EPSG:4326"
        assert src.dtypes[0] == "float32"
        assert src.read(1).shape == (3, 4)
        assert src.read(1)[2, 3] == 11


def test_fetch_missing_range() -> None:
    client = _mock_client({})
    with pytest.raises(OgcCoverageError):
        fetch_coverage_range(API, "amazon", "ghost", client=client)


def test_coverages_end_to_end(tmp_path) -> None:
    """coverage-ndvi skill drives the mock coverage server via GeoMCP."""
    mock = _build_mock_coverages_app()
    ogc_handle = _start_server(mock, 0)
    ogc_url = f"http://127.0.0.1:{ogc_handle.port}"

    node = GeoNode(name="cov-node", port=0, workdir=str(tmp_path))
    node.register_skill_object(make_coverage_ndvi_skill(ogc_url))
    node_handle = _start_server(node.create_app(), 0)
    try:
        out = tmp_path / "cov_ndvi.tif"
        with GeoMCPClient(f"http://127.0.0.1:{node_handle.port}", timeout=30) as client:
            result = client.execute(
                skill="coverage-ndvi",
                params={"collection": "amazon", "output": str(out)},
                request_id="cov-test",
            )
        assert result["status"] == "ok"
        stats = result["outputs"]["stats"]
        assert 0.0 < stats["mean"] < 1.0
        assert stats["count"] > 0
        assert out.exists()
        import rasterio

        with rasterio.open(out) as src:
            assert src.dtypes[0] == "float32"
    finally:
        node_handle.stop()
        ogc_handle.stop()
