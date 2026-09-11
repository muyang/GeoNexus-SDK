"""OGC API adapter: translate OGC API services into GeoCards (V1.0).

Supports the read side of the OGC API family:

- **OGC API - Features**: collections and features (GeoJSON) become GeoCard
  ``data`` assets, preserving spatial extent, temporal extent, CRS and
  license.
- **OGC API - Processes**: process descriptions become GeoCard ``skill``
  assets (inputs/outputs mapped from the process's input/output schemas).

The adapter follows the GeoNexus principle: adapters bridge standards, they
do not replace them. A GeoCard imported from an OGC service points at the
service endpoint (``access.protocol = "ogcapi"``), so execution stays on the
node that owns the data.

Mapping (Features):
    OGC collection                    -> GeoCard
    id                                -> id
    title                             -> name
    description                       -> description
    extent.spatial.bbox[0]            -> spatial.bbox
    extent.spatial.crs                -> spatial.crs (default EPSG:4326)
    extent.temporal.interval[0]       -> temporal.start / temporal.end
    license (link rel=license)        -> license
    links[self]                       -> access.endpoint

Mapping (Processes):
    OGC process description           -> GeoCard (type=skill)
    id                                -> id
    title / description               -> name / description
    inputs (json schema)              -> inputs
    outputs                           -> outputs
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..geocard.builder import GeoCardBuilder
from ..geocard.model import GeoCard

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class OgcAdapterError(Exception):
    """Raised when an OGC API source cannot be fetched or mapped."""


class OgcApiClient:
    """Minimal OGC API client (read side) speaking ``f=json``.

    Args:
        base_url: OGC API root, e.g. ``https://demo.pygeoapi.io/master``.
        timeout: Request timeout in seconds.
        client: Optional pre-configured ``httpx.Client`` (advanced use).
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url, timeout=timeout, follow_redirects=True
        )

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a JSON resource, raising :class:`OgcAdapterError` on failure."""
        query = dict(params or {})
        query.setdefault("f", "json")
        try:
            response = self._client.get(path, params=query)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OgcAdapterError(f"OGC API request failed ({self.base_url}{path}): {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise OgcAdapterError(f"OGC API response is not JSON ({self.base_url}{path})") from exc
        if not isinstance(data, dict):
            raise OgcAdapterError(f"OGC API response must be an object ({path})")
        return data

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OgcApiClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _first_link(links: list[dict[str, Any]], rel: str, href: str | None = None) -> str | None:
    for link in links or []:
        if link.get("rel") == rel:
            return link.get("href") or href
    return href


def _bbox_from_collection(collection: dict[str, Any]) -> list[float] | None:
    extent = collection.get("extent") or {}
    spatial = extent.get("spatial") or {}
    bboxes = spatial.get("bbox")
    if bboxes:
        return [float(x) for x in bboxes[0][:4]]
    return None


def _crs_from_collection(collection: dict[str, Any]) -> str | None:
    extent = collection.get("extent") or {}
    spatial = extent.get("spatial") or {}
    crs_value = spatial.get("crs") or collection.get("crs")
    if crs_value is None:
        return None
    # OGC API allows both a single CRS string and a list of CRS URIs.
    if isinstance(crs_value, list):
        return str(crs_value[0]) if crs_value else None
    return str(crs_value)


def _temporal_from_collection(
    collection: dict[str, Any],
) -> tuple[str | None, str | None]:
    extent = collection.get("extent") or {}
    temporal = extent.get("temporal") or {}
    interval = temporal.get("interval")
    if interval and interval[0]:
        start, end = interval[0][0], interval[0][1]
        return (str(start) if start else None, str(end) if end else None)
    return (None, None)


def _license_from_collection(collection: dict[str, Any]) -> str | None:
    return _first_link(collection.get("links") or [], "license")


# --------------------------------------------------------------------------- #
# Features: collection
# --------------------------------------------------------------------------- #
def ogc_collection_to_geocard(
    collection: dict[str, Any],
    api_url: str,
    geocard_version: str = "0.1",
) -> GeoCard:
    """Convert an OGC API Features collection description into a GeoCard."""
    collection_id = str(collection.get("id", "ogc-collection"))
    links = collection.get("links") or []
    self_href = _first_link(links, "self")

    builder = (
        GeoCardBuilder(
            id=collection_id,
            type="data",
            name=str(collection.get("title") or collection_id),
            description=str(
                collection.get("description") or f"OGC API Features collection '{collection_id}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogcapi", "ogcapi-features")
        .access(
            protocol="ogcapi",
            endpoint=self_href or api_url,
            format="GeoJSON",
        )
        .provenance(
            provider=str(api_url),
            source=collection_id,
            lineage=f"Imported from OGC API Features (collection '{collection_id}')",
        )
    )

    bbox = _bbox_from_collection(collection)
    crs = _crs_from_collection(collection)
    if bbox is not None:
        kwargs: dict[str, Any] = {"bbox": bbox}
        if crs is not None:
            kwargs["crs"] = crs
        builder.spatial(**kwargs)

    start, end = _temporal_from_collection(collection)
    if start or end:
        builder.temporal(start=start, end=end)

    license_value = _license_from_collection(collection)
    if license_value:
        builder.license(name=license_value)

    return builder.build()


def fetch_ogc_collection(
    api_url: str,
    collection_id: str,
    validate: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> GeoCard:
    """Fetch an OGC API Features collection and convert it to a GeoCard."""
    with OgcApiClient(api_url, timeout=timeout, client=client) as api:
        collection = api.get_json(f"/collections/{collection_id}")
    card = ogc_collection_to_geocard(collection, api_url)
    if validate:
        card.validate()
    logger.info("Imported OGC collection '%s' as GeoCard", card.id)
    return card


def list_ogc_collections(
    api_url: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> list[str]:
    """List collection ids of an OGC API Features service."""
    with OgcApiClient(api_url, timeout=timeout, client=client) as api:
        root = api.get_json("/collections")
    return [str(c.get("id")) for c in root.get("collections", []) if c.get("id")]


# --------------------------------------------------------------------------- #
# Features: item (GeoJSON Feature)
# --------------------------------------------------------------------------- #
def _sanitize_id(value: str) -> str:
    """Keep only characters allowed by the GeoCard id pattern."""
    import re

    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def ogc_feature_to_geocard(
    feature: dict[str, Any],
    api_url: str,
    collection_id: str | None = None,
    geocard_version: str = "0.1",
) -> GeoCard:
    """Convert an OGC API Features item (GeoJSON Feature) into a GeoCard."""
    feature_id = str(feature.get("id", "ogc-feature"))
    card_id = (
        f"{_sanitize_id(collection_id)}.{_sanitize_id(feature_id)}"
        if collection_id
        else _sanitize_id(feature_id)
    )
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}

    builder = (
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=str(properties.get("name") or properties.get("title") or feature_id),
            description=str(
                properties.get("description") or f"OGC API Features item '{feature_id}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogcapi", "ogcapi-features")
        .access(protocol="ogcapi", endpoint=api_url, format="GeoJSON")
        .provenance(
            provider=str(api_url),
            source=feature_id,
            lineage=(
                "Imported from OGC API Features"
                + (f" (collection '{collection_id}')" if collection_id else "")
            ),
        )
    )

    bbox = feature.get("bbox")
    if not bbox and geometry.get("type") == "Polygon":
        coords = geometry.get("coordinates") or [[]]
        xs = [p[0] for ring in coords for p in ring]
        ys = [p[1] for ring in coords for p in ring]
        if xs and ys:
            bbox = [min(xs), min(ys), max(xs), max(ys)]
    if bbox:
        builder.spatial(bbox=[float(x) for x in bbox[:4]])

    start = properties.get("start_datetime") or properties.get("datetime")
    end = properties.get("end_datetime") or properties.get("datetime")
    if start or end:
        builder.temporal(start=str(start) if start else None, end=str(end) if end else None)

    return builder.build()


def fetch_ogc_feature(
    api_url: str,
    collection_id: str,
    feature_id: str,
    validate: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> GeoCard:
    """Fetch an OGC API Features item and convert it to a GeoCard."""
    with OgcApiClient(api_url, timeout=timeout, client=client) as api:
        feature = api.get_json(f"/collections/{collection_id}/items/{feature_id}")
    card = ogc_feature_to_geocard(feature, api_url, collection_id=collection_id)
    if validate:
        card.validate()
    logger.info("Imported OGC feature '%s' as GeoCard", card.id)
    return card


# --------------------------------------------------------------------------- #
# Processes
# --------------------------------------------------------------------------- #
def ogc_process_to_geocard(
    process: dict[str, Any],
    api_url: str,
    geocard_version: str = "0.1",
) -> GeoCard:
    """Convert an OGC API - Processes process description into a GeoCard.

    The process becomes a ``skill``-type card: its input/output schemas map
    onto the GeoCard ``inputs`` / ``outputs`` sections, and its execution
    endpoint is recorded in ``access``.
    """
    process_id = str(process.get("id", "ogc-process"))
    builder = (
        GeoCardBuilder(
            id=process_id,
            type="skill",
            name=str(process.get("title") or process_id),
            description=str(
                process.get("description") or f"OGC API - Processes process '{process_id}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogcapi", "ogcapi-processes")
        .access(protocol="ogcapi", endpoint=api_url, format="json")
        .interface(type="ogcapi-process", version="1.0")
        .provenance(
            provider=str(api_url),
            source=process_id,
            lineage=f"Imported from OGC API - Processes (process '{process_id}')",
        )
    )

    for name, spec in (process.get("inputs") or {}).items():
        spec = spec or {}
        schema = spec.get("schema") or {}
        builder.input(
            name=str(name),
            type=str(spec.get("type") or schema.get("type") or "object"),
            description=spec.get("title") or spec.get("description"),
            required=bool(spec.get("required", False)),
        )
    for name, spec in (process.get("outputs") or {}).items():
        spec = spec or {}
        builder.output(
            name=str(name),
            type=str(spec.get("type") or spec.get("schema", {}).get("type") or "object"),
            description=spec.get("title") or spec.get("description"),
        )
    return builder.build()


def fetch_ogc_process(
    api_url: str,
    process_id: str,
    validate: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> GeoCard:
    """Fetch an OGC API - Processes process description as a GeoCard."""
    with OgcApiClient(api_url, timeout=timeout, client=client) as api:
        process = api.get_json(f"/processes/{process_id}")
    card = ogc_process_to_geocard(process, api_url)
    if validate:
        card.validate()
    logger.info("Imported OGC process '%s' as GeoCard", card.id)
    return card


def list_ogc_processes(
    api_url: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> list[str]:
    """List process ids of an OGC API - Processes service."""
    with OgcApiClient(api_url, timeout=timeout, client=client) as api:
        root = api.get_json("/processes")
    return [str(p.get("id")) for p in root.get("processes", []) if p.get("id")]
