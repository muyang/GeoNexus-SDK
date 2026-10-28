"""End-to-end acceptance driver: platform + real SDK registry + real GeoNode.

This is **not** a unit test. It wires the three real processes together —

    Python GeoCard/Recipe registry  (:8791)  →  catalogue & review
    Python GeoNode with geo.plan    (:8792)  →  materialisation
    Python SDK Web BFF              (:8793)  →  task submission (task_id)
    Node platform BFF               (:33xx)  →  cases, deliverables, download

— and drives the acceptance chain the batch-1 plan promised:

    a "2 data + 1 skill + 1 report" case can be forked, re-parameterised,
    re-run, and its deliverable downloaded.

Run it with the SDK venv:

    ../.venv-py39/bin/python scripts/acceptance_recipe_e2e.py

It prints every step's evidence (HTTP status + a salient field) and exits
non-zero on the first unmet expectation, so it can be used as a gate.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # core/
PLATFORM = ROOT.parent / "tmp" / "platform-repo"
PYTHON = sys.executable
CHECKS: list[tuple[str, bool, str]] = []


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def call(url: str, method: str = "GET", body: dict | None = None, token: str | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    if data is not None:
        req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read().decode("utf-8")
            try:
                return res.status, json.loads(raw), res.headers.get("content-type", "")
            except json.JSONDecodeError:
                return res.status, {"raw": raw}, res.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw), exc.headers.get("content-type", "")
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}, exc.headers.get("content-type", "")


def wait_http(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _, _ = call(url)
            if status < 500:
                return True
        except Exception:  # noqa: BLE001 - startup polling
            pass
        time.sleep(0.25)
    return False


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------- #
# the three skills of the SDG case (real files on disk, no mocking)
# --------------------------------------------------------------------------- #
SKILL_SOURCE = r'''
"""Acceptance drivers for the SDG 15.3.1 recipe (synthetic, deterministic)."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _workdir(context) -> Path:
    base = Path(getattr(context, "workdir", None) or os.getcwd())
    out = base / "sdg-out"
    out.mkdir(parents=True, exist_ok=True)
    return out


def aoi_handler(params, context):
    """s1 (uses: geocard.packed.menggu): turn the region into an analysis mask."""
    out = _workdir(context)
    region = params.get("region")
    if not region:
        raise ValueError("sdg-aoi requires 'region'")
    aoi = out / f"aoi-{region}.geojson"
    aoi.write_text(json.dumps(
        {"type": "FeatureCollection", "region": region, "features": []}, ensure_ascii=False
    ), encoding="utf-8")
    mask = out / f"mask-{region}.txt"
    mask.write_text(f"mask for {region}\n", encoding="utf-8")
    return {"aoi": str(aoi), "mask": str(mask)}


def degradation_handler(params, context):
    """s2 (the operator): compare the target year against the baseline."""
    out = _workdir(context)
    year = params.get("year")
    baseline = params.get("baseline")
    mask = params.get("mask")
    if year is None or baseline is None:
        raise ValueError("sdg-degrade requires 'year' and 'baseline'")
    if int(year) < int(baseline):
        raise ValueError(f"year {year} is before baseline {baseline}")
    degradation = out / f"degradation-{year}.csv"
    degradation.write_text(f"pixel,class\n1,degraded\n2,stable\n", encoding="utf-8")
    transition = out / f"transition-{year}.csv"
    transition.write_text(f"from,to,area\n{baseline},{year},0.42\n", encoding="utf-8")
    return {
        "degradation": str(degradation),
        "transition": str(transition),
        "stats": {"year": int(year), "baseline": int(baseline), "mask": mask},
    }


def report_handler(params, context):
    """s3 (uses: report.sdg1531.mng.2019): render the conclusion."""
    out = _workdir(context)
    degradation = params.get("degradation")
    if not degradation:
        raise ValueError("sdg-report requires 'degradation'")
    report = out / "report.html"
    report.write_text(
        "<html><body><h1>SDG 15.3.1 土地退化评估</h1>"
        f"<p>源数据：{degradation}</p></body></html>",
        encoding="utf-8",
    )
    return {"report": str(report)}


# 技能名必须与方案 steps[].uses **完全一致**：方案引用的是资产/技能名，
# 而不是某种内部别名 —— 名字对不上就该失败，不该有"猜一个相近的"逻辑。
SKILL_NAMES = {
    "aoi": "geocard.packed.menggu",
    "degrade": "2026.9.16 SDG15.3.1",
    "report": "report.sdg1531.mng.2019",
}


def register(node):
    node.register_skill(name=SKILL_NAMES["aoi"], handler=aoi_handler,
                        description="AOI + mask for the SDG 15.3.1 case")
    node.register_skill(name=SKILL_NAMES["degrade"], handler=degradation_handler,
                        description="land-cover transition / degradation operator")
    node.register_skill(name=SKILL_NAMES["report"], handler=report_handler,
                        description="render the SDG 15.3.1 report")
'''

NODE_SOURCE = r'''
"""Run a GeoNode with the recipe catalogue bound (so geo.plan works)."""
import sys
from pathlib import Path

sys.path.insert(0, {core_src!r})
sys.path.insert(0, {scripts!r})

from geonexus.geonode import GeoNode           # noqa: E402
from geonexus.recipe import RecipeRegistry     # noqa: E402
import acceptance_skills as skills            # noqa: E402

node = GeoNode(
    name="acceptance-node",
    host="127.0.0.1",
    port={port},
    workdir={workdir!r},
    # 共享目录在注册中心：本地只是缓存，节点查不到时会去注册中心问一次
    registry_url={registry_url!r},
    recipe_registry=RecipeRegistry({recipes!r}),
)
skills.register(node)
node.run()
'''


def main() -> int:  # noqa: PLR0915 - a linear checklist is the point
    tmp = Path(tempfile.mkdtemp(prefix="gnx-acceptance-"))
    scripts = tmp / "drivers"
    scripts.mkdir()
    (scripts / "acceptance_skills.py").write_text(SKILL_SOURCE, encoding="utf-8")
    artifacts = tmp / "artifacts"
    artifacts.mkdir()

    reg_port, node_port, web_port, plat_port = (free_port() for _ in range(4))
    catalogue = tmp / "registry.json"
    recipes = Path(str(catalogue) + ".recipes.json")
    procs: list[subprocess.Popen] = []

    def spawn(argv, cwd, env=None, name=""):
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            argv,
            cwd=str(cwd),
            env={**os.environ, **(env or {})},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        procs.append(proc)
        print(f"  started {name or argv[0]} (pid {proc.pid})")
        return proc

    try:
        section("0. 起三个真进程（注册中心 / 节点 / SDK Web BFF）")
        spawn(
            [
                PYTHON,
                "-m",
                "geonexus.cli",
                "registry",
                "start",
                "--port",
                str(reg_port),
                "--persist",
                str(catalogue),
            ],
            ROOT,
            name="SDK registry",
        )
        check("注册中心就绪", wait_http(f"http://127.0.0.1:{reg_port}/health"))

        # 注册方案：走 CLI 的 HTTP 模式（与人工操作同一条路径，且写进**运行中**的注册中心）
        reg = subprocess.run(  # noqa: S603
            [
                PYTHON,
                "-m",
                "geonexus.cli",
                "recipe",
                "register",
                "examples/sdg_15_3_1/recipe.yaml",
                "--url",
                f"http://127.0.0.1:{reg_port}",
                "--provider",
                f"http://127.0.0.1:{node_port}",
                "--status",
                "approved",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        check(
            "方案进入目录（approved）",
            reg.returncode == 0,
            (reg.stdout or reg.stderr).strip().splitlines()[0]
            if (reg.stdout or reg.stderr)
            else "",
        )

        # 方案的 steps[].uses 指向的算子也登记成卡片（type: skill）：
        # 组合案例的可见性由组成项推出，目录里没有 = 解析不到 = 整案不可见。
        # 只登记方案 requires 里的**数据**卡片：可见性继承看的是承载内容的组成项。
        # 算子（如 "2026.9.16 SDG15.3.1"）常常只是个名字，本就不该因为"不在目录里"
        # 而让整案不可见 —— 而且它含空格，压根不是合法的卡片 id。
        # 数据卡片带覆盖范围：血缘弧线的两端（案例范围 ↔ 资产范围）都得有位置
        data_cards = {
            "geocard.packed.menggu": [87.7, 41.6, 119.9, 52.1],
            "geocard.oge.lc08-l2": [110.4, 14.5, 117.1, 32.7],
            "report.sdg1531.mng.2019": [100.0, 20.0, 120.0, 45.0],
        }
        for card_id, card_bbox in data_cards.items():
            status, _, body = call(
                f"http://127.0.0.1:{reg_port}/cards",
                "POST",
                {
                    "card": {
                        "geocard_version": "1.0",
                        "id": card_id,
                        "type": "knowledge",
                        "name": card_id,
                        "description": "验收数据资产",
                        "spatial": {"bbox": card_bbox, "crs": "EPSG:4326"},
                        "compliance": {"sensitivity": "public"},
                    },
                    "node_url": f"http://127.0.0.1:{node_port}",
                    "status": "approved",
                },
            )
            if status >= 400:
                print(f"  WARN: component card {card_id} -> {status} {body}")

        (scripts / "run_node.py").write_text(
            NODE_SOURCE.format(
                core_src=str(ROOT / "src"),
                scripts=str(scripts),
                port=node_port,
                workdir=str(artifacts),
                recipes=str(recipes),
                registry_url=f"http://127.0.0.1:{reg_port}",
            ),
            encoding="utf-8",
        )
        spawn([PYTHON, str(scripts / "run_node.py")], ROOT, name="GeoNode")
        check("GeoNode 就绪", wait_http(f"http://127.0.0.1:{node_port}/health"))
        caps = call(f"http://127.0.0.1:{node_port}/capabilities")[1]
        check(
            "节点声明 geo.plan 可用",
            "geo.plan" in caps.get("methods", []),
            str(caps.get("recipe_planning")),
        )

        spawn(
            [
                PYTHON,
                "-m",
                "geonexus.cli",
                "web",
                "start",
                "--port",
                str(web_port),
                "--node",
                f"http://127.0.0.1:{node_port}",
                "--user",
                "admin=admin",
            ],
            ROOT,
            name="SDK Web BFF",
        )
        check("SDK Web BFF 就绪", wait_http(f"http://127.0.0.1:{web_port}/api/health"))

        section("1. 起平台 BFF，指向上面三个真服务")
        plat_env = {
            "PORT": str(plat_port),
            "DATA_DIR": str(tmp / "platform-data"),
            "UPLOADS_DIR": str(tmp / "platform-uploads"),
            "REGISTRY_URL": f"http://127.0.0.1:{reg_port}",
            "SDK_NODE_URL": f"http://127.0.0.1:{node_port}",
            "SDK_WEB_URL": f"http://127.0.0.1:{web_port}",
            "SDK_WEB_USER": "admin",
            "SDK_WEB_PASSWORD": "admin",
            "ARTIFACT_ROOTS": str(artifacts),
            "IDENTITY_BASE_URL": "",
            "ADMIN_EMAILS": "acceptance@test.local",
        }
        spawn(["node", "server.js"], PLATFORM, env=plat_env, name="platform BFF")
        base = f"http://127.0.0.1:{plat_port}"
        check("平台就绪", wait_http(f"{base}/api/health"))

        reg_user = call(
            f"{base}/api/auth/register",
            "POST",
            {"name": "验收", "email": "acceptance@test.local", "password": "pw-acceptance-1"},
        )
        token = (reg_user[1] or {}).get("token")
        check("平台账号可用", bool(token), f"status={reg_user[0]}")

        section("2. 平台看到的是真目录")
        catalogue_view = call(f"{base}/api/recipes")[1]
        ids = [r.get("id") for r in catalogue_view.get("items", [])]
        check("目录里出现 SDG 方案", "recipe://geonexus/sdg-15-3-1@1.0.0" in ids, str(ids))
        check("SDK 目录状态 up", catalogue_view.get("sdk", {}).get("status") == "up")

        section("3. 物化：参数契约由 SDK 判定")
        bad = call(
            f"{base}/api/recipes/plan",
            "POST",
            {
                "recipe": "recipe://geonexus/sdg-15-3-1@1.0.0",
                "params": {"year": 1990},
                "nodeUrl": f"http://127.0.0.1:{node_port}",
            },
        )
        check(
            "越界参数 → 400 且带字段名",
            bad[0] == 400 and bad[1].get("field") == "year",
            f"status={bad[0]} field={bad[1].get('field')} code={bad[1].get('detail', {}).get('code')}",
        )
        plan = call(
            f"{base}/api/recipes/plan",
            "POST",
            {
                "recipe": "recipe://geonexus/sdg-15-3-1@1.0.0",
                "params": {"year": 2019, "region": "蒙古"},
                "runId": "acceptance-plan",
                "nodeUrl": f"http://127.0.0.1:{node_port}",
            },
        )
        plan_body = plan[1].get("plan") or {}
        tasks = plan_body.get("tasks", [])
        declared = sorted((plan_body.get("deliverables") or {}).keys())
        check(
            "任务图三步且顺序正确",
            [t.get("step") for t in tasks] == ["s1", "s2", "s3"],
            str([t.get("step") for t in tasks]),
        )
        check("物化不执行（无运行记录）", call(f"{base}/api/cases/does-not-exist/layers")[0] == 404)

        section("4. fork：换一组参数，源方案不动")
        forked = call(
            f"{base}/api/recipes/fork",
            "POST",
            {
                "recipe_id": "recipe://geonexus/sdg-15-3-1@1.0.0",
                "namespace": "acceptance",
                "name": "sdg-mexico-2019",
                "title": "墨西哥土地退化 2019",
                "params": {"year": 2019, "region": "墨西哥"},
                "bbox": [110.4, 14.5, 117.1, 32.7],
                "temporal": {"start": "2019-01-01", "end": "2019-12-31"},
            },
            token=token,
        )
        check("fork 返回 201", forked[0] == 201, f"status={forked[0]}")
        forked_id = (forked[1].get("forked") or {}).get("id")
        case_id = forked[1].get("caseId")
        check(
            "派生件是新地址",
            forked_id == "recipe://acceptance/sdg-mexico-2019@1.0.0",
            str(forked_id),
        )
        check("派生件默认待审", (forked[1].get("binding") or {}).get("status") == "pending")
        check("复用者得到自己的草稿案例", bool(case_id), str(case_id))

        source = call(f"{base}/api/recipes?all=true")[1]
        source_recipe = (
            call(f"http://127.0.0.1:{reg_port}/recipes/recipe://geonexus/sdg-15-3-1@1.0.0")[1]
            .get("entry", {})
            .get("recipe", {})
        )
        src_params = {p["name"]: p for p in source_recipe.get("params", [])}
        check(
            "源方案参数未被改写",
            src_params.get("region", {}).get("default") == "蒙古",
            json.dumps(src_params.get("region"), ensure_ascii=False),
        )
        check(
            "目录同时保留了源与派生件",
            len(source.get("items", [])) >= 2,
            str([r.get("id") or r.get("recipeId") for r in source.get("items", [])]),
        )

        section("5. 重跑：草稿不能跑；派生件过审后按任务图逐步执行")
        too_early = call(f"{base}/api/recipes/run", "POST", {"caseId": case_id}, token=token)
        check("草稿态运行被拒（409）", too_early[0] == 409, f"status={too_early[0]}")

        call(
            f"{base}/api/cases/{case_id}",
            "PUT",
            {"title": "墨西哥土地退化 2019", "status": "published"},
            token=token,
        )

        # 派生件默认 pending，而 geo.plan 只认已批准的方案 —— 这是有意的：
        # 复用别人的方法要过审。平台侧的审批页签是批 2 的事，这里直接走 SDK 审核。
        pending_run = call(f"{base}/api/recipes/run", "POST", {"caseId": case_id}, token=token)
        check(
            "未过审的派生件不能运行（2005）",
            pending_run[0] == 404 and pending_run[1].get("code") == 2005,
            f"status={pending_run[0]} body={json.dumps(pending_run[1], ensure_ascii=False)[:300]}",
        )
        approval_id = (forked[1].get("approval") or {}).get("id")
        check("fork 同时生成平台审批单", bool(approval_id), str(approval_id))
        approved = call(
            f"{base}/api/approvals/{approval_id}/decide",
            "POST",
            {"decision": "approve", "note": "验收：口径已核对"},
            token=token,
        )
        check(
            "在平台审批 → SDK 侧 approved",
            approved[0] == 200 and approved[1].get("item", {}).get("sdkState") == "approved",
            json.dumps(approved[1], ensure_ascii=False)[:200],
        )
        sdk_state = call(f"http://127.0.0.1:{reg_port}/recipes/{forked_id}")[1]
        check(
            "SDK 目录里确实是 approved",
            (sdk_state.get("entry") or {}).get("status") == "approved",
            str((sdk_state.get("entry") or {}).get("status")),
        )

        run = call(
            f"{base}/api/recipes/run",
            "POST",
            {"caseId": case_id, "params": {"year": 2019}, "runId": "acceptance-run-1"},
            token=token,
        )
        check(
            "运行返回 202",
            run[0] == 202,
            f"status={run[0]} {json.dumps(run[1], ensure_ascii=False)[:200]}",
        )
        steps = run[1].get("steps", [])
        # 202 只承诺"整组已登记"：此时步骤是 queued，推进在后台进行
        check(
            "202 时整组已登记为 queued",
            [s.get("step") for s in steps] == ["s1", "s2", "s3"]
            and all(s.get("status") == "queued" for s in steps),
            json.dumps(steps, ensure_ascii=False),
        )
        group = run[1].get("group") or run[1].get("runId")
        rows: list[dict] = []
        for _ in range(120):  # 轮询到终态（后台推进器每步都是真实任务）
            rows = call(f"{base}/api/cases/{case_id}/runs?group={group}", token=token)[1].get(
                "items", []
            )
            if rows and all(
                r.get("status") in ("succeeded", "failed", "skipped", "canceled") for r in rows
            ):
                break
            time.sleep(0.25)
        failed_steps = [r for r in rows if r.get("status") != "succeeded"]
        detail = ""
        if failed_steps:
            failed_run = call(f"{base}/api/runs/{failed_steps[0].get('id')}", token=token)[1]
            detail = json.dumps((failed_run.get("run") or {}).get("error"), ensure_ascii=False)
        check(
            "三步都成功（组轮询到终态）",
            [r.get("stepId") for r in rows] == ["s1", "s2", "s3"] and not failed_steps,
            detail or json.dumps(rows, ensure_ascii=False)[:400],
        )

        section("6. 交付物：结论 + 可下载报告")
        dels = call(f"{base}/api/cases/{case_id}/deliverables", token=token)[1]
        by_name = {d["name"]: d for d in dels.get("items", [])}
        # 交付物 = 方案 outputs 里声明的那几项（不是每一步的副产品）
        check(
            "交付物与方案声明一致",
            sorted(by_name) == declared,
            f"声明={declared} 实际={sorted(by_name)}",
        )
        check("报告角色是 knowledge", by_name.get("report", {}).get("role") == "knowledge")
        check("交付物已就绪（有实体产物）", by_name.get("report", {}).get("status") == "ready")
        report_id = by_name.get("report", {}).get("id")
        report_url = f"{base}/api/deliverables/{report_id}/report"
        status, body, ctype = call(report_url, token=token)
        check(
            "报告可下载且是 HTML",
            status == 200 and "text/html" in ctype,
            f"status={status} ctype={ctype}",
        )
        check(
            "报告内容正确",
            "SDG 15.3.1" in json.dumps(body, ensure_ascii=False) or "SDG" in str(body),
        )
        check("未登录不能下载报告", call(report_url)[0] == 401)

        section("7. 图层视图：四级 LOD + 双时间轴")
        view = call(f"{base}/api/cases/{case_id}/layers")[1].get("view", {})
        levels = [layer.get("level") for layer in view.get("layers", [])]
        check("四级 LOD 齐备", levels == ["L1", "L2", "L3", "L4"], str(levels))
        check(
            "案例带 AOI 面",
            view.get("spatial") is True and view.get("bbox") == [110.4, 14.5, 117.1, 32.7],
        )
        footprint = next(
            (lay for lay in view.get("layers", []) if lay.get("kind") == "step-footprint"), {}
        )
        check(
            "步骤足迹按执行顺序",
            [st.get("stepId") for st in footprint.get("steps", [])] == ["s1", "s2", "s3"],
            str([st.get("stepId") for st in footprint.get("steps", [])]),
        )
        # 组成项带 bbox：血缘弧线的两端（案例范围 ↔ 提供数据的资产）
        with_bbox = [c for c in view.get("components", []) if c.get("bbox")]
        check(
            "组成项给出范围（弧线两端）",
            len(with_bbox) >= 1,
            f"components={[c.get('id') for c in view.get('components', [])]}",
        )
        check(
            "算子不参与可见性判定",
            any(c.get("role") == "skill" for c in view.get("components", [])),
        )
        # 后端只给数据；"默认关"是前端画法（见 frontend/src/composables/caseLayers.js）。
        # 这里能验证的是：承载内容的组成项都有可判定的档位，而算子可以没有。
        content_roles = {"data", "model", "knowledge", "workflow"}
        content = [c for c in view.get("components", []) if c.get("role") in content_roles]
        check(
            "承载内容的组成项都有档位（算子可以没有）",
            all(c.get("visibility") for c in content)
            and all(
                c.get("visibility") is None
                for c in view.get("components", [])
                if c.get("role") not in content_roles
            ),
            f"content={[(c.get('id'), c.get('visibility')) for c in content]}",
        )

        timeline = view.get("timeline") or {}
        check(
            "双时间轴各自独立",
            (timeline.get("dataTime") or {}).get("start") == "2019-01-01"
            and bool((timeline.get("executionTime") or {}).get("start")),
            json.dumps(timeline, ensure_ascii=False),
        )

        section("8. 换参数再跑一次：两次运行互不覆盖")
        again = call(
            f"{base}/api/recipes/run",
            "POST",
            {"caseId": case_id, "params": {"year": 2021}, "runId": "acceptance-run-2"},
            token=token,
        )
        check("第二次运行成功", again[0] == 202, f"status={again[0]}")
        # 后台推进：等第二组也到终态，再数交付物（异步语义下不能抢跑）
        again_group = again[1].get("group") or again[1].get("runId")
        for _ in range(120):
            rows2 = call(f"{base}/api/cases/{case_id}/runs?group={again_group}", token=token)[
                1
            ].get("items", [])
            if rows2 and all(
                r.get("status") in ("succeeded", "failed", "skipped", "canceled") for r in rows2
            ):
                break
            time.sleep(0.25)
        check(
            "第二次也三步成功",
            [r.get("stepId") for r in rows2] == ["s1", "s2", "s3"]
            and all(r.get("status") == "succeeded" for r in rows2),
            json.dumps([r.get("status") for r in rows2]),
        )
        total = call(f"{base}/api/cases/{case_id}/deliverables", token=token)[1].get("count")
        check(
            f"交付物累计 {2 * len(declared)} 条（两次运行各 {len(declared)} 份）",
            total == 2 * len(declared),
            f"count={total}",
        )
        overflow = call(
            f"{base}/api/recipes/run",
            "POST",
            {"caseId": case_id, "params": {"year": 1990}},
            token=token,
        )
        check(
            "越界参数在运行前被拒",
            overflow[0] == 400 and overflow[1].get("field") == "year",
            f"status={overflow[0]}",
        )

        section("9. 可见性继承：组成项受限 ⇒ 整案对外不存在")
        anon_before = call(f"{base}/api/cases/{case_id}")
        check("组成项全公开时访客可见", anon_before[0] == 200, f"status={anon_before[0]}")

        restricted_card = "geocard.packed.menggu"
        # 注册中心不做 replace：先撤下再以新档位登记（真实运维也是这个动作）
        call(f"http://127.0.0.1:{reg_port}/cards/{restricted_card}", "DELETE")
        call(
            f"http://127.0.0.1:{reg_port}/cards",
            "POST",
            {
                "card": {
                    "geocard_version": "1.0",
                    "id": restricted_card,
                    "type": "knowledge",
                    "name": restricted_card,
                    "description": "验收数据资产（受限）",
                    "compliance": {"sensitivity": "restricted"},
                },
                "node_url": f"http://127.0.0.1:{node_port}",
                "status": "approved",
            },
        )
        anon_after = call(f"{base}/api/cases/{case_id}")
        check(
            "组成项受限 ⇒ 访客 404（不是 403）",
            anon_after[0] == 404 and anon_after[1].get("error") == "案例不存在",
            f"status={anon_after[0]} body={json.dumps(anon_after[1], ensure_ascii=False)[:120]}",
        )
        check("图层视图同样 404", call(f"{base}/api/cases/{case_id}/layers")[0] == 404)
        check("交付物清单同样 404", call(f"{base}/api/cases/{case_id}/deliverables")[0] == 404)
        listed = call(f"{base}/api/cases")[1]
        check(
            "案例列表里不出现隐藏条目（访客看不出少了一条）",
            all(item.get("id") != case_id for item in listed.get("items", [])),
            f"items={len(listed.get('items', []))} hidden={listed.get('hidden')}",
        )
        check("管理员仍可见", call(f"{base}/api/cases/{case_id}", token=token)[0] == 200)
        admin_view = call(f"{base}/api/cases/{case_id}/layers", token=token)[1].get("view", {})
        check(
            "管理员能看到档位与原因",
            (admin_view.get("visibility") or {}).get("visibility") == "restricted",
            json.dumps(admin_view.get("visibility"), ensure_ascii=False)[:200],
        )

        section("10. 非空间案例：进侧栏，不上地球")
        # 方案绑定的是同一份配方，这里只把几何声明去掉：没有范围就没有位置
        # （真实场景里"指标口径说明"这类案例本就没有空间范围）
        non_spatial = call(
            f"{base}/api/cases/{case_id}",
            "PUT",
            {"title": "非空间：口径说明", "bbox": None},
            token=token,
        )
        check("去掉范围后案例仍存在", non_spatial[0] == 200, f"status={non_spatial[0]}")
        ns_view = call(f"{base}/api/cases/{case_id}/layers", token=token)[1].get("view", {})
        check(
            "spatial=false 且不给假位置",
            ns_view.get("spatial") is False and ns_view.get("bbox") is None,
            f"spatial={ns_view.get('spatial')} bbox={ns_view.get('bbox')}",
        )
        check(
            "非空间案例仍有 L2/L3/L4（只是没有 L1）",
            [x.get("level") for x in ns_view.get("layers", [])] == ["L2", "L3", "L4"],
            str([x.get("level") for x in ns_view.get("layers", [])]),
        )

        section("11. 第二个案例：EAAF 优先湿地（真目录 + 真物化）")
        # 这一节的证据分两半：**平台侧**证明"另一个案例能被目录收录并物化"，
        # **本机侧**由 examples/flightway_wetland/run_case.py 证明它真的跑得出交付物
        # （5 个算子在进程内执行，产出站点表/取舍表/刻画表/报告）。
        # 案例 2.0.0 按论文五步重建（Crosby et al. 2026, DOI 10.1038/s41598-025-31727-2）。
        wetland_recipe = ROOT / "examples" / "flightway_wetland" / "recipe.yaml"
        wetland_card = ROOT / "examples" / "flightway_wetland" / "case.card.yaml"
        reg2 = subprocess.run(  # noqa: S603
            [
                PYTHON,
                "-m",
                "geonexus.cli",
                "recipe",
                "register",
                str(wetland_recipe),
                "--url",
                f"http://127.0.0.1:{reg_port}",
                "--status",
                "approved",
                "--provider",
                f"http://127.0.0.1:{node_port}",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        check(
            "湿地方案进入目录",
            reg2.returncode == 0,
            (reg2.stdout or reg2.stderr).strip().splitlines()[0]
            if (reg2.stdout or reg2.stderr)
            else "",
        )
        wetland_catalogue = call(f"{base}/api/recipes?all=true")[1]
        check(
            "平台目录里能看到它",
            any(
                r.get("id") == "recipe://geonexus/eaaf-wetland-priority@2.0.0"
                for r in wetland_catalogue.get("items", [])
            ),
            str([r.get("id") for r in wetland_catalogue.get("items", [])]),
        )

        wetland_plan = call(
            f"{base}/api/recipes/plan",
            "POST",
            {
                "recipe": "recipe://geonexus/eaaf-wetland-priority@2.0.0",
                "params": {"threshold_prc_mongolia": 1, "threshold_others": 1},
                "runId": "wetland-plan",
                "nodeUrl": f"http://127.0.0.1:{node_port}",
            },
        )
        wetland_tasks = (wetland_plan[1].get("plan") or {}).get("tasks", [])
        check(
            "湿地方案物化出 5 步",
            sorted(t.get("step") for t in wetland_tasks) == ["s1", "s2", "s3", "s4", "s5"],
            str([t.get("step") for t in wetland_tasks]),
        )
        check(
            "交付物是方案声明的四项",
            sorted((wetland_plan[1].get("plan") or {}).get("deliverables", {}))
            == ["country_characteristics", "priority_selection", "priority_sites", "report"],
            str(sorted((wetland_plan[1].get("plan") or {}).get("deliverables", {}))),
        )
        wx = next((t for t in wetland_tasks if t.get("step") == "s1"), {})
        check(
            "参数落到步骤上（data_dir 传给第一步）",
            (wx.get("params") or {}).get("data_dir") == "data/rfi",
            str((wx.get("params") or {}).get("data_dir")),
        )
        wx3 = next((t for t in wetland_tasks if t.get("step") == "s3"), {})
        check(
            "区域校准阈值真的传到 PC1 那一步",
            (wx3.get("params") or {}).get("threshold_prc_mongolia") == 1,
            str((wx3.get("params") or {}).get("threshold_prc_mongolia")),
        )
        wetland_bad = call(
            f"{base}/api/recipes/plan",
            "POST",
            {
                "recipe": "recipe://geonexus/eaaf-wetland-priority@2.0.0",
                "params": {"threshold_prc_mongolia": 999},
                "nodeUrl": f"http://127.0.0.1:{node_port}",
            },
        )
        check(
            "越界阈值在物化阶段就被拒（带字段名）",
            wetland_bad[0] == 400 and wetland_bad[1].get("field") == "threshold_prc_mongolia",
            f"status={wetland_bad[0]} field={wetland_bad[1].get('field')}",
        )
        wetland_fixed = call(
            f"{base}/api/recipes/plan",
            "POST",
            {
                "recipe": "recipe://geonexus/eaaf-wetland-priority@2.0.0",
                "params": {"one_percent_rule": False},
                "nodeUrl": f"http://127.0.0.1:{node_port}",
            },
        )
        check(
            "定义性参数（1% 规则）改不动",
            wetland_fixed[0] == 400 and wetland_fixed[1].get("field") == "one_percent_rule",
            f"status={wetland_fixed[0]} field={wetland_fixed[1].get('field')}",
        )
        # 卡片也要能过契约校验（案例卡片本身是 workflow，带组成与参数契约）
        card_check = subprocess.run(  # noqa: S603
            [PYTHON, "-m", "geonexus.cli", "card", "validate", str(wetland_card)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        check(
            "湿地案例卡片契约合法",
            card_check.returncode == 0,
            card_check.stdout.strip().splitlines()[0] if card_check.stdout else "",
        )

        section("12. 后台页签的数据面")
        admin_recipes = call(f"{base}/api/admin/recipes", token=token)[1]
        check(
            "方案页签：绑定 + 目录 + 待审计数",
            admin_recipes.get("bindingCount", 0) >= 1
            and admin_recipes.get("catalogueCount", 0) >= 1,
            f"bindings={admin_recipes.get('bindingCount')} catalogue={admin_recipes.get('catalogueCount')}",
        )
        admin_deliv = call(f"{base}/api/admin/deliverables", token=token)[1]
        check(
            "交付物页签：条数与角色统计",
            admin_deliv.get("count", 0) >= 3
            and admin_deliv.get("byRole", {}).get("knowledge", 0) >= 1,
            json.dumps(admin_deliv.get("byRole"), ensure_ascii=False),
        )
        check("后台端点需要权限", call(f"{base}/api/admin/recipes")[0] == 401)

        section("结果")
        failed = [c for c in CHECKS if not c[1]]
        print(f"  {len(CHECKS) - len(failed)}/{len(CHECKS)} 项通过")
        for label, _, detail in failed:
            print(f"  FAILED: {label}  [{detail}]")
        print(f"\n  临时目录：{tmp}")
        return 1 if failed else 0
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:  # pragma: no cover - best effort
                proc.kill()
        if os.environ.get("KEEP_ACCEPTANCE_TMP") != "1":
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
