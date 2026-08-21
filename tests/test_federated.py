"""Tests for V0.3 federation: discovery and pushdown execution."""

from __future__ import annotations

from test_registry import _start_server

from geonexus.federation import FederatedExecutionError, FederatedGeoMCPClient
from geonexus.geocard import GeoCardBuilder
from geonexus.geonode import GeoNode
from geonexus.registry import RegistryServer


def _card(card_id: str):
    return (
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=card_id,
            description="Federated test asset.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .capability("ndvi")
        .build()
    )


def _echo_skill_handler(params, context):
    return {"echo": params, "cards": [c.id for c in context.geocards]}


def test_federated_execution() -> None:
    """Requests are routed to the node that owns each card (pushdown)."""
    # Registry
    registry = RegistryServer(name="fed-registry")
    reg = _start_server(registry.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg.port}"

    # Node A: owns asset-a, provides echo + ndvi skills.
    node_a = GeoNode(name="node-a", port=0)
    node_a.register_geocard(_card("asset-a"))
    node_a.register_skill(
        name="echo",
        handler=_echo_skill_handler,
        input_schema={"required": []},
    )
    run_a = _start_server(node_a.create_app(), 0)
    url_a = f"http://127.0.0.1:{run_a.port}"
    node_a.advertise(reg_url, endpoint=url_a)

    # Node B: owns asset-b, provides echo.
    node_b = GeoNode(name="node-b", port=0)
    node_b.register_geocard(_card("asset-b"))
    node_b.register_skill(
        name="echo",
        handler=_echo_skill_handler,
        input_schema={"required": []},
    )
    run_b = _start_server(node_b.create_app(), 0)
    url_b = f"http://127.0.0.1:{run_b.port}"
    node_b.advertise(reg_url, endpoint=url_b)

    try:
        with FederatedGeoMCPClient(reg_url) as client:
            # Discovery: the registry knows both nodes.
            discovered = client.discover()
            assert discovered["nodes"] == {url_a: ["asset-a"], url_b: ["asset-b"]}

            # Contract-gated search finds both.
            hits = client.search(
                capability="ndvi",
                bbox=[-70.0, -10.0, -50.0, 0.0],
                crs="EPSG:4326",
            )
            assert {h["entry"]["card"]["id"] for h in hits} == {"asset-a", "asset-b"}

            # Single-node routing: asset-a -> node A.
            result = client.execute(
                skill="echo",
                geocards=["asset-a"],
                params={"hello": "A"},
                request_id="fed-1",
            )
            assert result["status"] == "ok"
            assert result["outputs"]["cards"] == ["asset-a"]
            assert result["outputs"]["echo"] == {"hello": "A"}

            # Multi-node fan-out: asset-a on A, asset-b on B.
            fanned = client.execute(
                skill="echo",
                geocards=["asset-a", "asset-b"],
                params={"hello": "both"},
                request_id="fed-2",
            )
            assert fanned["status"] == "ok"
            assert len(fanned["results"]) == 2
            by_node = {r["node"]: r for r in fanned["results"]}
            assert set(by_node) == {url_a, url_b}
            assert by_node[url_a]["result"]["outputs"]["cards"] == ["asset-a"]
            assert by_node[url_b]["result"]["outputs"]["cards"] == ["asset-b"]

            # Unknown card -> FederatedExecutionError with details.
            try:
                client.execute(skill="echo", geocards=["ghost-card"])
                raise AssertionError("expected failure")
            except FederatedExecutionError as exc:
                assert "ghost-card" in exc.message
    finally:
        run_b.stop()
        run_a.stop()
        reg.stop()


def test_federated_requires_geocards() -> None:
    registry = RegistryServer(name="fed-registry")
    reg = _start_server(registry.create_app(), 0)
    try:
        with FederatedGeoMCPClient(f"http://127.0.0.1:{reg.port}") as client:
            try:
                client.execute(skill="echo")
                raise AssertionError("expected failure")
            except FederatedExecutionError as exc:
                assert "geocard" in exc.message
    finally:
        reg.stop()
