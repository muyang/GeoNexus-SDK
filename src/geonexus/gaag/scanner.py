"""GAAG scan pipeline — turn files into contract-ready metadata.

scan 流水线（参考 GAAG 设计：scan → metadata → LLM 描述 → embed → register）:
1. :func:`scan_raster` — rasterio 读取 GeoTIFF/COG 的 CRS/bbox/波段/分辨率
2. :func:`scan_vector` — GeoJSON/Shapefile 的 bbox/几何类型/要素数
3. :func:`scan_asset_to_contract` — 一步扫描 → GAAGContract（含 GeoCard）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..geocard.builder import GeoCardBuilder
from ..geocard.model import Band
from .contract import GAAGContract, GAAGContractSpec, _now

logger = logging.getLogger(__name__)


SUPPORTED_RASTER_EXTS = {".tif", ".tiff", ".gtiff", ".vrt", ".img"}
SUPPORTED_VECTOR_EXTS = {".geojson", ".json", ".gpkg", ".shp"}


def scan_raster(path: str | Path) -> dict[str, Any]:
    """Extract raster metadata with rasterio.

    Returns a dict with crs, bbox, bands, resolution, width, height,
    count, dtype, driver, nodata.
    """
    import rasterio

    with rasterio.open(path) as src:
        band_meta = []
        for idx in range(1, src.count + 1):
            band_meta.append(
                {
                    "name": src.descriptions[idx - 1] or f"B{idx:02d}",
                    "index": idx,
                    "dtype": src.dtypes[idx - 1],
                }
            )
        return {
            "driver": src.driver,
            "crs": src.crs.to_string() if src.crs else None,
            "bbox": [float(v) for v in src.bounds],
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "resolution": float(max(src.res)),
            "bands": band_meta,
            "nodata": src.nodata,
        }


def scan_vector(path: str | Path) -> dict[str, Any]:
    """Extract vector metadata from a GeoJSON file (pure Python, no GDAL)."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".geojson":
        return _scan_geojson(path)

    # Fallback: try fiona for gpkg/shp when available.
    try:
        import fiona

        with fiona.open(path) as src:
            bbox = list(src.bounds)
            geom_types: set[str] = set()
            count = 0
            for feature in src:
                geom_types.add(
                    feature["geometry"]["type"] if feature["geometry"] else "None"
                )
                count += 1
                if count >= 100:
                    break
            return {
                "driver": src.driver,
                "crs": str(src.crs) if src.crs else None,
                "bbox": [float(v) for v in bbox],
                "count": count,
                "geometry_types": sorted(geom_types),
            }
    except ImportError:
        raise GAAGUnsupportedError(
            f"fiona required to scan {suffix} files; install 'fiona' or use GeoJSON"
        )


def _scan_geojson(path: Path) -> dict[str, Any]:
    """Parse a GeoJSON FeatureCollection/Feature without any geodeps."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    if data.get("type") == "FeatureCollection":
        features = data.get("features", [])
    elif data.get("type") == "Feature":
        features = [data]
    else:
        raise GAAGUnsupportedError(f"Unsupported GeoJSON structure in {path}")

    xs: list[float] = []
    ys: list[float] = []
    geom_types: set[str] = set()
    for feat in features[:100]:
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        if gtype:
            geom_types.add(gtype)
            _collect_coords(geom.get("coordinates"), xs, ys)

    bbox = data.get("bbox")
    if bbox is None and xs and ys:
        bbox = [min(xs), min(ys), max(xs), max(ys)]

    return {
        "driver": "GeoJSON",
        "crs": "EPSG:4326",  # GeoJSON spec mandates WGS84
        "bbox": [float(v) for v in bbox] if bbox else None,
        "count": len(features),
        "geometry_types": sorted(geom_types),
    }


def _collect_coords(coord: Any, xs: list[float], ys: list[float]) -> None:
    """Recursively collect x/y values from a possibly nested coordinate list."""
    if not isinstance(coord, list):
        return
    if len(coord) >= 2 and isinstance(coord[0], (int, float)) and isinstance(coord[1], (int, float)):
        xs.append(float(coord[0]))
        ys.append(float(coord[1]))
        return
    for item in coord:
        _collect_coords(item, xs, ys)


def _detect_asset_type(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in SUPPORTED_RASTER_EXTS:
        return "raster"
    if suffix in SUPPORTED_VECTOR_EXTS:
        return "vector"
    return "other"


def scan_asset_to_contract(
    path: str | Path,
    *,
    contract_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    embedding: list[float] | None = None,
) -> GAAGContract:
    """Scan a geospatial asset file into a full GAAGContract.

    This is the ``scan → metadata → register`` step of the GAAG pipeline.
    The caller may supply an ``embedding`` computed from the description
    (see :mod:`geonexus.gaag.embed`).
    """
    path = Path(path)
    asset_type = _detect_asset_type(path)
    logger.info("GAAG scan: %s (type=%s)", path, asset_type)

    if asset_type == "raster":
        meta = scan_raster(path)
    elif asset_type == "vector":
        meta = scan_vector(path)
    else:
        raise GAAGUnsupportedError(f"Unsupported asset type for scan: {path}")

    cid = contract_id or f"contract.{path.stem}"
    card = (
        GeoCardBuilder(
            id=cid,
            type="data",
            name=name or path.stem,
            description=description or f"Auto-scanned from {path.name}",
        )
        .tag("gaag", asset_type)
        .spatial(bbox=meta.get("bbox"), crs=meta.get("crs"), resolution=meta.get("resolution"))
        .build()
    )
    if asset_type == "raster":
        for band in meta.get("bands", []):
            card.bands.append(
                Band(name=band["name"], dtype=band.get("dtype"), description=f"Band {band['index']}")
            )

    provenance = f"scanned:{path.name}"
    return GAAGContract(
        contract_id=cid,
        asset_type=asset_type,
        card=card,
        scanned_meta=meta,
        semantic_embedding=embedding,
        provenance=provenance,
        registered_at=_now(),
    )


class GAAGUnsupportedError(Exception):
    """Raised when scanning an unsupported file type."""


class RasterScanner:
    """Object-oriented scanner for raster assets (wraps :func:`scan_raster`)."""

    def scan(self, path: str | Path) -> dict[str, Any]:
        return scan_raster(path)


class VectorScanner:
    """Object-oriented scanner for vector assets (wraps :func:`scan_vector`)."""

    def scan(self, path: str | Path) -> dict[str, Any]:
        return scan_vector(path)