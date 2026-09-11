"""STAC write-side adapter (V1.0+): publish GeoCards as STAC items.

Complements the read-side adapter (`geonexus.adapters.stac`): a GeoCard is
exported back into the STAC ecosystem as a **STAC Item** (GeoJSON Feature)
or as part of a **STAC Catalog**, so GeoNexus assets are publishable to any
STAC-compatible catalog (e.g. a STAC API / static catalog on object
storage).

Mapping (GeoCard -> STAC):
    GeoCard                      -> STAC Item
    id (sanitized)               -> id
    spatial.bbox                 -> bbox
    spatial.geometry (GeoJSON)   -> geometry (bbox polygon fallback)
    temporal.start / end         -> properties.datetime /
                                     start_datetime / end_datetime
    name / description           -> properties.title / description
    license                      -> properties.license
    spatial.crs (EPSG:xxxx)      -> properties["proj:epsg"]
    spatial.resolution           -> properties.gsd
    bands[]                      -> assets (one per band, eo:bands)
    access.endpoint / format     -> asset href / type (fallback asset)
    capabilities / type          -> properties["geonexus:..."] (extension)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..geocard.model import GeoCard

logger = logging.getLogger(__name__)

_STAC_ID_RE = re.compile(r"[^a-zA-Z0-9._~-]+")


def _sanitize_stac_id(value: str) -> str:
    """STAC ids allow [a-zA-Z0-9._~-]; replace anything else."""
    return _STAC_ID_RE.sub("-", value)


def _epsg_from_crs(crs: str | None) -> int | None:
    if not crs:
        return None
    match = re.search(r"EPSG:(\d+)", crs, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _bbox_polygon(bbox: list[float]) -> dict[str, Any]:
    west, south, east, north = bbox[0], bbox[1], bbox[2], bbox[3]
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


def geocard_to_stac_item(
    card: GeoCard,
    collection: str | None = None,
    stac_version: str = "1.0.0",
) -> dict[str, Any]:
    """Convert a GeoCard into a STAC Item (GeoJSON Feature)."""
    item_id = _sanitize_stac_id(card.id)
    spatial = card.spatial
    temporal = card.temporal

    bbox = list(spatial.bbox) if spatial and spatial.bbox else None
    geometry: dict[str, Any] | None = None
    if spatial and spatial.geometry:
        try:
            geometry = json.loads(spatial.geometry)
        except json.JSONDecodeError:
            geometry = None
    if geometry is None and bbox:
        geometry = _bbox_polygon(bbox)

    # Properties (temporal + core + geonexus extension).
    datetime_value = None
    start_value = None
    end_value = None
    if temporal:
        start_value = temporal.start
        end_value = temporal.end
        if temporal.start == temporal.end or temporal.start:
            datetime_value = temporal.start
    properties: dict[str, Any] = {}
    if datetime_value:
        properties["datetime"] = datetime_value
    if start_value:
        properties["start_datetime"] = start_value
    if end_value:
        properties["end_datetime"] = end_value
    properties["title"] = card.name
    properties["description"] = card.description
    if card.license is not None:
        properties["license"] = (
            card.license.name if hasattr(card.license, "name") else str(card.license)
        )
    epsg = _epsg_from_crs(spatial.crs) if spatial else None
    if epsg:
        properties["proj:epsg"] = epsg
    if spatial and spatial.resolution:
        properties["gsd"] = spatial.resolution
    # GeoNexus extension (namespace geonexus:).
    properties["geonexus:type"] = card.type
    properties["geonexus:geocard_version"] = card.geocard_version
    caps = card.capability_names()
    if caps:
        properties["geonexus:capabilities"] = caps

    # Assets: one per band, or a single fallback asset from access.
    endpoint = card.access.endpoint if card.access else None
    media_type = card.access.format if card.access else None
    assets: dict[str, Any] = {}
    if card.bands:
        for band in card.bands:
            assets[band.name] = {
                "href": endpoint or f"{item_id}/{band.name}",
                "type": media_type or "application/octet-stream",
                "eo:bands": [
                    {
                        "name": band.name,
                        **({"data_type": band.dtype} if band.dtype else {}),
                        **({"unit": band.units} if band.units else {}),
                    }
                ],
            }
    else:
        assets["geocard"] = {
            "href": endpoint or f"{item_id}.json",
            "type": media_type or "application/geo+json",
            "roles": ["metadata"],
            "description": "GeoCard of this asset.",
        }

    item: dict[str, Any] = {
        "type": "Feature",
        "stac_version": stac_version,
        "id": item_id,
        "properties": properties,
        "assets": assets,
    }
    if collection:
        item["collection"] = collection
    if bbox:
        item["bbox"] = bbox
    if geometry:
        item["geometry"] = geometry
    links: list[dict[str, Any]] = []
    if endpoint:
        links.append({"rel": "self", "href": endpoint})
    if collection:
        links.append({"rel": "collection", "href": f"collections/{collection}"})
    if links:
        item["links"] = links
    return item


def geocard_to_stac_catalog(
    cards: list[GeoCard],
    catalog_id: str,
    title: str = "",
    description: str = "",
    collection: str | None = None,
    stac_version: str = "1.0.0",
) -> dict[str, Any]:
    """Build a STAC Catalog containing the given items (GeoJSON Features)."""
    items = [geocard_to_stac_item(c, collection=collection) for c in cards]
    catalog: dict[str, Any] = {
        "type": "Catalog",
        "stac_version": stac_version,
        "id": _sanitize_stac_id(catalog_id),
        "title": title or catalog_id,
        "description": description or f"GeoNexus catalog '{catalog_id}'.",
        "links": [{"rel": "root", "href": "./catalog.json", "type": "application/json"}],
    }
    if collection:
        catalog["links"].append({"rel": "collection", "href": f"collections/{collection}.json"})
    for item in items:
        catalog["links"].append(
            {
                "rel": "item",
                "href": f"items/{item['id']}.json",
                "type": "application/geo+json",
            }
        )
    return {"catalog": catalog, "items": items}


def save_stac_item(item: dict[str, Any], path: str | Path) -> str:
    """Write a STAC Item (or Catalog) to a JSON file."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote STAC document to %s", out)
    return str(out)
