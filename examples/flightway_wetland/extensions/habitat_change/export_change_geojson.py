"""扩展演示的矢量化：把 NDWI 变化栅格转成 GeoJSON（**合成样例**）。

案例主体的 `../../export_geojson.py` 导出的是论文公开的真实站点；这里导出的是
本目录演示链路产生的合成变化面，属性里带 `synthetic: true`，
界面上的"合成/示意数据"标注靠它，不能靠人记得。

    python run_extension.py && python export_change_geojson.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

#: 变化栅格的类别 → 属性（与 water_skills.water_change_handler 的约定一致）
CHANGE_CLASSES: dict[int, str] = {-1: "loss", 0: "stable", 1: "gain"}


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def vectorize_raster(path: Path, *, keep: list[int] | None = None) -> dict[str, Any]:
    """栅格 → MultiPolygon 分级要素（用 rasterio 的 shapes，不自造追踪算法）。"""
    import rasterio
    from rasterio import features

    with rasterio.open(path) as src:
        # rasterio 的 shapes 不接受 int8（变化栅格就是 int8）：升到 int16 再矢量化
        data = src.read(1).astype("int16")
        transform = src.transform
        count = src.count
    if count != 1:
        raise SystemExit(f"{path} 应为单波段，实际 {count} 波段")

    wanted = set(keep) if keep is not None else set(CHANGE_CLASSES)
    buckets: dict[int, list[Any]] = {}
    for geometry, value in features.shapes(data, transform=transform):
        value = int(value)
        if value not in wanted or value not in CHANGE_CLASSES:
            continue
        buckets.setdefault(value, []).append(geometry["coordinates"])

    out_features = []
    for value, polygons in sorted(buckets.items()):
        out_features.append(
            _feature(
                {"type": "MultiPolygon", "coordinates": polygons},
                {
                    "class": CHANGE_CLASSES[value],
                    "class_code": value,
                    "pixels": int((data == value).sum()),
                    "synthetic": True,
                },
            )
        )
    # RFC 7946 不认 GeoJSON 里的 crs 成员（规定必须是 WGS84），Cesium 会把字符串型 crs
    # 当对象解析并抛 "crs.properties is undefined" —— 所以这里不写 crs。
    return {"type": "FeatureCollection", "features": out_features}


def export(workdir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    change_raster = workdir / "water_change.tif"
    if not change_raster.exists():
        raise SystemExit(f"找不到 {change_raster}；先跑 `python run_extension.py`")
    files: dict[str, Any] = {}
    change = vectorize_raster(change_raster, keep=[-1, 1])  # stable 面积太大，默认不画
    (out_dir / "water_change.geojson").write_text(
        json.dumps(change, ensure_ascii=False), encoding="utf-8"
    )
    files["water_change"] = {
        "path": str(out_dir / "water_change.geojson"),
        "features": len(change["features"]),
        "classes": [f["properties"]["class"] for f in change["features"]],
    }
    baseline = workdir / "water_mask_2010.tif"
    if baseline.exists():
        mask = vectorize_raster(baseline, keep=[1])
        for feature in mask["features"]:
            feature["properties"]["class"] = "water"
        (out_dir / "water_baseline.geojson").write_text(
            json.dumps(mask, ensure_ascii=False), encoding="utf-8"
        )
        files["water_baseline"] = {
            "path": str(out_dir / "water_baseline.geojson"),
            "features": len(mask["features"]),
        }
    manifest = {
        "case": "geocard.case.eaaf-habitat-change-demo",
        "synthetic": True,
        "crs": "EPSG:4326",
        "note": "扩展演示：几何来自合成样例，仅供平台可视化与教学；不是论文方法，也不是论文数据。",
        "files": files,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把扩展演示的产物转成地图可用的 GeoJSON")
    parser.add_argument("--workdir", default=str(HERE / "output"))
    parser.add_argument("--out", default=str(HERE / "output" / "geometry"))
    args = parser.parse_args(argv)
    manifest = export(Path(args.workdir), Path(args.out))
    for key, info in manifest["files"].items():
        print(f"  {key:16s} {info['path']} （{info['features']} 个要素）")
    print("  注意：属性里带 synthetic=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
