"""Tests for the MCP client bridge (mcp_client.py).

Uses a fake ``ClientSession`` injected via monkeypatch, so no real MCP
server process is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from geonexus.mcp_client import (
    MCPClientError,
    MCPTool,
    MCPToolClient,
    _tool_result_to_dict,
)


@dataclass
class FakeTool:
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeContent:
    text: str = ""


@dataclass
class FakeResult:
    content: list[Any] = field(default_factory=list)
    structuredContent: Any = None


class FakeSession:
    """Stands in for mcp.ClientSession."""

    def __init__(self, tools: list[FakeTool], behavior: dict[str, Any] | None = None) -> None:
        self.tools = tools
        self.behavior = behavior or {}
        self.initialized = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self) -> Any:
        return type("ListToolsResult", (), {"tools": self.tools})()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls.append((name, arguments or {}))
        if name in self.behavior:
            return self.behavior[name]
        return FakeResult(content=[FakeContent(text=f"result of {name}")])


class _Ctx:
    """Async context manager adapter over a FakeSession (for ClientSession usage)."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        await self.session.initialize()
        return self.session

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _patch_mcp(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> None:
    """Monkeypatch mcp_client's ClientSession with a fake factory."""
    import geonexus.mcp_client as mod

    calls: dict[str, int] = {"n": 0}

    class FakeClientSession:
        def __init__(self, *a: Any, **kw: Any) -> None:
            calls["n"] += 1
            self._ctx = _Ctx(session)

        async def __aenter__(self) -> FakeSession:
            return await self._ctx.__aenter__()

        async def __aexit__(self, *exc: Any) -> None:
            return await self._ctx.__aexit__(*exc)

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> Any:
            return await session.list_tools()

        async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
            return await session.call_tool(name, arguments)

    monkeypatch.setattr(mod, "ClientSession", FakeClientSession)

    # The stdio() classmethod also needs StdioServerParameters; without the
    # official SDK installed that symbol is None, so patch a stand-in too.
    if getattr(mod, "StdioServerParameters", None) is None:

        class FakeStdioParams:
            def __init__(self, command: str = "", args: Any = None, env: Any = None) -> None:
                self.command = command
                self.args = args or []
                self.env = env

        monkeypatch.setattr(mod, "StdioServerParameters", FakeStdioParams)

    mod._require_sdk()  # ensure the guard passes with the fake session
    return session


def _client(monkeypatch: pytest.MonkeyPatch, tools: list[FakeTool], **kw: Any) -> MCPToolClient:
    session = FakeSession(tools)
    _patch_mcp(monkeypatch, session)
    # A stdio-style client whose session factory is trivial (patched anyway).
    client = MCPToolClient.stdio("fake-cmd", timeout=10)
    # Replace the factory with one yielding the fake session.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    client._session_factory = factory  # type: ignore[assignment]
    return client


class TestListTools:
    def test_list_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(
            monkeypatch,
            [
                FakeTool("echo", "Echo input", {"type": "object", "properties": {"msg": {"type": "string"}}}),
                FakeTool("add", "Add two numbers"),
            ],
        )
        tools = client.list_tools()
        assert [t.name for t in tools] == ["echo", "add"]
        assert tools[0].description == "Echo input"
        assert tools[0].input_schema["properties"]["msg"]["type"] == "string"

    def test_list_tools_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, [])
        assert client.list_tools() == []


class TestCallTool:
    def test_call_tool_plain_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, [FakeTool("echo")])
        result = client.call_tool("echo", {"msg": "hello"})
        assert result == {"text": "result of echo"}

    def test_call_tool_structured_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = FakeSession([FakeTool("ndvi")])
        _patch_mcp(monkeypatch, session)
        session.behavior["ndvi"] = FakeResult(structuredContent={"mean": 0.4, "count": 10})
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def factory() -> Any:
            yield session

        client = MCPToolClient.stdio("fake", timeout=10)
        client._session_factory = factory  # type: ignore[assignment]
        result = client.call_tool("ndvi", {"bands": ["B4", "B8"]})
        assert result == {"mean": 0.4, "count": 10}

    def test_call_tool_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = FakeSession([FakeTool("slow")])

        async def slow_call(name: str, arguments: dict[str, Any] | None = None) -> Any:
            import asyncio

            await asyncio.sleep(5)
            return FakeResult()

        session.call_tool = slow_call  # type: ignore[assignment]
        _patch_mcp(monkeypatch, session)
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def factory() -> Any:
            yield session

        client = MCPToolClient.stdio("fake", timeout=0.2)
        client._session_factory = factory  # type: ignore[assignment]
        with pytest.raises(MCPClientError, match="timed out"):
            client.call_tool("slow", {})


class TestToSkills:
    def test_to_skills_wraps_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(
            monkeypatch,
            [
                FakeTool("echo", "Echo", {"type": "object", "properties": {"m": {"type": "string"}}}),
            ],
        )
        skills = client.to_skills(prefix="ext-")
        assert len(skills) == 1
        skill = skills[0]
        assert skill.name == "ext-echo"
        assert skill.description == "Echo"
        assert skill.input_schema["properties"]["m"]["type"] == "string"
        # The handler forwards to the MCP server.
        out = skill.handler({"m": "hi"}, None)  # type: ignore[arg-type]
        assert out["outputs"] == {"text": "result of echo"}
        assert out["mcp_tool"] == "echo"
        assert skill.mcp_source == "mcp:echo"

    def test_to_skills_no_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _client(monkeypatch, [FakeTool("echo")])
        skills = client.to_skills()
        assert skills[0].name == "echo"


class TestRegisterInto:
    def test_register_into_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeNode:
            def __init__(self) -> None:
                self.skills: list[Any] = []

            def register_skill_object(self, skill: Any) -> None:
                self.skills.append(skill)

        node = FakeNode()
        client = _client(monkeypatch, [FakeTool("a"), FakeTool("b")])
        skills = client.register_into(node, prefix="ext-")
        assert len(node.skills) == 2
        assert [s.name for s in node.skills] == ["ext-a", "ext-b"]
        assert skills == node.skills


class TestToolResultToDict:
    def test_structured_content_preferred(self) -> None:
        result = FakeResult(structuredContent={"x": 1}, content=[FakeContent(text="ignored")])
        assert _tool_result_to_dict(result) == {"x": 1}

    def test_text_blocks_flattened(self) -> None:
        result = FakeResult(content=[FakeContent(text="a"), FakeContent(text="b")])
        assert _tool_result_to_dict(result) == {"text": "a\nb"}

    def test_empty_result(self) -> None:
        assert _tool_result_to_dict(FakeResult()) == {"text": ""}


class TestMCPTool:
    def test_requires_call_function(self) -> None:
        tool = MCPTool(name="x")
        with pytest.raises(AssertionError, match="call function"):
            tool.to_skill()
