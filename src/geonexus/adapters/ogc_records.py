"""OGC API - Records adapter (v1.1).

Bridges the **OGC API - Records** standard (the catalogue/data-discovery
plane of OGC APIs): list record collections, fetch individual records, and
convert records into GeoCards so they join the GeoNexus discovery ecosystem.

A record describes a dataset, service or other resource with spatial /
temporal extent, themes, keywords, contacts and links (including data
access links). Converting to a GeoCard makes the record discoverable and
contract-checkable by the registry and the agent planner.
"""

from __future__ import annotations

import logging
from typing import Any

from ..geocard import GeoCard, GeoCardBuilder
from .ogc import DEFAULT_TIMEOUT, OgcAdapterError, OgcApiClient, _first_link

logger = logging.getLogger(__name__)

# Default OGC API - Records collection used when none is given.
DEFAULT_RECORDS_PATH = "collections"


def list_ogc_record_collections(
    api_url: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: OgcApiClient | None = None,
) -> list[dict[str, Any]]:
    """List record collections (data catalogues) at an OGC API - Records root.

    Returns raw collection description dicts; each can be passed to
    :func:`ogc_record_collection_to_geocard`.
    """
    owns = client is None
    client = client or OgcApiClient(api_url, timeout=timeout)
    try:
        data = client.get_json("/")
        collections = (data.get("collections") or []) if isinstance(data, dict) else []
        return list(collections)
    except OgcAdapterError:
        raise
    finally:
        if owns:
            client.close()


def ogc_record_collection_to_geocard(
    collection: dict[str, Any],
    api_url: str,
    geocard_version: str = "1.0",
) -> GeoCard:
    """Convert an OGC API - Records collection description into a GeoCard."""
    cid = str(collection.get("id", "ogc-records"))
    links = collection.get("links") or []
    self_href = _first_link(links, "self")

    builder = (
        GeoCardBuilder(
            id=cid,
            type="data",
            name=str(collection.get("title") or cid),
            description=str(
                collection.get("description") or f"OGC API - Records collection '{cid}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogcapi", "ogcapi-records", "catalogue")
        .access(
            protocol="ogcapi-records",
            endpoint=self_href or api_url,
            format="JSON",
        )
        .provenance(
            provider=str(api_url),
            source=cid,
            lineage=f"Imported from OGC API - Records (collection '{cid}')",
        )
    )
    _apply_extent(builder, collection.get("extent"))
    return builder.build()


def fetch_ogc_record(
    api_url: str,
    record_id: str,
    collection_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: OgcApiClient | None = None,
) -> dict[str, Any]:
    """Fetch a single record from an OGC API - Records server.

    Args:
        api_url: OGC API - Records root URL.
        record_id: Record identifier.
        collection_id: Optional parent collection; when given the record is
            fetched via ``collections/{collection_id}/items/{record_id}``,
            else via ``records/{record_id}``.
    """
    owns = client is None
    client = client or OgcApiClient(api_url, timeout=timeout)
    try:
        if collection_id:
            return client.get_json(f"/collections/{collection_id}/items/{record_id}")
        return client.get_json(f"/records/{record_id}")
    finally:
        if owns:
            client.close()


def list_ogc_records(
    api_url: str,
    collection_id: str | None = None,
    limit: int = 50,
    bbox: list[float] | None = None,
    q: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: OgcApiClient | None = None,
) -> list[dict[str, Any]]:
    """List records from an OGC API - Records server.

    Supports paging via ``limit`` and contract pre-filtering via ``bbox`` /
    free-text ``q``.
    """
    owns = client is None
    client = client or OgcApiClient(api_url, timeout=timeout)
    params: dict[str, Any] = {"limit": limit}
    if bbox is not None:
        params["bbox"] = ",".join(str(x) for x in bbox)
    if q:
        params["q"] = q
    try:
        path = (
            f"/collections/{collection_id}/items"
            if collection_id
            else "/records"
        )
        data = client.get_json(path, params=params)
        return list(data.get("features") or data.get("records") or [])
    finally:
        if owns:
            client.close()


def ogc_record_to_geocard(
    record: dict[str, Any],
    api_url: str,
    geocard_version: str = "1.0",
) -> GeoCard:
    """Convert an OGC API - Records record into a GeoCard.

    Maps: id/title/description, time+bbox extent, themes (as capabilities),
    keywords (as tags), and access links (data/self/alternate → access).
    """
    rid = str(record.get("id", "ogc-record"))
    links = record.get("links") or []
    self_href = _first_link(links, "self")
    # Prefer a data-download link for access; fall back to self.
    data_href = (
        _first_link(links, "data", href=None)
        or _first_link(links, "enclosure")
        or self_href
    )
    fmt = None
    for link in links:
        if isinstance(link, dict) and link.get("type"):
            fmt = link["type"]
            break

    builder = (
        GeoCardBuilder(
            id=rid,
            type="data",
            name=str(record.get("title") or rid),
            description=str(
                record.get("description") or f"OGC API - Records record '{rid}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogcapi", "ogcapi-records", "record")
        .access(
            protocol="ogcapi-records",
            endpoint=data_href or self_href or api_url,
            format=fmt or "JSON",
        )
        .provenance(
            provider=str(api_url),
            source=rid,
            lineage=f"Imported from OGC API - Records (record '{rid}')",
        )
    )

    # Keywords → tags.
    for keyword in record.get("keywords") or []:
        if isinstance(keyword, str) and keyword:
            builder.tag(keyword)

    # Themes → capabilities (each theme is a discoverable capability).
    for theme in record.get("themes") or []:
        if isinstance(theme, dict):
            name = theme.get("concepts") or theme.get("id")
            if isinstance(name, list):
                for concept in name:
                    if isinstance(concept, dict) and concept.get("id"):
                        builder.capability(str(concept["id"]))
            elif name:
                builder.capability(str(name))
        elif isinstance(theme, str):
            builder.capability(theme)

    _apply_extent(builder, record.get("extent") or record.get("time"))
    return builder.build()


def _apply_extent(builder: GeoCardBuilder, extent: Any) -> None:
    """Apply OGC record extent (spatial + temporal) to a card builder.

    Handles the OGC API - Records extent shape::

        {"spatial": {"bbox": [[w,s,e,n]], "crs": ...},
         "temporal": {"interval": [["start","end"]], "trs": ...}}
    """
    if not isinstance(extent, dict):
        return
    spatial = extent.get("spatial")
    if isinstance(spatial, dict):
        bbox_raw = spatial.get("bbox")
        crs = spatial.get("crs")
        bbox = None
        if isinstance(bbox_raw, list) and bbox_raw:
            first = bbox_raw[0] if isinstance(bbox_raw[0], list) else bbox_raw
            try:
                bbox = [float(x) for x in first]
            except (TypeError, ValueError):
                bbox = None
        if bbox is not None:
            kwargs: dict[str, Any] = {"bbox": bbox}
            if crs:
                kwargs["crs"] = str(crs)
            builder.spatial(**kwargs)
    temporal = extent.get("temporal")
    if isinstance(temporal, dict):
        interval = temporal.get("interval")
        if isinstance(interval, list) and interval and isinstance(interval[0], list):
            start, end = interval[0][0], interval[0][1]
            if start or end:
                builder.temporal(start=start or None, end=end or None)
