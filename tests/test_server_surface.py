"""Tests for the GeoMCPServer tool/resource surface and contract gating."""

from __future__ import annotations

import pytest

from geonexus.geocard import GeoCardBuilder
from geonexus.geomcp import (
    CONTRACT_NOT_SATISFIED,
    GEOCARD_NOT_FOUND,
    SKILL_NOT_FOUND,
    GeoMCPProtocolError,
    GeoMCPServer,
)
from geonexus.geomcp.models import DescribeParams, ExecuteParams


def _card(card_id: str = "asset-x"):
    return (
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=card_id,
            description="Test card.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .capability("ndvi")
        .build()
    )


def _tool_handler(params: dict) -> dict:
    return {"echo": params.get("value")}


def test_server_tools_and_resources() -> None:
    """register_tool / register_resource advertise and execute."""
    server = GeoMCPServer(name="tool-server")
    server.register_tool(
        name="echo",
        handler=_tool_handler,
        description="Echo a value",
        input_schema={"properties": {"value": {"type": "string"}}},
    )
    server.register_resource(name="demo-resource", kind="reference")

    caps = server.capabilities()
    assert any(t["name"] == "echo" for t in caps["tools"])
    assert "demo-resource" in caps["resources"]

    result = server.execute(ExecuteParams(skill="echo", params={"value": "hello"}))
    assert result["outputs"]["echo"] == "hello"

    # Unknown skill -> SKILL_NOT_FOUND (2001).
    with pytest.raises(GeoMCPProtocolError) as exc_info:
        server.execute(ExecuteParams(skill="nope"))
    assert exc_info.value.code == SKILL_NOT_FOUND


def test_server_contract_gate_and_missing_cards() -> None:
    """Contract failures and unknown geocards map to protocol errors."""
    server = GeoMCPServer(name="gate-server")
    server.register_geocard(_card())

    # Unknown geocard -> GEOCARD_NOT_FOUND (2002).
    with pytest.raises(GeoMCPProtocolError) as exc_info:
        server.execute(ExecuteParams(skill="anything", geocards=["ghost"]))
    assert exc_info.value.code == GEOCARD_NOT_FOUND

    # Out-of-contract request -> CONTRACT_NOT_SATISFIED (2000).
    with pytest.raises(GeoMCPProtocolError) as exc_info:
        server.execute(
            ExecuteParams(
                skill="anything",
                geocards=["asset-x"],
                spatial={"bbox": [100.0, 100.0, 120.0, 120.0], "crs": "EPSG:4326"},
            )
        )
    assert exc_info.value.code == CONTRACT_NOT_SATISFIED
    assert any("Spatial mismatch" in r for r in exc_info.value.data["reasons"])

    # describe with a missing geocard id -> GEOCARD_NOT_FOUND.
    with pytest.raises(GeoMCPProtocolError) as exc_info:
        server.describe(DescribeParams(geocards=["missing-card"]))
    assert exc_info.value.code == GEOCARD_NOT_FOUND


def test_server_health_and_describe() -> None:
    server = GeoMCPServer(name="health-server")
    server.register_geocard(_card("asset-a"))

    health = server.health()
    assert health["status"] == "ok"
    assert health["node"] == "health-server"

    described = server.describe()
    assert [c["id"] for c in described["geocards"]] == ["asset-a"]

    # geo.capabilities via dispatcher includes the internal card registry.
    caps = server._dispatcher.dispatch(
        {"jsonrpc": "2.0", "id": "c1", "method": "geo.capabilities", "params": {}}
    )
    assert "asset-a" in caps["result"]["geocards"]
