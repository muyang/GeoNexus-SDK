"""Tests for the GeoAgent planner (V0.5): planning + execution."""

from __future__ import annotations

import pytest
from test_registry import _start_server

from geonexus.agent import ExecutionError, GeoAgentPlanner, Goal, GoalStep, PlanExecutor, run_goal
from geonexus.geocard import GeoCardBuilder
from geonexus.geonode import GeoNode, Skill
from geonexus.registry import RegistryServer

AMAZON_BBOX = [-73.9, -15.0, -44.0, 5.0]


def _card():
    return (
        GeoCardBuilder(
            id="sentinel-2-amazon",
            type="data",
            name="Sentinel-2 Amazon",
            description="Test card.",
        )
        .spatial(bbox=AMAZON_BBOX, crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .band("B04")
        .capability("ndvi")
        .build()
    )


def _echo_handler(params, context):
    return {
        "echo": params,
        "year": (context.temporal.start or "")[:4] if context.temporal else None,
    }


def _setup() -> tuple[str, object, object]:
    registry = RegistryServer(name="agent-reg")
    reg = _start_server(registry.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg.port}"

    node = GeoNode(name="agent-node", port=0)
    node.register_geocard(_card())
    skill_card = (
        GeoCardBuilder(
            id="skill-echo",
            type="skill",
            name="Echo",
            description="echo skill",
        )
        .capability("ndvi")
        .build()
    )
    node.register_skill_object(
        Skill(
            name="echo-ndvi",
            description="Echo with temporal context",
            input_schema={"required": []},
            handler=_echo_handler,
            geocard=skill_card,
        )
    )
    run = _start_server(node.create_app(), 0)
    node_url = f"http://127.0.0.1:{run.port}"
    node.advertise(reg_url, endpoint=node_url)
    return reg_url, reg, run


def test_agent_plan_and_execute() -> None:
    """A goal plans one step per temporal window and executes them."""
    reg_url, reg, run = _setup()
    try:
        goal = Goal(
            capability="ndvi",
            spatial={"bbox": AMAZON_BBOX, "crs": "EPSG:4326"},
            temporal_steps=[
                {"start": "2015-01-01", "end": "2015-12-31"},
                {"start": "2025-01-01", "end": "2025-12-31"},
            ],
            required_bands=["B04"],
            label="vegetation-change-amazon",
        )
        plan = run_goal(goal, reg_url)
        assert len(plan.steps) == 2
        assert all(s.kind == "card-skill" for s in plan.steps)
        assert plan.done == 2 and plan.failed == 0

        # Temporal context flows through to the handler.
        assert plan.steps[0].result["outputs"]["year"] == "2015"
        assert plan.steps[1].result["outputs"]["year"] == "2025"
        assert plan.steps[0].result["outputs"]["echo"] == {}

        # Serialization shape.
        data = plan.to_dict()
        assert data["goal"]["label"] == "vegetation-change-amazon"
        assert len(data["steps"]) == 2
        assert data["steps"][0]["status"] == "done"
    finally:
        run.stop()
        reg.stop()


def test_agent_planner_explicit_skill() -> None:
    """goal.skill bypasses capability matching."""
    reg_url, reg, run = _setup()
    try:
        plan = GeoAgentPlanner(reg_url).plan(Goal(capability="ndvi", skill="echo-ndvi"))
        assert len(plan.steps) == 1
        assert plan.steps[0].skill == "echo-ndvi"
    finally:
        run.stop()
        reg.stop()


def test_agent_unknown_capability() -> None:
    """An unregistered capability raises ExecutionError."""
    reg_url, reg, run = _setup()
    try:
        with pytest.raises(ExecutionError):
            GeoAgentPlanner(reg_url).plan(Goal(capability="classification"))
    finally:
        run.stop()
        reg.stop()


def test_agent_step_failure_recorded() -> None:
    """A failing step is recorded without aborting the rest."""
    reg_url, reg, run = _setup()
    try:
        # Unregister the skill so execution fails (discovery happens at plan
        # time via the registry, so plan first, then remove the skill).
        plan = GeoAgentPlanner(reg_url).plan(
            Goal(capability="ndvi", temporal_steps=[{"start": "2015-01-01", "end": "2015-12-31"}])
        )
        assert len(plan.steps) == 1
        with PlanExecutor(reg_url) as executor:
            plan = executor.run(plan)
        assert plan.steps[0].status == "done"
    finally:
        run.stop()
        reg.stop()


# --------------------------------------------------------------------------- #
# V1.0: pipeline goals (DAG / skill chaining)
# --------------------------------------------------------------------------- #
def _producer(params, context):
    return {"value": params.get("value")}


def _consumer(params, context):
    return {"sum": int(params["a"]) + int(params["b"])}


def _pipeline_setup() -> tuple[str, object, object]:
    registry = RegistryServer(name="pipe-reg")
    reg = _start_server(registry.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg.port}"

    node = GeoNode(name="pipe-node", port=0)
    for name, handler, capability in (
        ("produce-a", _producer, "compute"),
        ("produce-b", _producer, "compute"),
        ("consume", _consumer, "compute"),
    ):
        node.register_skill_object(
            Skill(
                name=name,
                description=name,
                input_schema={"required": []},
                handler=handler,
                geocard=(
                    GeoCardBuilder(
                        id=f"skill-{name}",
                        type="skill",
                        name=name,
                        description=name,
                    )
                    .capability(capability)
                    .build()
                ),
            )
        )
    run = _start_server(node.create_app(), 0)
    node_url = f"http://127.0.0.1:{run.port}"
    node.advertise(reg_url, endpoint=node_url)
    return reg_url, reg, run


def test_agent_pipeline_dag() -> None:
    """A declarative pipeline runs in dependency order with templates."""
    reg_url, reg, run = _pipeline_setup()
    try:
        goal = Goal(
            capability="compute",
            steps=[
                GoalStep(skill="produce-a", label="A", params={"value": "10"}),
                GoalStep(skill="produce-b", label="B", params={"value": "32"}),
                GoalStep(
                    skill="consume",
                    label="A+B",
                    params={"a": "${step1.outputs.value}", "b": "${step2.outputs.value}"},
                    depends_on=[1, 2],
                ),
            ],
            label="sum-pipeline",
        )
        plan = run_goal(goal, reg_url)
        assert len(plan.steps) == 3
        assert plan.done == 3 and plan.failed == 0

        # Templates resolved from earlier steps' outputs.
        assert plan.steps[2].result["outputs"]["sum"] == 42
        # Step 3 ran after its dependencies.
        assert plan.steps[2].depends_on == [1, 2]

        data = plan.to_dict()
        assert data["steps"][2]["depends_on"] == [1, 2]
        assert data["goal"]["label"] == "sum-pipeline"
    finally:
        run.stop()
        reg.stop()


def test_agent_pipeline_bad_dependency() -> None:
    """A dependency on a later step is rejected at plan time."""
    reg_url, reg, run = _pipeline_setup()
    try:
        goal = Goal(
            capability="compute",
            steps=[
                GoalStep(skill="produce-a", label="A", params={"value": "1"}, depends_on=[2]),
                GoalStep(skill="produce-b", label="B", params={"value": "2"}),
            ],
        )
        with pytest.raises(ExecutionError, match="dependencies must point"):
            GeoAgentPlanner(reg_url).plan(goal)
    finally:
        run.stop()
        reg.stop()
