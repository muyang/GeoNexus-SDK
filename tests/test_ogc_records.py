"""Tests for the OGC API - Records adapter (ogc_records.py)."""

from __future__ import annotations

import httpx

from geonexus.adapters import (
    OgcApiClient,
    fetch_ogc_record,
    list_ogc_record_collections,
    list_ogc_records,
    ogc_record_collection_to_geocard,
    ogc_record_to_geocard,
)


def _records_client(routes: dict[str, dict]) -> OgcApiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = routes.get(path)
        if body is None:
            return httpx.Response(404, json={"detail": "not found"}, request=request)
        return httpx.Response(200, json=body, request=request)

    transport = httpx.MockTransport(handler)
    return OgcApiClient(
        "https://records.test", client=httpx.Client(base_url="https://records.test", transport=transport)
    )


SAMPLE_COLLECTION = {
    "id": "demo-catalogue",
    "title": "Demo Catalogue",
    "description": "A demo record collection",
    "links": [{"rel": "self", "href": "https://records.test/collections/demo-catalogue"}],
    "extent": {
        "spatial": {"bbox": [[-10, -10, 10, 10]], "crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
        "temporal": {"interval": [["2015-01-01", "2025-12-31"]]},
    },
}

SAMPLE_RECORD = {
    "id": "rec-1",
    "title": "Amazon NDVI",
    "description": "Vegetation index over the Amazon",
    "keywords": ["ndvi", "amazon", "vegetation"],
    "themes": [{"concepts": [{"id": "ndvi"}]}, "change-detection"],
    "extent": {
        "spatial": {"bbox": [[-73.9, -15.0, -44.0, 5.0]], "crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
        "temporal": {"interval": [["2015-01-01", "2025-12-31"]]},
    },
    "links": [
        {"rel": "self", "href": "https://records.test/records/rec-1"},
        {"rel": "data", "href": "https://data.test/rec-1.geojson", "type": "application/geo+json"},
    ],
}


class TestRecordsClient:
    def test_list_collections(self) -> None:
        client = _records_client({"/": {"collections": [SAMPLE_COLLECTION]}})
        with client:
            cols = list_ogc_record_collections(
                "https://records.test", client=client
            )
        assert len(cols) == 1
        assert cols[0]["id"] == "demo-catalogue"

    def test_list_records(self) -> None:
        client = _records_client({"/records": {"features": [SAMPLE_RECORD]}})
        with client:
            recs = list_ogc_records("https://records.test", client=client)
        assert len(recs) == 1
        assert recs[0]["id"] == "rec-1"

    def test_list_records_in_collection_with_filters(self) -> None:
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(dict(request.url.params))
            return httpx.Response(200, json={"features": []}, request=request)

        client = OgcApiClient(
            "https://records.test",
            client=httpx.Client(base_url="https://records.test", transport=httpx.MockTransport(handler)),
        )
        with client:
            list_ogc_records(
                "https://records.test",
                collection_id="c1",
                limit=10,
                bbox=[1.0, 2.0, 3.0, 4.0],
                q="ndvi",
                client=client,
            )
        assert captured[0]["limit"] == "10"
        assert captured[0]["bbox"] == "1.0,2.0,3.0,4.0"
        assert captured[0]["q"] == "ndvi"

    def test_fetch_record_collection_path(self) -> None:
        client = _records_client(
            {"/collections/c1/items/rec-1": SAMPLE_RECORD}
        )
        with client:
            rec = fetch_ogc_record(
                "https://records.test", "rec-1", collection_id="c1", client=client
            )
        assert rec["id"] == "rec-1"

    def test_fetch_record_records_path(self) -> None:
        client = _records_client({"/records/rec-1": SAMPLE_RECORD})
        with client:
            rec = fetch_ogc_record("https://records.test", "rec-1", client=client)
        assert rec["id"] == "rec-1"


class TestRecordToGeoCard:
    def test_record_to_geocard(self) -> None:
        card = ogc_record_to_geocard(SAMPLE_RECORD, "https://records.test")
        assert card.id == "rec-1"
        assert card.type == "data"
        assert "ndvi" in card.tags
        assert "amazon" in card.tags
        # Themes → capabilities.
        cap_names = [c.name for c in card.capabilities]
        assert "ndvi" in cap_names
        assert "change-detection" in cap_names
        # Access link = data link.
        assert card.access is not None
        assert card.access.endpoint == "https://data.test/rec-1.geojson"
        assert card.access.protocol == "ogcapi-records"
        # Spatial extent.
        assert card.spatial is not None
        assert card.spatial.bbox == [-73.9, -15.0, -44.0, 5.0]
        assert card.temporal is not None
        assert card.temporal.start == "2015-01-01"
        assert card.temporal.end == "2025-12-31"

    def test_record_without_extent(self) -> None:
        rec = {"id": "bare", "title": "Bare", "links": []}
        card = ogc_record_to_geocard(rec, "https://records.test")
        assert card.id == "bare"
        assert card.spatial is None
        assert card.temporal is None

    def test_collection_to_geocard(self) -> None:
        card = ogc_record_collection_to_geocard(SAMPLE_COLLECTION, "https://records.test")
        assert card.id == "demo-catalogue"
        assert card.type == "data"
        assert "ogcapi-records" in card.tags
        assert card.spatial is not None
        assert card.spatial.bbox == [-10.0, -10.0, 10.0, 10.0]
