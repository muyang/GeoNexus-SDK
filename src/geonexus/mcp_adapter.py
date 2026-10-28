"""Official MCP SDK adapter (V0.5).

Exposes a GeoMCP server's ``geo.*`` methods as **Model Context Protocol**
tools using the official ``mcp`` Python SDK. An MCP client (Claude Desktop,
any MCP host, or the stdio smoke test) can then drive GeoNexus through the
standard MCP interface.

Transport for the MVP: **stdio** (``geonexus mcp run``).
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
from typing import Any

from . import __version__
from .geomcp.models import (
    DescribeParams,
    ExecuteParams,
    InvokeParams,
    PlanParams,
    SpatialContext,
    TemporalContext,
)
from .geomcp.server import GeoMCPServer

logger = logging.getLogger(__name__)

# Protocol versions the official SDK understands; advertise a recent one.
DEFAULT_MCP_PROTOCOL_VERSION = "2025-06-18"


def create_mcp_server(server: GeoMCPServer, *, http_guarded: bool = False) -> Any:
    """Build an MCP server (``mcp.server.mcpserver.MCPServer``) wrapping a
    GeoMCP server's methods as MCP tools."""
    try:
        try:
            MCPServer = importlib.import_module("mcp.server.mcpserver").MCPServer
        except ImportError:
            MCPServer = importlib.import_module("mcp.server.fastmcp").FastMCP
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ImportError(
            "The MCP bridge requires the official MCP SDK. Install it with "
            "`pip install -e '.[mcp]'` or `pip install 'mcp>=1.2'`."
        ) from exc

    options = dict(
        name="geonexus",
        title=f"GeoNexus GeoMCP bridge ({server.name})",
        version=__version__,
        instructions=(
            "GeoNexus geospatial interaction protocol over MCP. "
            "Use geo_plan to see what a reusable recipe would do (and which of its "
            "parameters you may change) before spending compute, then geo_execute "
            "to run a skill on the node that owns the data."
        ),
    )

    supported = inspect.signature(MCPServer).parameters
    mcp = MCPServer(**{k: v for k, v in options.items() if k in supported})

    def require_execution_key() -> None:
        if (
            server.api_keys
            and not http_guarded
            and os.environ.get("GEONEXUS_NODE_API_KEY") not in server.api_keys
        ):
            raise PermissionError("A valid GEONEXUS_NODE_API_KEY is required for stdio execution.")

    @mcp.tool(description="List the GeoMCP protocol surface (methods, skills, tools, geocards).")
    async def geo_capabilities() -> dict[str, Any]:
        return server.capabilities()

    @mcp.tool(
        description="Describe registered GeoCards and skills. "
        "Pass geocards and/or skills as lists of ids (None = all)."
    )
    async def geo_describe(
        geocards: list[str] | None = None,
        skills: list[str] | None = None,
        recipes: list[str] | None = None,
    ) -> dict[str, Any]:
        return server.describe(DescribeParams(geocards=geocards, skills=skills, recipes=recipes))

    @mcp.tool(
        description=(
            "Execute a GeoSkill on the node that owns the data. "
            "skill: name; geocards: card ids the request references; "
            "spatial: {bbox: [w,s,e,n], crs}; temporal: {start, end}; "
            "params: skill-specific arguments (e.g. red/nir raster paths)."
        )
    )
    async def geo_execute(
        skill: str,
        geocards: list[str] | None = None,
        spatial: dict[str, Any] | None = None,
        temporal: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        contract_binding: str | None = None,
        pushdown_spec: dict[str, Any] | None = None,
        federated_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        require_execution_key()
        return server.execute(
            ExecuteParams(
                skill=skill,
                geocards=geocards or [],
                spatial=SpatialContext(**spatial) if spatial else None,
                temporal=TemporalContext(**temporal) if temporal else None,
                params=params or {},
                request_id=request_id,
                contract_binding=contract_binding,
                pushdown_spec=pushdown_spec,
                federated_sources=federated_sources,
            )
        )

    @mcp.tool(description="Invoke a model GeoCard through its compute-gated model backend.")
    async def geo_invoke(
        model: str,
        inputs: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        require_execution_key()
        return server.invoke(
            InvokeParams(model=model, inputs=inputs or {}, params=params or {}, timeout=timeout)
        )

    @mcp.tool(
        description=(
            "Materialise a reusable recipe into a run WITHOUT executing it: returns "
            "the tasks (step, kind, uses, depends_on, resolved params) and the "
            "deliverables, plus the recipe's parameter contract. recipe: address "
            "such as 'recipe://ns/name@1.0.0' (version optional); params: values to "
            "validate; run_id: identity for this run. Read-only."
        )
    )
    async def geo_plan(
        recipe: str,
        params: dict[str, Any] | None = None,
        run_id: str = "run",
        resolve: bool = False,
    ) -> dict[str, Any]:
        return server.plan(
            PlanParams(recipe=recipe, params=params or {}, run_id=run_id, resolve=resolve)
        )

    @mcp.tool(description="GeoMCP health check (node status, version, time).")
    async def geo_health() -> dict[str, Any]:
        return server.health()

    return mcp


def run_stdio(server: GeoMCPServer) -> None:
    """Run the MCP adapter over stdio (blocking)."""
    import asyncio

    mcp = create_mcp_server(server)
    logger.info("Starting MCP stdio bridge for GeoMCP server '%s'", server.name)
    asyncio.run(mcp.run_stdio_async())


def create_streamable_http_app(server: GeoMCPServer) -> Any:
    """Build the MCP Streamable HTTP app (for serving under uvicorn)."""
    mcp = create_mcp_server(server, http_guarded=True)
    return _ExecutionAuthApp(mcp.streamable_http_app(), server.api_keys)


def run_http(
    server: GeoMCPServer,
    host: str = "127.0.0.1",
    port: int = 9000,
    path: str = "/mcp",
) -> None:
    """Run the MCP adapter over Streamable HTTP (blocking)."""
    import uvicorn

    logger.info(
        "Starting MCP Streamable HTTP bridge for GeoMCP server '%s' on http://%s:%s%s",
        server.name,
        host,
        port,
        path,
    )
    mcp = create_mcp_server(server, http_guarded=True)
    app = _ExecutionAuthApp(
        mcp.streamable_http_app(streamable_http_path=path, host=host), server.api_keys
    )
    uvicorn.run(app, host=host, port=port)


class _ExecutionAuthApp:
    """Apply the node's write credentials before any MCP dispatch.

    Discovery stays public. Lifespan and streaming responses pass through.
    The wrapper consumes at most 4 MiB and replays the body exactly once.
    """

    def __init__(self, app: Any, api_keys: set[str]) -> None:
        self.app, self.api_keys = app, api_keys

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not self.api_keys:
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                await self._reject(send, 413, "Request body too large")
                return
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {}
        messages = payload if isinstance(payload, list) else [payload]
        writes = any(
            isinstance(item, dict)
            and item.get("method") == "tools/call"
            and isinstance(item.get("params"), dict)
            and item["params"].get("name") in {"geo_execute", "geo_invoke"}
            for item in messages
        )
        headers = dict(scope.get("headers", []))
        key = headers.get(b"x-api-key", b"").decode()
        if writes and key not in self.api_keys:
            await self._reject(send, 401, "Missing or invalid node execution key")
            return
        sent = False

        async def replay() -> Any:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send: Any, status: int, reason: str) -> None:
        body = json.dumps({"error": reason}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
