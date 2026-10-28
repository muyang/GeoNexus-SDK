"""生成候鸟—湿地案例的**合成**样例数据。

为什么要合成：论文用的亚洲水鸟普查（AWC）、各国监测、eBird 记录与地点边界都在各自
机构手里，本案拿不到、也不该伪造。所以这份脚本生成**形状正确、量级合理、来源明确
标注为合成**的输入，让整条链路真的能跑；真实数据到位后按同名文件替换即可（见 README
的"数据槽位"表）。

生成的每份产物都带 `GEONEXUS_SYNTHETIC=TRUE` 标记（栅格写进 GeoTIFF 标签，表格写进
首行注释），沿用 amazon-ndvi 示例的做法：**合成就必须看得出来是合成**。

    python make_sample_data.py --out data --region yellow-sea --seed 20260925
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

# ── 研究区（示意范围） ────────────────────────────────────────────────────────
# 说明：这是**用于裁剪样例数据的示意范围**，不是论文的地点边界。
# 论文的地点边界由各国专家核定（原文第四步），属于必须替换的数据槽位。
#: 示意窗口：约 1° × 1°（百公里量级）的**站点尺度**窗口。
#: 论文的地点边界由各国专家核定（原文第四步），属于必须替换的数据槽位；
#: 这里的窗口只用来让样例数据落在一个合理尺度上，不声称是任何真实地点。
REGIONS: dict[str, dict[str, Any]] = {
    "yellow-sea": {
        "name": "黄海—渤海沿海湿地（示意窗口）",
        "bbox": [120.30, 32.60, 121.40, 33.40],
        "note": "原文称黄海—渤海为迁徙通道上的「咽喉」；2000–2015 年该区域约 1794.8 km²（29%）沿海湿地损失",
        "wetland_kind": "coastal",
    },
    "yangtze": {
        "name": "长江中下游湖群（示意窗口）",
        "bbox": [115.80, 28.60, 117.00, 29.80],
        "note": "原文称长江中下游湖群是雁鸭、鹤与东方白鹳的重要越冬区域",
        "wetland_kind": "inland",
    },
    "sanjiang": {
        "name": "三江平原湿地（示意窗口）",
        "bbox": [132.50, 46.50, 134.00, 47.60],
        "note": "原文以三江平原为例：湿地转农田造成的生态系统服务损失约 574.6 亿美元",
        "wetland_kind": "inland",
    },
}

# ── 物种（示意种群量） ────────────────────────────────────────────────────────
# 说明：这里的水鸟种群量**不是**论文使用的 1% 阈值（那来自 EAAF 首次保护状况评估）。
# 它们是示意值，用来验证 PC1 的计算形状；替换为官方阈值是必须完成的数据槽位。
SPECIES: list[dict[str, Any]] = [
    # 原文点名的类群（勺嘴鹬、大滨鹬、远东杓鹬、斑腿鹬、黑脸琵鹭、丹顶鹤、鸿雁…）
    {"species": "勺嘴鹬", "scientific": "Calidris pygmaea", "population": 700, "threatened": True},
    {
        "species": "大滨鹬",
        "scientific": "Calidris tenuirostris",
        "population": 380000,
        "threatened": True,
    },
    {
        "species": "远东杓鹬",
        "scientific": "Numenius madagascariensis",
        "population": 32000,
        "threatened": True,
    },
    {"species": "斑腿鹬", "scientific": "Tringa guttifer", "population": 1200, "threatened": True},
    {"species": "黑脸琵鹭", "scientific": "Platalea minor", "population": 6200, "threatened": True},
    {"species": "丹顶鹤", "scientific": "Grus japonensis", "population": 3800, "threatened": True},
    {
        "species": "白鹤",
        "scientific": "Leucogeranus leucogeranus",
        "population": 4000,
        "threatened": True,
    },
    {"species": "白头鹤", "scientific": "Grus monacha", "population": 16000, "threatened": True},
    {"species": "白枕鹤", "scientific": "Antigone vipio", "population": 10000, "threatened": True},
    {
        "species": "东方白鹳",
        "scientific": "Ciconia boyciana",
        "population": 9000,
        "threatened": True,
    },
    {"species": "鸿雁", "scientific": "Anser cygnoides", "population": 90000, "threatened": True},
    {
        "species": "小白额雁",
        "scientific": "Anser erythropus",
        "population": 30000,
        "threatened": True,
    },
    {"species": "青头潜鸭", "scientific": "Aythya baeri", "population": 1000, "threatened": True},
    {
        "species": "大杓鹬",
        "scientific": "Numenius arquata",
        "population": 20000,
        "threatened": True,
    },
    {
        "species": "半蹼鹬",
        "scientific": "Limnodromus semipalmatus",
        "population": 23000,
        "threatened": True,
    },
    # 常见种：数量大，用来体现"常见种与濒危种被同等计数"的偏差（原文第五节）
    {
        "species": "绿头鸭",
        "scientific": "Anas platyrhynchos",
        "population": 5000000,
        "threatened": False,
    },
    {
        "species": "赤颈鸭",
        "scientific": "Mareca penelope",
        "population": 2000000,
        "threatened": False,
    },
    {
        "species": "翘鼻麻鸭",
        "scientific": "Tadorna tadorna",
        "population": 700000,
        "threatened": False,
    },
    {
        "species": "环颈鸻",
        "scientific": "Charadrius hiaticula",
        "population": 400000,
        "threatened": False,
    },
    {
        "species": "红嘴鸥",
        "scientific": "Chroicocephalus ridibundus",
        "population": 3000000,
        "threatened": False,
    },
]

#: 每个地点的"人设"：水量、鸟况。用固定种子生成，保证可复现。
SITE_PROFILES: list[dict[str, Any]] = [
    {
        "site_id": "site-01",
        "name": "示意地点 01（大型潮滩）",
        "water": 0.62,
        "abundance": 1.00,
        "kind": "coastal",
    },
    {
        "site_id": "site-02",
        "name": "示意地点 02（河口沙洲）",
        "water": 0.41,
        "abundance": 0.72,
        "kind": "coastal",
    },
    {
        "site_id": "site-03",
        "name": "示意地点 03（围垦养殖塘）",
        "water": 0.33,
        "abundance": 0.45,
        "kind": "artificial",
    },
    {
        "site_id": "site-04",
        "name": "示意地点 04（高潮位休息地）",
        "water": 0.18,
        "abundance": 0.60,
        "kind": "coastal",
    },
    {
        "site_id": "site-05",
        "name": "示意地点 05（盐田）",
        "water": 0.28,
        "abundance": 0.38,
        "kind": "artificial",
    },
    {
        "site_id": "site-06",
        "name": "示意地点 06（内陆湖泊）",
        "water": 0.55,
        "abundance": 0.66,
        "kind": "inland",
    },
    {
        "site_id": "site-07",
        "name": "示意地点 07（洪泛平原）",
        "water": 0.47,
        "abundance": 0.30,
        "kind": "inland",
    },
    {
        "site_id": "site-08",
        "name": "示意地点 08（小型停歇地）",
        "water": 0.12,
        "abundance": 0.22,
        "kind": "inland",
    },
]


def _require_numpy() -> Any:
    import numpy as np

    return np


def _require_rasterio() -> Any:
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise SystemExit(
            "生成样例栅格需要 rasterio（GeoNexus venv 自带）：pip install rasterio"
        ) from exc
    return rasterio


def _water_field(
    profile: dict[str, Any], year_kind: str, np: Any, shape: tuple[int, int], rng: Any
) -> Any:
    """造一片"湿地水面"：椭圆主体 + 潮沟，按年份缩放。"""
    height, width = shape
    yy, xx = np.mgrid[0:height, 0:width].astype("float32")
    cy, cx = height * 0.5, width * 0.5
    ry, rx = height * 0.34, width * 0.38

    # 基准年更"完整"，目标年按地点的退化程度收缩（示例里统一收缩 12%）
    scale = 1.0 if year_kind == "baseline" else 0.88
    body = ((yy - cy) / (ry * scale)) ** 2 + ((xx - cx) / (rx * scale)) ** 2 <= 1.0

    # 潮沟/支汊：让水面不是一个干净的圆
    channel = np.abs((xx - cx) * 0.35 - (yy - cy) * 0.9) < 3.0
    water = body | (channel & body)

    # 斑块化：低水位地点更碎
    noise = rng.random(shape)
    water = water & (noise > (0.10 * (1.0 - profile["water"])))
    return water.astype("float32")


def write_band_stack(
    path: Path,
    *,
    bbox: list[float],
    water: Any,
    np: Any,
    rng: Any,
    synthetic: bool = True,
) -> dict[str, Any]:
    """写一个 2 波段（green, nir）反射率栈，让 NDWI 能算出来。

    NDWI = (Green − NIR) / (Green + NIR)：水面 Green 高、NIR 低。
    """
    rasterio = _require_rasterio()
    height, width = water.shape

    green = np.where(water > 0, 0.22, 0.11) + rng.normal(0, 0.01, size=water.shape)
    nir = np.where(water > 0, 0.03, 0.30) + rng.normal(0, 0.01, size=water.shape)
    green = np.clip(green, 0.001, 0.9).astype("float32")
    nir = np.clip(nir, 0.001, 0.9).astype("float32")

    west, south, east, north = bbox
    transform = rasterio.transform.from_bounds(west, south, east, north, width, height)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=2,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(green, 1)
        dst.write(nir, 2)
        dst.set_band_description(1, "green")
        dst.set_band_description(2, "nir")
        dst.update_tags(
            GEONEXUS_SYNTHETIC="TRUE" if synthetic else "FALSE",
            BANDS="green,nir",
            NOTE="SYNTHETIC reflectance stack for pipeline verification; not real imagery",
        )
    pixel_m = _pixel_size_m(bbox, width, height)
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "pixel_size_m": pixel_m,
        "water_pixels": int(water.sum()),
        "true_water_km2": round(float(water.sum()) * (pixel_m**2) / 1e6, 3),
    }


def _pixel_size_m(bbox: list[float], width: int, height: int) -> float:
    """按 AOI 中心纬度把度换算成米（够用的近似：样例分辨率不追求投影精度）。"""
    import math

    west, south, east, north = bbox
    mid_lat = math.radians((south + north) / 2.0)
    dx_m = (east - west) * 111320.0 * math.cos(mid_lat)
    dy_m = (north - south) * 110540.0
    return float(round((dx_m / max(width, 1) + dy_m / max(height, 1)) / 2.0, 2))


def write_counts(path: Path, *, profiles: list[dict[str, Any]], rng: Any) -> dict[str, Any]:
    """写水鸟计数表（site_id, site_name, species, count, survey_year, kind）。

    形状照着论文的数据需求：**地点 × 物种 × 数量**，外加该物种的种群量，
    这样 PC1（各达标种群比例之和）才算得出来。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        for species in SPECIES:
            # 常见种数量大、濒危种数量小；再乘地点人设的"鸟况"
            base = species["population"] * 0.02
            if not species["threatened"]:
                base = species["population"] * 0.05
            count = int(max(0, rng.normal(base * profile["abundance"], base * 0.25)))
            rows.append(
                {
                    "site_id": profile["site_id"],
                    "site_name": profile["name"],
                    "kind": profile["kind"],
                    "species": species["species"],
                    "count": count,
                    "survey_year": 2024,
                    "count_type": "single_max_count",  # 原文：单次最大数量与多年平均混用是已知局限
                    "synthetic": "TRUE",
                }
            )
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return {"path": str(path), "rows": len(rows), "sites": len(profiles), "species": len(SPECIES)}


def write_populations(path: Path) -> dict[str, Any]:
    """写物种种群量表（1% 规则的除数）。**示意值**，不是官方阈值。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "# GEONEXUS_SYNTHETIC=TRUE",
                "illustrative flyway population sizes;",
                "replace with the EAAF 1% thresholds before any real use",
            ]
        )
        writer.writerow(["species", "scientific", "flyway_population", "globally_threatened"])
        for species in SPECIES:
            writer.writerow(
                [
                    species["species"],
                    species["scientific"],
                    species["population"],
                    str(species["threatened"]).upper(),
                ]
            )
    return {"path": str(path), "species": len(SPECIES)}


def write_sites(
    path: Path, *, bbox: list[float], profiles: list[dict[str, Any]], rng: Any
) -> dict[str, Any]:
    """写地点清单（点）：示意坐标，落在 AOI 内。真实的 147 处地点边界必须另行导入。"""
    west, south, east, north = bbox
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "# GEONEXUS_SYNTHETIC=TRUE",
                "illustrative coordinates inside the sample AOI;",
                "not the paper's 147 priority wetland boundaries",
            ]
        )
        writer.writerow(["site_id", "site_name", "kind", "lon", "lat", "protected_status"])
        for index, profile in enumerate(profiles):
            lon = round(west + (east - west) * (0.16 + 0.68 * rng.random()), 4)
            lat = round(south + (north - south) * (0.16 + 0.68 * rng.random()), 4)
            writer.writerow(
                [
                    profile["site_id"],
                    profile["name"],
                    profile["kind"],
                    lon,
                    lat,
                    "protected_or_partial" if index % 3 else "unprotected",
                ]
            )
    return {"path": str(path), "sites": len(profiles)}


def generate(
    out_dir: str | Path,
    *,
    region: str = "yellow-sea",
    seed: int = 20260925,
    shape: tuple[int, int] = (160, 240),
    baseline_year: int = 2010,
    target_year: int = 2024,
) -> dict[str, Any]:
    """生成整套样例数据，返回清单（写进 `sample-data.json`）。"""
    np = _require_numpy()
    rng = np.random.default_rng(seed)
    meta = REGIONS.get(region)
    if meta is None:
        raise SystemExit(f"未知研究区 {region!r}；可选：{', '.join(REGIONS)}")

    # 按 region 建子目录：配方里写的是 data/<region>/stack_<year>.tif，
    # 换研究区只改一个参数，不用改任何路径。
    out = Path(out_dir) / region
    out.mkdir(parents=True, exist_ok=True)
    bbox = list(meta["bbox"])
    baseline_profile = dict(SITE_PROFILES[0])

    baseline_water = _water_field(baseline_profile, "baseline", np, shape, rng)
    target_water = _water_field(baseline_profile, "target", np, shape, rng)

    manifest: dict[str, Any] = {
        "region": region,
        "region_name": meta["name"],
        "bbox": bbox,
        "region_note": meta["note"],
        "wetland_kind": meta["wetland_kind"],
        "synthetic": True,
        "seed": seed,
        "baseline_year": baseline_year,
        "target_year": target_year,
        "caveat": "全部数据为合成/示意，仅用于跑通链路与模版验证；不得用于任何科研结论。",
        "files": {},
    }
    manifest["files"]["baseline_stack"] = write_band_stack(
        out / f"stack_{baseline_year}.tif", bbox=bbox, water=baseline_water, np=np, rng=rng
    )
    manifest["files"]["target_stack"] = write_band_stack(
        out / f"stack_{target_year}.tif", bbox=bbox, water=target_water, np=np, rng=rng
    )
    manifest["files"]["counts"] = write_counts(
        out / "waterbird_counts.csv", profiles=SITE_PROFILES, rng=rng
    )
    manifest["files"]["populations"] = write_populations(out / "species_populations.csv")
    manifest["files"]["sites"] = write_sites(
        out / "sites.csv", bbox=bbox, profiles=SITE_PROFILES, rng=rng
    )
    manifest["species"] = [s["species"] for s in SPECIES]

    (out / "sample-data.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成候鸟—湿地案例的合成样例数据")
    parser.add_argument("--out", default="data", help="输出目录（默认 data/）")
    parser.add_argument("--region", default="yellow-sea", choices=sorted(REGIONS))
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--baseline-year", type=int, default=2010)
    parser.add_argument("--target-year", type=int, default=2024)
    args = parser.parse_args(argv)

    manifest = generate(
        args.out,
        region=args.region,
        seed=args.seed,
        baseline_year=args.baseline_year,
        target_year=args.target_year,
    )
    print(f"样例数据已生成：{args.out}/{args.region}/")
    print(
        f"  研究区：{manifest['region_name']}（{manifest['wetland_kind']}） bbox={manifest['bbox']}"
    )
    for key, value in manifest["files"].items():
        detail = value.get("path")
        print(f"  {key:16s} {detail}")
    print("  注意：全部为合成/示意数据，已在文件内标注 GEONEXUS_SYNTHETIC=TRUE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
