"""Tests for V0.3 federation: discovery and pushdown execution."""

from __future__ import annotations

from test_registry import _start_server

from geonexus.federation import FederatedExecutionError, FederatedGeoMCPClient
from geonexus.geocard import GeoCardBuilder
from geonexus.geonode import GeoNode
from geonexus.registry import RegistryClientError, RegistryServer


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


def test_advertise_against_authenticated_registry() -> None:
    """`advertise()` 必须能把 registry 的 API key 传下去。

    注册是写操作，registry 用 `--api-key` 启动时会在写接口上强制校验。
    之前 `advertise()` 不接受 api_key，于是对着带鉴权的 registry 广告必然
    401，并且会把调用方（ExecutionPlane 的启动流程）整个打断。
    """
    registry = RegistryServer(name="auth-registry", api_keys={"reg-key"})
    reg = _start_server(registry.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg.port}"

    node = GeoNode(name="auth-node", port=0)
    node.register_geocard(_card("auth-asset"))
    node.register_skill(name="echo", handler=_echo_skill_handler,
                        input_schema={"required": []})

    try:
        # 不带 key：写被拒
        try:
            node.advertise(reg_url, endpoint="http://127.0.0.1:9999")
            raise AssertionError("expected 401 without api_key")
        except RegistryClientError as exc:
            assert exc.code == 401, exc

        # 带对 key：注册成功
        node.advertise(reg_url, endpoint="http://127.0.0.1:9999", api_key="reg-key")
        assert registry.store.count() == 1
        assert {e.skill.name for e in registry.store.list_skills()} == {"echo"}
    finally:
        reg.stop()
