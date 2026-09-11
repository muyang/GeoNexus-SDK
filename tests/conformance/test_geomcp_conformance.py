"""GeoMCP conformance tests (v1.0).

Runs the protocol vectors through the transport-independent
:class:`GeoMCPDispatcher` wired to a stub server — the same envelope
semantics any GeoMCP transport (HTTP, stdio, future MCP bridge) must
preserve.
"""

from __future__ import annotations

import pytest
from geomcp_vectors import CASE_IDS, ENVELOPE_IDS, INVALID_ENVELOPES, REQUESTS

from geonexus.geocard import GeoCardBuilder
from geonexus.geomcp import (
    CONTRACT_NOT_SATISFIED,
    EXECUTION_FAILED,
    GEOCARD_NOT_FOUND,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    SKILL_NOT_FOUND,
    GeoMCPDispatcher,
    GeoMCPProtocolError,
    GeoMCPServer,
)
from geonexus.geomcp.models import ExecuteParams


class _StubEngine:
    """Minimal engine covering the success/error vector cases."""

    def execute(self, params: ExecuteParams) -> dict:
        if params.skill == "boom":
            raise GeoMCPProtocolError(EXECUTION_FAILED, "boom")
        if params.skill != "echo":
            raise GeoMCPProtocolError(SKILL_NOT_FOUND, "no skill")
        return {"status": "ok", "skill": params.skill, "outputs": {"echo": params.params}}


def _make_server() -> GeoMCPServer:
    server = GeoMCPServer(name="conformance", engine=_StubEngine())
    server.register_geocard(
        GeoCardBuilder(
            id="asset-x",
            type="data",
            name="Asset X",
            description="Conformance asset.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .capability("ndvi")
        .build()
    )
    return server


@pytest.mark.parametrize("method,params,request_id,expectation", REQUESTS, ids=CASE_IDS)
def test_geomcp_vectors(method: str, params: dict, request_id: object, expectation: dict) -> None:
    server = _make_server()
    dispatcher = GeoMCPDispatcher(
        capabilities_fn=server._handle_capabilities,
        describe_fn=server._handle_describe,
        execute_fn=server._handle_execute,
        health_fn=server._handle_health,
    )
    response = dispatcher.dispatch(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request_id

    if "error_code" in expectation:
        assert response["error"]["code"] == expectation["error_code"], response
    else:
        assert "error" not in response, response
        result = response["result"]
        for key in expectation["result"]["must_contain"]:
            assert key in result, f"result missing {key}: {result}"


@pytest.mark.parametrize("name,payload", INVALID_ENVELOPES, ids=ENVELOPE_IDS)
def test_geomcp_invalid_envelopes(name: str, payload: dict) -> None:
    dispatcher = GeoMCPDispatcher()
    response = dispatcher.dispatch(payload)
    assert response["error"]["code"] == INVALID_REQUEST, response


def test_geomcp_conformance_error_codes_are_frozen() -> None:
    """The error code registry is frozen for the 1.x line."""
    assert CONTRACT_NOT_SATISFIED == 2000
    assert SKILL_NOT_FOUND == 2001
    assert GEOCARD_NOT_FOUND == 2002
    assert EXECUTION_FAILED == 2003
    assert INVALID_PARAMS == -32602
    assert METHOD_NOT_FOUND == -32601
    assert INVALID_REQUEST == -32600
