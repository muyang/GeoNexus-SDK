"""Tests for the GeoMCP protocol layer and server/client."""

from __future__ import annotations

import pytest

from geonexus.geocard import GeoCardBuilder
from geonexus.geomcp import (
    METHOD_NOT_FOUND,
    GeoMCPClient,
    GeoMCPClientError,
    GeoMCPProtocolError,
    GeoMCPServer,
    build_request,
    make_request_id,
    parse_request,
)
from geonexus.geomcp.models import ExecuteParams
from geonexus.geonode import GeoNode


def _demo_card():
    return (
        GeoCardBuilder(
            id="test-asset",
            type="data",
            name="Test Asset",
            description="A test asset.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .band("B04")
        .build()
    )


def test_geomcp_request() -> None:
    """Request envelopes build and parse correctly (JSON-RPC 2.0)."""
    payload = build_request(
        "geo.execute",
        {"skill": "ndvi-analysis", "geocards": ["sentinel-2-amazon"]},
        id="task-001",
    )
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == "task-001"
    assert payload["method"] == "geo.execute"

    request = parse_request(payload)
    assert request.method == "geo.execute"
    assert request.params == {
        "skill": "ndvi-analysis",
        "geocards": ["sentinel-2-amazon"],
    }

    # Auto-generated ids are unique.
    assert make_request_id() != make_request_id()

    # Malformed envelopes are rejected.
    with pytest.raises(GeoMCPProtocolError):
        parse_request({"jsonrpc": "1.0", "method": "geo.execute"})
    with pytest.raises(GeoMCPProtocolError):
        parse_request({"jsonrpc": "2.0"})
    with pytest.raises(GeoMCPProtocolError):
        parse_request({"jsonrpc": "2.0", "method": "geo.execute", "params": [1, 2]})


class _EchoEngine:
    """Minimal ExecutionEngine that echoes the request."""

    def execute(self, params: ExecuteParams) -> dict:
        return {
            "status": "ok",
            "skill": params.skill,
            "outputs": {"echo": params.params, "geocards": params.geocards},
        }


def test_geomcp_response() -> None:
    """The dispatcher returns well-formed success and error responses."""
    server = GeoMCPServer(name="test", engine=_EchoEngine())

    response = server._dispatcher.dispatch(
        build_request("geo.execute", {"skill": "echo", "params": {"a": 1}}, id="r1")
    )
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "r1"
    assert response["result"]["status"] == "ok"
    assert response["result"]["outputs"]["echo"] == {"a": 1}

    # Unknown method -> METHOD_NOT_FOUND.
    response = server._dispatcher.dispatch(build_request("geo.not-a-method", {}, id="r2"))
    assert response["error"]["code"] == METHOD_NOT_FOUND

    # Invalid envelope -> INVALID_REQUEST (-32600) with id None.
    response = server._dispatcher.dispatch({"jsonrpc": "2.0", "id": 5})
    assert response["error"]["code"] == -32600

    # Capabilities / health responses.
    caps = server._dispatcher.dispatch(build_request("geo.capabilities", {}, id="r3"))
    assert "methods" in caps["result"]
    health = server._dispatcher.dispatch(build_request("geo.health", {}, id="r4"))
    assert health["result"]["status"] == "ok"


def _start_node(tmp_path) -> tuple[GeoNode, object]:
    node = GeoNode(name="test-node", port=0, workdir=str(tmp_path))
    node.register_geocard(_demo_card())
    node.register_skill(
        name="echo",
        handler=lambda params, context: {"echo": params, "cards": [c.id for c in context.geocards]},
        input_schema={"required": []},
    )
    running = node.start_in_thread(port=0)
    running.wait_until_ready()
    return node, running


def test_geomcp_over_http(tmp_path) -> None:
    """The full HTTP round trip works via the GeoMCP client."""
    _, running = _start_node(tmp_path)
    port = running.port
    try:
        with GeoMCPClient(f"http://127.0.0.1:{port}", timeout=10) as client:
            assert client.health()["status"] == "ok"

            caps = client.capabilities()
            assert "geo.execute" in caps["methods"]
            assert caps["node"] == "test-node"
            assert "test-asset" in caps["geocards"]
            assert any(s["name"] == "echo" for s in caps["skills"])

            described = client.describe(geocards=["test-asset"])
            assert described["geocards"][0]["id"] == "test-asset"

            result = client.execute(
                skill="echo",
                geocards=["test-asset"],
                params={"hello": "world"},
                request_id="http-1",
            )
            assert result["status"] == "ok"
            assert result["outputs"]["echo"] == {"hello": "world"}
            assert result["outputs"]["cards"] == ["test-asset"]

            # Unknown skill -> application error code 2001.
            with pytest.raises(GeoMCPClientError) as exc_info:
                client.execute(skill="does-not-exist")
            assert exc_info.value.code == 2001
    finally:
        running.stop()


def test_geomcp_contract_gate(tmp_path) -> None:
    """A request outside the GeoCard contract is refused (code 2000)."""
    _, running = _start_node(tmp_path)
    port = running.port
    try:
        with GeoMCPClient(f"http://127.0.0.1:{port}", timeout=10) as client:
            with pytest.raises(GeoMCPClientError) as exc_info:
                client.execute(
                    skill="echo",
                    geocards=["test-asset"],
                    spatial={"bbox": [100.0, 100.0, 120.0, 120.0], "crs": "EPSG:4326"},
                )
            assert exc_info.value.code == 2000
            assert "contract" in exc_info.value.message.lower()
    finally:
        running.stop()
