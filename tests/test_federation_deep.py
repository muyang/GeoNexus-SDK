"""Tests for GeoNode-to-GeoNode federation (V1.0+): delegation + health."""

from __future__ import annotations

import time

import pytest
from test_registry import _start_server

from geonexus.federation import FederatedExecutionError, FederatedGeoMCPClient
from geonexus.geocard import GeoCardBuilder
from geonexus.geomcp import GeoMCPClient, GeoMCPClientError
from geonexus.geonode import GeoNode, Skill
from geonexus.registry import RegistryClient, RegistryServer


def _card(card_id: str = "asset-a"):
    return (
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=card_id,
            description="Federated test asset.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .capability("ndvi")
        .build()
    )


def _echo_handler(params, context):
    return {"ran_on": context.node_name, "echo": params}


def _node_with_skill(name: str, card: object | None, skill_name: str) -> GeoNode:
    node = GeoNode(name=name, port=0)
    if card is not None:
        node.register_geocard(card)
    node.register_skill_object(
        Skill(
            name=skill_name,
            description="echo",
            input_schema={"required": []},
            handler=_echo_handler,
            geocard=(
                GeoCardBuilder(
                    id=f"skill-{skill_name}",
                    type="skill",
                    name=skill_name,
                    description="echo",
                )
                .capability("echo")
                .build()
            ),
        )
    )
    return node


def _start_registry(health_probe: bool = True) -> tuple[str, object]:
    registry = RegistryServer(name="deep-reg", health_probe=health_probe, health_cache_ttl=1.0)
    handle = _start_server(registry.create_app(), 0)
    return f"http://127.0.0.1:{handle.port}", handle


def test_node_delegation() -> None:
    """A request to a node that owns nothing is delegated to the owner."""
    reg_url, reg = _start_registry()
    node_a = _node_with_skill("data-node-a", _card("asset-a"), "echo")
    a_handle = _start_server(node_a.create_app(), 0)
    a_url = f"http://127.0.0.1:{a_handle.port}"
    node_a.advertise(reg_url, endpoint=a_url)

    node_b = GeoNode(name="relay-node-b", port=0, registry_url=reg_url)
    b_handle = _start_server(node_b.create_app(), 0)
    b_url = f"http://127.0.0.1:{b_handle.port}"
    try:
        with GeoMCPClient(b_url, timeout=15) as client:
            result = client.execute(
                skill="echo",
                geocards=["asset-a"],
                params={"hello": "world"},
                request_id="delegate-1",
            )
        assert result["status"] == "ok"
        # Execution actually happened on node A, relayed through B.
        assert result["outputs"]["ran_on"] == "data-node-a"
        assert result["outputs"]["echo"] == {"hello": "world"}
    finally:
        b_handle.stop()
        a_handle.stop()
        reg.stop()


def test_delegation_loop_guard() -> None:
    """The delegation marker prevents infinite forwarding."""
    reg_url, reg = _start_registry()
    node_b = GeoNode(name="relay-node-b", port=0, registry_url=reg_url)
    b_handle = _start_server(node_b.create_app(), 0)
    b_url = f"http://127.0.0.1:{b_handle.port}"
    try:
        with GeoMCPClient(b_url, timeout=15) as client:
            with pytest.raises(GeoMCPClientError) as exc_info:
                client.execute(
                    skill="echo",
                    geocards=["asset-ghost"],
                    params={"__geonode_delegate": True},  # marker set
                    request_id="loop-guard",
                )
            assert exc_info.value.code == 2002  # GEOCARD_NOT_FOUND, no loop
    finally:
        b_handle.stop()
        reg.stop()


def test_registry_nodes_view_and_health() -> None:
    """/nodes aggregates cards per node and probes health."""
    reg_url, reg = _start_registry(health_probe=True)
    node_a = _node_with_skill("health-a", _card("asset-a"), "echo")
    a_handle = _start_server(node_a.create_app(), 0)
    a_url = f"http://127.0.0.1:{a_handle.port}"
    node_a.advertise(reg_url, endpoint=a_url)
    try:
        with RegistryClient(reg_url, timeout=10) as client:
            view = client.get_nodes()
            assert view["count"] == 1
            assert a_url in view["nodes"]
            assert view["nodes"][a_url]["cards"] == ["asset-a"]
            assert view["nodes"][a_url]["skills"] == ["echo"]
            assert view["nodes"][a_url]["healthy"] is True

            # Stop the node -> probe flips to unhealthy after cache expiry.
            a_handle.stop()
            time.sleep(1.5)
            view = client.get_nodes()
            assert view["nodes"][a_url]["healthy"] is False
    finally:
        a_handle.stop()
        reg.stop()


def test_health_aware_fanout() -> None:
    """The federated client skips unhealthy nodes and refuses all-unhealthy."""
    reg_url, reg = _start_registry(health_probe=True)
    node_a = _node_with_skill("fan-a", _card("asset-a"), "echo")
    a_handle = _start_server(node_a.create_app(), 0)
    a_url = f"http://127.0.0.1:{a_handle.port}"
    node_a.advertise(reg_url, endpoint=a_url)
    node_b = _node_with_skill("fan-b", _card("asset-b"), "echo")
    b_handle = _start_server(node_b.create_app(), 0)
    b_url = f"http://127.0.0.1:{b_handle.port}"
    node_b.advertise(reg_url, endpoint=b_url)
    try:
        with FederatedGeoMCPClient(reg_url) as fed:
            # Both healthy -> both executed.
            fanned = fed.execute(skill="echo", geocards=["asset-a", "asset-b"], params={"x": 1})
            assert len(fanned["results"]) == 2

            # Stop B -> only A is used (single node -> direct result envelope).
            b_handle.stop()
            time.sleep(1.5)
            single = fed.execute(skill="echo", geocards=["asset-a", "asset-b"], params={"x": 2})
            assert single["status"] == "ok"
            assert single["outputs"]["ran_on"] == "fan-a"
            assert single["outputs"]["echo"] == {"x": 2}

            # Only B's card -> all unhealthy -> refused.
            with pytest.raises(FederatedExecutionError, match="unhealthy"):
                fed.execute(skill="echo", geocards=["asset-b"], params={})
    finally:
        a_handle.stop()
        b_handle.stop()
        reg.stop()
