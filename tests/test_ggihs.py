"""Tests for GGIHS (cross-registry health/catalog aggregation)."""

from __future__ import annotations

import time

import httpx
from test_registry import _start_server

from geonexus.geocard import GeoCardBuilder
from geonexus.geonode import GeoNode, Skill
from geonexus.ggihs import GGIHSService
from geonexus.registry import RegistryServer


def _card(card_id: str):
    return (
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=card_id,
            description="GGIHS test card.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .capability("ndvi")
        .build()
    )


def _node(name: str, card_id: str) -> GeoNode:
    node = GeoNode(name=name, port=0)
    node.register_geocard(_card(card_id))
    node.register_skill_object(
        Skill(
            name=f"skill-{card_id}",
            description="echo",
            input_schema={"required": []},
            handler=lambda params, context: {"ran_on": context.node_name},
            geocard=(
                GeoCardBuilder(
                    id=f"skill-{card_id}",
                    type="skill",
                    name="echo",
                    description="echo",
                )
                .capability("echo")
                .build()
            ),
        )
    )
    return node


def _advertise(node: GeoNode, reg_url: str) -> tuple[str, object]:
    handle = _start_server(node.create_app(), 0)
    url = f"http://127.0.0.1:{handle.port}"
    node.advertise(reg_url, endpoint=url)
    return url, handle


def test_ggihs_aggregates_two_registries() -> None:
    """GGIHS merges nodes/catalog across two registries."""
    reg_a = RegistryServer(name="a", health_probe=True, health_cache_ttl=1.0)
    a_handle = _start_server(reg_a.create_app(), 0)
    a_url = f"http://127.0.0.1:{a_handle.port}"
    reg_b = RegistryServer(name="b", health_probe=True, health_cache_ttl=1.0)
    b_handle = _start_server(reg_b.create_app(), 0)
    b_url = f"http://127.0.0.1:{b_handle.port}"

    node_a = _node("agg-a", "asset-a")
    a_node_url, a_node_handle = _advertise(node_a, a_url)
    node_b = _node("agg-b", "asset-b")
    b_node_url, b_node_handle = _advertise(node_b, b_url)
    try:
        service = GGIHSService([a_url, b_url])
        summary = service.summary()
        assert summary["nodes"] == 2
        assert summary["nodes_healthy"] == 2
        assert summary["cards"] == 2
        assert summary["skills"] == 2

        catalog = service.catalog()
        ids = {c["id"] for c in catalog["cards"]}
        assert ids == {"asset-a", "asset-b"}
        assert catalog["counts"]["cards"] == 2

        nodes = service.nodes()
        assert a_node_url in nodes["nodes"]
        assert nodes["nodes"][a_node_url]["cards"] == ["asset-a"]
    finally:
        b_node_handle.stop()
        a_node_handle.stop()
        b_handle.stop()
        a_handle.stop()


def test_ggihs_health_flip() -> None:
    """A stopped node flips to unhealthy; its catalog entry survives."""
    reg = RegistryServer(name="r", health_probe=True, health_cache_ttl=1.0)
    reg_handle = _start_server(reg.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg_handle.port}"

    node = _node("flip-node", "asset-x")
    node_url, node_handle = _advertise(node, reg_url)
    try:
        service = GGIHSService([reg_url])
        assert service.summary()["nodes_healthy"] == 1

        node_handle.stop()
        time.sleep(1.5)
        summary = service.summary()
        assert summary["nodes_unhealthy"] == 1
        assert summary["nodes_healthy"] == 0

        catalog = service.catalog()
        entry = next(c for c in catalog["cards"] if c["id"] == "asset-x")
        assert entry["healthy"] is False
        assert entry["node_url"] == node_url
    finally:
        node_handle.stop()
        reg_handle.stop()


def test_ggihs_live_probe() -> None:
    """live_probe=True probes node /health directly."""
    reg = RegistryServer(name="r", health_cache_ttl=0.5)
    reg_handle = _start_server(reg.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg_handle.port}"

    node = _node("live-node", "asset-l")
    node_url, node_handle = _advertise(node, reg_url)
    try:
        service = GGIHSService([reg_url], live_probe=True, probe_timeout=2.0)
        assert service.summary()["nodes_healthy"] == 1
        node_handle.stop()
        time.sleep(0.6)
        assert service.summary()["nodes_unhealthy"] == 1
    finally:
        node_handle.stop()
        reg_handle.stop()


def test_ggihs_resilient_to_dead_registry() -> None:
    """A dead registry is recorded; the other registry is still served."""
    reg = RegistryServer(name="ok", health_probe=True, health_cache_ttl=1.0)
    reg_handle = _start_server(reg.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg_handle.port}"

    node = _node("ok-node", "asset-ok")
    node_url, node_handle = _advertise(node, reg_url)
    try:
        service = GGIHSService([reg_url, "http://127.0.0.1:1"])
        data = service.collect()
        assert data["errors"] != {}
        assert data["registries"][reg_url] == "ok"
        summary = service.summary()
        assert summary["nodes"] == 1
        assert summary["cards"] == 1
        assert summary["registries_error"] == 1
    finally:
        node_handle.stop()
        reg_handle.stop()


def test_ggihs_http_endpoints() -> None:
    """/summary, /nodes, /catalog, /health respond over HTTP."""
    reg = RegistryServer(name="h", health_probe=True, health_cache_ttl=1.0)
    reg_handle = _start_server(reg.create_app(), 0)
    reg_url = f"http://127.0.0.1:{reg_handle.port}"

    node = _node("http-node", "asset-h")
    node_url, node_handle = _advertise(node, reg_url)
    service = GGIHSService([reg_url])
    g_handle = _start_server(service.create_app(), 0)
    g_url = f"http://127.0.0.1:{g_handle.port}"
    try:
        with httpx.Client(base_url=g_url, timeout=15) as client:
            assert client.get("/health").json()["status"] == "ok"
            summary = client.get("/summary").json()
            assert summary["nodes"] == 1
            nodes = client.get("/nodes").json()
            assert nodes["nodes"][node_url]["cards"] == ["asset-h"]
            catalog = client.get("/catalog").json()
            assert catalog["counts"]["cards"] == 1

            # Dashboard page is served and embeds the live data hooks.
            dashboard = client.get("/").text
            assert "<title>GGIHS" in dashboard
            assert "federation dashboard" in dashboard
            assert 'id="cards"' in dashboard
            assert 'id="nodes"' in dashboard
            assert 'id="catalog"' in dashboard
            assert "refresh()" in dashboard  # vanilla JS data hook
    finally:
        g_handle.stop()
        node_handle.stop()
        reg_handle.stop()
