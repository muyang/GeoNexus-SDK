"""Tests for the Web-layer REST router (geonexus.web.router / app).

Uses FastAPI TestClient with monkeypatched RegistryClient / GeoMCPClient /
planner so no live services are required.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from geonexus.web import WebConfig, create_web_app
from geonexus.web.auth import JWTConfig

SECRET = "test-secret-that-is-long-enough-0123456789abcdef"

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def config() -> WebConfig:
    return WebConfig(
        registry_url="http://registry.test:8790",
        jwt=JWTConfig(secret=SECRET),
        users={"alice": "pw123", "bob": "pw456"},
        node_api_keys={"http://node-a:8787": "nodekey-a"},
        default_node_url="http://node-a:8787",
    )


@pytest.fixture
def client(config: WebConfig) -> TestClient:
    return TestClient(create_web_app(config))


@pytest.fixture
def token(client: TestClient) -> str:
    resp = client.post("/api/auth/login", json={"username": "alice", "password": "pw123"})
    assert resp.status_code == 200
    return resp.json()["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


class TestAuth:
    def test_login_ok(self, client: TestClient) -> None:
        resp = client.post("/api/auth/login", json={"username": "alice", "password": "pw123"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["subject"] == "alice"
        assert body["expires_in"] == 3600

    def test_login_bad_password(self, client: TestClient) -> None:
        resp = client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
        assert resp.status_code == 401

    def test_login_unknown_user(self, client: TestClient) -> None:
        resp = client.post("/api/auth/login", json={"username": "mallory", "password": "x"})
        assert resp.status_code == 401

    def test_health_is_open(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_protected_endpoints_require_token(self, client: TestClient) -> None:
        for path in ("/api/cards", "/api/skills", "/api/nodes", "/api/tasks"):
            assert client.get(path).status_code == 401, path
        assert client.post("/api/execute", json={}).status_code == 401
        assert client.post("/api/goals", json={}).status_code == 401

    def test_bad_token_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/cards", headers=_auth("not-a-jwt"))
        assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Discovery endpoints
# --------------------------------------------------------------------------- #


class TestDiscovery:
    def test_cards_list(self, client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeRegistryClient([{"id": "c1"}])
        monkeypatch.setattr("geonexus.web.router.RegistryClient", lambda *a, **k: fake)
        resp = client.get("/api/cards", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["cards"] == [{"id": "c1"}]

    def test_cards_search_passes_params(
        self, client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class _SearchRegistry:
            def __enter__(self) -> _SearchRegistry:
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def search(
                self,
                capability: str | None = None,
                bbox: list[float] | None = None,
                **kw: Any,
            ) -> dict[str, Any]:
                captured["capability"] = capability
                captured["bbox"] = bbox
                return {"results": []}

            def list_cards(self) -> dict[str, Any]:
                return {"cards": []}

        monkeypatch.setattr("geonexus.web.router.RegistryClient", lambda *a, **k: _SearchRegistry())
        resp = client.get("/api/cards?q=ndvi&bbox=1,2,3,4", headers=_auth(token))
        assert resp.status_code == 200
        assert captured["capability"] == "ndvi"
        assert captured["bbox"] == [1.0, 2.0, 3.0, 4.0]

    def test_skills(self, client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeRegistryClient([{"name": "ndvi"}])
        monkeypatch.setattr("geonexus.web.router.RegistryClient", lambda *a, **k: fake)
        resp = client.get("/api/skills", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["skills"] == [{"name": "ndvi"}]

    def test_nodes(self, client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeRegistryClient({"nodes": [{"url": "http://node-a:8787"}]})
        monkeypatch.setattr("geonexus.web.router.RegistryClient", lambda *a, **k: fake)
        resp = client.get("/api/nodes", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["nodes"] == [{"url": "http://node-a:8787"}]


# --------------------------------------------------------------------------- #
# Async execution
# --------------------------------------------------------------------------- #


class TestExecute:
    def test_execute_returns_task(self, client: TestClient, token: str) -> None:
        resp = client.post(
            "/api/execute",
            headers=_auth(token),
            json={"skill": "ndvi", "geocards": ["c1"], "params": {"bands": ["B4", "B8"]}},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        assert body["task_id"]

    def test_execute_requires_node(self, client: TestClient, token: str) -> None:
        cfg = WebConfig(
            registry_url="http://registry.test:8790",
            jwt=JWTConfig(secret=SECRET),
            users={"alice": "pw123"},
            default_node_url=None,
        )
        c = TestClient(create_web_app(cfg))
        tok = c.post("/api/auth/login", json={"username": "alice", "password": "pw123"}).json()["token"]
        resp = c.post("/api/execute", headers=_auth(tok), json={"skill": "ndvi"})
        assert resp.status_code == 400

    def test_execute_flow_with_task_state(
        self, client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeNode:
            def __init__(self, url: str, api_key: str | None = None, **kw: Any) -> None:
                self.url = url
                self.api_key = api_key

            def __enter__(self) -> _FakeNode:
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def execute(self, **kwargs: Any) -> dict[str, Any]:
                return {"status": "ok", "skill": kwargs["skill"]}

        created: list[tuple[str, str | None]] = []
        monkeypatch.setattr(
            "geonexus.web.router.GeoMCPClient",
            lambda url, api_key=None, **kw: (
                created.append((url, api_key)) or _FakeNode(url, api_key)
            ),
        )
        resp = client.post(
            "/api/execute", headers=_auth(token), json={"skill": "ndvi", "geocards": ["c1"]}
        )
        tid = resp.json()["task_id"]
        # Poll until done (task manager runs in-process).
        for _ in range(100):
            state = client.get(f"/api/tasks/{tid}", headers=_auth(token)).json()
            if state["status"] in ("done", "failed"):
                break
            time.sleep(0.05)
        assert state["status"] == "done"
        assert state["result"] == {"status": "ok", "skill": "ndvi"}
        # The BFF forwarded the node API key (never exposed to the browser).
        assert created and created[0][1] == "nodekey-a"


class TestGoals:
    def test_goals_returns_task(self, client: TestClient, token: str) -> None:
        resp = client.post(
            "/api/goals", headers=_auth(token), json={"text": "分析植被变化"}
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "queued"

    def test_goals_requires_llm_config(
        self, client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _NoLLM:
            def is_configured(self) -> bool:
                return False

        monkeypatch.setattr(
            "geonexus.web.router.LLMConfig.from_env", staticmethod(lambda: _NoLLM())
        )
        resp = client.post("/api/goals", headers=_auth(token), json={"text": "x"})
        tid = resp.json()["task_id"]
        for _ in range(100):
            state = client.get(f"/api/tasks/{tid}", headers=_auth(token)).json()
            if state["status"] == "failed":
                break
            time.sleep(0.05)
        assert state["status"] == "failed"
        assert "GEONEXUS_LLM_API_KEY" in (state["error"] or "")


# --------------------------------------------------------------------------- #
# Task endpoints
# --------------------------------------------------------------------------- #


class TestTasks:
    def test_task_not_found(self, client: TestClient, token: str) -> None:
        assert client.get("/api/tasks/nope", headers=_auth(token)).status_code == 404

    def test_cancel_unknown(self, client: TestClient, token: str) -> None:
        assert client.post("/api/tasks/nope/cancel", headers=_auth(token)).status_code == 404

    def test_stream_sse(
        self, client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _job(tid: str) -> dict[str, Any]:
            time.sleep(0.3)
            return {"finished": True}

        class _FakeNode:
            def __init__(self, *a: Any, **kw: Any) -> None:
                pass

            def __enter__(self) -> _FakeNode:
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def execute(self, **kwargs: Any) -> dict[str, Any]:
                return _job("")

        monkeypatch.setattr("geonexus.web.router.GeoMCPClient", lambda *a, **kw: _FakeNode())
        resp = client.post("/api/execute", headers=_auth(token), json={"skill": "ndvi"})
        tid = resp.json()["task_id"]
        with client.stream("GET", f"/api/tasks/{tid}/stream", headers=_auth(token)) as stream:
            assert stream.status_code == 200
            assert "text/event-stream" in stream.headers["content-type"]
            lines = list(stream.iter_lines())
        events = [json.loads(ln[6:]) for ln in lines if ln.startswith("data: ")]
        assert events[-1]["status"] in ("done", "failed")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _FakeRegistryClient:
    """Minimal RegistryClient stand-in with context-manager support."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def __enter__(self) -> _FakeRegistryClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def list_cards(self) -> list[dict[str, Any]]:
        return self._response

    def list_skills(self) -> list[dict[str, Any]]:
        return self._response

    def get_nodes(self) -> dict[str, Any]:
        return self._response

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._response
