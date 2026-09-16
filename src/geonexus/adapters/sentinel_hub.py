"""Sentinel Hub / Copernicus Data Space adapter.

将 Sentinel-2 卫星数据产品转换为 GeoNexus GeoCard，支持：
- Copernicus Data Space Ecosystem (CDSE) STAC API 搜索
- Sentinel Hub OGC WMS/WMTS 获取
- 自动提取 CRS/bbox/波段/时间覆盖 → GAAG 合约
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..gaag import GAAGContract, GAAGRegistry
from ..gaag.embed import embed_text
from ..geocard.builder import GeoCardBuilder
from ..geocard.model import GeoCard

logger = logging.getLogger(__name__)

# Copernicus Data Space Ecosystem STAC endpoint
CDSE_STAC_URL = "https://catalogue.dataspace.copernicus.eu/stac"

# Sentinel-2 L2A collection
SENTINEL2_L2A = "sentinel-2-l2a"

# Key spectral bands
S2_BANDS = {
    "B02": {"name": "Blue", "wavelength_nm": 490, "resolution_m": 10},
    "B03": {"name": "Green", "wavelength_nm": 560, "resolution_m": 10},
    "B04": {"name": "Red", "wavelength_nm": 665, "resolution_m": 10},
    "B08": {"name": "NIR", "wavelength_nm": 842, "resolution_m": 10},
    "B11": {"name": "SWIR1", "wavelength_nm": 1610, "resolution_m": 20},
    "B12": {"name": "SWIR2", "wavelength_nm": 2190, "resolution_m": 20},
}


class SentinelHubError(Exception):
    """Raised when Sentinel Hub / CDSE interaction fails."""


def search_sentinel2(
    bbox: list[float],
    start: str | None = None,
    end: str | None = None,
    max_cloud: int = 30,
    limit: int = 10,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """通过 CDSE STAC API 搜索 Sentinel-2 L2A 影像。

    Args:
        bbox: [west, south, east, north]
        start: ISO 8601 start (default: 30 days ago)
        end: ISO 8601 end (default: now)
        max_cloud: 最大云覆盖率百分比
        limit: 最多返回条数
    """
    cl = client or httpx.Client(timeout=30.0)
    if start is None:
        start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
    if end is None:
        end = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")

    search_payload = {
        "collections": [SENTINEL2_L2A],
        "bbox": bbox,
        "datetime": f"{start}/{end}",
        "query": {"eo:cloud_cover": {"lte": max_cloud}},
        "limit": limit,
    }

    try:
        resp = cl.post(
            f"{CDSE_STAC_URL}/search",
            json=search_payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", [])
        logger.info("Sentinel-2 search: bbox=%s → %d features", bbox, len(features))
        return features
    except Exception as exc:
        logger.warning("Sentinel-2 STAC search failed: %s", exc)
        raise SentinelHubError(f"STAC search failed: {exc}") from exc


def stac_item_to_geocard(feature: dict[str, Any]) -> GeoCard:
    """将 STAC Feature 转换为 GeoNexus GeoCard。

    映射：STAC id/properties/bbox → GeoCard spatial/temporal/bands。
    """
    props = feature.get("properties", {})
    item_id = feature["id"]
    bbox = feature.get("bbox")
    assets = feature.get("assets", {})

    builder = (
        GeoCardBuilder(
            id=f"s2.{item_id}",
            type="data",
            name=f"Sentinel-2 {item_id}",
            description=(
                f"Sentinel-2 L2A scene {item_id}. "
                f"Cloud: {props.get('eo:cloud_cover', '?')}%. "
                f"Platform: {props.get('platform', 'S2')}."
            ),
        )
        .tag("sentinel-2", "satellite", "copernicus", "stac")
        .spatial(
            bbox=bbox,
            crs=f"EPSG:{props.get('proj:epsg', 4326)}",
            resolution=10.0,
        )
    )

    # Temporal
    dt = props.get("datetime")
    if dt:
        builder = builder.temporal(start=dt, end=dt)

    # Bands from assets
    for asset_key, asset in assets.items():
        eo_bands = asset.get("eo:bands", [])
        for band_info in eo_bands:
            band_name = band_info.get("name", asset_key)
            builder = builder.band(
                name=band_name,
                dtype=band_info.get("data_type", "uint16"),
                description=band_info.get("description", ""),
            )

    # Access
    visual_href = assets.get("visual", {}).get("href", "")
    builder = builder.access(
        protocol="https",
        endpoint=visual_href or "https://catalogue.dataspace.copernicus.eu/stac",
        format="GeoTIFF",
    )

    return builder.build()


def register_sentinel2_contracts(
    registry: GAAGRegistry,
    bbox: list[float],
    **search_kwargs,
) -> list[GAAGContract]:
    """搜索 Sentinel-2 影像并注册为 GAAG 合约。

    一站式流水线：CDSE STAC search → GeoCard → semantic embed → GAAG register。
    """
    features = search_sentinel2(bbox, **search_kwargs)
    contracts: list[GAAGContract] = []
    for feature in features:
        geocard = stac_item_to_geocard(feature)
        contract_id = f"contract.{feature['id']}"

        # 生成语义嵌入
        desc = geocard.description
        embedding = embed_text(desc)

        # 构建合约
        scanned_meta = {
            "platform": feature.get("properties", {}).get("platform", "S2"),
            "cloud_cover": feature.get("properties", {}).get("eo:cloud_cover"),
            "stac_id": feature["id"],
            "collection": feature.get("collection", SENTINEL2_L2A),
        }

        from datetime import datetime as dt
        from datetime import timezone

        contract = GAAGContract(
            contract_id=contract_id,
            asset_type="raster",
            card=geocard,
            scanned_meta=scanned_meta,
            semantic_embedding=embedding,
            provenance=f"stac:{feature['id']}",
            registered_at=dt.now(timezone.utc).isoformat(),
        )
        try:
            registry.register(contract)
            contracts.append(contract)
        except Exception:
            # 可能已注册，跳过
            logger.debug("Contract %s already registered", contract_id)
    return contracts