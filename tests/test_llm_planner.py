"""Tests for the LLM-assisted goal planner (V1.0, OpenAI-compatible)."""

from __future__ import annotations

import json

import httpx
import pytest

from geonexus.agent import ExecutionError, Goal, LLMConfig, LLMGoalPlanner, plan_from_text


def _completion(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


def _client_returning(content: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(content))

    return httpx.Client(base_url="https://example.test/v1", transport=httpx.MockTransport(handler))


def test_llm_planner_parses_goal() -> None:
    """A valid JSON Goal from the LLM becomes a validated Goal."""
    llm_json = json.dumps(
        {
            "capability": "ndvi",
            "spatial": {"bbox": [-73.9, -15.0, -44.0, 5.0], "crs": "EPSG:4326"},
            "temporal_steps": [
                {"start": "2015-01-01", "end": "2015-12-31"},
                {"start": "2025-01-01", "end": "2025-12-31"},
            ],
            "required_bands": ["B04", "B08"],
            "params": {},
        }
    )
    config = LLMConfig(base_url="https://example.test/v1", api_key="test-key")
    client = _client_returning(llm_json)
    with LLMGoalPlanner(config=config, client=client) as planner:
        goal = planner.plan(
            "Analyze vegetation change in the Amazon between 2015 and 2025",
            available_capabilities=["ndvi", "change-detection"],
            available_skills=["ndvi-analysis"],
        )
    assert isinstance(goal, Goal)
    assert goal.capability == "ndvi"
    assert len(goal.temporal_steps) == 2
    assert goal.required_bands == ["B04", "B08"]


def test_llm_planner_pipeline_goal() -> None:
    """The LLM can produce a pipeline (steps) goal."""
    llm_json = json.dumps(
        {
            "capability": "ndvi",
            "steps": [
                {
                    "skill": "ndvi-analysis",
                    "label": "2015",
                    "temporal": {"start": "2015-01-01", "end": "2015-12-31"},
                },
                {
                    "skill": "ndvi-analysis",
                    "label": "2025",
                    "temporal": {"start": "2025-01-01", "end": "2025-12-31"},
                },
                {
                    "skill": "ndvi-change",
                    "label": "change",
                    "params": {
                        "ndvi_a": "${step1.outputs.ndvi_raster}",
                        "ndvi_b": "${step2.outputs.ndvi_raster}",
                    },
                    "depends_on": [1, 2],
                },
            ],
        }
    )
    config = LLMConfig(base_url="https://example.test/v1", api_key="test-key")
    client = _client_returning(llm_json)
    goal = plan_from_text(
        "Compute NDVI for 2015 and 2025, then the change",
        config=config,
        client=client,
    )
    assert len(goal.steps) == 3
    assert goal.steps[2].depends_on == [1, 2]
    assert "${step1.outputs.ndvi_raster}" in goal.steps[2].params["ndvi_a"]


def test_llm_planner_retries_bad_json() -> None:
    """Invalid JSON is retried once with a repair instruction."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json=_completion("not json at all"))
        return httpx.Response(
            200,
            json=_completion(json.dumps({"capability": "ndvi", "temporal_steps": []})),
        )

    client = httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    )
    config = LLMConfig(base_url="https://example.test/v1", api_key="test-key")
    with LLMGoalPlanner(config=config, client=client) as planner:
        goal = planner.plan("NDVI please")
    assert goal.capability == "ndvi"
    assert len(calls) == 2  # retried
    assert "not valid" in calls[1]["messages"][-1]["content"]


def test_llm_planner_requires_key() -> None:
    """Without an API key the planner refuses to run."""
    with pytest.raises(ExecutionError, match="GEONEXUS_LLM_API_KEY"):
        LLMGoalPlanner(config=LLMConfig(base_url="https://x/v1", api_key=""))


def test_llm_planner_bad_response_shape() -> None:
    """A malformed LLM response surfaces as ExecutionError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    client = httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    )
    config = LLMConfig(base_url="https://example.test/v1", api_key="test-key")
    with (
        LLMGoalPlanner(config=config, client=client) as planner,
        pytest.raises(ExecutionError, match="response shape"),
    ):
        planner.plan("NDVI please")
