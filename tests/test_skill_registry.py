"""Tests for the shared GeoSkill registry (V0.5) and skill-first routing."""

from __future__ import annotations

from test_registry import _start_server

from geonexus.federation import FederatedExecutionError, FederatedGeoMCPClient
from geonexus.geocard import GeoCardBuilder
from geonexus.geonode import GeoNode
from geonexus.registry import RegistryClient, RegistryClientError, RegistryServer


def _ndvi_card(card_id: str = "sentinel-2-amazon"):
    return (
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=card_id,
            description="Test asset.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .capability("ndvi")
        .build()
    )


def _echo_handler(params, context):
    return {"echo": params, "node": context.node_name}


def test_skill_registry_http() -> None:
    """Skills can be registered, listed, fetched and removed over HTTP."""
    server = RegistryServer(name="skill-reg")
    running = _start_server(server.create_app(), 0)
    url = f"http://127.0.0.1:{running.port}"
    try:
        with RegistryClient(url, timeout=10) as client:
            client.register_skill(
                name="ndvi-analysis",
                node_url="http://127.0.0.1:8787",
                description="NDVI demo skill",
                input_schema={"required": ["red", "nir"]},
                capabilities=["ndvi"],
            )
            client.register_skill(
                name="echo",
                node_url="http://127.0.0.1:8788",
                capabilities=[],
            )

            assert len(client.list_skills()) == 2

            got = client.get_skill("ndvi-analysis")
            assert got["node_url"] == "http://127.0.0.1:8787"
            assert got["skill"]["capabilities"] == ["ndvi"]

            by_cap = client.search_skills(capability="ndvi")
            assert [e["skill"]["name"] for e in by_cap] == ["ndvi-analysis"]

            # Duplicate name -> 409.
            try:
                client.register_skill(name="echo", node_url="http://x")
                raise AssertionError("expected duplicate error")
            except RegistryClientError as exc:
                assert exc.code == 409

            client.unregister_skill("echo")
            assert len(client.list_skills()) == 1
    finally:
        running.stop()


def test_advertise_registers_skills() -> None:
    """GeoNode.advertise publishes both cards and skills."""
    registry = RegistryServer(name="adv-reg")
    reg = _start_server(registry.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg.port}"

    node = GeoNode(name="adv-node", port=0)
    node.register_geocard(_ndvi_card())
    node.register_skill(
        name="ndvi-analysis",
        handler=_echo_handler,
        description="NDVI demo skill",
        input_schema={"required": ["red", "nir"]},
        geocard=(
            GeoCardBuilder(
                id="skill-ndvi",
                type="skill",
                name="NDVI",
                description="skill card",
            )
            .capability("ndvi")
            .build()
        ),
    )
    run = _start_server(node.create_app(), 0)
    node_url = f"http://127.0.0.1:{run.port}"
    try:
        node.advertise(reg_url, endpoint=node_url)
        with RegistryClient(reg_url, timeout=10) as client:
            assert len(client.list_cards()) == 1
            skills = client.list_skills()
            assert len(skills) == 1
            assert skills[0]["skill"]["capabilities"] == ["ndvi"]
    finally:
        run.stop()
        reg.stop()


def test_federated_skill_first_routing() -> None:
    """execute_skill routes by skill name to the offering node."""
    registry = RegistryServer(name="fed-skill")
    reg = _start_server(registry.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg.port}"

    node = GeoNode(name="skill-node", port=0)
    node.register_skill(
        name="echo",
        handler=_echo_handler,
        description="Echo skill",
        input_schema={"required": []},
    )
    run = _start_server(node.create_app(), 0)
    node_url = f"http://127.0.0.1:{run.port}"
    node.advertise(reg_url, endpoint=node_url)
    try:
        with FederatedGeoMCPClient(reg_url) as client:
            discovered = client.discover_skills()
            assert discovered["skills_by_node"] == {node_url: ["echo"]}

            found = client.search_skills(name="echo")
            assert len(found) == 1 and found[0]["node_url"] == node_url

            result = client.execute_skill(
                skill="echo",
                params={"hello": "skill-first"},
                request_id="sf-1",
            )
            assert result["status"] == "ok"
            assert result["outputs"]["echo"] == {"hello": "skill-first"}
            assert result["outputs"]["node"] == "skill-node"

            # Unknown skill -> FederatedExecutionError.
            try:
                client.execute_skill(skill="ghost-skill")
                raise AssertionError("expected failure")
            except FederatedExecutionError as exc:
                assert "ghost-skill" in exc.message
    finally:
        run.stop()
        reg.stop()
