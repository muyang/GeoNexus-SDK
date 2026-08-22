"""Tests for node-level X-API-Key authentication (GeoMCP server + client).

Verifies:
- ``GeoMCPServer`` with ``api_keys`` rejects ``geo.execute`` without a key,
  keeps read methods open, and accepts the correct key.
- ``GeoMCPClient(api_key=...)`` forwards ``X-API-Key``.
- Delegated execution forwards the node's key.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx
import pytest

from geonexus.geomcp import GeoMCPClient, GeoMCPClientError, GeoMCPServer


def _start_server(app: Any, port: int) -> tuple[Any, str]:
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("server did not start")
    actual_port = server.servers[0].sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{actual_port}"


@pytest.fixture
def node_url() -> str:
    server = GeoMCPServer(name="auth-node", api_keys={"secret-key-1"})
    server.register_tool("echo", lambda params: {"echo": params}, description="echo")
    _, url = _start_server(server.create_app(), 0)
    yield url


class TestNodeAuth:
    def test_execute_without_key_rejected(self, node_url: str) -> None:
        with pytest.raises(GeoMCPClientError) as exc, GeoMCPClient(node_url) as c:
            c.execute(skill="echo")
        assert exc.value.code == -32602

    def test_execute_with_key_ok(self, node_url: str) -> None:
        with GeoMCPClient(node_url, api_key="secret-key-1") as c:
            result = c.execute(skill="echo", params={"x": 1})
        assert result["outputs"]["echo"]["x"] == 1

    def test_execute_with_wrong_key_rejected(self, node_url: str) -> None:
        with pytest.raises(GeoMCPClientError), GeoMCPClient(node_url, api_key="wrong") as c:
            c.execute(skill="echo")

    def test_read_methods_open(self, node_url: str) -> None:
        # Discovery stays open without a key (registry-style policy).
        with GeoMCPClient(node_url) as c:
            caps = c.capabilities()
            assert "geo.execute" in caps.get("methods", [])
            health = c.health()
            assert health.get("status") == "ok"

    def test_open_node_needs_no_key(self) -> None:
        server = GeoMCPServer(name="open-node")
        server.register_tool("echo", lambda params: {"echo": params})
        _, url = _start_server(server.create_app(), 0)
        with GeoMCPClient(url) as c:
            assert c.execute(skill="echo")["outputs"]["echo"] == {}


class TestClientApiKeyForwarding:
    def test_header_sent(self) -> None:
        captured: dict[str, str] = {}

        def _handle(request: httpx.Request) -> httpx.Response:
            captured["key"] = request.headers.get("X-API-Key")
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body.get("id"), "result": {}},
                request=request,
            )

        transport = httpx.MockTransport(_handle)
        client = GeoMCPClient(
            "http://testserver",
            api_key="key-123",
            client=httpx.Client(base_url="http://testserver", transport=transport),
        )
        client.execute(skill="s")
        assert captured["key"] == "key-123"
        client.close()

    def test_no_header_when_no_key(self) -> None:
        captured: dict[str, str] = {}

        def _handle(request: httpx.Request) -> httpx.Response:
            captured["key"] = request.headers.get("X-API-Key")
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body.get("id"), "result": {}},
                request=request,
            )

        transport = httpx.MockTransport(_handle)
        client = GeoMCPClient(
            "http://testserver",
            client=httpx.Client(base_url="http://testserver", transport=transport),
        )
        client.execute(skill="s")
        assert captured["key"] is None
        client.close()


class _FakeResponse:
    def json(self) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": None, "result": {}}


class TestDelegationForwardsKey:
    def test_delegate_sends_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A node with api_keys delegates to an authenticated owner with its key."""
        def owner_echo(params: dict[str, Any]) -> dict[str, Any]:
            return {"delegated": True, **params}

        owner = GeoMCPServer(name="owner", api_keys={"owner-key"})
        owner.register_tool("echo", owner_echo)

        # Registry stand-in: owner serves the card.
        from geonexus.geocard.model import GeoCard

        card = GeoCard.model_validate(
            {
                "id": "card-1",
                "type": "data",
                "name": "c1",
                "description": "test card",
                "geocard_version": "1.0",
                "assets": [],
            }
        )
        owner.register_geocard(card)

        # We need a registry mapping card-1 -> owner URL. Build a tiny fake.
        class _FakeRegistry:
            def __init__(self, url: str) -> None:
                self.url = url

            def __enter__(self) -> _FakeRegistry:
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def get(self, card_id: str) -> dict[str, Any]:
                return {"id": card_id, "node_url": self.url}

        _, owner_url = _start_server(owner.create_app(), 0)

        delegator = GeoMCPServer(
            name="delegator",
            api_keys={"delegator-key"},
            forward_api_key="owner-key",
            registry_url="http://fake-registry",
        )
        # `_delegate` imports RegistryClient inside the function body, so we
        # patch the source module attribute (`geonexus.registry`).
        import geonexus.registry as registry_mod

        monkeypatch.setattr(
            registry_mod, "RegistryClient", lambda url, **kw: _FakeRegistry(owner_url)
        )
        # Deliberately do NOT register the card on the delegator: the request
        # must be forwarded to the owner via the registry.
        _, delegator_url = _start_server(delegator.create_app(), 0)
        with GeoMCPClient(delegator_url, api_key="delegator-key") as c:
            result = c.execute(skill="echo", geocards=["card-1"])

        # The owner executed it (result came back through the delegation chain).
        assert result["outputs"]["delegated"] is True
