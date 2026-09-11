"""Tests for the official MCP SDK adapter (V0.5).

The heavy test talks to ``geonexus mcp run`` over stdio using the MCP
JSON-RPC framing (newline-delimited JSON) — exactly what an MCP client
would do.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


def _rpc(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def _read_line(proc: subprocess.Popen) -> dict:
    line = proc.stdout.readline()
    assert line, "MCP server closed stdout unexpectedly"
    return json.loads(line)


def test_mcp_stdio_bridge() -> None:
    """initialize -> tools/list -> tools/call(geo_health) over stdio."""
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "geonexus.cli", "mcp", "run", "--bare"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=str(REPO_ROOT),
    )
    try:
        # 1. initialize
        proc.stdin.write(
            _rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "geonodex-test", "version": "0.0.1"},
                    },
                }
            )
        )
        proc.stdin.flush()
        init = _read_line(proc)
        assert init["id"] == 1
        assert "serverInfo" in init["result"]
        assert init["result"]["serverInfo"]["name"] == "geonexus"
        protocol_version = init["result"].get("protocolVersion", "2025-06-18")

        # 2. initialized notification (no response expected)
        proc.stdin.write(_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        proc.stdin.flush()

        # 3. tools/list
        proc.stdin.write(
            _rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {"protocolVersion": protocol_version},
                }
            )
        )
        proc.stdin.flush()
        tools_resp = _read_line(proc)
        tools = {t["name"]: t for t in tools_resp["result"]["tools"]}
        assert {"geo_capabilities", "geo_describe", "geo_execute", "geo_health"} <= set(tools)
        assert "spatial" in json.dumps(tools["geo_execute"])

        # 4. tools/call geo_health
        proc.stdin.write(
            _rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "geo_health",
                        "arguments": {},
                        "protocolVersion": protocol_version,
                    },
                }
            )
        )
        proc.stdin.flush()
        call_resp = _read_line(proc)
        assert call_resp["id"] == 3
        content = call_resp["result"]["content"][0]["text"]
        health = json.loads(content)
        assert health["status"] == "ok"

        # 5. tools/call geo_capabilities (bare node: no skills but methods list)
        proc.stdin.write(
            _rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "geo_capabilities",
                        "arguments": {},
                        "protocolVersion": protocol_version,
                    },
                }
            )
        )
        proc.stdin.flush()
        caps_resp = _read_line(proc)
        caps = json.loads(caps_resp["result"]["content"][0]["text"])
        assert "geo.execute" in caps["methods"]
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


def test_create_mcp_server_tools() -> None:
    """create_mcp_server exposes exactly the four GeoMCP tools."""
    import asyncio

    from geonexus.geocard import GeoCardBuilder
    from geonexus.geomcp import GeoMCPServer
    from geonexus.mcp_adapter import create_mcp_server

    server = GeoMCPServer(name="unit-test")
    server.register_geocard(GeoCardBuilder(id="a", type="data", name="A", description="d").build())
    mcp_server = create_mcp_server(server)
    assert mcp_server.name == "geonexus"
    tool_names = {t.name for t in asyncio.run(mcp_server.list_tools())}
    assert tool_names == {"geo_capabilities", "geo_describe", "geo_execute", "geo_health"}


# --------------------------------------------------------------------------- #
# V1.0: Streamable HTTP transport
# --------------------------------------------------------------------------- #
def _sse_payload(text: str) -> dict:
    """Extract the JSON payload of the first `message` event in SSE text."""
    for block in text.split("\n\n"):
        data_lines = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
        if data_lines:
            return json.loads("\n".join(data_lines))
    raise AssertionError(f"no SSE message event in: {text[:200]!r}")


def _start_http_app(app):
    import threading
    import time

    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise TimeoutError("http app not ready")

    class _Handle:
        @property
        def port(self) -> int:
            return server.servers[0].sockets[0].getsockname()[1]

        def stop(self) -> None:
            server.should_exit = True
            thread.join(timeout=10)

    return _Handle()


def test_mcp_streamable_http() -> None:
    """initialize -> tools/list -> tools/call over Streamable HTTP."""
    import httpx

    from geonexus.geomcp import GeoMCPServer
    from geonexus.mcp_adapter import create_streamable_http_app

    app = create_streamable_http_app(GeoMCPServer(name="http-test"))
    handle = _start_http_app(app)
    base = f"http://127.0.0.1:{handle.port}"
    try:
        with httpx.Client(base_url=base, timeout=15) as client:
            # 1. initialize -> SSE response + session id
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "http-test-client", "version": "1"},
                    },
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                },
            )
            assert resp.status_code == 200
            session_id = resp.headers.get("mcp-session-id")
            assert session_id
            init = _sse_payload(resp.text)
            assert init["id"] == 1
            assert init["result"]["serverInfo"]["name"] == "geonexus"

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
                "MCP-Session-Id": session_id,
            }

            # 2. notifications/initialized (no response expected)
            client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )

            # 3. tools/list
            resp = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=headers,
            )
            tools = {t["name"] for t in _sse_payload(resp.text)["result"]["tools"]}
            assert {"geo_capabilities", "geo_describe", "geo_execute", "geo_health"} <= tools

            # 4. tools/call geo_health
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "geo_health", "arguments": {}},
                },
                headers=headers,
            )
            call = _sse_payload(resp.text)
            health = json.loads(call["result"]["content"][0]["text"])
            assert health["status"] == "ok"
            assert health["node"] == "http-test"
    finally:
        handle.stop()
