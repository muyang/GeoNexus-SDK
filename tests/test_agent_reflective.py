"""Tests for reflective GeoAgent execution (reflective.py)."""

from __future__ import annotations

import json

import httpx
import pytest

from geonexus.agent import (
    Goal,
    GoalStep,
    Plan,
    PlanReflector,
    ReflectionError,
    ReflectiveExecutor,
    evaluate_plan,
)


def _completion(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


def _reflector(advice: str | list[str]) -> tuple[PlanReflector, list[dict]]:
    """Build a PlanReflector whose LLM returns the given advice JSON."""
    calls: list[dict] = []
    queue = advice if isinstance(advice, list) else [advice]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        content = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(200, json=_completion(content))

    client = httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    )
    config = __import__("geonexus.agent", fromlist=["LLMConfig"]).LLMConfig(
        base_url="https://example.test/v1", api_key="k"
    )
    return PlanReflector(config=config, client=client), calls


class _FakeClient:
    """Stands in for FederatedGeoMCPClient: controllable per-skill behavior.

    ``behavior`` maps skill -> "ok" | "boom" | "boom-then-ok". A call count
    is kept per skill so transient failures can be simulated.
    """

    def __init__(self, behavior: dict[str, str]) -> None:
        self.behavior = behavior
        self.calls: list[tuple[str, dict]] = []
        self._counts: dict[str, int] = {}

    def execute(self, skill: str, **kw: object) -> dict:
        self.calls.append(("execute", {"skill": skill, **kw}))
        return self._run(skill)

    def execute_skill(self, skill: str, **kw: object) -> dict:
        self.calls.append(("execute_skill", {"skill": skill, **kw}))
        return self._run(skill)

    def _run(self, skill: str) -> dict:
        n = self._counts.get(skill, 0)
        self._counts[skill] = n + 1
        outcome = self.behavior.get(skill, "ok")
        if outcome == "boom-then-ok":
            if n == 0:
                raise RuntimeError(f"boom in {skill}")
            return {"outputs": {f"{skill}_result": "ok"}}
        if outcome == "boom":
            raise RuntimeError(f"boom in {skill}")
        return {"outputs": {f"{skill}_result": "ok"}}


def _plan(*steps: GoalStep) -> Plan:
    goal = Goal(capability="ndvi", steps=list(steps))
    # Build the plan directly; executor resolves nodes from registry, but we
    # replace the client, so node_url is never actually contacted.
    plan = Plan(goal=goal.model_dump(exclude_none=True), registry_url="http://r:8790")
    for i, gs in enumerate(steps, start=1):
        from geonexus.agent import PlanStep

        plan.steps.append(
            PlanStep(
                step_id=i,
                kind="skill-only",
                skill=gs.skill,
                node_url="http://node:8787",
                geocards=list(gs.geocards),
                spatial=None,
                temporal=gs.temporal,
                params=dict(gs.params),
                description=gs.label or gs.skill,
                depends_on=list(gs.depends_on),
            )
        )
    return plan


class TestPlanReflector:
    def test_valid_advice(self) -> None:
        reflector, calls = _reflector(
            json.dumps({"action": "retry", "reason": "transient", "params": {"x": 1}})
        )
        step = _plan(GoalStep(skill="ndvi")).steps[0]
        advice = reflector.reflect(
            Goal(capability="ndvi"), step, "timeout", available_skills=["ndvi"]
        )
        assert advice.action == "retry"
        assert advice.params == {"x": 1}
        assert "Available skills" in calls[0]["messages"][-1]["content"]
        reflector.close()

    def test_unknown_action_rejected(self) -> None:
        reflector, _ = _reflector(json.dumps({"action": "explode", "reason": "x"}))
        step = _plan(GoalStep(skill="ndvi")).steps[0]
        with pytest.raises(ReflectionError, match="unknown action"):
            reflector.reflect(Goal(capability="ndvi"), step, "err")
        reflector.close()

    def test_bad_json_rejected(self) -> None:
        reflector, _ = _reflector("not json")
        step = _plan(GoalStep(skill="ndvi")).steps[0]
        with pytest.raises(ReflectionError):
            reflector.reflect(Goal(capability="ndvi"), step, "err")
        reflector.close()

    def test_requires_config(self) -> None:
        from geonexus.agent import LLMConfig

        with pytest.raises(ReflectionError, match="GEONEXUS_LLM_API_KEY"):
            PlanReflector(config=LLMConfig(base_url="https://x/v1", api_key=""))


class TestReflectiveExecutor:
    def test_retry_recovers(self) -> None:
        """A transient failure is retried per LLM advice and succeeds."""
        reflector, _ = _reflector(json.dumps({"action": "retry", "reason": "transient"}))
        plan = _plan(GoalStep(skill="ndvi"))
        client = _FakeClient({"ndvi": "boom-then-ok"})
        ex = ReflectiveExecutor("http://r:8790", reflector, available_skills=["ndvi"])
        ex.client = client  # type: ignore[assignment]
        plan = ex.run(plan)
        assert plan.steps[0].status == "done"
        assert len(plan.steps[0].reflections) == 1  # type: ignore[attr-defined]
        assert plan.steps[0].reflections[0]["action"] == "retry"  # type: ignore[attr-defined]
        # The step ran twice (initial + retry).
        assert len(client.calls) == 2

    def test_replace_changes_skill(self) -> None:
        """replace advice swaps to a working skill."""
        reflector, _ = _reflector(
            json.dumps({"action": "replace", "skill": "ndvi-alt", "reason": "wrong skill"})
        )
        plan = _plan(GoalStep(skill="ndvi"))
        client = _FakeClient({"ndvi": "boom", "ndvi-alt": "ok"})
        ex = ReflectiveExecutor("http://r:8790", reflector, available_skills=["ndvi", "ndvi-alt"])
        ex.client = client  # type: ignore[assignment]
        plan = ex.run(plan)
        assert plan.steps[0].status == "done"
        assert plan.steps[0].skill == "ndvi-alt"
        assert any(c[1]["skill"] == "ndvi-alt" for c in client.calls)

    def test_skip_marks_step(self) -> None:
        reflector, _ = _reflector(json.dumps({"action": "skip", "reason": "optional"}))
        plan = _plan(GoalStep(skill="ndvi"))
        client = _FakeClient({"ndvi": "boom"})
        ex = ReflectiveExecutor("http://r:8790", reflector, available_skills=["ndvi"])
        ex.client = client  # type: ignore[assignment]
        plan = ex.run(plan)
        assert plan.steps[0].status == "skipped"
        assert "skipped by reflection" in (plan.steps[0].error or "")

    def test_abort_stops(self) -> None:
        reflector, _ = _reflector(json.dumps({"action": "abort", "reason": "fundamental"}))
        plan = _plan(GoalStep(skill="ndvi"))
        client = _FakeClient({"ndvi": "boom"})
        ex = ReflectiveExecutor("http://r:8790", reflector, available_skills=["ndvi"])
        ex.client = client  # type: ignore[assignment]
        plan = ex.run(plan)
        assert plan.steps[0].status == "failed"
        assert "reflection abort" in (plan.steps[0].error or "")

    def test_budget_limits_reflections(self) -> None:
        """Endless retry advice cannot spin forever."""
        reflector, _ = _reflector(json.dumps({"action": "retry", "reason": "again"}))
        plan = _plan(GoalStep(skill="ndvi"))
        client = _FakeClient({"ndvi": "boom"})
        ex = ReflectiveExecutor(
            "http://r:8790", reflector, max_reflections=2, available_skills=["ndvi"]
        )
        ex.client = client  # type: ignore[assignment]
        plan = ex.run(plan)
        assert plan.steps[0].status == "failed"
        assert len(plan.steps[0].reflections) == 2  # type: ignore[attr-defined]
        assert len(client.calls) == 3  # 1 initial + 2 retries

    def test_no_failure_no_reflection(self) -> None:
        reflector, _ = _reflector(json.dumps({"action": "retry", "reason": "x"}))
        plan = _plan(GoalStep(skill="ndvi"))
        client = _FakeClient({})
        ex = ReflectiveExecutor("http://r:8790", reflector, available_skills=["ndvi"])
        ex.client = client  # type: ignore[assignment]
        plan = ex.run(plan)
        assert plan.steps[0].status == "done"
        assert len(plan.steps[0].reflections) == 0  # type: ignore[attr-defined]

    def test_multi_step_context_in_prompt(self) -> None:
        """The reflector sees summaries of already-completed steps."""
        reflector, calls = _reflector(json.dumps({"action": "skip", "reason": "x"}))
        plan = _plan(
            GoalStep(skill="a", label="first"),
            GoalStep(skill="b", label="second", depends_on=[1]),
        )
        client = _FakeClient({"a": "ok", "b": "boom"})
        ex = ReflectiveExecutor("http://r:8790", reflector, available_skills=["a", "b"])
        ex.client = client  # type: ignore[assignment]
        plan = ex.run(plan)
        assert plan.steps[0].status == "done"
        assert plan.steps[1].status == "skipped"
        # The advice prompt contained the completed step's context.
        assert "step 1 (a): OK" in calls[0]["messages"][-1]["content"]


class TestEvaluatePlan:
    def test_evaluate_ok(self) -> None:
        reflector, _ = _reflector(
            json.dumps({"satisfied": True, "score": 92, "notes": "goal met"})
        )
        plan = _plan(GoalStep(skill="ndvi"))
        plan.steps[0].status = "done"
        plan.steps[0].result = {"outputs": {"mean": 0.4}}
        verdict = evaluate_plan(plan, reflector=reflector)
        assert verdict["satisfied"] is True
        assert verdict["score"] == 92
        assert "goal met" in verdict["notes"]

    def test_evaluate_failure_surfaces(self) -> None:
        reflector, _ = _reflector("garbage")
        plan = _plan(GoalStep(skill="ndvi"))
        with pytest.raises(ReflectionError, match="Plan evaluation failed"):
            evaluate_plan(plan, reflector=reflector)
