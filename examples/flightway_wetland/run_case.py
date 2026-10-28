"""把候鸟—湿地案例**真的跑一遍**（进程内，不 mock 任何算子）。

    python run_case.py                          # 按原文五步跑一遍，产出 4 个交付物
    python run_case.py --threshold-prc-mongolia 1 --threshold-others 1   # 敏感性：取消区域校准
    python run_case.py --no-expert-exceptions   # 去掉原文点名的 3 处蒙古专家例外
    python run_case.py --counts imports/counts.csv                       # 导入受限计数走完整计算

与 1.x 的区别：这一版按论文**真正的方法**组织（框架比较 → 数据汇集 → PC1 与区域校准 →
专家核定边界 → 刻画与 χ²），数据用论文公开的站点级结果；原来那条 NDWI 水面变化演示链路
已移到 `extensions/habitat_change/`，它是原文第 24 节建议的**后续工作**，不是论文方法。

它做的事，与平台侧的 `/api/recipes/run` 是同一条链路的两种执行器：

    recipe.yaml ──materialize()──▶ TaskGraph ──逐个 geo.execute──▶ 产物 ──▶ 交付物清单

区别只在于：平台用 SDK Web BFF 提交到 GeoNode（有 task_id、可取消、可审计），
这里用 `LocalRuntime` 在同一进程里跑（便于本地开发与 CI 复现）。
两者共用同一套「上游产物按 `step://<step>/<output>` 引用传给下游」的接线规则。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "src"))

import rfi_skills  # noqa: E402  (同目录模块)

from geonexus.geocard import GeoCard, effective_visibility, load_geocard  # noqa: E402
from geonexus.geomcp.models import ExecuteParams  # noqa: E402
from geonexus.geonode.registry import SkillRegistry  # noqa: E402
from geonexus.geonode.runtime import LocalRuntime  # noqa: E402
from geonexus.geonode.skill import Skill  # noqa: E402
from geonexus.recipe import load_recipe, materialize  # noqa: E402


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def jsonable(value: Any) -> Any:
    """把 Path 等非 JSON 类型转成可写盘的形态（算子返回的是路径对象，便于调用方直接读文件）。"""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def run_plan(plan: Any, workdir: Path) -> dict[str, Any]:
    """按任务图顺序执行，并把上游产物按方案声明传给下游。"""
    registry = SkillRegistry()
    for name, handler in rfi_skills.HANDLERS.items():
        registry.register(
            Skill(name=name, description=rfi_skills.DESCRIPTIONS[name], handler=handler)
        )
    runtime = LocalRuntime(registry, workdir=str(workdir), node_name="flightway-case")

    results: dict[str, dict[str, Any]] = {}
    runs: list[dict[str, Any]] = []
    for task_id in plan.graph.topological_order():
        task = plan.graph.task(task_id)
        step_id = (task.labels or {}).get("recipe_step") or task_id.split(":")[-1]

        params = dict(task.spec.get("params") or {})
        # 接线规则：`step://<step>/<output>` → 上游那一步返回的同名输出
        for name, io in task.inputs.items():
            target, _, field = str(io.ref).partition("://")[2].partition("/")
            upstream = results.get(target) or {}
            if field not in upstream:
                raise RuntimeError(
                    f"步骤 {step_id} 需要 {target} 的 {field}，但上游没有产出该字段"
                    f"（上游实际产出：{sorted(upstream)}）"
                )
            params[name] = upstream[field]

        if step_id == "s5":
            # 运行身份由**执行器**补：方案自己不知道这次运行的 id。
            # 平台侧目前也没有注入（见 docs/cases/flightway-wetland.md 的待办），
            # 这里显式补上，报告里才能指回这一次运行。
            params.setdefault("run_id", plan.run_id)

        outcome = runtime.execute(ExecuteParams(skill=task.spec["uses"], params=params))
        outputs = dict(outcome.get("outputs") or {})
        results[step_id] = outputs
        runs.append(
            {
                "step": step_id,
                "skill": task.spec["uses"],
                "params": {k: v for k, v in params.items() if not str(v).startswith("step://")},
                "outputs": outputs,
                "status": outcome.get("status", "ok"),
            }
        )
    return {"runs": runs, "results": results}


def deliverables_of(
    plan: Any, results: dict[str, dict[str, Any]], workdir: Path
) -> list[dict[str, Any]]:
    """按方案 `outputs` 声明解析交付物 —— 结论，不是每一步的副产品。"""
    listed: list[dict[str, Any]] = []
    for out in plan.outputs:
        step_id, _, field = out.from_.partition(".")
        raw = (results.get(step_id) or {}).get(field)
        path = Path(raw) if isinstance(raw, (str, Path)) and str(raw) else None
        if path is not None and not path.is_absolute():
            path = workdir / path
        exists = bool(path and path.exists())
        listed.append(
            {
                "name": out.name,
                "role": out.role,
                "media_type": out.media_type,
                "from": out.from_,
                "path": str(path) if path else None,
                "exists": exists,
                "size_bytes": path.stat().st_size if exists and path else None,
                "checksum": sha256_of(path) if exists and path and path.is_file() else None,
            }
        )
    return listed


def visibility_report(card: GeoCard, *, restricted_hidden: bool) -> dict[str, Any]:
    """可见性继承：含受限组成项时，整案对外是什么状态。

    `restricted_hidden=True` 模拟"调用方看不到那份逐笔计数数据"（不存在与无权同等对待）。
    精确坐标这次是**公开**的（论文补充材料已发表），因此不再拿坐标当受限项 ——
    真正受限的是支撑 PC1 的原始计数数据，这也更符合原文的数据可用性声明。
    """

    def _card(cid: str, name: str, desc: str, sensitivity: str) -> GeoCard:
        return GeoCard.model_validate(
            {
                "geocard_version": "1.0",
                "id": cid,
                "type": "data",
                "name": name,
                "description": desc,
                "compliance": {"sensitivity": sensitivity},
            }
        )

    sites = _card(
        "geocard.eaaf.rfi-priority-sites",
        "RFI 优先湿地站点表（公开）",
        "147 处优先湿地的名称、中心坐标、PC1 与物种记录（论文补充材料 Table 3）。",
        "public",
    )
    summary = _card(
        "geocard.eaaf.rfi-country-summary",
        "国别汇总与框架站点数（公开）",
        "论文 Table 1/3/4 的国别汇总。",
        "public",
    )
    counts = _card(
        "geocard.eaaf.waterbird-counts-restricted",
        "逐笔水鸟计数（受限）",
        "支撑 PC1 计算的原始计数，由 Wetlands International 许可提供，未公开。",
        "restricted",
    )
    library: dict[str, GeoCard] = {
        "geocard.eaaf.rfi-priority-sites": sites,
        "geocard.eaaf.rfi-country-summary": summary,
    }
    if not restricted_hidden:
        library["geocard.eaaf.waterbird-counts-restricted"] = counts
    report = effective_visibility(card, library.get)
    return report.to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按原文五步跑一遍 RFI 优先湿地案例")
    parser.add_argument(
        "--threshold-prc-mongolia",
        type=float,
        default=10.0,
        help="中国与蒙古的 PC1 入选阈值（原文区域校准取 10）",
    )
    parser.add_argument(
        "--threshold-others", type=float, default=1.0, help="其余八国的 PC1 入选阈值（原文取 1）"
    )
    parser.add_argument(
        "--no-expert-exceptions", action="store_true", help="不纳入原文点名的 3 处蒙古专家例外"
    )
    parser.add_argument(
        "--counts", default="", help="可选：受限的逐笔计数数据集路径（走完整 PC1 计算）"
    )
    parser.add_argument("--run-id", default="local-run-1")
    parser.add_argument("--data-dir", default=str(HERE / "data" / "rfi"))
    parser.add_argument("--workdir", default=str(HERE / "output"))
    parser.add_argument("--keep-workdir", action="store_true", help="保留上一次运行的产物")
    parser.add_argument("--json", action="store_true", help="输出机器可读的运行清单")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir)
    if workdir.exists() and not args.keep_workdir:
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # 数据放在工作目录的 data/rfi/ 下：配方里写的是相对路径（"我的数据在哪"由执行器决定）
    data_target = workdir / "data" / "rfi"
    if data_target.exists():
        shutil.rmtree(data_target)
    shutil.copytree(Path(args.data_dir), data_target)

    card = load_geocard(HERE / "case.card.yaml")
    recipe = load_recipe(HERE / "recipe.yaml")
    params = {
        "data_dir": "data/rfi",
        "threshold_prc_mongolia": args.threshold_prc_mongolia,
        "threshold_others": args.threshold_others,
        "include_expert_exceptions": not args.no_expert_exceptions,
        "one_percent_rule": True,
        "counts": args.counts,
        "report_format": "html",
    }
    plan = materialize(recipe, params, run_id=args.run_id)
    execution = run_plan(plan, workdir)
    deliverables = deliverables_of(plan, execution["results"], workdir)

    s2 = execution["results"]["s2"]
    s3 = execution["results"]["s3"]
    s4 = execution["results"]["s4"]
    funnel = s2.get("funnel") or {}
    report = {
        "case": card.id,
        "recipe": recipe.id,
        "run_id": args.run_id,
        "params": plan.params,
        "steps": execution["runs"],
        "funnel": funnel,
        "validation": {"validated": s2.get("validated"), "problems": s2.get("problems") or []},
        "selection": {
            "threshold_prc_mongolia": args.threshold_prc_mongolia,
            "threshold_others": args.threshold_others,
            "expert_exceptions": not args.no_expert_exceptions,
            "selected_count": s3.get("selected_count"),
            "reproduces_147": s3.get("reproduces_147"),
        },
        "characterisation": s4.get("totals"),
        "deliverables": deliverables,
        "visibility": {
            "as_author": visibility_report(card, restricted_hidden=False),
            "as_public_viewer": visibility_report(card, restricted_hidden=True),
        },
    }
    (workdir / "run.json").write_text(
        json.dumps(jsonable(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if args.json:
        print(json.dumps(jsonable(report), ensure_ascii=False, indent=2))
        return 0 if s2.get("validated") else 1

    print(f"案例：{card.name}")
    print(f"方案：{recipe.id}    运行：{args.run_id}")
    print("\n步骤（原文五步）：")
    for run in execution["runs"]:
        print(f"  {run['step']:3s} {run['skill']:24s} {run['status']}")
    print("\n数据校验（与原文公开数字对照）：")
    print(
        f"  站点 {funnel.get('priority_sites')} 处 · 沿海 {funnel.get('coastal')} / "
        f"内陆 {funnel.get('inland')} · 物种记录 {funnel.get('species_rows')} 条"
    )
    print(
        f"  候选 {funnel.get('candidate_sites')} → 评估 ≥{funnel.get('assessed_sites_min')} "
        f"（列出 {funnel.get('assessed_sites_listed')}）→ 优先 {funnel.get('priority_sites')}"
    )
    problems = report["validation"]["problems"]
    print("  校验：" + ("通过（与原文一致）" if not problems else "；".join(problems)))
    print("\n取舍规则：")
    print(
        f"  阈值 中国/蒙古 {args.threshold_prc_mongolia} · 其余八国 {args.threshold_others} · "
        f"专家例外 {'纳入' if not args.no_expert_exceptions else '不纳入'}"
    )
    print(f"  入选 {s3.get('selected_count')} 处 · 重现原文 147 处：{s3.get('reproduces_147')}")
    totals = report["characterisation"] or {}
    print("\n刻画与统计：")
    print(
        f"  沿海 {totals.get('coastal')}（{totals.get('coastal_pct')}%）· "
        f"受保护/部分保护（正文 Table 4）{totals.get('paper_protected_or_partial')} · "
        f"补充材料粗体 {totals.get('bold_overlaps_in_supplement')}"
    )
    print("\n交付物：")
    for item in deliverables:
        size = f"{item['size_bytes']} B" if item["size_bytes"] else "缺失"
        print(f"  [{item['role']:9s}] {item['name']:22s} {size:>10s}  {item['path']}")
    print("\n可见性继承：")
    print(
        f"  作者视角（含受限计数）：{report['visibility']['as_author']['visibility']}"
        f" · visible={report['visibility']['as_author']['visible']}"
    )
    public = report["visibility"]["as_public_viewer"]
    print(
        f"  访客视角（受限计数不可见）：visible={public['visible']}"
        f" · missing={public['missing']}  ⇒ 平台应返回 404"
    )
    print(f"\n运行清单：{workdir / 'run.json'}")
    return 0 if s2.get("validated") else 1


if __name__ == "__main__":
    raise SystemExit(main())
