"""把案例产物转成地图可用的 GeoJSON。

平台侧（Cesium / MapLibre）只认 GeoJSON/矢量。案例主体这一版导出的是**真实站点**：

    output/geometry/priority_sites.geojson   147 处优先湿地（点，属性齐全）
    output/geometry/priority_sites_by_country.geojson  同上的分国别子集（按国家拆文件，便于按需加载）

属性里带 `synthetic: false` 与 `source`：坐标与分值来自论文补充材料 Table 3（CC-BY），
**不是**合成数据。原 NDWI 变化面（合成样例）的导出已移到
`extensions/habitat_change/export_change_geojson.py`。

    python run_case.py && python export_geojson.py
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

#: PC1 分级：与前端图例共用一套（前端只负责画，不自己定义分数区间）。
#: 依据是原文的区域校准阈值（中国与蒙古 10、其余八国 1）与分值量级。
PC1_BANDS: list[tuple[float, str]] = [
    (100.0, "very-high"),  # 承载该地点所依赖种群 100% 以上的等价比例
    (10.0, "high"),
    (1.0, "medium"),
    (0.0, "low"),
]
PC1_BAND_LABELS = {
    "very-high": "极高（≥100）",
    "high": "高（10–100）",
    "medium": "中（1–10）",
    "low": "低（<1 或未公布分值）",
}


def pc1_band(value: float | None) -> str:
    if value is None:
        return "low"
    for threshold, label in PC1_BANDS:
        if value >= threshold:
            return label
    return "low"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.reader(handle) if r and not r[0].startswith("#")]
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]  # noqa: B905 (py3.9)


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def sites_features(sites_csv: Path, species_csv: Path | None = None) -> dict[str, Any]:
    """147 处优先湿地 → 点要素。

    属性刻意与论文的字段一一对应，方便地图上按"沿海/内陆""是否与保护地重叠""PC1 分值"
    着色与缩放；`species_count` 等来自补充材料同一张表。
    """
    species_by_site: dict[str, list[str]] = {}
    if species_csv and species_csv.exists():
        for row in _read_csv(species_csv):
            species_by_site.setdefault(row["site_id"], []).append(
                row["english"] or row["scientific"]
            )
    features = []
    for row in _read_csv(sites_csv):
        pc1 = float(row["pc1"]) if row.get("pc1") else None
        rank = int(float(row["rank"])) if row.get("rank") else None
        names = species_by_site.get(row["site_id"], [])
        features.append(
            _feature(
                {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
                {
                    "site_id": row["site_id"],
                    "name": row["name"],
                    "country": row["country"],
                    "province": row.get("section") or None,
                    "wetland_type": row.get("wetland_type") or None,
                    "type_source": row.get("type_source") or None,
                    "protected": row.get("overlaps_protected_bold") == "True",
                    "designations": row.get("designations") or None,
                    "ramsar": row.get("has_ramsar") == "True",
                    "flyway_network": row.get("has_flyway_network") == "True",
                    "pc1": pc1,
                    "pc1_band": pc1_band(pc1),
                    "rank": rank,
                    "species_meeting_1pct": int(float(row.get("species_count") or 0)),
                    "over_10pct": row.get("over_10pct") == "True",
                    "over_50pct": row.get("over_50pct") == "True",
                    "threatened": row.get("threatened") or None,
                    "top_species": "、".join(names[:5]) or None,
                    "synthetic": False,
                    "source": "Scientific Reports 16:1916 (2026) Supplementary Table 3",
                },
            )
        )
    return {"type": "FeatureCollection", "features": features}


def export(
    workdir: Path, *, out_dir: Path | None = None, data_dir: Path | None = None
) -> dict[str, Any]:
    out = out_dir or (workdir / "geometry")
    out.mkdir(parents=True, exist_ok=True)
    data = data_dir or (HERE / "data" / "rfi")
    sites_csv = data / "sites.csv"
    if not sites_csv.exists():
        raise SystemExit(f"找不到 {sites_csv}；先用 extract_supp_table3.py 生成站点数据集")

    sites = sites_features(sites_csv, data / "site_species.csv")
    (out / "priority_sites.geojson").write_text(
        json.dumps(sites, ensure_ascii=False), encoding="utf-8"
    )

    by_country: dict[str, list[dict[str, Any]]] = {}
    for feature in sites["features"]:
        by_country.setdefault(feature["properties"]["country"], []).append(feature)
    for country, feats in by_country.items():
        slug = country.lower().replace(" ", "-")
        (out / f"priority_sites_{slug}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False),
            encoding="utf-8",
        )

    manifest = {
        "case": "geocard.case.eaaf-wetland-priority",
        "synthetic": False,
        "crs": "EPSG:4326",
        "source": {
            "citation": "Crosby et al. Identifying priority wetland sites in the East "
            "Asian–Australasian Flyway for migratory bird conservation. "
            "Scientific Reports 16, 1916 (2026)",
            "doi": "10.1038/s41598-025-31727-2",
            "table": "Supplementary Table 3（CC-BY 4.0）",
        },
        "note": "站点坐标与 PC1 分值为论文公开发表的结果；支撑 PC1 的逐笔计数数据受许可限制未公开。",
        "files": {
            "priority_sites": {
                "path": str(out / "priority_sites.geojson"),
                "features": len(sites["features"]),
                "legend": {
                    "wetland_type": {"coastal": "沿海", "inland": "内陆"},
                    "protected": {
                        "true": "与保护地重叠（原文粗体标记）",
                        "false": "未与保护地重叠",
                    },
                    "pc1_bands": [
                        {"min": t, "band": b, "label": PC1_BAND_LABELS[b]} for t, b in PC1_BANDS
                    ],
                },
            },
            "by_country": {
                country: {
                    "file": f"priority_sites_{country.lower().replace(' ', '-')}.geojson",
                    "features": len(feats),
                }
                for country, feats in by_country.items()
            },
        },
        "caveats": [
            "正文 Table 4 记 108 处受保护或部分受保护，补充材料粗体标记为 117 处，原文未解释差异。",
            "蒙古 11 处原文给的是名次（rank）而非 PC1 分值；我国内陆 15 处只有叙述性说明。",
        ],
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把案例产物转成地图可用的 GeoJSON")
    parser.add_argument("--workdir", default=str(HERE / "output"))
    parser.add_argument("--data-dir", default=str(HERE / "data" / "rfi"))
    parser.add_argument("--out", default=str(HERE / "output" / "geometry"))
    args = parser.parse_args(argv)
    manifest = export(Path(args.workdir), out_dir=Path(args.out), data_dir=Path(args.data_dir))
    print("GeoJSON 已生成：")
    for key, info in manifest["files"].items():
        if key == "by_country":
            for country, meta in info.items():
                print(f"  {country:14s} {meta['file']} （{meta['features']} 个要素）")
            continue
        print(f"  {key:16s} {info['path']} （{info['features']} 个要素）")
    print("  坐标 EPSG:4326；属性 synthetic=false，来源为论文补充材料 Table 3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
