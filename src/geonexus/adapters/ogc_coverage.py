"""OGC API - Coverages adapter (V1.0): raster retrieval (the data plane).

Bridges OGC API - Coverages services into GeoNexus:

- **metadata**: a coverage's range descriptions become GeoCard ``bands``
  (name / dataType -> dtype / unit), with the collection's spatial/temporal
  extent mapped onto the card.
- **data**: a coverage range is fetched as **CoverageJSON**
  (``application/prs.coverage+json``) and parsed into a numpy array plus its
  metadata (shape, axis names, CRS when available).
- **GeoTIFF**: arrays can be written to GeoTIFF via rasterio (fails loudly
  when rasterio is unavailable — the same policy as the NDVI demo).

This completes the standards data plane: STAC (catalog), OGC API - Features
(vector), OGC API - Processes (execution) and now OGC API - Coverages
(raster).

Mapping:
    OGC coverage ranges            -> GeoCard
    collection id                  -> id
    title / description            -> name / description
    ranges[].name                  -> bands[].name
    ranges[].dataType              -> bands[].dtype
    ranges[].unit                  -> bands[].units
    collection extent (spatial)    -> spatial (bbox, crs)
    collection extent (temporal)   -> temporal
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..geocard.builder import GeoCardBuilder
from ..geocard.model import GeoCard
from .ogc import DEFAULT_TIMEOUT, OgcApiClient

logger = logging.getLogger(__name__)

COVERAGEJSON_MEDIA_TYPE = "application/prs.coverage+json"

# CoverageJSON -> numpy dtype (subset).
_CJ_DTYPES: dict[str, str] = {
    "float": "float64",
    "float32": "float32",
    "float64": "float64",
    "integer": "int64",
    "int": "int64",
    "int32": "int32",
    "uint8": "uint8",
    "uint16": "uint16",
}


class OgcCoverageError(Exception):
    """Raised when an OGC coverage cannot be fetched or parsed."""


# --------------------------------------------------------------------------- #
# Metadata -> GeoCard
# --------------------------------------------------------------------------- #
def ogc_coverage_metadata_to_geocard(
    collection: dict[str, Any],
    ranges: dict[str, Any],
    api_url: str,
    geocard_version: str = "0.1",
) -> GeoCard:
    """Convert an OGC coverage (collection + ranges docs) into a GeoCard."""
    collection_id = str(collection.get("id", "ogc-coverage"))
    range_types = ranges.get("ranges") or []
    builder = (
        GeoCardBuilder(
            id=collection_id,
            type="data",
            name=str(collection.get("title") or collection_id),
            description=str(
                collection.get("description") or f"OGC API - Coverages '{collection_id}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogcapi", "ogcapi-coverages")
        .access(protocol="ogcapi", endpoint=api_url, format=COVERAGEJSON_MEDIA_TYPE)
        .provenance(
            provider=str(api_url),
            source=collection_id,
            lineage=f"Imported from OGC API - Coverages (collection '{collection_id}')",
        )
    )
    for rng in range_types:
        if not isinstance(rng, dict) or not rng.get("name"):
            continue
        builder.band(
            name=str(rng["name"]),
            dtype=str(rng.get("dataType") or rng.get("type")),
            units=str(rng.get("unit")) if rng.get("unit") else None,
            description=rng.get("description"),
        )

    extent = collection.get("extent") or {}
    spatial = extent.get("spatial") or {}
    bboxes = spatial.get("bbox")
    if bboxes:
        kwargs: dict[str, Any] = {"bbox": [float(x) for x in bboxes[0][:4]]}
        crs_value = spatial.get("crs") or collection.get("crs")
        if crs_value:
            crs = crs_value[0] if isinstance(crs_value, list) else crs_value
            kwargs["crs"] = str(crs)
        builder.spatial(**kwargs)
    temporal = extent.get("temporal") or {}
    interval = temporal.get("interval")
    if interval and interval[0]:
        start, end = interval[0][0], interval[0][1]
        builder.temporal(start=str(start) if start else None, end=str(end) if end else None)
    return builder.build()


def fetch_ogc_coverage_metadata(
    api_url: str,
    collection_id: str,
    validate: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> GeoCard:
    """Fetch coverage metadata (collection + ranges) and build a GeoCard."""
    with OgcApiClient(api_url, timeout=timeout, client=client) as api:
        collection = api.get_json(f"/collections/{collection_id}")
        try:
            ranges = api.get_json(f"/collections/{collection_id}/coverage/ranges")
        except Exception as exc:  # noqa: BLE001 - some servers expose /coverage only
            logger.warning("ranges endpoint unavailable (%s); using empty ranges", exc)
            ranges = {"ranges": []}
    card = ogc_coverage_metadata_to_geocard(collection, ranges, api_url)
    if validate:
        card.validate()
    logger.info("Imported OGC coverage '%s' as GeoCard", card.id)
    return card


# --------------------------------------------------------------------------- #
# Data: CoverageJSON -> numpy
# --------------------------------------------------------------------------- #
def parse_coveragejson(doc: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Parse a CoverageJSON document into ``(ndarray, metadata)``.

    Returns the first range of the coverage (raster data plane MVP) with its
    metadata: ``{"name", "axis_names", "shape", "dtype", "crs"}``.
    """
    ranges = doc.get("ranges") or {}
    if not ranges:
        raise OgcCoverageError("CoverageJSON document contains no ranges")
    name = next(iter(ranges))
    rng = ranges[name]
    values = rng.get("values")
    if values is None:
        raise OgcCoverageError(f"Coverage range '{name}' has no values")
    try:
        import numpy as np

        dtype = _CJ_DTYPES.get(str(rng.get("dataType", "float")), "float64")
        array = np.asarray(values, dtype=dtype)
    except Exception as exc:  # noqa: BLE001 - defensive
        raise OgcCoverageError(f"Could not convert coverage values to ndarray: {exc}") from exc
    shape = rng.get("shape") or list(array.shape)
    metadata = {
        "name": name,
        "axis_names": list(rng.get("axisNames") or []),
        "shape": list(shape),
        "dtype": str(array.dtype),
        "crs": doc.get("domain", {}).get("referencing", [{}])[0].get("crs", {}).get("type")
        if doc.get("domain", {}).get("referencing")
        else None,
    }
    return array.reshape(shape), metadata


def fetch_coverage_range(
    api_url: str,
    collection_id: str,
    range_name: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Fetch a coverage range as ``(ndarray, metadata)``.

    ``range_name=None`` fetches the ranges document (all ranges) and returns
    the first one.
    """
    if range_name:
        path = f"/collections/{collection_id}/coverage/ranges/{range_name}"
    else:
        path = f"/collections/{collection_id}/coverage/ranges"
    headers = {"Accept": COVERAGEJSON_MEDIA_TYPE}
    with OgcApiClient(api_url, timeout=timeout, client=client) as api:
        try:
            response = api._client.get(path, params={"f": "json"}, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OgcCoverageError(f"OGC coverage request failed ({api_url}{path}): {exc}") from exc
        try:
            doc = response.json()
        except ValueError as exc:
            raise OgcCoverageError(f"OGC coverage response is not JSON ({api_url}{path})") from exc
    return parse_coveragejson(doc)


# --------------------------------------------------------------------------- #
# GeoTIFF output
# --------------------------------------------------------------------------- #
def coverage_to_geotiff(
    array: Any,
    out_path: str,
    crs: str = "EPSG:4326",
    transform: Any | None = None,
    nodata: float | None = None,
) -> str:
    """Write a coverage array to GeoTIFF (requires rasterio)."""
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover - env dependent
        raise OgcCoverageError(
            "GeoTIFF output requires 'rasterio'. Install it with "
            "`pip install rasterio` (the GeoNexus venv includes it)."
        ) from exc
    import numpy as np
    from rasterio.transform import from_bounds

    array_2d = np.asarray(array)
    if array_2d.ndim == 3:
        array_2d = array_2d[0]
    height, width = array_2d.shape
    profile = dict(
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype=str(array_2d.dtype),
        crs=crs,
        transform=transform or from_bounds(-180, -90, 180, 90, width, height),
    )
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(array_2d, 1)
    logger.info("Wrote coverage GeoTIFF to %s (%sx%s)", out_path, width, height)
    return out_path
