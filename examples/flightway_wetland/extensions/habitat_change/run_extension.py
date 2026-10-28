"""扩展演示：遥感栖息地变化（NDWI）——**不是**原文的方法。

为什么单独放这里：原文第 24 节把"用遥感识别潮滩、红树林、湖泊水面与养殖塘的
长期变化"列为**未来的改进方向**之一，论文本身没有做这一步。案例主体严格按原文
五步组织（见上两级目录的 recipe.yaml），这条遥感链路作为该方向的演示实现单独保留，
数据为合成样例（`make_sample_data.py` 生成，文件内标注 GEONEXUS_SYNTHETIC=TRUE）。

原始说明：把候鸟—湿地案例**真的跑一遍**（进程内，不 mock 任何算子）。

    python run_case.py --region yellow-sea --baseline-year 2010 --target-year 2024
    python run_case.py --pc1-threshold 10      # 论文的区域校准阈值：样例数据下无人入选

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
sys.path.insert(0, str(HERE.parents[2] / "src"))

import water_skills as skills  # noqa: E402  (同目录模块)

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


def run_plan(plan: Any, workdir: Path) -> dict[str, Any]:
    """按任务图顺序执行，并把上游产物按方案声明传给下游。"""
    registry = SkillRegistry()
    for name, handler in skills.HANDLERS.items():
        registry.register(Skill(name=name, description=skills.DESCRIPTIONS[name], handler=handler))
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
        path = Path(raw) if isinstance(raw, str) else None
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

    `restricted_hidden=True` 模拟"调用方看不到那份精确点位"（不存在与无权同等对待）。
    """
    precise = GeoCard.model_validate(
        {
            "geocard_version": "1.0",
            "id": "geocard.eaaf.priority-sites-restricted",
            "type": "data",
            "name": "优先湿地精确点位（受限）",
            "description": "精确点位通常不宜公开：具体坐标会指向可被干扰的停歇地。",
            "compliance": {"sensitivity": "restricted"},
        }
    )
    imagery = GeoCard.model_validate(
        {
            "geocard_version": "1.0",
            "id": "geocard.eaaf.wetland-imagery-sample",
            "type": "data",
            "name": "合成影像栈（样例）",
            "description": "green/nir 两波段合成栈。",
            "compliance": {"sensitivity": "public"},
        }
    )
    counts = GeoCard.model_validate(
        {
            "geocard_version": "1.0",
            "id": "geocard.eaaf.waterbird-counts-sample",
            "type": "data",
            "name": "水鸟计数与种群量（样例）",
            "description": "地点 × 物种 × 数量 + 种群量（示意值）。",
            "compliance": {"sensitivity": "public"},
        }
    )
    library: dict[str, GeoCard] = {
        # 两份公开的数据组成项：任何视角都能解析到
        "geocard.eaaf.wetland-imagery-sample": imagery,
        "geocard.eaaf.waterbird-counts-sample": counts,
        # 算子不是卡片，也不参与判定（GATING_ROLES 只含承载内容的角色）
    }
    if not restricted_hidden:
        library["geocard.eaaf.priority-sites-restricted"] = precise
    report = effective_visibility(card, library.get)
    return report.to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扩展演示：NDWI 水面变化（合成数据，非原文方法）")
    parser.add_argument(
        "--region", default="yellow-sea", choices=["yellow-sea", "yangtze", "sanjiang"]
    )
    parser.add_argument("--baseline-year", type=int, default=2010)
    parser.add_argument("--target-year", type=int, default=2024)
    parser.add_argument("--ndwi-threshold", type=float, default=0.0)
    parser.add_argument(
        "--pc1-threshold",
        type=float,
        default=0.2,
        help="PC1 入选阈值；样例数据默认 0.2，论文的区域校准是 10/1",
    )
    parser.add_argument("--min-count", type=int, default=0)
    parser.add_argument("--run-id", default="local-run-1")
    parser.add_argument("--data-dir", default=str(HERE / "data"))
    parser.add_argument("--recipe", default=str(HERE / "recipe.yaml"))
    parser.add_argument("--workdir", default=str(HERE / "output"))
    parser.add_argument("--keep-workdir", action="store_true", help="保留上一次运行的产物")
    parser.add_argument("--json", action="store_true", help="输出机器可读的运行清单")
    args = parser.parse_args(argv)

    workdir = Path(args.workdir)
    if workdir.exists() and not args.keep_workdir:
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # 样例数据放在工作目录的 data/<region>/ 下：配方里写的就是相对路径
    data_target = workdir / "data"
    if data_target.exists():
        shutil.rmtree(data_target)
    shutil.copytree(Path(args.data_dir) / args.region, data_target / args.region)

    card = load_geocard(HERE / "case.card.yaml")
    recipe = load_recipe(Path(args.recipe))
    params = {
        "region": args.region,
        "baseline_year": args.baseline_year,
        "target_year": args.target_year,
        "ndwi_threshold": args.ndwi_threshold,
        "pc1_threshold": args.pc1_threshold,
        "min_count": args.min_count,
        "report_format": "html",
    }
    plan = materialize(recipe, params, run_id=args.run_id)
    execution = run_plan(plan, workdir)
    deliverables = deliverables_of(plan, execution["results"], workdir)

    s2 = execution["results"]["s2"]
    s3 = execution["results"]["s3"]
    s4 = execution["results"]["s4"]
    report = {
        "case": card.id,
        "recipe": recipe.id,
        "run_id": args.run_id,
        "params": plan.params,
        "steps": execution["runs"],
        "water": {
            "baseline_year": s2.get("year"),
            "baseline_water_km2": s3.get("baseline_water_km2"),
            "target_water_km2": round(s2.get("water_area_km2") or 0, 3),
            "lost_water_km2": s3.get("lost_water_km2"),
            "gained_water_km2": s3.get("gained_water_km2"),
            "net_change_km2": s3.get("net_change_km2"),
            "loss_rate_pct": s3.get("loss_rate_pct"),
        },
        "priority": {
            "threshold": s4.get("threshold"),
            "site_count": s4.get("site_count"),
            "priority_count": s4.get("priority_count"),
            "top_sites": s4.get("top_sites"),
        },
        "deliverables": deliverables,
        "visibility": {
            "as_author": visibility_report(card, restricted_hidden=False),
            "as_public_viewer": visibility_report(card, restricted_hidden=True),
        },
    }
    (workdir / "run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"案例：{card.name}")
    print(f"方案：{recipe.id}    运行：{args.run_id}")
    print(
        f"参数：region={args.region} {args.baseline_year}→{args.target_year} "
        f"ndwi={args.ndwi_threshold} pc1={args.pc1_threshold}"
    )
    print("\n步骤：")
    for run in execution["runs"]:
        print(f"  {run['step']:3s} {run['skill']:26s} {run['status']}")
    print("\n结论：")
    print(
        f"  水面：{report['water']['baseline_water_km2']} km² → "
        f"损失 {report['water']['lost_water_km2']} km²、增加 {report['water']['gained_water_km2']} km²"
        f"（净 {report['water']['net_change_km2']}，损失率 {report['water']['loss_rate_pct']}%）"
    )
    print(
        f"  优先地点：阈值 {report['priority']['threshold']} 下 "
        f"{report['priority']['priority_count']}/{report['priority']['site_count']} 入选"
    )
    print("\n交付物：")
    for item in deliverables:
        size = f"{item['size_bytes']} B" if item["size_bytes"] else "缺失"
        print(f"  [{item['role']:9s}] {item['name']:18s} {size:>10s}  {item['path']}")
    print("\n可见性继承：")
    print(
        f"  作者视角（组成项都解析得到）：{report['visibility']['as_author']['visibility']}"
        f" · visible={report['visibility']['as_author']['visible']}"
    )
    public = report["visibility"]["as_public_viewer"]
    print(
        f"  访客视角（精确点位不可见）：visible={public['visible']}"
        f" · missing={public['missing']}  ⇒ 平台应返回 404"
    )
    print(f"\n运行清单：{workdir / 'run.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
