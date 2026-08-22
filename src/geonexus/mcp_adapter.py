"""Official MCP SDK adapter (V0.5).

Exposes a GeoMCP server's ``geo.*`` methods as **Model Context Protocol**
tools using the official ``mcp`` Python SDK. An MCP client (Claude Desktop,
any MCP host, or the stdio smoke test) can then drive GeoNexus through the
standard MCP interface.

Transport for the MVP: **stdio** (``geonexus mcp run``).
"""

from __future__ import annotations

import logging
from typing import Any

from . import __version__
from .geomcp.models import DescribeParams, ExecuteParams, SpatialContext, TemporalContext
from .geomcp.server import GeoMCPServer

logger = logging.getLogger(__name__)

# Protocol versions the official SDK understands; advertise a recent one.
DEFAULT_MCP_PROTOCOL_VERSION = "2025-06-18"


def create_mcp_server(server: GeoMCPServer) -> Any:
    """Build an MCP server (``mcp.server.mcpserver.MCPServer``) wrapping a
    GeoMCP server's four methods as MCP tools."""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ImportError(
            "The MCP bridge requires the official MCP SDK. Install it with "
            "`pip install -e '.[mcp]'` or `pip install 'mcp>=1.2'`."
        ) from exc

    mcp = MCPServer(
        name="geonexus",
        title=f"GeoNexus GeoMCP bridge ({server.name})",
        version=__version__,
        instructions=(
            "GeoNexus geospatial interaction protocol over MCP. "
            "Use geo_execute to run a skill on the node that owns the data."
        ),
    )

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
    ) -> dict[str, Any]:
        return server.describe(DescribeParams(geocards=geocards, skills=skills))

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
    ) -> dict[str, Any]:
        return server.execute(
            ExecuteParams(
                skill=skill,
                geocards=geocards or [],
                spatial=SpatialContext(**spatial) if spatial else None,
                temporal=TemporalContext(**temporal) if temporal else None,
                params=params or {},
                request_id=request_id,
            )
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
    mcp = create_mcp_server(server)
    return mcp.streamable_http_app()


def run_http(
    server: GeoMCPServer,
    host: str = "127.0.0.1",
    port: int = 9000,
    path: str = "/mcp",
) -> None:
    """Run the MCP adapter over Streamable HTTP (blocking)."""
    import asyncio

    mcp = create_mcp_server(server)
    logger.info(
        "Starting MCP Streamable HTTP bridge for GeoMCP server '%s' on http://%s:%s%s",
        server.name,
        host,
        port,
        path,
    )
    asyncio.run(mcp.run_streamable_http_async(host=host, port=port, streamable_http_path=path))
