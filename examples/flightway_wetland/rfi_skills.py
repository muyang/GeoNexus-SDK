"""按原文（Crosby et al. 2026, Scientific Reports 16:1916）重建的五步分析。

为什么单独一个模块：原案例用的是"NDWI 水面变化 + 示意计数"的链路，那不是这篇论文的方法。
论文的方法是五步（框架比较 → 汇集站点与水鸟数据 → 用 CSR1 的 1% 阈值算 PC1 并按区域
校准阈值取舍 → 与各国专家核定边界 → 刻画沿海/内陆、保护状态与 HDI），核心指标是 PC1，
4 个统计量是 χ²。本模块把这五步实现成五个算子，输入是论文公开的站点级结果
（`data/rfi/`，由 `extract_supp_table3.py` 从补充材料解析）。

**诚实边界**（写进报告的"数据可用性"一节）：
    PC1 的原始计算需要逐笔水鸟计数与 CSR1 阈值明细，那套数据受 Wetlands International
    许可限制、未公开。因此这里做的是：用论文**公开的站点级结果**验证原取舍规则
    （中国/蒙古阈值 10、其余八国 1，加 3 处专家例外）能否重现 147 处这一结论，
    以及复算论文的 χ² 统计；**不重算** PC1 本身。拿到受限数据后走 `--counts` 导入路径。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

#: 原文第七节的区域校准阈值：中国与蒙古 10，其余八国 1。
PAPER_THRESHOLD_PRC_MONGOLIA = 10.0
PAPER_THRESHOLD_OTHERS = 1.0
#: 原文正文点名的三处专家例外（PC1 < 10 仍入选）
PAPER_EXPERT_EXCEPTIONS = {"Tolbo Lake": 9.4, "Terkhiin Tsagaan Lake": 6.0, "Buir Lake": 8.0}
#: 原文的候选 → 评估 → 入选漏斗
PAPER_CANDIDATE_SITES = 467  # Jaensch(36) 列出的 10 国候选地点数（"至少 467"）
PAPER_ASSESSED_MIN = 270  # 做了较完整数据整理与评估的地点（"至少 270"）
PAPER_PRIORITY_SITES = 147
#: HDI 档（原文 Table 4，2021/22）
HDI_TIERS = {"Very high": 0.800, "High": 0.700, "Medium": 0.550}

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# 共用小工具
# --------------------------------------------------------------------------- #
def _workdir(context: Any) -> Path:
    base = getattr(context, "workdir", None)
    out = Path(base) if base else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    return out


def _resolve(path: str | Path, context: Any) -> Path:
    """相对路径按执行工作目录解析（与 skills.py 同一套规则，保证可复现）。"""
    target = Path(str(path))
    if target.is_absolute():
        return target
    # 数据目录是相对案例目录的：先按工作目录找，找不到再按案例目录找
    candidate = _workdir(context) / target
    if candidate.exists():
        return candidate
    return (HERE / target).resolve()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    # 返回 str 而不是 Path：技能输出要走 GeoMCP 的 JSON 序列化，
    # WindowsPath 在 HTTP 传输上会直接 500（本机进程内跑则暴露不出来）。
    return str(path)


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def chi_square_gof(observed: list[float]) -> dict[str, float]:
    """拟合优度 χ²（等期望）——原文用它检验"优先站点是否偏向某类湿地/某个 HDI 档"。

    原文报告：沿海/内陆 χ²=8.33 (df=1)；保护/未保护 χ²=32.39 (df=1)；HDI 三档 χ²=72.53 (df=2)。
    这里用同一套公式复算，作为"我们确实按原文方法算了"的证据。
    """
    total = sum(observed)
    k = len(observed)
    expected = total / k if k else 0.0
    if not expected:
        return {"chi2": 0.0, "df": k - 1, "expected": 0.0}
    chi2 = sum((o - expected) ** 2 / expected for o in observed)
    return {"chi2": round(chi2, 2), "df": k - 1, "expected": round(expected, 1)}


def chi_square_2x2(table: list[list[float]]) -> dict[str, float]:
    """2×2 列联表 χ²（原文用它比较两类湿地分别在受保护/未保护中的比例，χ²=6.46, df=1）。"""
    (a, b), (c, d) = table
    n = a + b + c + d
    row1, row2 = a + b, c + d
    col1, col2 = a + c, b + d
    if not n or not row1 or not row2 or not col1 or not col2:
        return {"chi2": 0.0, "df": 1}
    expected = [[row1 * col1 / n, row1 * col2 / n], [row2 * col1 / n, row2 * col2 / n]]
    chi2 = sum(
        (table[i][j] - expected[i][j]) ** 2 / expected[i][j] for i in range(2) for j in range(2)
    )
    return {
        "chi2": round(chi2, 2),
        "df": 1,
        "expected": [
            [round(expected[0][0], 1), round(expected[0][1], 1)],
            [round(expected[1][0], 1), round(expected[1][1], 1)],
        ],
    }


# --------------------------------------------------------------------------- #
# s1：现有保护框架比较（原文第一步）
# --------------------------------------------------------------------------- #
#: 原文 Table 2：与候鸟有关的四条 Ramsar 标准
RAMSAR_CRITERIA = [
    {
        "code": 2,
        "text": "支持易危、濒危或极危物种或受威胁的生态群落",
        "used_by": "EAAFP、BirdLife（IBA）",
    },
    {"code": 4, "text": "在生命周期关键阶段支持动植物物种，或在不利条件下提供庇护", "used_by": "—"},
    {"code": 5, "text": "定期支持 20000 只及以上水鸟", "used_by": "EAAFP"},
    {
        "code": 6,
        "text": "定期支持某一水鸟种群 1% 及以上的个体",
        "used_by": "EAAFP、BirdLife（IBA）",
    },
]


def framework_review_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """比较三套国际框架，并给出各自的站点数（原文 Table 1 + Table 2）。"""
    data_dir = _resolve(params.get("data_dir") or "data/rfi", context)
    rows = _read_csv(data_dir / "framework_designations.csv")
    total = {
        k: sum(int(r[k]) for r in rows) for k in ("ramsar", "flyway_network", "wetland_iba_kba")
    }
    out_dir = _workdir(context) / "framework"
    table_path = _write_csv(
        out_dir / "framework_comparison.csv",
        [
            dict(
                country=r["country"],
                ramsar=r["ramsar"],
                flyway_network=r["flyway_network"],
                wetland_iba_kba=r["wetland_iba_kba"],
                priority_sites=int(
                    next(
                        (
                            x["priority_sites"]
                            for x in _read_csv(data_dir / "country_summary.csv")
                            if x["country"] == r["country"]
                        ),
                        0,
                    )
                ),
            )
            for r in rows
        ],
        ["country", "ramsar", "flyway_network", "wetland_iba_kba", "priority_sites"],
    )
    json_path = _write_json(
        out_dir / "framework_comparison.json",
        {
            "frameworks": [
                {
                    "name": "拉姆萨尔公约（Ramsar Convention on Wetlands of International Importance）",
                    "sites_in_10_countries": total["ramsar"],
                    "role": "政府间公约，9 条标准中 4 条与水鸟相关",
                },
                {
                    "name": "EAAFP 迁飞区网络（Flyway Site Network）",
                    "sites_in_10_countries": total["flyway_network"],
                    "role": "半正式网络，政府提名",
                },
                {
                    "name": "BirdLife 重要鸟区 / 关键生物多样性区（IBA / KBA）",
                    "sites_in_10_countries": total["wetland_iba_kba"],
                    "role": "民间主导，基于可度量标准",
                },
            ],
            "ramsar_criteria_relevant_to_waterbirds": RAMSAR_CRITERIA,
            "criteria_used_by_eaafp": [2, 5, 6],
            "criteria_used_by_birdlife": [2, 6],
            "by_country": rows,
            "totals": total,
            "note": "三套框架的候选站点在原文里都被纳入比较，但并非全部满足 RFI 优先站点标准（原文 Table 1 脚注）。",
        },
    )
    return {"framework_table": table_path, "framework_json": json_path, "totals": total}


# --------------------------------------------------------------------------- #
# s2：汇集站点与水鸟数据（原文第二步）
# --------------------------------------------------------------------------- #
def site_data_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """读入站点级结果数据集，校验它是否与原文公开数字一致，并给出漏斗。"""
    data_dir = _resolve(params.get("data_dir") or "data/rfi", context)
    sites = _read_csv(data_dir / "sites.csv")
    species = _read_csv(data_dir / "site_species.csv")
    countries = _read_csv(data_dir / "country_summary.csv")
    assessed = _read_csv(data_dir / "assessed_sites.csv")

    problems: list[str] = []
    if len(sites) != PAPER_PRIORITY_SITES:
        problems.append(f"站点数 {len(sites)} ≠ 原文 {PAPER_PRIORITY_SITES}")
    by_country = {r["country"]: int(r["priority_sites"]) for r in countries}
    for r in countries:
        got = sum(1 for s in sites if s["country"] == r["country"])
        if got != int(r["priority_sites"]):
            problems.append(f"{r['country']} 站点数 {got} ≠ 原文 {r['priority_sites']}")
    coastal = sum(1 for s in sites if s["wetland_type"] == "coastal")
    inland = sum(1 for s in sites if s["wetland_type"] == "inland")
    if coastal != 91:
        problems.append(f"沿海 {coastal} ≠ 原文 91")
    if inland != 56:
        problems.append(f"内陆 {inland} ≠ 原文 56")

    assessed_total = sum(int(r["assessed_sites"]) for r in assessed)
    funnel = {
        "candidate_sites": PAPER_CANDIDATE_SITES,
        "assessed_sites_min": PAPER_ASSESSED_MIN,
        "assessed_sites_listed": assessed_total,
        "priority_sites": len(sites),
        "by_country": by_country,
        "coastal": coastal,
        "inland": inland,
        "with_pc1_score": sum(1 for s in sites if s["pc1"]),
        "with_rank": sum(1 for s in sites if s["rank"]),
        "narrative_only": sum(1 for s in sites if not s["pc1"] and not s["rank"]),
        "species_rows": len(species),
        "species_over_10pct": sum(1 for s in species if s["over_10pct"] == "True"),
        "species_over_50pct": sum(1 for s in species if s["over_50pct"] == "True"),
        "threatened_species": sorted(
            {s["scientific"] for s in species if s["iucn"] in ("CR", "EN", "VU")}
        ),
    }
    out_dir = _workdir(context) / "site-data"
    table_path = _write_csv(out_dir / "sites_normalised.csv", sites, list(sites[0].keys()))
    json_path = _write_json(
        out_dir / "funnel.json", {"funnel": funnel, "problems": problems, "validated": not problems}
    )
    return {
        "sites_table": table_path,
        "funnel_json": json_path,
        "validated": not problems,
        "problems": problems,
        "funnel": funnel,
    }


# --------------------------------------------------------------------------- #
# s3：PC1 与区域校准阈值（原文第三步）
# --------------------------------------------------------------------------- #
def pc1_select_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """按原文的取舍规则复核优先站点：区域校准阈值 + 专家例外。

    原文：先按 `CSR1 score = count / CSR1 阈值`，把 >1.0 的物种分值相加得 PC1；
    再按 PC1 ≥ 10（中国、蒙古）或 ≥ 1（其余八国）入选，另有 3 处蒙古站点由专家判断入选。
    """
    data_dir = _resolve(params.get("data_dir") or "data/rfi", context)
    thr_prc_mn = float(params.get("threshold_prc_mongolia", PAPER_THRESHOLD_PRC_MONGOLIA))
    thr_others = float(params.get("threshold_others", PAPER_THRESHOLD_OTHERS))
    use_exceptions = str(params.get("include_expert_exceptions", True)).lower() not in (
        "false",
        "0",
        "no",
    )
    keep_published = str(params.get("include_published_sites", True)).lower() not in (
        "false",
        "0",
        "no",
    )
    counts_file = params.get("counts")  # 受限的逐笔计数（可选导入）

    sites = _read_csv(data_dir / "sites.csv")
    rows, selected, rejected, exceptions_used, discrepancies = [], [], [], [], []
    for s in sites:
        country = s["country"]
        threshold = thr_prc_mn if country in ("PRC", "Mongolia") else thr_others
        pc1 = float(s["pc1"]) if s["pc1"] else None
        is_exception = use_exceptions and s["name"].split(",")[0].strip() in PAPER_EXPERT_EXCEPTIONS
        if pc1 is None:
            # 蒙古给的是名次、我国内陆 23 处只给了叙述：原文已将其列为优先站点，
            # 这里如实标为"原文入选，未公布分值"，不臆造分数。
            decision, reason = True, "原文已列为优先站点（未公布 PC1 分值）"
        elif pc1 >= threshold:
            decision, reason = True, f"PC1 {pc1} ≥ 阈值 {threshold}"
        elif is_exception:
            decision, reason = True, f"专家例外（原文点名，PC1 {pc1} < {threshold}）"
            exceptions_used.append({"site": s["name"], "pc1": pc1, "threshold": threshold})
        elif keep_published:
            # 公开结果为准：补充材料已把它列为优先站点，但按公开分值严格套用阈值应落选。
            # 这是原文两份公开材料之间的不一致，本案例**两个数都报**，不替作者做选择。
            decision = True
            reason = f"按阈值应落选（PC1 {pc1} < {threshold}），但原文已列为优先站点"
            discrepancies.append(
                {"site": s["name"], "country": country, "pc1": pc1, "threshold": threshold}
            )
        else:
            decision, reason = False, f"PC1 {pc1} < 阈值 {threshold}"
        rows.append({**s, "applied_threshold": threshold, "selected": decision, "reason": reason})
        (selected if decision else rejected).append(s["name"])

    out_dir = _workdir(context) / "pc1"
    table_path = _write_csv(out_dir / "priority_selection.csv", rows, list(rows[0].keys()))
    payload = {
        "rule": {
            "metric": "PC1 = Σ(nᵢ/Nᵢ)×100，仅累加达到 1% 阈值（CSR1 score > 1.0）的种群",
            "threshold_prc_mongolia": thr_prc_mn,
            "threshold_others": thr_others,
            "one_percent_rule": "Ramsar 第 6 条标准：定期支持某种群 ≥1% 个体",
            "expert_exceptions": PAPER_EXPERT_EXCEPTIONS if use_exceptions else {},
        },
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "exceptions_used": exceptions_used,
        "threshold_discrepancies": discrepancies,
        "include_published_sites": keep_published,
        "published_priority_count": PAPER_PRIORITY_SITES,
        "rule_only_selected_count": len(selected) - len(discrepancies),
        "reproduces_147": len(selected) == PAPER_PRIORITY_SITES,
        "counts_imported": bool(counts_file),
        "note": (
            "PC1 的原始计算需要逐笔计数与 CSR1 阈值明细（受限数据，未公开）；"
            "本步复核的是取舍规则与最终 147 处的一致性，不是从计数重算 PC1。"
            + (
                f" 按公开分值严格执行区域校准阈值得 {len(selected) - len(discrepancies)} 处，"
                f"原文名单为 {PAPER_PRIORITY_SITES} 处；低于阈值仍在名单内的有 "
                f"{len(discrepancies)} 处，原文未说明原因，本案例两个数都保留。"
                if discrepancies
                else ""
            )
            if not counts_file
            else "已导入受限计数数据集，走完整的 count → CSR1 score → PC1 → 取舍链路。"
        ),
    }
    if counts_file:
        payload["counts_summary"] = _summarise_counts(_resolve(counts_file, context))
    json_path = _write_json(out_dir / "priority_selection.json", payload)
    return {
        "selection_table": table_path,
        "selection_json": json_path,
        "selected_count": len(selected),
        "reproduces_147": payload["reproduces_147"],
        "rule_only_selected_count": payload["rule_only_selected_count"],
        "threshold_discrepancies": discrepancies,
        "exceptions_used": exceptions_used,
    }


def _summarise_counts(path: Path) -> dict[str, Any]:
    """导入计数数据时给出可核对的小结（字段沿用原文补充 Table 1 的结构）。"""
    rows = _read_csv(path)
    sites: dict[str, int] = {}
    species: set[str] = set()
    for r in rows:
        site = r.get("Site name") or r.get("site_name") or ""
        sites[site] = sites.get(site, 0) + 1
        name = r.get("Scientific name") or r.get("species") or ""
        if name:
            species.add(name)
    return {
        "rows": len(rows),
        "sites": len(sites),
        "species": len(species),
        "expected_fields": [
            "Seq",
            "Reference",
            "English name",
            "Scientific name",
            "IUCN",
            "Country: Province",
            "Locality name",
            "Site name",
            "Count",
            "Selected count",
            "Date",
            "Primary Source",
            "CSR1",
            "CSR1 Score",
        ],
    }


# --------------------------------------------------------------------------- #
# s4：刻画优先站点（原文第五步）+ 复算 χ²
# --------------------------------------------------------------------------- #
def characterise_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """沿海/内陆、保护状态、HDI 三组刻画，并用原文的 χ² 复算。"""
    data_dir = _resolve(params.get("data_dir") or "data/rfi", context)
    sites = _read_csv(data_dir / "sites.csv")
    countries = _read_csv(data_dir / "country_summary.csv")

    coastal = sum(1 for s in sites if s["wetland_type"] == "coastal")
    inland = sum(1 for s in sites if s["wetland_type"] == "inland")
    bold_protected = sum(1 for s in sites if s["overlaps_protected_bold"] == "True")
    paper_protected = sum(int(c["protected_or_partial"]) for c in countries)
    paper_unprotected = sum(int(c["unprotected"]) for c in countries)

    hdi_counts = {"Very high": 0, "High": 0, "Medium": 0}
    for c in countries:
        hdi_counts[c["hdi_tier"]] += int(c["priority_sites"])

    # 2×2：沿海/内陆 × 受保护/未保护（用补充材料的粗体标记；正文 Table 4 只有国别合计）
    table_2x2 = [
        [
            sum(
                1
                for s in sites
                if s["wetland_type"] == "coastal" and s["overlaps_protected_bold"] == "True"
            ),
            sum(
                1
                for s in sites
                if s["wetland_type"] == "coastal" and s["overlaps_protected_bold"] != "True"
            ),
        ],
        [
            sum(
                1
                for s in sites
                if s["wetland_type"] == "inland" and s["overlaps_protected_bold"] == "True"
            ),
            sum(
                1
                for s in sites
                if s["wetland_type"] == "inland" and s["overlaps_protected_bold"] != "True"
            ),
        ],
    ]

    stats = {
        "coastal_vs_inland": {
            **chi_square_gof([coastal, inland]),
            "observed": [coastal, inland],
            "paper": {"chi2": 8.33, "df": 1, "p": "<0.004"},
        },
        "protection_status": {
            **chi_square_gof([paper_protected, paper_unprotected]),
            "observed": [paper_protected, paper_unprotected],
            "paper": {"chi2": 32.39, "df": 1, "p": "<0.001"},
        },
        "hdi_tiers": {
            **chi_square_gof([hdi_counts["Very high"], hdi_counts["High"], hdi_counts["Medium"]]),
            "observed": [hdi_counts["Very high"], hdi_counts["High"], hdi_counts["Medium"]],
            "paper": {"chi2": 72.53, "df": 2, "p": "<0.001"},
        },
        "wetland_type_x_protection": {
            **chi_square_2x2(table_2x2),
            "observed": table_2x2,
            "paper": {"chi2": 6.46, "df": 1},
            "note": "原文用正文 Table 4 的口径；这里用补充材料粗体标记，"
            "因此只作为方向性对照（原文：未保护中沿海偏多、内陆偏少）",
        },
    }
    report_rows = []
    for c in countries:
        report_rows.append(
            {
                "country": c["country"],
                "priority_sites": c["priority_sites"],
                "coastal": c["coastal"],
                "inland": c["inland"],
                "protected_or_partial": c["protected_or_partial"],
                "unprotected": c["unprotected"],
                "hdi_tier": c["hdi_tier"],
                "bold_overlaps": sum(
                    1
                    for s in sites
                    if s["country"] == c["country"] and s["overlaps_protected_bold"] == "True"
                ),
            }
        )
    out_dir = _workdir(context) / "characterise"
    table_path = _write_csv(
        out_dir / "country_characteristics.csv",
        report_rows,
        [
            "country",
            "priority_sites",
            "coastal",
            "inland",
            "protected_or_partial",
            "unprotected",
            "hdi_tier",
            "bold_overlaps",
        ],
    )
    payload = {
        "totals": {
            "sites": len(sites),
            "coastal": coastal,
            "inland": inland,
            "coastal_pct": round(coastal / len(sites) * 100, 1),
            "paper_protected_or_partial": paper_protected,
            "paper_unprotected": paper_unprotected,
            "bold_overlaps_in_supplement": bold_protected,
            "protection_source_difference": bold_protected - paper_protected,
        },
        "hdi_counts": hdi_counts,
        "statistics": stats,
        "by_country": report_rows,
        "protection_note": (
            f"正文 Table 4 记 {paper_protected} 处受保护或部分受保护、{paper_unprotected} 处未受保护；"
            f"补充材料 Table 3 的粗体标记（脚注：粗体=与保护地重叠）为 {bold_protected} 处，"
            f"两者相差 {bold_protected - paper_protected} 处。原文没有解释差异来源，"
            "本案例两个数字都保留，不做人工对齐。"
        ),
    }
    json_path = _write_json(out_dir / "characterisation.json", payload)
    return {
        "country_table": table_path,
        "characterisation_json": json_path,
        "totals": payload["totals"],
    }


# --------------------------------------------------------------------------- #
# s5：报告（论文形态）
# --------------------------------------------------------------------------- #
def _table(headers: list[str], rows: list[list[Any]], cls: str = "") -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "\n".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def rfi_report_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """按论文形态出报告：框架比较 → 漏斗 → 国别表 → 物种 → 刻画与 χ² → 局限 → 数据可用性。"""
    framework = params.get("framework") or {}
    funnel = params.get("funnel") or {}
    selection = params.get("selection") or {}
    characterisation = params.get("characterisation") or {}
    # 上游产物是"路径"（str 或 Path）时读回来；已经是 dict 时直接用
    for key, holder in (
        ("framework", framework),
        ("funnel", funnel),
        ("selection", selection),
        ("characterisation", characterisation),
    ):
        if isinstance(holder, (str, Path)) and str(holder):
            payload = json.loads(_resolve(holder, context).read_text(encoding="utf-8"))
            if key == "framework":
                framework = payload
            elif key == "funnel":
                funnel = payload.get("funnel", payload)
            elif key == "selection":
                selection = payload
            else:
                characterisation = payload
        elif not isinstance(holder, dict):
            holder = {}
            if key == "framework":
                framework = holder
            elif key == "funnel":
                funnel = holder
            elif key == "selection":
                selection = holder
            else:
                characterisation = holder
    source = params.get("source") or {}
    data_dir = _resolve(params.get("data_dir") or "data/rfi", context)
    countries = _read_csv(data_dir / "country_summary.csv")
    species_rows = _read_csv(data_dir / "site_species.csv")

    f = funnel if funnel else {}
    totals = characterisation.get("totals", {})
    stats = characterisation.get("statistics", {})
    by_country = characterisation.get("by_country", [])
    framework_rows = framework.get("by_country", [])
    priority_by_country = {c["country"]: c["priority_sites"] for c in countries}

    species_by_country: dict[str, list[str]] = {}
    for s in species_rows:
        if s["iucn"] in ("CR", "EN", "VU"):
            species_by_country.setdefault(s["country"], []).append(s["english"] or s["scientific"])
    country_species = _table(
        ["国家", "优先站点", "达到 1% 且受威胁的物种数", "代表物种（受威胁等级）"],
        [
            [
                c["country"],
                c["priority_sites"],
                len(set(species_by_country.get(c["country"], []))),
                "、".join(sorted(set(species_by_country.get(c["country"], [])))[:6]),
            ]
            for c in countries
        ],
    )

    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>东亚—澳大利亚迁飞区优先湿地 · 评估报告</title>
<style>
 :root {{ color-scheme: light; }}
 body {{ font-family: "PingFang SC", "Segoe UI", system-ui, sans-serif; max-width: 1080px;
        margin: 0 auto; padding: 28px 22px 60px; color: #16202e; line-height: 1.75; }}
 h1 {{ font-size: 26px; margin: 0 0 6px; }}
 h2 {{ font-size: 18px; margin: 30px 0 10px; border-left: 4px solid #0e7490; padding-left: 10px; }}
 .meta {{ color: #5b6b80; font-size: 13px; }}
 .notice {{ border: 1px solid #f0c36d; background: #fffaf0; border-radius: 10px; padding: 12px 14px;
            font-size: 13.5px; margin: 16px 0; }}
 .kpis {{ display: flex; gap: 14px; flex-wrap: wrap; margin: 14px 0 6px; }}
 .kpi {{ flex: 1 1 150px; border: 1px solid #d8e3ee; border-radius: 10px; padding: 10px 12px; }}
 .kpi b {{ display: block; font-size: 22px; }}
 .kpi span {{ font-size: 12.5px; color: #5b6b80; }}
 table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin: 10px 0 4px; }}
 th, td {{ border-bottom: 1px solid #e6eef6; padding: 6px 8px; text-align: left; }}
 th {{ background: #f5f9fc; font-weight: 600; }}
 code {{ background: #f2f6fa; padding: 1px 5px; border-radius: 4px; }}
 ul {{ padding-left: 20px; }}
</style></head><body>
<h1>东亚—澳大利亚迁飞区 · 优先湿地评估</h1>
<p class="meta">按 {source.get("citation", "原论文")}（DOI {source.get("doi", "—")}）的
 五步流程复现 · 报告由 GeoNexus 案例引擎生成</p>

<div class="notice">
  <b>本报告用什么数据算的</b>：站点级结果取自论文补充材料 Table 3（公开）；PC1 的原始计数数据
  受 Wetlands International 许可限制、未公开，因此本报告<b>复核</b>原文的取舍规则与统计量，
  <b>不重算</b> PC1 本身。详见文末「数据可用性」。
</div>

<h2>一、识别结果</h2>
<div class="kpis">
  <div class="kpi"><b>{f.get("priority_sites", 147)}</b><span>优先湿地（10 国）</span></div>
  <div class="kpi"><b>{f.get("coastal", 91)}</b><span>沿海湿地（{
        totals.get("coastal_pct", 61.9)
    }%）</span></div>
  <div class="kpi"><b>{f.get("inland", 56)}</b><span>内陆湿地</span></div>
  <div class="kpi"><b>{
        totals.get("paper_protected_or_partial", 108)
    }</b><span>受保护或部分受保护</span></div>
  <div class="kpi"><b>{len(f.get("threatened_species", []))}</b>
    <span>受威胁物种（≥1% 名单中，CR/EN/VU）</span></div>
</div>
<p class="meta">口径说明：原文称这 147 处共代表<b>至少 34 种</b>全球受威胁迁徙水鸟（含未达到 1% 的物种）；
 这里统计的是补充材料"某站点达到 1%"记录里 IUCN 为 CR/EN/VU 的种数（{
        len(f.get("threatened_species", []))
    } 种），
 两者口径不同，因此数字不同。</p>
<p>识别漏斗：候选 <b>{f.get("candidate_sites")}</b> 处 → 完整评估 <b>≥{
        f.get("assessed_sites_min")
    }</b> 处
 （补充材料列出的各国评估数为 {f.get("assessed_sites_listed")}）→ 优先湿地 <b>{
        f.get("priority_sites")
    }</b> 处。</p>

<h2>二、方法（原文五步）</h2>
<ol>
  <li><b>比较现有保护框架</b>：拉姆萨尔公约（10 国共 {
        framework.get("totals", {}).get("ramsar", 148)
    } 处）、
      EAAFP 迁飞区网络（{framework.get("totals", {}).get("flyway_network", 49)} 处）、
      BirdLife IBA/KBA（{framework.get("totals", {}).get("wetland_iba_kba", 465)} 处）；
      水鸟相关标准为 Ramsar 第 2、4、5、6 条，EAAFP 用 2/5/6，BirdLife 用 2/6。</li>
  <li><b>汇集水鸟与站点数据</b>：亚洲水鸟普查、国家监测项目、科学论文、调查报告、eBird 与各国专家资料。</li>
  <li><b>用统一阈值计算 PC1</b>：<code>PC1 = Σ(nᵢ/Nᵢ)×100</code>，只累加 <code>CSR1 score = count / CSR1 阈值 &gt; 1.0</code>
      的种群（1% 规则＝Ramsar 第 6 条）；入选阈值为中国与蒙古 <b>{
        selection.get("rule", {}).get("threshold_prc_mongolia", 10)
    }</b>、
      其余八国 <b>{selection.get("rule", {}).get("threshold_others", 1)}</b>，
      另有 3 处蒙古站点经专家判断入选（Tolbo 9.4 / Terkhiin Tsagaan 6.0 / Buir 8.0）。</li>
  <li><b>与各国专家核定边界</b>：使站点既是生态单元、也能作为现实管理单元（依据 IBA 与 AWC 的计数点映射指南）。</li>
  <li><b>刻画站点</b>：沿海/内陆、保护状态、所在国 HDI 档，并用 χ² 检验分布是否偏离均匀。</li>
</ol>

<h2>二·补、取舍规则的一致性检查</h2>
<p>按公开的逐站点分值严格执行原文的区域校准阈值（中国与蒙古 ≥10、其余八国 ≥1，并纳入 3 处蒙古专家例外），
 得到 <b>{selection.get("rule_only_selected_count", "—")}</b> 处；原文名单为
 <b>{selection.get("published_priority_count", 147)}</b> 处，本案例按"公开结果为准"保留全部
 {selection.get("selected_count", "—")} 处。</p>
{
        _table(
            ["站点", "国家", "公开 PC1", "适用阈值", "说明"],
            [
                [d["site"], d["country"], d["pc1"], d["threshold"], "低于阈值但原文已列为优先站点"]
                for d in selection.get("threshold_discrepancies", [])
            ],
        )
        if selection.get("threshold_discrepancies")
        else ""
    }
<p class="meta">这类差异来自原文两份公开材料（正文阈值说明与补充材料站点表）之间，本报告不做人工对齐。</p>

<h2>二·补、区域背景（原文引用的两项损失估计）</h2>
<p>优先排序要回答的是"哪些湿地不可替代"，而这条迁飞通道上最著名的生态瓶颈是<b>黄海—渤海沿岸</b>：
 论文引用区域资料指出，2000–2015 年间该区域约 <b>1794.8 km²（29%）</b>的沿海湿地因水产养殖、盐田等
 开发而损失；更长时间尺度上，过去约半个世纪潮滩损失比例可能达到 <b>65%</b>。另一项估计是：
 我国三江平原湿地转为农田，在过去约六十年中造成的生态系统服务价值损失约 <b>574.6 亿美元</b>。
 这些背景数字说明为什么"面积"不是判据 —— 关键在于哪些节点一旦失去就难以替代。</p>

<h2>三、国别分布（原文 Table 4）</h2>
{
        _table(
            [
                "国家",
                "优先站点",
                "沿海",
                "内陆",
                "受保护/部分保护",
                "未保护",
                "HDI 档",
                "补充材料粗体标记",
            ],
            [
                [
                    c["country"],
                    c["priority_sites"],
                    c["coastal"],
                    c["inland"],
                    c["protected_or_partial"],
                    c["unprotected"],
                    c["hdi_tier"],
                    c["bold_overlaps"],
                ]
                for c in by_country
            ],
        )
    }

<h2>四、各框架的候选站点（原文 Table 1）</h2>
{
        _table(
            ["国家", "拉姆萨尔", "EAAFP 迁飞区网络", "湿地 IBA/KBA", "最终优先站点"],
            [
                [
                    r["country"],
                    r["ramsar"],
                    r["flyway_network"],
                    r["wetland_iba_kba"],
                    (priority_by_country.get(r["country"]) or "—"),
                ]
                for r in framework_rows
            ],
        )
    }

<h2>五、物种：哪些种群依赖这些湿地</h2>
{country_species}
<p>补充材料共记录 <b>{f.get("species_rows")}</b> 条"某站点达到 1% 的物种"记录，其中
 <b>{f.get("species_over_10pct")}</b> 条超过该种群 10%、<b>{
        f.get("species_over_50pct")
    }</b> 条超过 50%
 （原文以 * 与 ** 标注）。</p>

<h2>六、刻画与统计检验（复算原文 χ²）</h2>
{
        _table(
            ["检验", "观测量", "我们的复算 χ²", "原文报告", "结论"],
            [
                [
                    "沿海 vs 内陆",
                    f"{stats.get('coastal_vs_inland', {}).get('observed')}",
                    stats.get("coastal_vs_inland", {}).get("chi2"),
                    f"χ²={stats.get('coastal_vs_inland', {}).get('paper', {}).get('chi2')} (df=1, P<0.004)",
                    "沿海显著偏多",
                ],
                [
                    "保护 vs 未保护",
                    f"{stats.get('protection_status', {}).get('observed')}",
                    stats.get("protection_status", {}).get("chi2"),
                    f"χ²={stats.get('protection_status', {}).get('paper', {}).get('chi2')} (df=1, P<0.001)",
                    "受保护显著偏多",
                ],
                [
                    "HDI 三档",
                    f"{stats.get('hdi_tiers', {}).get('observed')}",
                    stats.get("hdi_tiers", {}).get("chi2"),
                    f"χ²={stats.get('hdi_tiers', {}).get('paper', {}).get('chi2')} (df=2, P<0.001)",
                    "集中在高/极高人类发展档",
                ],
                [
                    "湿地类型 × 保护状态",
                    f"{stats.get('wetland_type_x_protection', {}).get('observed')}",
                    stats.get("wetland_type_x_protection", {}).get("chi2"),
                    f"χ²={stats.get('wetland_type_x_protection', {}).get('paper', {}).get('chi2')} (df=1)",
                    "未保护中沿海偏多、内陆偏少",
                ],
            ],
        )
    }
<p class="meta">{stats.get("wetland_type_x_protection", {}).get("note", "")}</p>

<h2>七、局限（原文自己列出的，必须一起交付）</h2>
<ul>
  <li><b>调查强度不均</b>：多数东南亚湿地十年内只被调查过一两次；监测越充分的地点越容易得高分，
      <b>"没有数据"不等于"没有鸟"</b>。</li>
  <li><b>单次最大数量与多年平均混用</b>：两者分别回答不同问题，都可能高估或低估地点价值。</li>
  <li><b>PC1 不含栖息地质量、当前威胁与未来开发风险</b>：高分地点可能已接近崩溃。</li>
  <li><b>国别间不宜直接比较</b>：阈值按区域校准（中国、蒙古 10，其余八国 1），名单不是同质的跨国排名。</li>
  <li><b>147 处是"当前证据下最有行动价值的组合"</b>，不是永久、封闭的终极名单。</li>
</ul>

<h2>八、建议与后续（原文第 24 节的改进方向）</h2>
<ul>
  <li>加入种群趋势：同样承载 5% 种群，下降中的物种紧迫性更高。</li>
  <li>加入栖息地变化速率：用遥感识别潮滩、红树林、湖面与养殖塘的长期变化（本案的
      <code>extensions/habitat-change</code> 是该方向的演示实现，<b>不是</b>原文方法）。</li>
  <li>加入地点互补性、迁徙连通性、恢复可行性与成本、生态系统服务，并建立动态更新机制。</li>
</ul>

<h2>九、数据可用性（原文声明与本案边界）</h2>
<p>原文 Data availability：支撑结论的逐笔水鸟计数数据由 Wetlands International 许可提供，
 <b>不公开</b>，可向作者申请。因此：本站点级结果（名称、中心坐标、PC1 分值、达到 1% 的物种、
 框架标记、粗体保护地标记）来自公开的补充材料 Table 3；<b>PC1 的原始计算不在本报告中重做</b>，
 只复核取舍规则与统计量。</p>
<p>保护状态的<b>口径差异</b>：正文 Table 4 记 {
        totals.get("paper_protected_or_partial")
    } 处受保护或部分受保护、
 {totals.get("paper_unprotected")} 处未受保护；补充材料 Table 3 的粗体标记为
 {totals.get("bold_overlaps_in_supplement")} 处。两者相差
 {totals.get("protection_source_difference")} 处，原文未解释差异来源，本报告两个数字都保留。</p>
<p class="meta">数据来源：Crosby, M., Wee, S. Q. W., Yong, D. L. et al. <i>Identifying priority wetland sites
 in the East Asian–Australasian Flyway for migratory bird conservation.</i> Scientific Reports 16, 1916 (2026).
 DOI 10.1038/s41598-025-31727-2（CC-BY 4.0）。</p>
</body></html>
"""
    out_path = _workdir(context) / "report.html"
    out_path.write_text(html, encoding="utf-8")
    return {"report": str(out_path), "bytes": out_path.stat().st_size}


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
HANDLERS = {
    "rfi-framework-review": framework_review_handler,
    "rfi-site-data": site_data_handler,
    "rfi-pc1-select": pc1_select_handler,
    "rfi-characterise": characterise_handler,
    "rfi-report": rfi_report_handler,
}

DESCRIPTIONS = {
    "rfi-framework-review": "比较 Ramsar / EAAFP / IBA-KBA 三套框架与其水鸟标准（原文第一步）",
    "rfi-site-data": "汇集并校验 147 处优先湿地的站点级数据，给出候选→评估→入选漏斗（第二步）",
    "rfi-pc1-select": "PC1 取舍规则复核：区域校准阈值 10/1 + 3 处专家例外（第三步）",
    "rfi-characterise": "刻画沿海/内陆、保护状态与 HDI 档，并复算原文 χ²（第五步）",
    "rfi-report": "按论文形态渲染报告（框架/漏斗/国别表/物种/χ²/局限/数据可用性）",
}


def register(node: Any) -> list[str]:
    """把本案五个算子注册到一个 GeoNode（或任何有 register_skill 的对象）。"""
    for name, handler in HANDLERS.items():
        node.register_skill(name=name, handler=handler, description=DESCRIPTIONS[name])
    return sorted(HANDLERS)
