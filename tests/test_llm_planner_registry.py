"""Tests for registry-grounded LLM planning (plan_from_text_with_registry)."""

from __future__ import annotations

import json

import httpx
import pytest

from geonexus.agent import (
    ExecutionError,
    Goal,
    LLMConfig,
    plan_from_text_with_registry,
)


def _completion(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


def _llm_client(content: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(content))

    return httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    )


class _FakeRegistry:
    def __init__(self, skills: list[dict]) -> None:
        self._skills = skills

    def __enter__(self) -> _FakeRegistry:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def list_skills(self) -> list[dict]:
        return self._skills


def _skill_entry(name: str, capabilities: list[str]) -> dict:
    return {
        "skill": {"name": name, "capabilities": capabilities},
        "node_url": "http://127.0.0.1:8787",
    }


def test_registry_grounded_planning_passes_real_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LLM prompt must contain the skills actually registered."""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        goal = json.dumps({"capability": "ndvi", "temporal_steps": []})
        return httpx.Response(200, json=_completion(goal))

    client = httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    )
    fake = _FakeRegistry(
        [
            _skill_entry("ndvi-analysis", ["ndvi"]),
            _skill_entry("ndvi-change", ["change-detection"]),
        ]
    )
    monkeypatch.setattr("geonexus.registry.RegistryClient", lambda *a, **k: fake)
    config = LLMConfig(base_url="https://example.test/v1", api_key="test-key")

    goal = plan_from_text_with_registry("NDVI please", "http://registry:8790", config=config, client=client)

    assert isinstance(goal, Goal)
    assert goal.capability == "ndvi"
    # The user prompt listed both registered skills and their capabilities.
    user = captured[0]["messages"][-1]["content"]
    assert "ndvi-analysis" in user
    assert "ndvi-change" in user
    assert "ndvi" in user and "change-detection" in user


def test_registry_grounded_planning_no_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty registry raises ExecutionError instead of planning blind."""
    fake = _FakeRegistry([])
    monkeypatch.setattr("geonexus.registry.RegistryClient", lambda *a, **k: fake)
    config = LLMConfig(base_url="https://example.test/v1", api_key="test-key")
    client = _llm_client(json.dumps({"capability": "ndvi"}))
    with pytest.raises(ExecutionError, match="No skills registered"):
        plan_from_text_with_registry("NDVI", "http://registry:8790", config=config, client=client)


def test_registry_grounded_planning_unreachable_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing registry lookup surfaces as ExecutionError."""

    class _Broken:
        def __enter__(self) -> _Broken:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def list_skills(self) -> list[dict]:
            from geonexus.registry import RegistryClientError

            raise RegistryClientError("connection refused", code=502)

    monkeypatch.setattr("geonexus.registry.RegistryClient", lambda *a, **k: _Broken())
    config = LLMConfig(base_url="https://example.test/v1", api_key="test-key")
    client = _llm_client(json.dumps({"capability": "ndvi"}))
    with pytest.raises(ExecutionError, match="Registry skill discovery failed"):
        plan_from_text_with_registry("NDVI", "http://registry:8790", config=config, client=client)


def test_registry_grounded_planning_dedupes() -> None:
    """Duplicate capabilities from multiple skills collapse to one list."""
    fake = _FakeRegistry(
        [
            _skill_entry("a", ["ndvi"]),
            _skill_entry("b", ["ndvi"]),
            _skill_entry("c", ["change-detection"]),
        ]
    )
    # Reuse plan_from_text_with_registry internals via a spy on plan_from_text.
    seen: dict = {}

    def fake_plan_from_text(text, available_capabilities=None, available_skills=None, **kw):
        seen["caps"] = available_capabilities
        seen["skills"] = available_skills
        return Goal(capability="ndvi")

    import geonexus.agent.llm_planner as mod

    original = mod.plan_from_text
    mod.plan_from_text = fake_plan_from_text  # type: ignore[assignment]
    try:
        fake_reg = _FakeRegistry(fake._skills)

        class _Ctx:
            def __enter__(self):
                return fake_reg

            def __exit__(self, *exc):
                return None

        import geonexus.registry as registry_mod

        original_client = registry_mod.RegistryClient
        registry_mod.RegistryClient = lambda *a, **k: _Ctx()  # type: ignore[assignment]
        try:
            config = LLMConfig(base_url="https://example.test/v1", api_key="k")
            plan_from_text_with_registry("x", "http://registry:8790", config=config)
        finally:
            registry_mod.RegistryClient = original_client  # type: ignore[assignment]
    finally:
        mod.plan_from_text = original  # type: ignore[assignment]

    assert seen["caps"] == ["ndvi", "change-detection"]
    assert seen["skills"] == ["a", "b", "c"]
