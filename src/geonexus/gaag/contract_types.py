"""GAAG 合约类型扩展 — 补齐 GeoAsset Contract 数据类型覆盖。

2026 年度考核要求合约模型覆盖 ≥6 种数据类型。本模块在 raster/vector
基础上补齐 5 类，使总计达到 7 类：

    raster      栅格影像        (rasterio)      — 已有
    vector      矢量要素        (GeoJSON)       — 已有
    temporal    时序立方体      (NetCDF/Zarr)   — 本模块
    volume3d    三维体数据      (voxel/3D)      — 本模块
    pointcloud  点云            (LAS/LAZ)       — 本模块
    statistical 统计表格        (CSV/Parquet)   — 本模块
    text        文本知识        (PDF/MD/TXT)    — 本模块

每类合约由 :class:`ContractSchema` 描述，声明其必填字段与校验规则，
供 GAAG 注册中心做合约一致性验证。
"""

from __future__ import annotations

import csv
import json
import logging
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 合约模板定义
# --------------------------------------------------------------------------- #
@dataclass
class ContractSchema:
    """一类 GeoAsset Contract 的模板定义。

    Attributes:
        asset_type: 合约类型标识（写入 GAAGContract.asset_type）。
        required_fields: 该类型合约必须具备的元数据字段。
        optional_fields: 可选字段。
        extensions: 典型文件扩展名。
        description: 人类可读说明。
    """

    asset_type: str
    required_fields: list[str]
    extensions: list[str]
    description: str
    optional_fields: list[str] = field(default_factory=list)


#: 全部 7 类合约模板
CONTRACT_SCHEMAS: dict[str, ContractSchema] = {
    "raster": ContractSchema(
        asset_type="raster",
        required_fields=["crs", "bbox", "resolution", "bands"],
        optional_fields=["nodata", "dtype", "count"],
        extensions=[".tif", ".tiff", ".vrt", ".img"],
        description="栅格影像：CRS + bbox + 分辨率 + 波段定义",
    ),
    "vector": ContractSchema(
        asset_type="vector",
        required_fields=["crs", "bbox", "geometry_types"],
        optional_fields=["feature_count", "attributes"],
        extensions=[".geojson", ".gpkg", ".shp"],
        description="矢量要素：CRS + bbox + 几何类型",
    ),
    "temporal": ContractSchema(
        asset_type="temporal",
        required_fields=["crs", "bbox", "time_start", "time_end", "time_interval"],
        optional_fields=["timesteps", "variables", "units"],
        extensions=[".nc", ".nc4", ".zarr"],
        description="时序立方体：时空范围 + 时间步长 + 变量定义",
    ),
    "volume3d": ContractSchema(
        asset_type="volume3d",
        required_fields=["crs", "bbox", "z_min", "z_max"],
        optional_fields=["z_units", "voxel_size", "dimensions"],
        extensions=[".voxel", ".3dtiles", ".nii"],
        description="三维体数据：水平范围 + 垂直范围 + 体元尺寸",
    ),
    "pointcloud": ContractSchema(
        asset_type="pointcloud",
        required_fields=["crs", "bbox", "point_count"],
        optional_fields=["z_range", "density", "classification", "intensity_range"],
        extensions=[".las", ".laz", ".ply", ".pcd"],
        description="点云：CRS + bbox + 点数 + 高程范围",
    ),
    "statistical": ContractSchema(
        asset_type="statistical",
        required_fields=["columns", "record_count"],
        optional_fields=["spatial_key", "time_key", "units", "aggregation"],
        extensions=[".csv", ".parquet", ".xlsx"],
        description="统计表格：列定义 + 记录数 + 空间/时间关联键",
    ),
    "text": ContractSchema(
        asset_type="text",
        required_fields=["content_type", "language"],
        optional_fields=["title", "page_count", "word_count", "topics"],
        extensions=[".pdf", ".md", ".txt", ".docx"],
        description="文本知识：内容类型 + 语言 + 主题标签",
    ),
}


# --------------------------------------------------------------------------- #
# 各类型扫描器
# --------------------------------------------------------------------------- #
def scan_temporal(path: str | Path) -> dict[str, Any]:
    """扫描时序立方体（NetCDF/Zarr），提取时空元数据。

    无 netCDF4/xarray 依赖时回退到文件名与文件大小推断，
    保证流水线在最小环境下仍可运行。
    """
    path = Path(path)
    meta: dict[str, Any] = {"driver": path.suffix.lstrip("."), "file_size": path.stat().st_size}

    try:
        import xarray as xr  # type: ignore

        with xr.open_dataset(path) as ds:
            coords = set(ds.coords)
            # 时间维
            time_name = next((c for c in ("time", "t", "date") if c in coords), None)
            if time_name is not None:
                times = ds[time_name].values
                meta["time_start"] = str(times[0])[:19]
                meta["time_end"] = str(times[-1])[:19]
                meta["timesteps"] = int(len(times))
                if len(times) > 1:
                    delta = times[1] - times[0]
                    meta["time_interval"] = str(delta)
            # 空间范围
            lat = next((c for c in ("lat", "latitude", "y") if c in coords), None)
            lon = next((c for c in ("lon", "longitude", "x") if c in coords), None)
            if lat and lon:
                meta["bbox"] = [
                    float(ds[lon].min()), float(ds[lat].min()),
                    float(ds[lon].max()), float(ds[lat].max()),
                ]
            meta["crs"] = str(ds.attrs.get("crs", "EPSG:4326"))
            meta["variables"] = list(ds.data_vars)
            return meta
    except ImportError:
        logger.debug("xarray unavailable, using filename inference for %s", path)

    # 回退：从文件名推断时间范围（如 flood_2020_2025.nc）
    years = re.findall(r"(?:19|20)\d{2}", path.stem)
    if years:
        meta["time_start"] = f"{min(years)}-01-01"
        meta["time_end"] = f"{max(years)}-12-31"
        meta["timesteps"] = len(years)
    meta.setdefault("crs", "EPSG:4326")
    meta.setdefault("bbox", None)
    meta.setdefault("time_interval", "P1D")
    return meta


def scan_volume3d(path: str | Path) -> dict[str, Any]:
    """扫描三维体数据，提取水平与垂直范围。"""
    path = Path(path)
    meta: dict[str, Any] = {
        "driver": path.suffix.lstrip("."),
        "file_size": path.stat().st_size,
        "crs": "EPSG:4979",  # 3D WGS84
    }
    # 3D Tiles 是 JSON 格式，可读取 root.boundingVolume
    if path.suffix.lower() == ".3dtiles" or path.suffix.lower() == ".json":
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            box = data.get("root", {}).get("boundingVolume", {}).get("box")
            # box = [cx,cy,cz, xHalfX,xHalfY,xHalfZ, yHalfX,yHalfY,yHalfZ, zHalfX,zHalfY,zHalfZ]
            if box and len(box) >= 12:
                cz = float(box[2])
                z_half = abs(float(box[11]))
                meta["z_min"] = cz - z_half
                meta["z_max"] = cz + z_half
                meta["dimensions"] = [
                    abs(float(box[3])) * 2, abs(float(box[7])) * 2, z_half * 2,
                ]
        except (OSError, json.JSONDecodeError, ValueError):
            logger.debug("3D Tiles boundingVolume not readable: %s", path)
    meta.setdefault("bbox", None)
    meta.setdefault("z_min", 0.0)
    meta.setdefault("z_max", 0.0)
    return meta


def scan_pointcloud(path: str | Path) -> dict[str, Any]:
    """扫描点云（LAS/LAZ/PLY），提取点数与高程范围。

    LAS/LAZ 头部为二进制定长结构，可无依赖解析前 375 字节。
    """
    path = Path(path)
    meta: dict[str, Any] = {
        "driver": path.suffix.lstrip("."),
        "file_size": path.stat().st_size,
        "crs": "EPSG:4326",
    }

    if path.suffix.lower() in (".las", ".laz"):
        try:
            with open(path, "rb") as fh:
                header = fh.read(375)
            if header[:4] == b"LASF" and len(header) >= 131:
                # LAS 1.2+ 头部字段偏移
                meta["point_count"] = struct.unpack_from("<I", header, 107)[0]
                xmin, ymin, zmin, xmax, ymax, zmax = struct.unpack_from("<6d", header, 179)
                meta["bbox"] = [xmin, ymin, xmax, ymax]
                meta["z_range"] = [zmin, zmax]
        except (OSError, struct.error):
            logger.debug("LAS header parse failed: %s", path)

    meta.setdefault("point_count", 0)
    meta.setdefault("bbox", None)
    return meta


def scan_statistical(path: str | Path, max_rows: int = 1000) -> dict[str, Any]:
    """扫描统计表格（CSV），提取列定义与记录数。"""
    path = Path(path)
    meta: dict[str, Any] = {"driver": path.suffix.lstrip("."), "file_size": path.stat().st_size}

    if path.suffix.lower() == ".csv":
        try:
            with open(path, encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader, [])
                count = sum(1 for _ in reader)
            meta["columns"] = header
            meta["record_count"] = count
            # 识别空间/时间关联键
            lower = [c.lower() for c in header]
            for key in ("iso3", "country", "adm1", "adm2", "region", "gadm"):
                if key in lower:
                    meta["spatial_key"] = header[lower.index(key)]
                    break
            for key in ("year", "date", "time", "period"):
                if key in lower:
                    meta["time_key"] = header[lower.index(key)]
                    break
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("CSV parse failed for %s: %s", path, exc)
            meta["columns"] = []
            meta["record_count"] = 0
    else:
        meta.setdefault("columns", [])
        meta.setdefault("record_count", 0)

    return meta


def scan_text(path: str | Path) -> dict[str, Any]:
    """扫描文本文档（MD/TXT），提取字数与主题标签。"""
    path = Path(path)
    meta: dict[str, Any] = {
        "driver": path.suffix.lstrip("."),
        "file_size": path.stat().st_size,
        "content_type": path.suffix.lstrip(".").lower() or "text",
        "language": "en",
    }

    if path.suffix.lower() in (".md", ".txt"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            meta["word_count"] = len(text.split())
            # 中文检测
            cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
            if cjk > len(text) * 0.05:
                meta["language"] = "zh"
            # 主题标签：取标题行
            meta["topics"] = [ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")][:5]
        except OSError:
            meta["word_count"] = 0
            meta["topics"] = []
    else:
        meta.setdefault("word_count", 0)
        meta.setdefault("topics", [])

    return meta


#: 类型 → 扫描函数
SCANNERS: dict[str, Callable[[str | Path], dict[str, Any]]] = {
    "temporal": scan_temporal,
    "volume3d": scan_volume3d,
    "pointcloud": scan_pointcloud,
    "statistical": scan_statistical,
    "text": scan_text,
}


# --------------------------------------------------------------------------- #
# 类型探测
# --------------------------------------------------------------------------- #
def detect_asset_type(path: str | Path) -> str:
    """按扩展名探测资产类型（覆盖全部 7 类）。"""
    suffix = Path(path).suffix.lower()
    for asset_type, schema in CONTRACT_SCHEMAS.items():
        if suffix in schema.extensions:
            return asset_type
    return "other"


# --------------------------------------------------------------------------- #
# 合约校验
# --------------------------------------------------------------------------- #
@dataclass
class ContractCheckResult:
    """合约一致性检查结果。"""

    satisfied: bool
    asset_type: str
    missing_fields: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def validate_contract(
    asset_type: str,
    metadata: dict[str, Any],
) -> ContractCheckResult:
    """校验元数据是否满足该类型合约的必填字段要求。

    与 :class:`~geonexus.geocard.ContractValidator` 的区别：后者校验
    "请求 vs 资产" 的时空/波段一致性；本函数校验 "资产 vs 合约模板"
    的字段完整性，属于注册入口的把关。
    """
    schema = CONTRACT_SCHEMAS.get(asset_type)
    if schema is None:
        return ContractCheckResult(
            satisfied=False,
            asset_type=asset_type,
            reasons=[f"未知合约类型: {asset_type}"],
        )

    missing = [
        f for f in schema.required_fields
        if metadata.get(f) in (None, "", [], {})
    ]
    reasons = []
    if missing:
        reasons.append(f"缺少必填字段: {', '.join(missing)}")
    else:
        reasons.append(f"{asset_type} 合约字段完整（{len(schema.required_fields)} 项必填）")

    return ContractCheckResult(
        satisfied=not missing,
        asset_type=asset_type,
        missing_fields=missing,
        reasons=reasons,
    )


def contract_coverage() -> dict[str, Any]:
    """返回合约类型的覆盖统计（用于考核指标报告）。"""
    return {
        "total_types": len(CONTRACT_SCHEMAS),
        "types": sorted(CONTRACT_SCHEMAS),
        "target": 6,
        "met": len(CONTRACT_SCHEMAS) >= 6,
    }