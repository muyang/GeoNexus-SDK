"""GeoAgent demo (V0.5) — capability-based orchestration.

Goal: "Analyze vegetation change in the Amazon (synthetic data)."

The GeoAgent planner discovers the ndvi capability and the amazon card at
the shared registry, plans one pushdown step per temporal window (2015,
2025), executes them on the data node, then the demo computes the change
raster.

Usage:
    python run_demo.py [--registry-port N] [--node-port N] [--output DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from examples.amazon_ndvi import skill as ndvi_skill  # noqa: E402
from geonexus.agent import Goal, GoalStep, run_goal  # noqa: E402
from geonexus.geocard import GeoCardBuilder, load_geocard  # noqa: E402
from geonexus.geonode import GeoNode, Skill  # noqa: E402
from geonexus.registry import RegistryServer  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent
AMAZON_DIR = DEMO_DIR.parent / "amazon_ndvi"
AMAZON_BBOX = [-73.9, -15.0, -44.0, 5.0]


def _temporal_ndvi_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """NDVI handler that derives red/nir/output from the temporal context.

    Demonstrates temporal-aware execution: the planner passes a temporal
    window per step, and the skill resolves the matching synthetic scene.
    """
    resolved = dict(params)
    if "red" not in resolved or "nir" not in resolved:
        year = "2015"
        if context is not None and context.temporal is not None:
            year = (context.temporal.start or "2015-01-01")[:4]
        base = Path(context.workdir) if context is not None and context.workdir else Path.cwd()
        resolved["red"] = str(base / f"synthetic_red_{year}.tif")
        resolved["nir"] = str(base / f"synthetic_nir_{year}.tif")
        resolved.setdefault("output", str(base / f"ndvi_{year}.tif"))
    return ndvi_skill.ndvi_analysis_handler(resolved, context)


def run(registry_port: int = 8790, node_port: int = 8787, output_dir: str | None = None) -> dict:
    print("=" * 74)
    print("GeoNexus Reference Stack - GeoAgent Demo (V1.0)")
    print("Pipeline planning (DAG) + pushdown execution (SYNTHETIC data)")
    print("=" * 74)

    out_dir = Path(output_dir) if output_dir else DEMO_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Registry + data node ------------------------------------------------ #
    registry = RegistryServer(name="agent-registry")
    reg = _start_server(registry.create_app(), registry_port)
    reg_url = f"http://127.0.0.1:{reg.port if registry_port == 0 else registry_port}"

    node = GeoNode(name="agent-data-node", port=node_port, workdir=str(out_dir))
    card = load_geocard(AMAZON_DIR / "geocard.yaml")
    skill_card = (
        GeoCardBuilder(
            id="skill-ndvi-analysis",
            type="skill",
            name="NDVI Analysis Skill",
            description="Temporal-aware NDVI analysis (demo, synthetic).",
        )
        .capability("ndvi")
        .input("nir", "raster", required=True)
        .input("red", "raster", required=True)
        .build()
    )
    change_card = (
        GeoCardBuilder(
            id="skill-ndvi-change",
            type="skill",
            name="NDVI Change Skill",
            description="Compute NDVI change between two rasters (pipeline step).",
        )
        .capability("change-detection")
        .input("ndvi_a", "raster", required=True)
        .input("ndvi_b", "raster", required=True)
        .build()
    )
    skill = Skill(
        name="ndvi-analysis",
        description="Compute NDVI from red/NIR rasters (temporal-aware).",
        input_schema={"required": []},
        output_schema={},
        handler=_temporal_ndvi_handler,
        geocard=skill_card,
    )
    change_skill = Skill(
        name="ndvi-change",
        description="Compute NDVI change raster between two NDVI inputs.",
        input_schema={"required": ["ndvi_a", "ndvi_b"]},
        output_schema={},
        handler=ndvi_skill.ndvi_change_handler,
        geocard=change_card,
    )
    node.register_geocard(card)
    node.register_skill_object(skill)
    node.register_skill_object(change_skill)
    run_node = _start_server(node.create_app(), node_port)
    node_url = f"http://127.0.0.1:{run_node.port if node_port == 0 else node_port}"
    node.advertise(reg_url, endpoint=node_url)
    print(f"\n[1/5] Registry {reg_url} | data node {node_url} advertised")

    # 2. Generate synthetic scenes ------------------------------------------- #
    print("[2/5] Generating SYNTHETIC scenes (2015, 2025)")
    for year in (2015, 2025):
        ndvi_skill.generate_synthetic_scene(year, out_dir, seed=year)

    # 3. Define the pipeline goal (DAG) -------------------------------------- #
    goal = Goal(
        capability="ndvi",
        spatial={"bbox": AMAZON_BBOX, "crs": "EPSG:4326"},
        steps=[
            GoalStep(
                skill="ndvi-analysis",
                label="NDVI 2015",
                temporal={"start": "2015-01-01", "end": "2015-12-31"},
            ),
            GoalStep(
                skill="ndvi-analysis",
                label="NDVI 2025",
                temporal={"start": "2025-01-01", "end": "2025-12-31"},
            ),
            GoalStep(
                skill="ndvi-change",
                label="Change 2025-2015",
                params={
                    "ndvi_a": "${step1.outputs.ndvi_raster}",
                    "ndvi_b": "${step2.outputs.ndvi_raster}",
                    "output": str(out_dir / "ndvi_change.tif"),
                },
                depends_on=[1, 2],
            ),
        ],
        label="vegetation-change-amazon-pipeline",
    )
    print(f"[3/5] Pipeline goal: {goal.label} ({len(goal.steps)} step(s), step3 depends_on [1,2])")

    # 4. Plan + execute (topological order, template resolution) -------------- #
    print(f"[4/5] Planning and executing via {reg_url}")
    plan = run_goal(goal, reg_url)
    for step in plan.steps:
        print(f"      step {step.step_id}: {step.description}")
        print(
            f"        -> {step.status} on {step.node_url}"
            + (f"  ({step.error})" if step.error else "")
        )

    if plan.failed:
        print("ERROR: some plan steps failed.", file=sys.stderr)
        raise SystemExit(1)

    # 5. Summary -------------------------------------------------------------- #
    print("[5/5] Summary")
    stats_2015 = plan.steps[0].result["outputs"]["stats"] if plan.steps[0].result else {}
    stats_2025 = plan.steps[1].result["outputs"]["stats"] if plan.steps[1].result else {}
    change_stats = plan.steps[2].result["outputs"]["stats"] if plan.steps[2].result else {}
    print(
        f"      NDVI 2015 mean={stats_2015.get('mean'):.3f} | "
        f"2025 mean={stats_2025.get('mean'):.3f} | change={change_stats.get('mean'):.3f}"
    )
    step3_result = plan.steps[2].result or {}
    print(f"      change raster: {step3_result['outputs']['change_raster']}")
    print(f"\n      Outputs (all SYNTHETIC) in {out_dir}:")
    for name in ("ndvi_2015.tif", "ndvi_2025.tif", "ndvi_change.tif"):
        print(f"        - {out_dir / name}")
    print("\nAgent demo complete.")
    run_node.stop()
    reg.stop()
    return {
        "demo": "agent-pipeline",
        "goal": goal.label,
        "steps": [s.to_dict() for s in plan.steps],
        "change_mean": change_stats.get("mean"),
        "synthetic": True,
    }


class _ServerHandle:
    def __init__(self, server, thread) -> None:
        self._server = server
        self._thread = thread

    def wait_ready(self, timeout: float = 15.0) -> _ServerHandle:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            time.sleep(0.05)
        raise TimeoutError("server not ready")

    @property
    def port(self) -> int:
        return self._server.servers[0].sockets[0].getsockname()[1]

    def stop(self, timeout: float = 10.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=timeout)


def _start_server(app, port: int) -> _ServerHandle:
    import threading

    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return _ServerHandle(server, thread).wait_ready()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GeoAgent demo")
    parser.add_argument("--registry-port", type=int, default=8790)
    parser.add_argument("--node-port", type=int, default=8787)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    run(registry_port=args.registry_port, node_port=args.node_port, output_dir=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
