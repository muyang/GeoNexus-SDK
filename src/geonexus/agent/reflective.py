"""Reflective GeoAgent execution (v1.1).

Adds LLM-assisted **reflection** on top of the deterministic planner and
executor:

- :class:`PlanReflector` — given a goal, a failed plan step and the error,
  asks an LLM to diagnose the failure and propose a repair action:
  ``retry`` (same step), ``replace`` (new skill/params), ``skip`` (drop the
  step), or ``abort`` (give up).
- :class:`ReflectiveExecutor` — runs a plan like :class:`PlanExecutor` but,
  when a step fails, feeds the failure (plus the summaries of steps that
  already succeeded — multi-turn context) to the reflector and retries per
  its advice, up to a bounded number of reflection rounds.
- :func:`evaluate_plan` — LLM-based self-assessment of a finished plan:
  checks that the goal was addressed and flags warnings.

The LLM only *advises*; the deterministic executor still performs all
execution, so a hallucinated repair is bounded by the same error handling as
any other step failure.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..registry import RegistryClient, RegistryClientError
from .llm_planner import LLMConfig
from .planner import (
    Goal,
    Plan,
    PlanExecutor,
    PlanStep,
)

logger = logging.getLogger(__name__)

# Reflection actions (stable strings for the API surface).
ACTION_RETRY = "retry"
ACTION_REPLACE = "replace"
ACTION_SKIP = "skip"
ACTION_ABORT = "abort"

DEFAULT_MAX_REFLECTIONS = 3


class ReflectionError(Exception):
    """Raised when reflection cannot proceed (LLM/config or invalid advice)."""


@dataclass
class ReflectionAdvice:
    """Structured repair advice from the reflector."""

    action: str
    reason: str = ""
    skill: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "skill": self.skill,
            "params": self.params,
            "description": self.description,
        }


_REFLECT_SYSTEM_PROMPT = (
    "You are the repair advisor of GeoNexus, a federated geospatial "
    "intelligence system. A plan step failed during execution. Diagnose the "
    "failure and output ONE strict JSON object (no markdown, no commentary):\n"
    "{\n"
    '  "action": "retry" | "replace" | "skip" | "abort",\n'
    '  "reason": string,          // why this action addresses the failure\n'
    '  "skill": string | null,    // required when action=replace: the new skill name\n'
    '  "params": {key: value},    // replacement params for retry/replace (omit to keep)\n'
    '  "description": string | null // optional new step description\n'
    "}\n"
    "Guidance:\n"
    "- retry: transient errors (timeouts, missing optional inputs) — same step.\n"
    "- replace: the skill is wrong for this step; pick from the available "
    "skills list and give the params it needs.\n"
    "- skip: the step is not essential to the goal; dropping it is safe.\n"
    "- abort: the failure is fundamental or no repair is plausible.\n"
    "Never invent skills outside the provided list."
)


_EVALUATE_SYSTEM_PROMPT = (
    "You are the plan reviewer of GeoNexus. Review a finished execution plan "
    "against its goal. Output ONE strict JSON object (no markdown, no "
    "commentary):\n"
    "{\n"
    '  "satisfied": true | false,  // whether the goal appears addressed\n'
    '  "score": 0..100,            // overall quality\n'
    '  "notes": string,            // concise review, what succeeded and gaps\n'
    "}\n"
)


class PlanReflector:
    """LLM-based diagnosis of a failed plan step."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config or LLMConfig.from_env()
        if not self.config.is_configured():
            raise ReflectionError(
                "Reflection requires LLM configuration: set GEONEXUS_LLM_API_KEY "
                "(and optionally GEONEXUS_LLM_BASE_URL / GEONEXUS_LLM_MODEL), "
                "or pass an LLMConfig."
            )
        self._client = client or httpx.Client(
            base_url=self.config.base_url.rstrip("/"), timeout=60.0
        )

    # ------------------------------------------------------------------ #
    def reflect(
        self,
        goal: Goal,
        step: PlanStep,
        error: str,
        available_skills: list[str] | None = None,
        context_summary: str = "",
    ) -> ReflectionAdvice:
        """Diagnose ``step``'s failure and return repair advice."""
        user_prompt = (
            f"Goal: {json.dumps(goal.model_dump(exclude_none=True), ensure_ascii=False)}\n"
            f"Available skills: {', '.join(available_skills or []) or '(none listed)'}\n"
            f"Context of completed steps:\n{context_summary or '(none)'}\n\n"
            f"Failed step {step.step_id}: skill={step.skill!r} kind={step.kind!r} "
            f"node={step.node_url!r} geocards={step.geocards!r}\n"
            f"params={json.dumps(step.params, ensure_ascii=False)}\n"
            f"error={error}\n\n"
            "Output ONLY the JSON repair advice object."
        )
        messages = [
            {"role": "system", "content": _REFLECT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        content = self._complete(messages)
        try:
            data = json.loads(content)
            action = data.get("action")
            if action not in (ACTION_RETRY, ACTION_REPLACE, ACTION_SKIP, ACTION_ABORT):
                raise ValueError(f"unknown action {action!r}")
            return ReflectionAdvice(
                action=action,
                reason=str(data.get("reason", "")),
                skill=data.get("skill"),
                params=dict(data.get("params") or {}),
                description=data.get("description"),
            )
        except Exception as exc:  # noqa: BLE001 - advice is advisory
            raise ReflectionError(f"Invalid reflection advice: {exc}") from exc

    def _complete(self, messages: list[dict[str, Any]]) -> str:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ReflectionError(f"LLM endpoint error: {exc}") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ReflectionError(f"Unexpected LLM response shape: {exc}") from exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PlanReflector:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class ReflectiveExecutor(PlanExecutor):
    """PlanExecutor that reflects on failed steps and retries per LLM advice.

    Args:
        registry_url: Base URL of the shared GeoCard Registry.
        reflector: :class:`PlanReflector` (or compatible) used for advice.
        max_reflections: Total number of reflection+retry rounds allowed
            across the whole plan.
        timeout: Request timeout for execution calls.
        available_skills: Skill names passed to the reflector so it can
            propose replacements from the real registry surface (defaults to
            discovering them from the registry).
    """

    def __init__(
        self,
        registry_url: str,
        reflector: PlanReflector,
        max_reflections: int = DEFAULT_MAX_REFLECTIONS,
        timeout: float = 30.0,
        available_skills: list[str] | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(registry_url, timeout=timeout, api_key=api_key)
        self.reflector = reflector
        self.max_reflections = max_reflections
        self._available_skills = available_skills

    # ------------------------------------------------------------------ #
    def run(self, plan: Plan) -> Plan:
        """Execute the plan, reflecting on failed steps up to the budget.

        The returned plan records, on each step that failed at least once,
        the reflection history in ``step.error`` (final) and a
        ``reflections`` attribute (list of dicts) for observability.
        """
        by_id = {s.step_id: s for s in plan.steps}
        reflections_used = 0
        # Reset any per-step reflection history from a previous run.
        for step in plan.steps:
            step.reflections = []

        # Deterministic first pass (same order semantics as PlanExecutor).
        self._execute_available(plan, by_id)

        # Reflection loop: retry failed steps that are not blocked by a
        # failed dependency, until the budget is exhausted.
        while reflections_used < self.max_reflections:
            failed = [
                s
                for s in plan.steps
                if s.status == "failed"
                and not any(
                    by_id[d].status == "failed" for d in s.depends_on if d in by_id
                )
            ]
            if not failed:
                break
            step = failed[0]
            context = self._context_summary(plan, by_id)
            advice = self._advise(plan, step, context)
            step.reflections.append(
                {**advice.to_dict(), "round": reflections_used + 1}
            )
            reflections_used += 1
            logger.info(
                "Reflection %d on step %d: %s (%s)",
                reflections_used,
                step.step_id,
                advice.action,
                advice.reason,
            )
            if advice.action == ACTION_ABORT:
                step.error = f"reflection abort: {advice.reason}"
                break
            if advice.action == ACTION_SKIP:
                step.status = "skipped"
                step.error = f"skipped by reflection: {advice.reason}"
                continue
            # retry / replace: adjust the step then re-run it.
            if advice.action == ACTION_REPLACE and advice.skill:
                step.skill = advice.skill
                step.description = advice.description or f"{advice.skill} (replacement)"
            if advice.params:
                step.params = {**step.params, **advice.params}
            step.error = None
            self._run_step(step, by_id)

        return plan

    # ------------------------------------------------------------------ #
    def _advise(self, plan: Plan, step: PlanStep, context: str) -> ReflectionAdvice:
        skills = self._available_skills
        if skills is None:
            skills = self._discover_skills(plan.registry_url)
        try:
            return self.reflector.reflect(
                Goal(**plan.goal),
                step,
                step.error or "unknown error",
                available_skills=skills,
                context_summary=context,
            )
        except ReflectionError as exc:
            # Advice itself failed: treat as abort to avoid an infinite loop.
            return ReflectionAdvice(action=ACTION_ABORT, reason=str(exc))

    def _discover_skills(self, registry_url: str) -> list[str]:
        try:
            with RegistryClient(registry_url, timeout=10.0) as registry:
                entries = registry.list_skills()
        except RegistryClientError as exc:
            logger.warning("Skill discovery for reflection failed: %s", exc)
            return []
        names: list[str] = []
        for entry in entries:
            skill = entry.get("skill") if isinstance(entry, dict) else None
            if isinstance(skill, dict) and skill.get("name"):
                names.append(str(skill["name"]))
        return names

    @staticmethod
    def _context_summary(plan: Plan, by_id: dict[int, PlanStep]) -> str:
        """Multi-turn context: what already succeeded (or was skipped)."""
        lines: list[str] = []
        for step in plan.steps:
            if step.status == "done" and step.result is not None:
                outputs = step.result.get("outputs", {})
                summary = {
                    k: (str(v)[:120] if not isinstance(v, dict) else {kk: str(vv)[:60] for kk, vv in list(v.items())[:5]})
                    for k, v in list(outputs.items())[:5]
                }
                lines.append(f"  step {step.step_id} ({step.skill}): OK outputs={json.dumps(summary, ensure_ascii=False)}")
            elif step.status == "skipped":
                lines.append(f"  step {step.step_id} ({step.skill}): skipped")
        return "\n".join(lines)

    def _execute_available(self, plan: Plan, by_id: dict[int, PlanStep]) -> None:
        """Deterministic pass executing every step whose deps are satisfiable."""
        for step in plan.steps:
            deps_ok = all(by_id[d].status == "done" for d in step.depends_on if d in by_id)
            if not deps_ok:
                step.status = "failed"
                step.error = "a dependency step failed"
                continue
            if step.status in ("done", "skipped"):
                continue
            self._run_step(step, by_id)

    def _run_step(self, step: PlanStep, by_id: dict[int, PlanStep]) -> None:
        from .planner import _resolve_templates

        try:
            params = _resolve_templates(step.params, by_id)
            if step.kind == "card-skill":
                result = self.client.execute(
                    step.skill,
                    geocards=step.geocards,
                    spatial=step.spatial,
                    temporal=step.temporal,
                    params=params,
                    request_id=f"agent-step-{step.step_id}",
                )
            else:
                result = self.client.execute_skill(
                    step.skill,
                    params=params,
                    spatial=step.spatial,
                    temporal=step.temporal,
                    request_id=f"agent-step-{step.step_id}",
                )
            step.result = result
            step.status = "done"
        except Exception as exc:  # noqa: BLE001 - surface to reflection
            logger.warning("Plan step %d failed: %s", step.step_id, exc)
            step.status = "failed"
            step.error = str(exc)


def evaluate_plan(
    plan: Plan,
    reflector: PlanReflector | None = None,
    config: LLMConfig | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """LLM-based self-assessment of a finished plan.

    Args:
        plan: The executed plan (steps carry ``result`` when done).
        reflector: Reuse a :class:`PlanReflector` (its client/config); when
            omitted one is built from ``config``/env.
        config: :class:`LLMConfig` used when ``reflector`` is not given.

    Returns:
        ``{"satisfied": bool, "score": int, "notes": str}``.
    """
    owns_reflector = reflector is None
    reflector = reflector or PlanReflector(config=config, client=client)
    try:
        goal = json.dumps(plan.goal, ensure_ascii=False)
        steps = json.dumps(
            [
                {
                    "step_id": s.step_id,
                    "skill": s.skill,
                    "status": s.status,
                    "error": s.error,
                    "outputs": (
                        {k: str(v)[:120] for k, v in (s.result or {}).get("outputs", {}).items()}
                        if s.result else None
                    ),
                }
                for s in plan.steps
            ],
            ensure_ascii=False,
        )
        messages = [
            {"role": "system", "content": _EVALUATE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Goal: {goal}\n\nExecuted plan:\n{steps}\n\n"
                "Output ONLY the JSON review object.",
            },
        ]
        content = reflector._complete(messages)  # noqa: SLF001 - same class family
        data = json.loads(content)
        return {
            "satisfied": bool(data.get("satisfied")),
            "score": int(data.get("score", 0)),
            "notes": str(data.get("notes", "")),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, ReflectionError) as exc:
        raise ReflectionError(f"Plan evaluation failed: {exc}") from exc
    finally:
        if owns_reflector:
            reflector.close()
