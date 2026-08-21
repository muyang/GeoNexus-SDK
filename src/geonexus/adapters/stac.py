"""STAC adapter: translate SpatioTemporal Asset Catalog items into GeoCards.

A STAC Item is a GeoJSON Feature describing a geospatial asset (satellite
scene, tile, ...). This adapter maps the essential STAC fields onto the
GeoCard sections so that any STAC catalog can feed the GeoNexus discovery
layer without losing provenance.

Mapping:
    STAC                       -> GeoCard
    id                         -> id
    type=data (always)         -> type
    properties.title           -> name
    properties.description     -> description
    bbox                       -> spatial.bbox
    properties.proj:epsg       -> spatial.crs (default EPSG:4326)
    properties.gsd             -> spatial.resolution
    properties.start_datetime /
      properties.datetime      -> temporal.start
    properties.end_datetime /
      properties.datetime      -> temporal.end
    assets[*].eo:bands[].name  -> bands
    assets[*].href             -> access.endpoint
    assets[*].type             -> access.format
    collection / properties    -> provenance, license, tags
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from ..geocard.builder import GeoCardBuilder
from ..geocard.model import GeoCard

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class StacAdapterError(Exception):
    """Raised when a STAC source cannot be fetched or mapped."""


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def fetch_stac_item(
    source: str | os.PathLike,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch a STAC Item from a URL or a local file path.

    Returns the parsed item JSON (a GeoJSON Feature).
    """
    path = Path(source)
    if path.exists():
        import json

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StacAdapterError(f"Invalid STAC JSON in {path}: {exc}") from exc
        return _require_item(data, str(path))

    if "://" not in str(source):
        raise StacAdapterError(f"STAC source not found: {source}")

    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        response = client.get(str(source))
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise StacAdapterError(f"Failed to fetch STAC item {source}: {exc}") from exc
    finally:
        if own_client:
            client.close()
    try:
        data = response.json()
    except ValueError as exc:
        raise StacAdapterError(f"STAC response is not JSON: {source}") from exc
    return _require_item(data, str(source))


def _require_item(data: dict[str, Any], source: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StacAdapterError(f"STAC source {source} must be a JSON object")
    if data.get("type") != "Feature" or "assets" not in data:
        raise StacAdapterError(
            f"STAC source {source} is not a STAC Item (expected a Feature with assets)"
        )
    return data


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #
def stac_item_to_geocard(
    item: dict[str, Any],
    node_url: str | None = None,
    geocard_version: str = "0.1",
) -> GeoCard:
    """Convert a STAC Item (dict) into a GeoCard."""
    properties = item.get("properties") or {}
    assets = item.get("assets") or {}
    item_id = str(item.get("id", "stac-item"))
    collection = item.get("collection")
    bbox = item.get("bbox")

    builder = GeoCardBuilder(
        id=item_id,
        type="data",
        name=str(properties.get("title") or item_id),
        description=str(
            properties.get("description")
            or f"STAC item '{item_id}'" + (f" from collection '{collection}'" if collection else "")
        ),
        geocard_version=geocard_version,
    ).tag("stac")
    if collection:
        builder.tag(f"stac-collection:{collection}")

    # --- spatial ----------------------------------------------------------- #
    crs = properties.get("proj:epsg")
    spatial_kwargs: dict[str, Any] = {}
    if bbox:
        spatial_kwargs["bbox"] = [float(x) for x in bbox[:4]]
    if crs is not None:
        spatial_kwargs["crs"] = f"EPSG:{int(crs)}"
    gsd = properties.get("gsd")
    if gsd is not None:
        with contextlib.suppress(TypeError, ValueError):
            spatial_kwargs["resolution"] = float(gsd)
    if spatial_kwargs:
        builder.spatial(**spatial_kwargs)

    # --- temporal ---------------------------------------------------------- #
    dt = properties.get("datetime")
    start_dt = properties.get("start_datetime", dt)
    end_dt = properties.get("end_datetime", dt)
    if start_dt or end_dt:
        builder.temporal(
            start=str(start_dt) if start_dt else None, end=str(end_dt) if end_dt else None
        )

    # --- bands & access ---------------------------------------------------- #
    formats: set[str] = set()
    for asset in assets.values():
        if not isinstance(asset, dict):
            continue
        media_type = asset.get("type")
        if media_type:
            formats.add(str(media_type))
        for band in asset.get("eo:bands") or []:
            if isinstance(band, dict) and band.get("name"):
                builder.band(
                    name=str(band["name"]),
                    dtype=band.get("data_type"),
                    units=band.get("unit"),
                    description=band.get("description"),
                )
    first_link = item.get("links", [{}])[0].get("href") if item.get("links") else None
    access_kwargs: dict[str, Any] = {
        "protocol": "stac",
        "endpoint": node_url or first_link or item_id,
        "format": ",".join(sorted(formats)) or "unknown",
    }
    builder.access(**access_kwargs)

    # --- provenance & license ---------------------------------------------- #
    builder.provenance(
        provider=str(properties.get("provider") or collection or "unknown"),
        source=item_id,
        lineage=f"Imported from STAC (collection={collection})",
    )
    license_value = properties.get("license")
    if license_value:
        builder.license(name=str(license_value))

    return builder.build()


def import_stac_item(
    source: str | os.PathLike,
    node_url: str | None = None,
    validate: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> GeoCard:
    """Fetch a STAC Item (URL or file) and convert it to a GeoCard.

    Args:
        source: URL or local path of a STAC Item (GeoJSON Feature).
        node_url: Optional endpoint to record in ``access.endpoint``.
        validate: Validate the resulting card against the official schema.
        timeout: HTTP timeout for remote sources.
        client: Optional pre-configured ``httpx.Client`` (advanced use).
    """
    item = fetch_stac_item(source, timeout=timeout, client=client)
    card = stac_item_to_geocard(item, node_url=node_url)
    if validate:
        card.validate()
    logger.info("Imported STAC item '%s' as GeoCard", card.id)
    return card
