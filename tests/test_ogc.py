"""Tests for the OGC API adapter (Features + Processes -> GeoCard)."""

from __future__ import annotations

import httpx
import pytest

from geonexus.adapters import (
    OgcAdapterError,
    OgcApiClient,
    fetch_ogc_collection,
    fetch_ogc_feature,
    fetch_ogc_process,
    list_ogc_collections,
    list_ogc_processes,
    ogc_collection_to_geocard,
    ogc_feature_to_geocard,
    ogc_process_to_geocard,
)

API = "https://demo.example/master"

_COLLECTION = {
    "id": "lakes",
    "title": "Lakes of the world",
    "description": "A test OGC API Features collection.",
    "extent": {
        "spatial": {
            "bbox": [[-180.0, -90.0, 180.0, 90.0]],
            "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
        },
        "temporal": {"interval": [["2015-01-01T00:00:00Z", "2025-12-31T23:59:59Z"]]},
    },
    "links": [
        {"rel": "self", "href": f"{API}/collections/lakes"},
        {"rel": "items", "href": f"{API}/collections/lakes/items"},
        {"rel": "license", "href": "https://example.test/license"},
    ],
}

_FEATURE = {
    "type": "Feature",
    "id": "lake-42",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[10.0, 20.0], [11.0, 20.0], [11.0, 21.0], [10.0, 21.0], [10.0, 20.0]]],
    },
    "properties": {
        "name": "Lake Forty-Two",
        "description": "A test lake.",
        "start_datetime": "2015-06-01T00:00:00Z",
        "end_datetime": "2025-06-01T00:00:00Z",
    },
}

_PROCESS = {
    "id": "echo",
    "title": "Echo process",
    "description": "Echoes its inputs (test process).",
    "inputs": {
        "message": {"title": "Message", "schema": {"type": "string"}},
        "count": {"title": "Count", "schema": {"type": "integer"}, "required": True},
    },
    "outputs": {
        "result": {"title": "Result", "schema": {"type": "string"}},
    },
}


def _mock_client(routes: dict[str, dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # httpx merges the base_url path; match on the suffix.
        for key, body in routes.items():
            if path.endswith(key):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"code": "NotFound"})

    return httpx.Client(base_url=API, transport=httpx.MockTransport(handler))


def test_ogc_collection_to_geocard() -> None:
    """Collection extent/temporal/crs/license map onto the GeoCard."""
    card = ogc_collection_to_geocard(_COLLECTION, API)
    assert card.id == "lakes"
    assert card.type == "data"
    assert card.name == "Lakes of the world"
    assert "ogcapi" in card.tags

    assert card.spatial is not None
    assert card.spatial.bbox == [-180.0, -90.0, 180.0, 90.0]
    assert card.spatial.crs == "http://www.opengis.net/def/crs/OGC/1.3/CRS84"

    assert card.temporal is not None
    assert card.temporal.start == "2015-01-01T00:00:00Z"
    assert card.temporal.end == "2025-12-31T23:59:59Z"

    assert card.access is not None
    assert card.access.protocol == "ogcapi"
    assert card.access.endpoint == f"{API}/collections/lakes"
    assert card.access.format == "GeoJSON"

    assert card.provenance is not None
    assert "OGC API Features" in card.provenance.lineage
    assert card.license is not None and card.license.name == "https://example.test/license"

    card.validate()  # conforms to the official GeoCard schema


def test_ogc_crs_as_string() -> None:
    """Some servers (e.g. pygeoapi) expose extent.spatial.crs as a string."""
    collection = dict(_COLLECTION)
    collection["extent"]["spatial"]["crs"] = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
    card = ogc_collection_to_geocard(collection, API)
    assert card.spatial is not None
    assert card.spatial.crs == "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
    card.validate()


def test_ogc_fetch_collection() -> None:
    """fetch_ogc_collection works over HTTP with f=json."""
    client = _mock_client({"/collections/lakes": _COLLECTION})
    card = fetch_ogc_collection(API, "lakes", client=client)
    assert card.id == "lakes"
    assert card.spatial is not None and card.spatial.bbox[0] == -180.0


def test_ogc_feature_to_geocard() -> None:
    """Feature bbox/geometry/properties map onto the GeoCard."""
    card = ogc_feature_to_geocard(_FEATURE, API, collection_id="lakes")
    assert card.id == "lakes.lake-42"
    assert card.name == "Lake Forty-Two"
    assert card.spatial is not None
    assert card.spatial.bbox == [10.0, 20.0, 11.0, 21.0]  # from polygon geometry
    assert card.temporal is not None
    assert card.temporal.start == "2015-06-01T00:00:00Z"
    card.validate()


def test_ogc_fetch_feature() -> None:
    client = _mock_client({"/collections/lakes/items/lake-42": _FEATURE})
    card = fetch_ogc_feature(API, "lakes", "lake-42", client=client)
    assert card.id == "lakes.lake-42"


def test_ogc_process_to_geocard() -> None:
    """A process description becomes a skill card with inputs/outputs."""
    card = ogc_process_to_geocard(_PROCESS, API)
    assert card.type == "skill"
    assert card.id == "echo"
    assert [i.name for i in card.inputs] == ["message", "count"]
    assert [i.name for i in card.outputs] == ["result"]
    assert card.inputs[1].required is True
    assert card.interface is not None and card.interface.type == "ogcapi-process"
    assert card.access is not None and card.access.protocol == "ogcapi"
    card.validate()


def test_ogc_fetch_process() -> None:
    client = _mock_client({"/processes/echo": _PROCESS})
    card = fetch_ogc_process(API, "echo", client=client)
    assert card.id == "echo"
    assert card.type == "skill"


def test_ogc_list_endpoints() -> None:
    client = _mock_client(
        {
            "/collections": {"collections": [{"id": "lakes"}, {"id": "rivers"}]},
            "/processes": {"processes": [{"id": "echo"}, {"id": "clip"}]},
        }
    )
    with OgcApiClient(API, client=client) as api:
        assert list_ogc_collections(API, client=api._client) == ["lakes", "rivers"]
        assert list_ogc_processes(API, client=api._client) == ["echo", "clip"]


def test_ogc_errors() -> None:
    client = _mock_client({})
    with pytest.raises(OgcAdapterError):
        fetch_ogc_collection(API, "missing", client=client)

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    bad = httpx.Client(base_url=API, transport=httpx.MockTransport(broken))
    with pytest.raises(OgcAdapterError, match="not JSON"):
        fetch_ogc_collection(API, "lakes", client=bad)
