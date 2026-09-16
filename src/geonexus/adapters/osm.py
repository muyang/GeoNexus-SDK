"""OSM (OpenStreetMap) 适配器 — Overpass API → GeoCard → GAAG 注册。

通过 Overpass API 查询 OSM 要素（建筑、道路、水体），
转换为 GeoNexus GeoCard 格式，注册到 GAAG 合约注册中心。

支持查询类型:
- building: 建筑物 (landuse=residential/commercial/industrial)
- water: 水体 (natural=water, waterway=river)
- road: 道路 (highway=primary/secondary/tertiary)
- landuse: 土地利用 (landuse=forest/farmland/grassland)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..gaag import GAAGContract, GAAGRegistry
from ..gaag.embed import embed_text
from ..geocard.builder import GeoCardBuilder
from ..geocard.model import GeoCard

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: Overpass 镜像列表（主站 504 时按序回退）
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

#: Overpass API 要求可识别的 User-Agent（缺失会被拒绝为 406）
USER_AGENT = "GeoNexus/1.0 (geospatial capability network; +https://github.com/muyang/GeoNexus-SDK)"

# 查询模板
QUERY_TEMPLATES = {
    "building": '[out:json][timeout:25];(way["building"]({bbox}););out body;>;out skel qt;',
    "water": '[out:json][timeout:25];(way["natural"="water"]({bbox});way["waterway"="river"]({bbox}););out body;>;out skel qt;',
    "road": '[out:json][timeout:25];(way["highway"~"primary|secondary|tertiary"]({bbox}););out body;>;out skel qt;',
    "landuse": '[out:json][timeout:25];(way["landuse"~"forest|farmland|grassland|residential"]({bbox}););out body;>;out skel qt;',
}


class OSMOverpassError(Exception):
    """Overpass API 查询失败。"""


def query_osm(
    bbox: list[float],
    query_type: str = "building",
    client: httpx.Client | None = None,
    mirrors: list[str] | None = None,
) -> dict[str, Any]:
    """通过 Overpass API 查询 OSM 要素。

    自动在多个 Overpass 镜像间回退（主站过载时返回 504）。

    Args:
        bbox: [south, west, north, east] (Overpass format!)
        query_type: building | water | road | landuse
        mirrors: 自定义镜像列表（默认 OVERPASS_MIRRORS）
    """
    template = QUERY_TEMPLATES.get(query_type)
    if template is None:
        raise ValueError(f"Unknown query_type: {query_type}. Available: {list(QUERY_TEMPLATES)}")

    bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
    query = template.format(bbox=bbox_str)

    cl = client or httpx.Client(
        timeout=60.0,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    endpoints = mirrors or OVERPASS_MIRRORS
    last_error: Exception | None = None

    for endpoint in endpoints:
        try:
            resp = cl.post(
                endpoint,
                data={"data": query},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            elements = data.get("elements", [])
            logger.info("OSM query %s @ %s: bbox=%s → %d elements",
                        query_type, endpoint.split("/")[2], bbox_str, len(elements))
            return {"elements": elements, "query_type": query_type, "bbox": bbox,
                    "endpoint": endpoint}
        except Exception as exc:
            last_error = exc
            logger.warning("Overpass mirror %s failed: %s", endpoint, exc)
            continue

    raise OSMOverpassError(f"All Overpass mirrors failed. Last error: {last_error}")


def osm_to_geocard(
    element: dict[str, Any],
    query_type: str,
    bbox: list[float],
) -> GeoCard:
    """将 OSM 要素转换为 GeoCard。

    映射：OSM tags → GeoCard spatial/properties/bands。
    """
    eid = element["id"]
    etype = element.get("type", "way")
    tags = element.get("tags", {})

    name = tags.get("name", f"OSM {query_type} {eid}")
    desc = ", ".join(f"{k}={v}" for k, v in list(tags.items())[:5]) if tags else f"OSM {query_type}"

    builder = (
        GeoCardBuilder(
            id=f"osm.{eid}",
            type="data",
            name=name,
            description=desc,
        )
        .tag("openstreetmap", query_type, "vector")
        .spatial(bbox=bbox, crs="EPSG:4326")
    )

    # Add access info
    builder = builder.access(
        protocol="https",
        endpoint=f"https://www.openstreetmap.org/{etype}/{eid}",
    )

    return builder.build()


def register_osm_contracts(
    registry: GAAGRegistry,
    bbox: list[float],
    query_type: str = "building",
    **search_kwargs,
) -> list[GAAGContract]:
    """查询 OSM → GeoCard → embed → GAAG register 一站式流水线。"""
    result = query_osm(bbox, query_type, **search_kwargs)
    elements = result["elements"]
    contracts: list[GAAGContract] = []

    for element in elements:
        geocard = osm_to_geocard(element, query_type, bbox)
        contract_id = f"contract.osm.{element['id']}"

        desc = geocard.description or ""
        embedding = embed_text(desc)

        contract = GAAGContract(
            contract_id=contract_id,
            asset_type="vector",
            card=geocard,
            scanned_meta={
                "osm_type": element.get("type"),
                "osm_id": element["id"],
                "query_type": query_type,
                "bbox": bbox,
                "tags": element.get("tags", {}),
            },
            semantic_embedding=embedding,
            provenance=f"osm:{element['id']}",
            registered_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            registry.register(contract)
            contracts.append(contract)
        except Exception:
            logger.debug("Contract %s already registered", contract_id)

    return contracts