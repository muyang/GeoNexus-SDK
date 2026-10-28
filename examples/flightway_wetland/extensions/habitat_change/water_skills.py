"""候鸟—湿地案例的四个 GeoSkill —— 按论文的方法链路实现。

论文的五步流程（比较既有框架 → 收集候选湿地与水鸟数量 → 按统一阈值算优先分值 →
与各国专家核定边界 → 分析类型与保护状况）里，**可计算**的是第二、三步；本案把这两步
做成能真跑的算子，其余三步作为案例的元数据与数据槽位（见 `README.md`）。

    s1  wetland-water-extent      NDWI 提取基准年水面
    s2  wetland-water-extent      NDWI 提取目标年水面
    s3  wetland-water-change      两年水面变化（损失/增加/净变化）
    s4  waterbird-priority-score  PC1：各达标种群比例之和 + 1% 规则 + 入选阈值
    s5  wetland-report            渲染结论报告（含方法、来源、局限）

**两个方法的忠实度说明**

* NDWI（McFeeters 1996）是论文所引遥感变化监测里最基础的水体指数；论文本身没有给出
  它用的遥感产品与阈值，所以这里的阈值是**案例参数**（可改），不是论文的定值。
* PC1 是论文的核心指标：对每个在该地点达到国际重要数量（≥ 该种群 1%）的种群，累加
  「该地点个体数 ÷ 种群总量」。本案按原文描述实现，并保留原文明说的区域校准
  （中国、蒙古阈值 10，其余国家 1）。
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: 1% 规则：Ramsar 第 6 条标准 / 原文第六节。
ONE_PERCENT = 0.01

#: 默认 PC1 入选阈值（原文第七节：中国与蒙古为 10，其余八国为 1）。
DEFAULT_PC1_THRESHOLD = 10.0


# --------------------------------------------------------------------------- #
# 共用小工具
# --------------------------------------------------------------------------- #
def _workdir(context: Any) -> Path:
    base = getattr(context, "workdir", None)
    out = Path(base) if base else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    return out


def _resolve(path: str | Path, context: Any) -> str:
    """把配方里的相对路径按**执行工作目录**解析。

    配方写的是 `data/<region>/stack_2010.tif` 这种相对路径：它描述的是"我的数据放在
    工作目录的哪里"，而不是某台机器上的绝对路径。相对路径按进程 CWD 解析会让同一份
    配方在不同执行器下指向不同的东西 —— 这正是"可复现"最容易漏掉的一环。
    """
    text = str(path)
    target = Path(text)
    if target.is_absolute():
        return text
    return str((_workdir(context) / target).resolve())


def _require_numpy() -> Any:
    import numpy as np

    return np


def _require_rasterio() -> Any:
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise ValueError("本算子需要 rasterio（GeoNexus venv 自带）") from exc
    return rasterio


def _pixel_area_km2(dataset: Any) -> float:
    """按数据集自身的 CRS 估算像元面积（度→米用中心纬度近似，与样例数据一致）。"""
    import math

    transform = dataset.transform
    pixel_x = abs(transform.a)
    pixel_y = abs(transform.e)
    if dataset.crs and str(dataset.crs).upper() not in ("EPSG:4326", "OGC:CRS84"):
        return float(pixel_x * pixel_y) / 1e6
    centre = (dataset.bounds.top + dataset.bounds.bottom) / 2.0
    dx = pixel_x * 111320.0 * math.cos(math.radians(centre))
    dy = pixel_y * 110540.0
    return float(dx * dy) / 1e6


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "是")


# --------------------------------------------------------------------------- #
# s1 / s2：水面提取
# --------------------------------------------------------------------------- #
def water_extent_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """NDWI 水体提取。

    params:
        stack     : 2 波段（green, nir）栅格路径，如 Sentinel-2 的 B03/B08
        year      : 年份（只用于命名与报告）
        threshold : NDWI 阈值，默认 0.0（越大越严格，水面越少）
        output    : 可选，水面掩膜输出路径
        min_patch_px : 可选，去掉小于该像元数的碎斑（默认 0 = 不去）
    """
    rasterio = _require_rasterio()
    np = _require_numpy()
    stack = params.get("stack")
    if not stack:
        raise ValueError("wetland-water-extent 需要 'stack'（green/nir 两波段栅格）")
    threshold = float(params.get("threshold", 0.0))
    year = params.get("year")

    with rasterio.open(_resolve(stack, context)) as src:
        if src.count < 2:
            raise ValueError(f"{stack} 至少需要 2 个波段（green, nir），实际 {src.count}")
        green = src.read(1).astype("float32")
        nir = src.read(2).astype("float32")
        profile = src.profile.copy()
        pixel_km2 = _pixel_area_km2(src)
        bounds = list(src.bounds)
        crs = str(src.crs) if src.crs else None
        tags = dict(src.tags())

    denominator = green + nir
    valid = denominator != 0
    ndwi = np.zeros_like(green)
    ndwi[valid] = (green[valid] - nir[valid]) / denominator[valid]
    water = (ndwi > threshold) & valid

    min_patch = int(params.get("min_patch_px") or 0)
    if min_patch > 1:
        water = _drop_small_patches(water, min_patch, np)

    out_path = params.get("output")
    if not out_path:
        out_path = str(_workdir(context) / f"water_mask_{year or 'year'}.tif")
    else:
        out_path = _resolve(out_path, context)
    profile.update(dtype="uint8", count=1, nodata=0)
    profile.pop("blockxsize", None)
    profile.pop("blockysize", None)
    with rasterio.open(str(out_path), "w", **profile) as dst:
        dst.write(water.astype("uint8"), 1)
        dst.update_tags(
            GEONEXUS_SYNTHETIC=tags.get("GEONEXUS_SYNTHETIC", "UNKNOWN"),
            METHOD="NDWI=(green-nir)/(green+nir)",
            THRESHOLD=str(threshold),
            YEAR=str(year),
        )

    water_pixels = int(water.sum())
    stats_path = params.get("stats_output")
    stats_path = (
        _resolve(stats_path, context)
        if stats_path
        else str(_workdir(context) / f"water_stats_{year or 'year'}.json")
    )
    result = {
        "water_mask": str(out_path),
        "stats_json": stats_path,
        "year": year,
        "threshold": threshold,
        "water_pixels": water_pixels,
        "water_area_km2": round(water_pixels * pixel_km2, 3),
        "water_fraction": round(float(water_pixels) / float(water.size), 4),
        "ndwi_mean_water": round(float(ndwi[water].mean()), 4) if water_pixels else None,
        "pixel_area_km2": round(pixel_km2, 6),
        "bbox": [bounds[0], bounds[1], bounds[2], bounds[3]] if bounds else None,
        "crs": crs,
        "synthetic": tags.get("GEONEXUS_SYNTHETIC") == "TRUE",
    }
    Path(stats_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def _drop_small_patches(water: Any, min_patch: int, np: Any) -> Any:
    """连通域去碎斑（四邻域，纯 numpy 实现，避免为一个功能引入 scipy）。"""
    height, width = water.shape
    labels = np.zeros((height, width), dtype="int32")
    current = 0
    for y in range(height):
        for x in range(width):
            if not water[y, x] or labels[y, x]:
                continue
            current += 1
            stack = [(y, x)]
            labels[y, x] = current
            size = 0
            while stack:
                cy, cx = stack.pop()
                size += 1
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if (
                        0 <= ny < height
                        and 0 <= nx < width
                        and water[ny, nx]
                        and not labels[ny, nx]
                    ):
                        labels[ny, nx] = current
                        stack.append((ny, nx))
            if size < min_patch:
                water[labels == current] = False
    return water


# --------------------------------------------------------------------------- #
# s3：水面变化
# --------------------------------------------------------------------------- #
def water_change_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """对比两期水面掩膜：损失 / 增加 / 净变化 / 损失率。

    params:
        baseline_mask, target_mask : 两期 uint8 掩膜（1=水）
        baseline_year, target_year : 只用于报告措辞
    """
    rasterio = _require_rasterio()
    np = _require_numpy()
    baseline_mask = params.get("baseline_mask")
    target_mask = params.get("target_mask")
    if not baseline_mask or not target_mask:
        raise ValueError("wetland-water-change 需要 'baseline_mask' 与 'target_mask'")

    with (
        rasterio.open(_resolve(baseline_mask, context)) as src_a,
        rasterio.open(_resolve(target_mask, context)) as src_b,
    ):
        if (src_a.height, src_a.width) != (src_b.height, src_b.width):
            raise ValueError(
                f"两期掩膜尺寸不一致：{src_a.height}x{src_a.width} vs {src_b.height}x{src_b.width}"
            )
        baseline = src_a.read(1) > 0
        target = src_b.read(1) > 0
        profile = src_a.profile.copy()
        pixel_km2 = _pixel_area_km2(src_a)

    lost = baseline & ~target
    gained = ~baseline & target
    stable_water = baseline & target

    # -1 损失 / 0 稳定 / 1 增加（2 = 两期都非水，写 0 更省事：这里用 255 表示非水）
    change = np.zeros(baseline.shape, dtype="int8")
    change[stable_water] = 0
    change[lost] = -1
    change[gained] = 1

    out_path = (
        _resolve(params["output"], context)
        if params.get("output")
        else str(_workdir(context) / "water_change.tif")
    )
    profile.update(dtype="int8", count=1, nodata=-9)
    profile.pop("blockxsize", None)
    profile.pop("blockysize", None)
    with rasterio.open(str(out_path), "w", **profile) as dst:
        dst.write(change, 1)
        dst.update_tags(
            CLASSES="0=stable water, 1=gained, -1=lost, 0(non-water)",
            BASELINE_YEAR=str(params.get("baseline_year")),
            TARGET_YEAR=str(params.get("target_year")),
        )

    baseline_km2 = float(baseline.sum()) * pixel_km2
    lost_km2 = float(lost.sum()) * pixel_km2
    gained_km2 = float(gained.sum()) * pixel_km2
    summary_path = params.get("summary_output")
    summary_path = (
        _resolve(summary_path, context)
        if summary_path
        else str(_workdir(context) / "water_change.json")
    )
    result = {
        "change_raster": str(out_path),
        "summary_json": summary_path,
        "baseline_year": params.get("baseline_year"),
        "target_year": params.get("target_year"),
        "baseline_water_km2": round(baseline_km2, 3),
        "lost_water_km2": round(lost_km2, 3),
        "gained_water_km2": round(gained_km2, 3),
        "net_change_km2": round(gained_km2 - lost_km2, 3),
        "loss_rate_pct": round(lost_km2 / baseline_km2 * 100.0, 2) if baseline_km2 else None,
        "stable_water_km2": round(float(stable_water.sum()) * pixel_km2, 3),
    }
    Path(summary_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


# --------------------------------------------------------------------------- #
# s4：PC1 优先分值（本案的核心指标）
# --------------------------------------------------------------------------- #
def _read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.reader(handle)]
    # 跳过以 '#' 开头的注释行与表头
    data = [row for row in rows if row and not row[0].startswith("#")]
    if not data:
        return []
    header = data[0]
    return [dict(zip(header, row)) for row in data[1:]]  # noqa: B905 (py3.9)


def pc1_priority_score_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """PC1：一个地点所承载的各国际重要水鸟种群比例之和。

    params:
        counts      : 计数表（site_id, site_name, species, count[, kind]）
        populations : 种群量表（species, flyway_population, globally_threatened）
        threshold   : PC1 入选阈值，默认 10（原文：中国与蒙古用 10，其余八国用 1）
        one_percent : 是否启用 1% 规则（默认启用；本案把它设为 fixed 参数）
        min_count   : 可选，计数低于该值不计（默认 0）
    """
    counts_path = params.get("counts")
    populations_path = params.get("populations")
    if not counts_path or not populations_path:
        raise ValueError("waterbird-priority-score 需要 'counts' 与 'populations'")
    counts_path = _resolve(counts_path, context)
    populations_path = _resolve(populations_path, context)

    threshold = float(params.get("threshold", DEFAULT_PC1_THRESHOLD))
    use_one_percent = _truthy(params.get("one_percent"), True)
    min_count = float(params.get("min_count", 0) or 0)

    populations: dict[str, dict[str, Any]] = {}
    for row in _read_csv_rows(populations_path):
        try:
            total = float(row.get("flyway_population") or 0)
        except ValueError:
            continue
        if total > 0:
            populations[row["species"]] = {
                "population": total,
                "threatened": str(row.get("globally_threatened", "")).upper() == "TRUE",
            }

    sites: dict[str, dict[str, Any]] = {}
    skipped_unknown_species: set[str] = set()
    for row in _read_csv_rows(counts_path):
        species = row.get("species", "")
        if species not in populations:
            skipped_unknown_species.add(species)
            continue
        try:
            count = float(row.get("count") or 0)
        except ValueError:
            continue
        if count < min_count:
            continue
        site_id = row.get("site_id") or row.get("site_name") or "unknown"
        entry = sites.setdefault(
            site_id,
            {
                "site_id": site_id,
                "site_name": row.get("site_name") or site_id,
                "kind": row.get("kind") or None,
                "pc1": 0.0,
                "species_meeting_1pct": 0,
                "species_records": 0,
                "populations": [],
            },
        )
        entry["species_records"] += 1
        population = populations[species]["population"]
        share = count / population
        meets = share >= ONE_PERCENT if use_one_percent else True
        if meets:
            # 1% 规则：只有达到国际重要数量的种群才计入 PC1（原文第五、六节）
            entry["pc1"] += share
            entry["species_meeting_1pct"] += 1
            entry["populations"].append(
                {
                    "species": species,
                    "count": int(count),
                    "flyway_population": int(population),
                    "share_pct": round(share * 100.0, 4),
                    "globally_threatened": populations[species]["threatened"],
                }
            )

    ranked = sorted(sites.values(), key=lambda s: s["pc1"], reverse=True)
    for rank, entry in enumerate(ranked, 1):
        entry["rank"] = rank
        entry["pc1"] = round(entry["pc1"], 4)
        entry["priority"] = entry["pc1"] >= threshold
        entry["threatened_species_meeting_1pct"] = sum(
            1 for p in entry["populations"] if p["globally_threatened"]
        )

    out_dir = _workdir(context)
    table_path = (
        _resolve(params["output"], context)
        if params.get("output")
        else str(out_dir / "priority_sites.csv")
    )
    with open(table_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "rank",
                "site_id",
                "site_name",
                "kind",
                "pc1",
                "priority",
                "species_meeting_1pct",
                "threatened_species_meeting_1pct",
                "top_species",
            ]
        )
        for entry in ranked:
            top = max(entry["populations"], key=lambda p: p["share_pct"], default=None)
            writer.writerow(
                [
                    entry["rank"],
                    entry["site_id"],
                    entry["site_name"],
                    entry["kind"] or "",
                    entry["pc1"],
                    str(entry["priority"]).upper(),
                    entry["species_meeting_1pct"],
                    entry["threatened_species_meeting_1pct"],
                    top["species"] if top else "",
                ]
            )

    scores_path = (
        _resolve(params["scores_output"], context)
        if params.get("scores_output")
        else str(out_dir / "priority_scores.json")
    )
    payload = {
        "metric": "PC1 = 各达到国际重要数量（≥1%）的种群比例之和",
        "threshold": threshold,
        "one_percent_rule": use_one_percent,
        "region_calibration": "原文：中国与蒙古阈值 10，其余八国阈值 1",
        "sites": ranked,
        "priority_sites": [entry["site_id"] for entry in ranked if entry["priority"]],
        "priority_count": sum(1 for entry in ranked if entry["priority"]),
        "site_count": len(ranked),
        "skipped_species": sorted(skipped_unknown_species),
        "synthetic": True,
    }
    Path(scores_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "priority_table": table_path,
        "scores_json": scores_path,
        "threshold": threshold,
        "priority_count": payload["priority_count"],
        "site_count": payload["site_count"],
        "top_sites": [
            {
                "site_id": entry["site_id"],
                "pc1": entry["pc1"],
                "priority": entry["priority"],
                "species_meeting_1pct": entry["species_meeting_1pct"],
            }
            for entry in ranked[:5]
        ],
        "skipped_species": payload["skipped_species"],
    }


# --------------------------------------------------------------------------- #
# s5：报告
# --------------------------------------------------------------------------- #
def _fmt(value: Any, digits: int = 2) -> str:
    """报告里的数字格式化：整数不带小数，布尔说人话。

    排名显示成 "1.00"、是否优先显示成 "1.00" —— 这种"技术正确但读不懂"的输出
    会让人怀疑整份报告；报告是交付物，不是调试输出。
    """
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def report_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """渲染结论报告（HTML）。结论、方法、来源、局限四块都要有。

    为什么把"局限"写进报告模板：这项研究自己就明确列了局限（调查强度不均、单次计数与
    多年平均混用、PC1 不含栖息地质量与威胁）。一份不写局限的自动化报告比没有报告更危险。
    """
    change = params.get("change") or {}
    priority = params.get("priority") or {}
    water = params.get("water") or {}
    if isinstance(change, str):
        change = json.loads(Path(_resolve(change, context)).read_text(encoding="utf-8"))
    if isinstance(priority, str):
        priority = json.loads(Path(_resolve(priority, context)).read_text(encoding="utf-8"))
    if isinstance(water, str):
        water = json.loads(Path(_resolve(water, context)).read_text(encoding="utf-8"))

    region = params.get("region") or "未指定研究区"
    baseline_year = params.get("baseline_year")
    target_year = params.get("target_year")
    source = params.get("source") or {}
    threshold = params.get("threshold")
    snapshot = params.get("data_snapshot") or {}

    def _rows(items: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
        out = []
        for item in items:
            cells = "".join(f"<td>{_fmt(item.get(key))}</td>" for key, _ in columns)
            out.append(f"<tr>{cells}</tr>")
        return "\n".join(out)

    top_sites = (priority.get("sites") or [])[:8]
    site_rows = _rows(
        top_sites,
        [
            ("rank", ""),
            ("site_name", ""),
            ("pc1", ""),
            ("species_meeting_1pct", ""),
            ("priority", ""),
        ],
    )

    synthetic_note = (
        (
            "<p class='warn'>本报告的全部输入为<strong>合成/示意数据</strong>"
            "（GEONEXUS_SYNTHETIC=TRUE），用于验证流程与模版，<strong>不得用于任何科研或保护决策</strong>。"
            "替换真实数据后本节会自动消失。</p>"
        )
        if params.get("synthetic")
        else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{region} · 候鸟栖息湿地水面变化与优先地点评估</title>
<style>
 body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
        max-width: 900px; margin: 32px auto; padding: 0 20px; line-height: 1.65; color: #1c2333; }}
 h1 {{ font-size: 22px; margin-bottom: 4px; }}
 h2 {{ font-size: 16px; margin-top: 26px; border-left: 3px solid #2f6fed; padding-left: 8px; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 8px 0 4px; }}
 th, td {{ border: 1px solid #dfe3ec; padding: 5px 8px; text-align: left; }}
 th {{ background: #f5f7fc; }}
 .kv td:first-child {{ width: 220px; color: #5b6478; }}
 .warn {{ background: #fff6e5; border: 1px solid #f0c987; padding: 8px 12px; border-radius: 6px; }}
 .meta {{ color: #67708a; font-size: 12px; }}
 code {{ background: #f2f4f9; padding: 1px 4px; border-radius: 3px; }}
</style></head><body>
<h1>{region} · 候鸟栖息湿地水面变化与优先地点评估</h1>
<p class="meta">基线年 {baseline_year} → 目标年 {target_year} ·
 方法：NDWI 水体提取 + PC1 优先分值（1% 规则） · 生成于 GeoNexus 案例链路</p>
{synthetic_note}

<h2>一、结论</h2>
<ul>
  <li>水面变化：基线期水面 <strong>{_fmt(change.get("baseline_water_km2"), 3)} km²</strong>，
      目标期损失 <strong>{_fmt(change.get("lost_water_km2"), 3)} km²</strong>、
      增加 <strong>{_fmt(change.get("gained_water_km2"), 3)} km²</strong>，
      净变化 <strong>{_fmt(change.get("net_change_km2"), 3)} km²</strong>
      （损失率 <strong>{_fmt(change.get("loss_rate_pct"))}%</strong>）。</li>
  <li>优先地点：PC1 阈值 <strong>{_fmt(threshold)}</strong> 下，
      {_fmt(priority.get("site_count"))} 个评估地点中有
      <strong>{_fmt(priority.get("priority_count"))} 个</strong>达到入选标准。</li>
  <li>水体提取：目标年水面占比 <strong>{_fmt((water or {}).get("water_fraction") and (water or {}).get("water_fraction") * 100)}%</strong>，
      NDWI 阈值 <strong>{_fmt((water or {}).get("threshold"))}</strong>。</li>
</ul>
<p>结论口径：PC1 衡量的是"一个地点承载了多少国际重要种群比例"，
<strong>它不评价栖息地质量、当前威胁与未来开发风险</strong>（原文第十五、十六节明确列为局限）。
因此本报告给出的是"哪些地点不可替代"，不是"哪些地点状况良好"。</p>

<h2>二、方法</h2>
<table class="kv">
  <tr><td>水面提取</td><td>NDWI = (Green − NIR) / (Green + NIR)，阈值 {_fmt((water or {}).get("threshold"))}；
      像元面积按中心纬度近似换算</td></tr>
  <tr><td>水面变化</td><td>两期掩膜逐像元对比，统计损失/增加/净变化与损失率</td></tr>
  <tr><td>优先分值 PC1</td><td>对每个达到国际重要数量（≥ 种群 1%）的种群累加"本地点个体数 ÷ 种群总量"，
      得到该地点综合分值；阈值 {_fmt(threshold)}</td></tr>
  <tr><td>1% 规则</td><td>{"启用" if priority.get("one_percent_rule", True) else "未启用"}（Ramsar 第 6 条标准）</td></tr>
  <tr><td>区域校准</td><td>{priority.get("region_calibration") or "—"}</td></tr>
</table>

<h2>三、优先地点（按 PC1 排序，前 8）</h2>
<table>
  <tr><th>排名</th><th>地点</th><th>PC1</th><th>达标种群数</th><th>是否优先</th></tr>
  {site_rows}
</table>
<p class="meta">达标种群数 = 在该地点达到或超过其种群 1% 的水鸟种群个数。</p>

<h2>四、数据来源与快照</h2>
<table class="kv">
  <tr><td>研究区</td><td>{region}</td></tr>
  <tr><td>数据快照</td><td><code>{json.dumps(snapshot, ensure_ascii=False) if snapshot else "—"}</code></td></tr>
  <tr><td>方法来源</td><td>{source.get("citation") or "—"}</td></tr>
  <tr><td>方法来源链接</td><td>{source.get("doi") or "—"}</td></tr>
  <tr><td>运行身份</td><td><code>{params.get("run_id") or "—"}</code></td></tr>
</table>

<h2>五、局限（必读）</h2>
<ol>
  <li><strong>调查强度不均</strong>：监测越充分的地点越容易记录到高峰数量与更多达标种群，
      "没有数据"不等于"没有鸟"（原文第十四节）。</li>
  <li><strong>单次最大数量与多年平均混用</strong>：会同时带来高估与低估（原文第十五节）。</li>
  <li><strong>PC1 不含栖息地质量与威胁</strong>：高分地点可能已接近崩溃（原文第十六节）。</li>
  <li><strong>本报告的遥感阈值与种群量是案例参数</strong>，不是论文的定值；
      论文的地点边界由各国专家核定，属于本案例待补的数据槽位。</li>
</ol>
<p class="meta">详细的事实台账与待补槽位见 <code>docs/cases/flightway-wetland.md</code>。</p>
</body></html>
"""
    out_path = (
        _resolve(params["output"], context)
        if params.get("output")
        else str(_workdir(context) / "report.html")
    )
    Path(out_path).write_text(html, encoding="utf-8")
    return {
        "report": str(out_path),
        "media_type": "text/html",
        "sections": ["结论", "方法", "优先地点", "数据来源与快照", "局限"],
        "word_count": len(html.split()),
    }


#: 技能名 → 处理函数（供节点注册与测试使用）
HANDLERS: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {
    "wetland-water-extent": water_extent_handler,
    "wetland-water-change": water_change_handler,
    "waterbird-priority-score": pc1_priority_score_handler,
    "wetland-report": report_handler,
}

#: 技能名 → 说明（注册到节点时用同一份文案，避免两处不一致）
DESCRIPTIONS: dict[str, str] = {
    "wetland-water-extent": "NDWI 水体提取：green/nir 栅格 → 水面掩膜 + 面积统计",
    "wetland-water-change": "两期水面掩膜对比：损失/增加/净变化/损失率",
    "waterbird-priority-score": "PC1 优先分值：1% 规则 + 各达标种群比例之和 + 入选阈值",
    "wetland-report": "渲染结论报告（结论/方法/优先地点/来源/局限）",
}


def register(node: Any) -> list[str]:
    """把本案四个算子注册到一个 GeoNode（或任何有 register_skill 的对象）。"""
    for name, handler in HANDLERS.items():
        node.register_skill(name=name, handler=handler, description=DESCRIPTIONS[name])
    return sorted(HANDLERS)
