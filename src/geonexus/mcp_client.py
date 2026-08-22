"""MCP client bridge (v1.1).

The **client side** of Model Context Protocol for GeoNexus: connect to an
*external* MCP server (stdio or Streamable HTTP), enumerate its tools, and
register them as GeoSkills on a GeoNode / into a skill registry, so the
deterministic planner and executor can use them like any local skill.

Together with the existing server-side adapter (:mod:`geonexus.mcp_adapter`),
this makes GeoNexus both an MCP host *and* an MCP client:

- server side: GeoMCP ``geo.*`` methods exposed as MCP tools (``mcp serve``).
- client side (this module): external MCP tools imported as GeoSkills.

Typical usage::

    with MCPToolClient.stdio("npx", "-y", "some-mcp-server") as mcp:
        skills = mcp.to_skills(prefix="ext")
        node.register_skills(skills)   # GeoSkills now call the MCP tool

MCP is async; skill handlers are sync. Each tool call opens its own
``ClientSession`` lifecycle (safe for the concurrent plan worker threads),
bridged with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .geonode.skill import Skill

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard for environments without the SDK
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamable_http_client
except ImportError:  # pragma: no cover
    ClientSession = None  # type: ignore[assignment,misc]
    StdioServerParameters = None  # type: ignore[assignment,misc]
    stdio_client = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]


class MCPClientError(Exception):
    """Raised for connection, enumeration or invocation failures."""


def _require_sdk() -> None:
    if ClientSession is None:
        raise MCPClientError(
            "The MCP client bridge requires the official MCP SDK. Install it "
            "with `pip install -e '.[mcp]'` or `pip install 'mcp>=1.2'`."
        )


def _as_plain(value: Any) -> Any:
    """Convert MCP structured values into plain JSON-ish values."""
    if isinstance(value, list):
        return [_as_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _as_plain(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(exclude_none=True)
        except Exception:  # noqa: BLE001 - best-effort conversion
            return str(value)
    return value


# A session factory: async context manager yielding a connected ClientSession.
SessionFactory = Callable[[], Any]


def _make_session_factory(
    *,
    stdio_params: StdioServerParameters | None = None,
    http_url: str | None = None,
    http_headers: dict[str, str] | None = None,
) -> SessionFactory:
    """Build an async context manager that yields an initialised session.

    Each entry creates a fresh client + session (connection-per-call), which
    is safe for the concurrent plan worker threads.
    """
    from contextlib import asynccontextmanager

    _require_sdk()
    session_cls = ClientSession
    assert session_cls is not None  # _require_sdk guarantees it

    if stdio_params is not None:

        @asynccontextmanager
        async def _session() -> Any:
            async with stdio_client(stdio_params) as (read, write), session_cls(
                read, write
            ) as session:
                await session.initialize()
                yield session

    else:
        url = http_url
        assert url is not None, "http client requires a URL"

        @asynccontextmanager
        async def _session() -> Any:
            # headers ride on a custom httpx client (the SDK's
            # streamable_http_client takes no headers argument).
            import httpx

            http_client: Any = httpx.AsyncClient(headers=http_headers or {})
            async with streamable_http_client(
                url,
                http_client=http_client,
            ) as (read, write), session_cls(read, write) as session:
                await session.initialize()
                yield session

    return _session


@dataclass
class MCPTool:
    """A tool advertised by an external MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    _call: Callable[[dict[str, Any]], dict[str, Any]] | None = field(
        default=None, repr=False
    )

    def to_skill(self, prefix: str = "") -> Skill:
        """Wrap this MCP tool as a GeoSkill.

        The handler forwards the call to the MCP server and returns the
        tool result as ``{"outputs": {...}}``.
        """
        assert self._call is not None, "MCPTool requires a call function"
        effective_name = f"{prefix}{self.name}" if prefix else self.name
        call = self._call

        def _handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
            result = call(params)
            return {"outputs": result, "mcp_tool": self.name}

        skill = Skill(
            name=effective_name,
            description=self.description or f"MCP tool {self.name}",
            input_schema=self.input_schema or {"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
            handler=_handler,
            mcp_source=f"mcp:{self.name}",
        )
        return skill


@dataclass
class MCPToolClient:
    """Connect to one external MCP server and expose its tools.

    Args:
        name: Human-readable name (for logging / skill description).
        _session_factory: Async context manager producing a connected
            ``ClientSession`` (built by the class factories).
        timeout: Per-call timeout in seconds.
    """

    name: str
    _session_factory: SessionFactory = field(repr=False)
    timeout: float = 120.0

    # ------------------------------------------------------------------ #
    # Factories
    # ------------------------------------------------------------------ #
    @classmethod
    def stdio(
        cls,
        command: str,
        *args: str,
        env: dict[str, str] | None = None,
        name: str | None = None,
        timeout: float = 120.0,
    ) -> MCPToolClient:
        """Connect to an MCP server launched over stdio.

        Args:
            command: Executable (e.g. ``npx``, ``uvx``, a local binary).
            *args: Command-line arguments (e.g. ``-y``, ``package``).
            env: Extra environment variables for the subprocess.
        """
        _require_sdk()
        factory = _make_session_factory(
            stdio_params=StdioServerParameters(command=command, args=list(args), env=env)
        )
        return cls(name=name or f"stdio:{command}", _session_factory=factory, timeout=timeout)

    @classmethod
    def http(
        cls,
        url: str,
        headers: dict[str, str] | None = None,
        name: str | None = None,
        timeout: float = 120.0,
    ) -> MCPToolClient:
        """Connect to an MCP server over Streamable HTTP."""
        _require_sdk()
        factory = _make_session_factory(http_url=url, http_headers=headers)
        return cls(name=name or f"http:{url}", _session_factory=factory, timeout=timeout)

    # ------------------------------------------------------------------ #
    # Tool enumeration
    # ------------------------------------------------------------------ #
    def list_tools(self) -> list[MCPTool]:
        """Enumerate the tools advertised by the server."""
        _require_sdk()
        tools = _run_async(self._list_tools_async(), timeout=self.timeout)
        # Attach the per-tool call function bound to this client.
        for tool in tools:
            tool._call = lambda params, _t=tool.name: self.call_tool(_t, params)
        return tools

    async def _list_tools_async(self) -> list[MCPTool]:
        async with self._session_factory() as session:
            result = await session.list_tools()
            tools: list[MCPTool] = []
            for tool in getattr(result, "tools", []) or []:
                tools.append(
                    MCPTool(
                        name=tool.name,
                        description=getattr(tool, "description", "") or "",
                        input_schema=_as_plain(getattr(tool, "inputSchema", {}) or {}),
                    )
                )
            return tools

    def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Invoke one tool on the server and return a plain dict."""
        _require_sdk()
        return _run_async(
            self._call_tool_async(tool_name, params), timeout=self.timeout
        )

    async def _call_tool_async(
        self, tool_name: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            result = await session.call_tool(tool_name, arguments=params)
            return _tool_result_to_dict(result)

    # ------------------------------------------------------------------ #
    # Registration helpers
    # ------------------------------------------------------------------ #
    def to_skills(self, prefix: str = "") -> list[Skill]:
        """Convert all advertised tools to GeoSkills (one per tool)."""
        return [t.to_skill(prefix=prefix) for t in self.list_tools()]

    def register_into(self, node: Any, prefix: str = "") -> list[Skill]:
        """Register all tools as GeoSkills on a GeoNode.

        Returns the created skills. The node must expose
        ``register_skill_object(skill)`` (GeoNode / skill registry).
        """
        skills = self.to_skills(prefix=prefix)
        for skill in skills:
            node.register_skill_object(skill)
            logger.info("Registered MCP tool as GeoSkill '%s'", skill.name)
        return skills


def _tool_result_to_dict(result: Any) -> dict[str, Any]:
    """Normalise an MCP CallToolResult into a plain dict.

    Prefer ``structuredContent`` when present (lossless); otherwise flatten
    text content blocks into ``{"text": ...}``.
    """
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return _as_plain(structured)
    content = getattr(result, "content", None) or []
    texts: list[str] = []
    for block in content:
        # MCP content blocks expose `text` / `type` attributes (pydantic
        # models or simple dataclasses). Extract text when present.
        block_text = getattr(block, "text", None)
        if block_text is None and isinstance(block, dict):
            block_text = block.get("text")
        if block_text is not None:
            texts.append(str(block_text))
    return {"text": "\n".join(texts)}


def _run_async(coro: Any, timeout: float) -> Any:
    """Run an async coroutine to completion (sync bridge for handlers)."""
    try:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
    except asyncio.TimeoutError as exc:
        raise MCPClientError(f"MCP call timed out after {timeout}s") from exc
    except MCPClientError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any MCP failure
        raise MCPClientError(f"MCP call failed: {exc}") from exc
