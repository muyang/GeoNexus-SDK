"""End-to-end test: remote OGC process skills inside a GeoAgent pipeline."""

from __future__ import annotations

from examples.ogc_pipeline.run_demo import _local_note_handler
from examples.ogc_process.run_demo import _build_mock_ogc_app
from test_registry import _start_server as _start_any

from geonexus.adapters import register_ogc_process_skill
from geonexus.agent import Goal, GoalStep, run_goal
from geonexus.geocard import GeoCardBuilder
from geonexus.geonode import GeoNode, Skill
from geonexus.registry import RegistryServer


def test_ogc_skill_in_pipeline() -> None:
    """A pipeline mixes a local skill and a remote OGC process skill.

    The template ``${step1.outputs.note}`` must flow from the local step
    into the OGC process inputs via the bridge.
    """
    ogc = _build_mock_ogc_app()
    ogc_handle = _start_any(ogc, 0)
    ogc_url = f"http://127.0.0.1:{ogc_handle.port}"

    registry = RegistryServer(name="pipe-reg")
    reg = _start_any(registry.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg.port}"

    node = GeoNode(name="pipe-node", port=0)
    node.register_skill_object(
        Skill(
            name="local-note",
            description="Local note skill.",
            input_schema={"required": []},
            handler=_local_note_handler,
            geocard=(
                GeoCardBuilder(
                    id="skill-local-note",
                    type="skill",
                    name="Local Note",
                    description="local",
                )
                .capability("note")
                .build()
            ),
        )
    )
    register_ogc_process_skill(node, ogc_url, "echo")
    node_handle = _start_any(node.create_app(), 0)
    node_url = f"http://127.0.0.1:{node_handle.port}"
    node.advertise(reg_url, endpoint=node_url)
    try:
        goal = Goal(
            capability="note",
            steps=[
                GoalStep(
                    skill="local-note",
                    label="Local 2015",
                    temporal={"start": "2015-01-01", "end": "2015-12-31"},
                ),
                GoalStep(
                    skill="echo",
                    label="OGC echo",
                    params={"message": "${step1.outputs.note}", "times": 2},
                    depends_on=[1],
                ),
            ],
            label="local-then-ogc",
        )
        plan = run_goal(goal, reg_url)
        assert len(plan.steps) == 2
        assert plan.done == 2 and plan.failed == 0

        note = plan.steps[0].result["outputs"]["note"]
        assert note == "local-note-2015"

        # Step 2 ran through the OGC bridge with the resolved template.
        ogc_outputs = plan.steps[1].result["outputs"]
        assert ogc_outputs["ogc_process"] == "echo"
        assert ogc_outputs["job_status"] == "successful"
        assert note in str(ogc_outputs["results"])

        # Serialization keeps dependencies.
        data = plan.to_dict()
        assert data["steps"][1]["depends_on"] == [1]
    finally:
        node_handle.stop()
        reg.stop()
        ogc_handle.stop()


def test_ogc_skill_registered_and_advertised() -> None:
    """The OGC bridge skill is registered and discoverable via the registry."""
    ogc = _build_mock_ogc_app()
    ogc_handle = _start_any(ogc, 0)
    ogc_url = f"http://127.0.0.1:{ogc_handle.port}"

    registry = RegistryServer(name="adv-reg")
    reg = _start_any(registry.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg.port}"

    node = GeoNode(name="adv-node", port=0)
    skill = register_ogc_process_skill(node, ogc_url, "echo")
    node_handle = _start_any(node.create_app(), 0)
    node_url = f"http://127.0.0.1:{node_handle.port}"
    node.advertise(reg_url, endpoint=node_url)
    try:
        from geonexus.federation import FederatedGeoMCPClient

        with FederatedGeoMCPClient(reg_url) as client:
            found = client.search_skills(name="echo")
            assert len(found) == 1
            assert found[0]["node_url"] == node_url
            # The skill's geocard carries the OGC interface annotation.
            assert skill.geocard is not None
            assert skill.geocard.interface is not None
            assert skill.geocard.interface.type == "ogcapi-process"
    finally:
        node_handle.stop()
        reg.stop()
        ogc_handle.stop()
