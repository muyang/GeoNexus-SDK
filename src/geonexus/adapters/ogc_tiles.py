"""OGC API - Tiles / Maps / Styles adapter (v1.1).

Bridges the **visualization plane** of OGC APIs: tilesets (raster/vector
tiles), map tilesets (rendered maps) and styles. Each becomes a GeoCard
(type ``data`` with a ``map`` / ``tiles`` capability) so Web frontends can
discover renderable layers through the registry and put them straight on a
map client (MapLibre / Leaflet / etc.).

Endpoints (OGC API - Tiles / Maps / Styles, per collection where relevant):

- ``GET /tiles`` / ``GET /collections/{id}/tiles``          → tilesets list
- ``GET /tiles/{tilesetId}``                                 → tileset metadata
- ``GET /styles`` / ``GET /collections/{id}/styles``         → styles list
- ``GET /styles/{styleId}``                                  → style metadata
"""

from __future__ import annotations

import logging
from typing import Any

from ..geocard import GeoCard, GeoCardBuilder
from .ogc import DEFAULT_TIMEOUT, OgcApiClient, _first_link

logger = logging.getLogger(__name__)


def list_ogc_tilesets(
    api_url: str,
    collection_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: OgcApiClient | None = None,
) -> list[dict[str, Any]]:
    """List tilesets advertised by an OGC API - Tiles server."""
    owns = client is None
    client = client or OgcApiClient(api_url, timeout=timeout)
    try:
        path = f"/collections/{collection_id}/tiles" if collection_id else "/tiles"
        data = client.get_json(path)
        return list(data.get("tilesets") or [])
    finally:
        if owns:
            client.close()


def fetch_ogc_tileset(
    api_url: str,
    tileset_id: str,
    collection_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: OgcApiClient | None = None,
) -> dict[str, Any]:
    """Fetch one tileset's metadata (tileMatrixSet links, CRS, bbox…)."""
    owns = client is None
    client = client or OgcApiClient(api_url, timeout=timeout)
    try:
        if collection_id:
            return client.get_json(f"/collections/{collection_id}/tiles/{tileset_id}")
        return client.get_json(f"/tiles/{tileset_id}")
    finally:
        if owns:
            client.close()


def ogc_tileset_to_geocard(
    tileset: dict[str, Any],
    api_url: str,
    geocard_version: str = "1.0",
) -> GeoCard:
    """Convert a tileset description into a renderable GeoCard."""
    tid = str(tileset.get("id", "ogc-tileset"))
    links = tileset.get("links") or []
    self_href = _first_link(links, "self")
    tile_href = _first_link(links, "item") or _first_link(links, "tiles")

    builder = (
        GeoCardBuilder(
            id=tid,
            type="data",
            name=str(tileset.get("title") or tid),
            description=str(
                tileset.get("description") or f"OGC API - Tiles tileset '{tid}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogcapi", "ogcapi-tiles", "tiles")
        .capability("tiles", "Renderable tile layer (map client)")
        .access(
            protocol="ogcapi-tiles",
            endpoint=tile_href or self_href or api_url,
            format="tiles",
        )
        .provenance(
            provider=str(api_url),
            source=tid,
            lineage=f"Imported from OGC API - Tiles (tileset '{tid}')",
        )
    )

    crs = tileset.get("crs") or tileset.get("tileMatrixSetURI")
    bbox = tileset.get("boundingBox") or tileset.get("extent", {}).get("spatial")
    if isinstance(bbox, dict):
        bbox = bbox.get("bbox")
    if isinstance(bbox, list) and bbox:
        first = bbox[0] if isinstance(bbox[0], list) else bbox
        try:
            box = [float(x) for x in first]
            kwargs: dict[str, Any] = {"bbox": box}
            if crs:
                kwargs["crs"] = str(crs)
            builder.spatial(**kwargs)
        except (TypeError, ValueError):
            pass
    elif crs:
        builder.spatial(crs=str(crs))

    return builder.build()


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #
def list_ogc_styles(
    api_url: str,
    collection_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: OgcApiClient | None = None,
) -> list[dict[str, Any]]:
    """List styles advertised by an OGC API - Styles server."""
    owns = client is None
    client = client or OgcApiClient(api_url, timeout=timeout)
    try:
        path = f"/collections/{collection_id}/styles" if collection_id else "/styles"
        data = client.get_json(path)
        return list(data.get("styles") or [])
    finally:
        if owns:
            client.close()


def fetch_ogc_style(
    api_url: str,
    style_id: str,
    collection_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: OgcApiClient | None = None,
) -> dict[str, Any]:
    """Fetch one style's metadata."""
    owns = client is None
    client = client or OgcApiClient(api_url, timeout=timeout)
    try:
        if collection_id:
            return client.get_json(f"/collections/{collection_id}/styles/{style_id}")
        return client.get_json(f"/styles/{style_id}")
    finally:
        if owns:
            client.close()


def ogc_style_to_geocard(
    style: dict[str, Any],
    api_url: str,
    geocard_version: str = "1.0",
) -> GeoCard:
    """Convert a style description into a GeoCard (type ``skill``: styling)."""
    sid = str(style.get("id", "ogc-style"))
    links = style.get("links") or []
    self_href = _first_link(links, "self")
    style_href = _first_link(links, "style") or self_href

    builder = (
        GeoCardBuilder(
            id=sid,
            type="skill",
            name=str(style.get("title") or sid),
            description=str(
                style.get("description") or f"OGC API - Styles style '{sid}'"
            ),
            geocard_version=geocard_version,
        )
        .tag("ogcapi", "ogcapi-styles", "style")
        .capability("styling", "Cartographic style for a renderable layer")
        .input("layer", "string", description="Layer / tileset id to apply the style to")
        .output("style_url", "string", description="Resolved style document URL")
        .access(
            protocol="ogcapi-styles",
            endpoint=style_href or api_url,
            format="style",
        )
        .provenance(
            provider=str(api_url),
            source=sid,
            lineage=f"Imported from OGC API - Styles (style '{sid}')",
        )
    )
    return builder.build()


# --------------------------------------------------------------------------- #
# Convenience: tiles + styles → cards in one call
# --------------------------------------------------------------------------- #
def ogc_visualization_to_geocards(
    api_url: str,
    collection_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[GeoCard]:
    """Import both tilesets and styles as GeoCards (visualization discovery)."""
    cards: list[GeoCard] = []
    with OgcApiClient(api_url, timeout=timeout) as client:
        for tileset in list_ogc_tilesets(
            api_url, collection_id=collection_id, timeout=timeout, client=client
        ):
            try:
                cards.append(ogc_tileset_to_geocard(tileset, api_url))
            except Exception as exc:  # noqa: BLE001 - skip one bad tileset
                logger.warning("Skipping tileset: %s", exc)
        for style in list_ogc_styles(
            api_url, collection_id=collection_id, timeout=timeout, client=client
        ):
            try:
                cards.append(ogc_style_to_geocard(style, api_url))
            except Exception as exc:  # noqa: BLE001 - skip one bad style
                logger.warning("Skipping style: %s", exc)
    return cards
