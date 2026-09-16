"""Tests for GeoAsset Contract type coverage (7 types).

2026 年度考核要求合约模型覆盖 ≥6 种数据类型。
"""

from __future__ import annotations

import csv
import json
import struct

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from geonexus.gaag import (
    CONTRACT_SCHEMAS,
    GAAGRegistry,
    contract_coverage,
    detect_asset_type,
    scan_asset_to_contract,
    scan_pointcloud,
    scan_statistical,
    scan_temporal,
    scan_text,
    scan_volume3d,
    validate_contract,
)

# --------------------------------------------------------------------------- #
# 覆盖率
# --------------------------------------------------------------------------- #

class TestContractCoverage:
    def test_at_least_six_types(self):
        """考核要求 ≥6 类。"""
        cov = contract_coverage()
        assert cov["total_types"] >= 6
        assert cov["met"] is True

    def test_seven_types_present(self):
        expected = {"raster", "vector", "temporal", "volume3d", "pointcloud", "statistical", "text"}
        assert expected.issubset(set(CONTRACT_SCHEMAS))

    def test_every_schema_has_required_fields(self):
        for name, schema in CONTRACT_SCHEMAS.items():
            assert schema.required_fields, f"{name} 缺必填字段定义"
            assert schema.extensions, f"{name} 缺扩展名定义"
            assert schema.description


# --------------------------------------------------------------------------- #
# 类型探测
# --------------------------------------------------------------------------- #

class TestTypeDetection:
    @pytest.mark.parametrize("filename,expected", [
        ("a.tif", "raster"),
        ("a.geojson", "vector"),
        ("a.nc", "temporal"),
        ("a.zarr", "temporal"),
        ("a.las", "pointcloud"),
        ("a.laz", "pointcloud"),
        ("a.csv", "statistical"),
        ("a.pdf", "text"),
        ("a.md", "text"),
        ("a.3dtiles", "volume3d"),
        ("a.unknown", "other"),
    ])
    def test_detect(self, filename, expected):
        assert detect_asset_type(filename) == expected


# --------------------------------------------------------------------------- #
# 各类扫描器
# --------------------------------------------------------------------------- #

class TestTemporalScanner:
    def test_filename_year_inference(self, tmp_path):
        """无 xarray 时从文件名推断时间范围。"""
        p = tmp_path / "flood_2020_2025.nc"
        p.write_bytes(b"fake netcdf")
        meta = scan_temporal(p)
        assert meta["time_start"] == "2020-01-01"
        assert meta["time_end"] == "2025-12-31"
        assert meta["crs"] == "EPSG:4326"

    def test_no_years_fallback(self, tmp_path):
        p = tmp_path / "dataset.nc"
        p.write_bytes(b"fake")
        meta = scan_temporal(p)
        assert "time_interval" in meta


class TestVolume3DScanner:
    def test_3dtiles_bounding_volume(self, tmp_path):
        tiles = {
            "asset": {"version": "1.0"},
            "root": {"boundingVolume": {"box": [10.0, 0, 50.0, 10, 0, 0, 0, 10, 0, 0, 0, 20]}},
        }
        p = tmp_path / "city.3dtiles"
        p.write_text(json.dumps(tiles))
        meta = scan_volume3d(p)
        assert meta["crs"] == "EPSG:4979"
        assert meta["z_max"] > meta["z_min"]

    def test_fallback_defaults(self, tmp_path):
        p = tmp_path / "model.voxel"
        p.write_bytes(b"x" * 100)
        meta = scan_volume3d(p)
        assert meta["z_min"] == 0.0 and meta["z_max"] == 0.0


class TestPointCloudScanner:
    def _write_las_header(self, path, point_count=12345):
        """构造最小合法 LAS 头部（375 字节）。"""
        header = bytearray(375)
        header[0:4] = b"LASF"
        header[24] = 1  # version major
        header[25] = 2  # version minor
        header[26:58] = b"TestSystem".ljust(32, b"\x00")
        struct.pack_into("<I", header, 107, point_count)  # legacy point count
        # bbox + z range at offset 179 (6 doubles)
        struct.pack_into("<6d", header, 179, 100.0, 20.0, 5.0, 110.0, 30.0, 45.0)
        path.write_bytes(bytes(header))

    def test_las_header_parse(self, tmp_path):
        p = tmp_path / "scan.las"
        self._write_las_header(p, point_count=54321)
        meta = scan_pointcloud(p)
        assert meta["point_count"] == 54321
        assert meta["bbox"] == [100.0, 20.0, 110.0, 30.0]
        assert meta["z_range"] == [5.0, 45.0]

    def test_laz_supported(self, tmp_path):
        p = tmp_path / "scan.laz"
        self._write_las_header(p, point_count=999)
        meta = scan_pointcloud(p)
        assert meta["point_count"] == 999

    def test_non_las_fallback(self, tmp_path):
        p = tmp_path / "cloud.ply"
        p.write_bytes(b"ply\nformat ascii 1.0\n")
        meta = scan_pointcloud(p)
        assert meta["point_count"] == 0


class TestStatisticalScanner:
    def test_csv_columns_and_count(self, tmp_path):
        p = tmp_path / "sdg.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["iso3", "year", "value", "indicator"])
            for i in range(25):
                w.writerow(["KEN", 2020 + i % 5, 0.1 * i, "sdg.6.6.1"])
        meta = scan_statistical(p)
        assert meta["columns"] == ["iso3", "year", "value", "indicator"]
        assert meta["record_count"] == 25
        assert meta["spatial_key"] == "iso3"
        assert meta["time_key"] == "year"

    def test_empty_csv(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("a,b\n", encoding="utf-8")
        meta = scan_statistical(p)
        assert meta["columns"] == ["a", "b"]
        assert meta["record_count"] == 0


class TestTextScanner:
    def test_markdown_parse(self, tmp_path):
        p = tmp_path / "guide.md"
        p.write_text("# Flood Monitoring\n\nUse NDVI and NDWI to assess.\n## Data\n", encoding="utf-8")
        meta = scan_text(p)
        assert meta["content_type"] == "md"
        assert meta["word_count"] > 5
        assert "Flood Monitoring" in meta["topics"]

    def test_chinese_detection(self, tmp_path):
        p = tmp_path / "中文指南.txt"
        p.write_text("洪水监测指南：使用NDVI和NDWI指数评估洪涝范围。", encoding="utf-8")
        meta = scan_text(p)
        assert meta["language"] == "zh"

    def test_english_detection(self, tmp_path):
        p = tmp_path / "guide.txt"
        p.write_text("Flood monitoring guide using NDVI indices for assessment.", encoding="utf-8")
        meta = scan_text(p)
        assert meta["language"] == "en"


# --------------------------------------------------------------------------- #
# 合约校验
# --------------------------------------------------------------------------- #

class TestValidateContract:
    def test_complete_contract_passes(self, tmp_path):
        p = tmp_path / "sdg.csv"
        p.write_text("iso3,value\nKEN,1\n", encoding="utf-8")
        meta = scan_statistical(p)
        result = validate_contract("statistical", meta)
        assert result.satisfied
        assert not result.missing_fields

    def test_incomplete_contract_fails(self):
        result = validate_contract("pointcloud", {"crs": "EPSG:4326"})
        assert not result.satisfied
        assert "bbox" in result.missing_fields
        assert "point_count" in result.missing_fields

    def test_unknown_type(self):
        result = validate_contract("nonexistent", {})
        assert not result.satisfied
        assert "未知合约类型" in result.reasons[0]


# --------------------------------------------------------------------------- #
# 端到端：扫描 → 注册到 GAAG
# --------------------------------------------------------------------------- #

class TestEndToEndRegistration:
    def test_register_all_new_types(self, tmp_path):
        """5 类新合约都能扫描并注册到 GAAG。"""
        # temporal
        (tmp_path / "cube_2020_2024.nc").write_bytes(b"nc")
        # volume3d
        (tmp_path / "city.3dtiles").write_text(
            json.dumps({"root": {"boundingVolume": {"box": [10, 0, 50, 10, 0, 0, 0, 10, 0, 0, 0, 20]}}})
        )
        # pointcloud
        las = bytearray(375)
        las[0:4] = b"LASF"
        struct.pack_into("<I", las, 107, 777)
        struct.pack_into("<6d", las, 179, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        (tmp_path / "scan.las").write_bytes(bytes(las))
        # statistical
        (tmp_path / "stats.csv").write_text("iso3,year,value\nKEN,2020,1.5\n", encoding="utf-8")
        # text
        (tmp_path / "guide.md").write_text("# Guide\nContent here.\n", encoding="utf-8")

        registry = GAAGRegistry()
        expected = {"temporal", "volume3d", "pointcloud", "statistical", "text"}
        found = set()
        for f in sorted(tmp_path.iterdir()):
            contract = registry.register_asset(str(f), description=f"{f.name} test asset")
            found.add(contract.asset_type)

        assert expected.issubset(found), f"缺少类型: {expected - found}"
        assert registry.count() == 5

    def test_raster_still_works(self, tmp_path):
        """回归：原有 raster 扫描不受影响。"""
        p = str(tmp_path / "r.tif")
        data = np.full((16, 16), 0.5, np.float32)
        with rasterio.open(p, "w", driver="GTiff", dtype=rasterio.float32, count=2,
                           width=16, height=16, crs="EPSG:4326",
                           transform=from_bounds(-10, -5, 10, 5, 16, 16)) as dst:
            dst.write(data, 1)
            dst.write(data, 2)
        contract = scan_asset_to_contract(p)
        assert contract.asset_type == "raster"
        assert len(contract.card.bands) == 2
        assert contract.scanned_meta["crs"] == "EPSG:4326"