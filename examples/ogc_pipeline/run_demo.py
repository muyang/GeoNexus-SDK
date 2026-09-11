"""OGC process skill in GeoAgent pipeline demo (V1.0).

Demonstrates mixing **local GeoSkills** and **remote OGC API - Processes
skills** as steps of one GeoAgent pipeline goal:

    step 1: local-note            (local GeoSkill, runs on the node)
    step 2: echo                  (OGC process bridge skill -> OGC server)
            params.message = ${step1.outputs.note}   (cross-skill template)

The OGC process skill is registered on the node, advertised at the shared
registry, and executed by the pipeline's skill-first routing.

Usage:
    python run_demo.py [--registry-port N] [--node-port N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from examples.ogc_process.run_demo import _build_mock_ogc_app, _start_server  # noqa: E402
from geonexus.adapters import register_ogc_process_skill  # noqa: E402
from geonexus.agent import Goal, GoalStep, run_goal  # noqa: E402
from geonexus.geocard import GeoCardBuilder  # noqa: E402
from geonexus.geonode import GeoNode, Skill  # noqa: E402
from geonexus.registry import RegistryServer  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent


def _local_note_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    year = (context.temporal.start or "2015-01-01")[:4] if context and context.temporal else "?"
    return {"note": f"local-note-{year}", "year": year}


def run(registry_port: int = 8790, node_port: int = 8787) -> dict:
    print("=" * 74)
    print("GeoNexus Reference Stack - OGC-in-Pipeline Demo (V1.0)")
    print("GeoAgent pipeline: local skills + remote OGC process skills")
    print("=" * 74)

    # 1. Mock OGC API - Processes server ------------------------------------- #
    print("\n[1/5] Starting LOCAL mock OGC API - Processes server (offline)")
    ogc = _build_mock_ogc_app()
    ogc_handle = _start_server(ogc, 0)
    ogc_url = f"http://127.0.0.1:{ogc_handle.port}"

    # 2. Registry + node ------------------------------------------------------ #
    print("[2/5] Starting registry and data node")
    registry = RegistryServer(name="pipeline-registry")
    reg = _start_server(registry.create_app(), registry_port)
    reg_url = f"http://127.0.0.1:{reg.port if registry_port == 0 else registry_port}"

    node = GeoNode(name="pipeline-node", port=node_port)
    node.register_skill_object(
        Skill(
            name="local-note",
            description="Local skill: notes the temporal year.",
            input_schema={"required": []},
            handler=_local_note_handler,
            geocard=(
                GeoCardBuilder(
                    id="skill-local-note",
                    type="skill",
                    name="Local Note",
                    description="Local note skill.",
                )
                .capability("note")
                .build()
            ),
        )
    )
    # Remote OGC process as a skill (bridges to the mock OGC server).
    register_ogc_process_skill(node, ogc_url, "echo")
    node_handle = _start_server(node.create_app(), node_port)
    node_url = f"http://127.0.0.1:{node_handle.port if node_port == 0 else node_port}"
    node.advertise(reg_url, endpoint=node_url)
    print(
        f"      registry {reg_url} | node {node_url} | skills "
        f"{[s.name for s in node.skill_registry.list_skills()]}"
    )

    try:
        # 3. Pipeline goal: local -> OGC process ------------------------------- #
        goal = Goal(
            capability="note",
            steps=[
                GoalStep(
                    skill="local-note",
                    label="Local note (2015)",
                    temporal={"start": "2015-01-01", "end": "2015-12-31"},
                ),
                GoalStep(
                    skill="echo",
                    label="OGC echo (uses step1 output)",
                    params={"message": "${step1.outputs.note}", "times": 2},
                    depends_on=[1],
                ),
            ],
            label="local-then-ogc",
        )
        print(f"[3/5] Goal: {goal.label} ({len(goal.steps)} step(s), step2 -> OGC bridge)")

        # 4. Plan + execute ---------------------------------------------------- #
        print(f"[4/5] Planning and executing via {reg_url}")
        plan = run_goal(goal, reg_url)
        for step in plan.steps:
            print(f"      step {step.step_id}: {step.description}")
            print(
                f"        -> {step.status} on {step.node_url}"
                + (f"  ({step.error})" if step.error else "")
            )
        if plan.failed:
            print("ERROR: some steps failed", file=sys.stderr)
            raise SystemExit(1)

        # 5. Summary ----------------------------------------------------------- #
        print("[5/5] Summary")
        step0_result = plan.steps[0].result or {}
        step1_result = plan.steps[1].result or {}
        note = step0_result["outputs"]["note"]
        ogc_outputs = step1_result["outputs"]
        print(f"      step1 output: {note}")
        print(
            f"      step2 job:    {ogc_outputs['job_status']} "
            f"(ogc_process={ogc_outputs['ogc_process']})"
        )
        print(f"      step2 result: {ogc_outputs['results']}")
        assert note in str(ogc_outputs["results"]), "template must flow step1 -> step2"
        print("\nOGC-in-pipeline demo complete.")
        return {
            "demo": "ogc-pipeline",
            "goal": goal.label,
            "note": note,
            "ogc_results": ogc_outputs["results"],
        }
    finally:
        node_handle.stop()
        reg.stop()
        ogc_handle.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OGC-in-pipeline demo")
    parser.add_argument("--registry-port", type=int, default=8790)
    parser.add_argument("--node-port", type=int, default=8787)
    args = parser.parse_args(argv)
    run(registry_port=args.registry_port, node_port=args.node_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
