"""GeoAgent planner (V0.5).

A **deterministic, capability-based** planner: given a goal, it discovers
matching skills and GeoCards at a shared registry (with contract
pre-filtering), produces an explicit plan of pushdown execution steps, and
executes them node-by-node.

This is deliberately not an LLM agent. It is the orchestration layer that a
future GeoAgent can build on.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..federation import FederatedExecutionError, FederatedGeoMCPClient
from ..registry import RegistryClient, RegistryClientError

logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    """Raised when a goal cannot be planned or executed."""


class GoalStep(BaseModel):
    """One declarative step of a pipeline goal (V1.0: DAG / skill chaining).

    ``params`` values may reference the outputs of earlier steps with
    ``${stepN.outputs.key}`` templates, where ``N`` is the 1-based ``step``
    index of that step in ``Goal.steps``.
    """

    skill: str
    label: str = ""
    temporal: dict[str, str] | None = None
    geocards: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)


class Goal(BaseModel):
    """A structured orchestration goal.

    Attributes:
        capability: Required capability, e.g. ``ndvi``.
        skill: Optional explicit skill name (bypasses capability matching).
        spatial: Optional geospatial context ``{"bbox": [...], "crs": ...}``.
        temporal_steps: Optional list of temporal windows
            ``[{"start": ..., "end": ...}, ...]`` — one plan step per window.
        required_bands: Bands the asset must provide.
        params: Skill parameters passed through to every step.
        steps: Optional declarative pipeline (``GoalStep`` list) with
            dependencies — when set, it defines the plan directly instead of
            the capability-based expansion.
        label: Optional human-readable goal name.
    """

    capability: str
    skill: str | None = None
    spatial: dict[str, Any] = field(default_factory=dict)
    temporal_steps: list[dict[str, str]] = field(default_factory=list)
    required_bands: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    steps: list[GoalStep] = field(default_factory=list)
    label: str = ""


@dataclass
class PlanStep:
    """One pushdown execution step of a plan."""

    step_id: int
    kind: str  # "card-skill" (execute on the card's node) | "skill-only"
    skill: str
    node_url: str
    geocards: list[str]
    spatial: dict[str, Any] | None
    temporal: dict[str, str] | None
    params: dict[str, Any]
    description: str
    depends_on: list[int] = field(default_factory=list)
    status: str = "pending"  # pending | done | failed
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "kind": self.kind,
            "skill": self.skill,
            "node_url": self.node_url,
            "geocards": self.geocards,
            "spatial": self.spatial,
            "temporal": self.temporal,
            "params": self.params,
            "description": self.description,
            "depends_on": self.depends_on,
            "status": self.status,
            "error": self.error,
        }


@dataclass
class Plan:
    """An executable plan: goal + ordered steps."""

    goal: dict[str, Any]
    registry_url: str
    steps: list[PlanStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "registry": self.registry_url,
            "steps": [s.to_dict() for s in self.steps],
        }

    @property
    def done(self) -> int:
        return sum(1 for s in self.steps if s.status == "done")

    @property
    def failed(self) -> int:
        return sum(1 for s in self.steps if s.status == "failed")


class GeoAgentPlanner:
    """Plans a goal into pushdown execution steps via registry discovery."""

    def __init__(self, registry_url: str, timeout: float = 30.0) -> None:
        self.registry_url = registry_url.rstrip("/")
        self._registry = RegistryClient(self.registry_url, timeout=timeout)

    def plan(self, goal: Goal) -> Plan:
        """Discover skills + cards and build the step list.

        When ``goal.steps`` is set, the declarative pipeline is planned
        directly instead of the capability-based expansion.
        """
        if goal.steps:
            return self.plan_pipeline(goal)
        try:
            if goal.skill is not None:
                skills = self._registry.search_skills(name=goal.skill)
            else:
                skills = self._registry.search_skills(capability=goal.capability)
        except RegistryClientError as exc:
            raise ExecutionError(f"Registry skill discovery failed: {exc}") from exc
        if not skills:
            raise ExecutionError(
                f"No skill provides capability '{goal.capability}' at {self.registry_url}"
            )

        bbox = goal.spatial.get("bbox") if goal.spatial else None
        crs = goal.spatial.get("crs") if goal.spatial else None
        # A spatial context without a bbox is meaningless for execution; only
        # pass it through when it can satisfy the SpatialContext contract.
        step_spatial: dict[str, Any] | None = None
        if bbox is not None:
            step_spatial = {"bbox": bbox}
            if crs is not None:
                step_spatial["crs"] = crs
        card_results: list[dict[str, Any]] = []
        try:
            card_results = self._registry.search(
                capability=goal.capability,
                bbox=bbox,
                crs=crs,
                required_bands=goal.required_bands or None,
            )
        except RegistryClientError as exc:
            raise ExecutionError(f"Registry card discovery failed: {exc}") from exc
        cards = [r["entry"] for r in card_results]

        steps: list[PlanStep] = []
        windows: list[dict[str, str] | None] = list(goal.temporal_steps) or [None]
        for skill_entry in skills:
            skill_name = skill_entry["skill"]["name"]
            if cards:
                for card in cards:
                    for window in windows:
                        label = f"{skill_name} on {card['card']['id']}"
                        if window:
                            label += f" [{window.get('start')}..{window.get('end')}]"
                        steps.append(
                            PlanStep(
                                step_id=len(steps) + 1,
                                kind="card-skill",
                                skill=skill_name,
                                node_url=card["node_url"],
                                geocards=[card["card"]["id"]],
                                spatial=step_spatial,
                                temporal=window,
                                params=goal.params,
                                description=label,
                            )
                        )
            else:
                for window in windows:
                    label = f"{skill_name} (no card)"
                    if window:
                        label += f" [{window.get('start')}..{window.get('end')}]"
                    steps.append(
                        PlanStep(
                            step_id=len(steps) + 1,
                            kind="skill-only",
                            skill=skill_name,
                            node_url=skill_entry["node_url"],
                            geocards=[],
                            spatial=step_spatial,
                            temporal=window,
                            params=goal.params,
                            description=label,
                        )
                    )
        logger.info("Planned %d step(s) for goal '%s'", len(steps), goal.label or goal.capability)
        return Plan(
            goal=goal.model_dump(exclude_none=True),
            registry_url=self.registry_url,
            steps=steps,
        )

    # ------------------------------------------------------------------ #
    # Pipeline goals (V1.0: DAG / skill chaining)
    # ------------------------------------------------------------------ #
    def plan_pipeline(self, goal: Goal) -> Plan:
        """Plan a declarative pipeline from ``goal.steps``.

        Each ``GoalStep`` becomes a plan step whose node is resolved from the
        registry (by the step's geocards if given, otherwise by the skill's
        offering node). ``depends_on`` references are validated; cycles are
        rejected. Output templates (``${stepN.outputs.key}``) are resolved by
        :class:`PlanExecutor` at execution time.
        """
        if not goal.steps:
            raise ExecutionError("plan_pipeline requires goal.steps")
        for index, gstep in enumerate(goal.steps, start=1):
            bad_deps = [d for d in gstep.depends_on if not (1 <= d < index)]
            if bad_deps:
                raise ExecutionError(
                    f"Step {index} depends_on invalid reference(s) {bad_deps} "
                    "(dependencies must point at earlier step ids)"
                )

        # Dependency graph cycle check (defensive; forward-only deps already
        # guarantee acyclic, but validate anyway).
        try:
            _topo_order([(i, g.depends_on) for i, g in enumerate(goal.steps, start=1)])
        except ExecutionError as exc:
            raise ExecutionError(f"Pipeline goal has a dependency cycle: {exc}") from exc

        steps: list[PlanStep] = []
        for index, gstep in enumerate(goal.steps, start=1):
            entry = self._registry.get_skill(gstep.skill)
            if not isinstance(entry, dict) or "node_url" not in entry:
                raise ExecutionError(
                    f"Pipeline step {index}: skill '{gstep.skill}' not registered "
                    f"at {self.registry_url}"
                )
            node_url = entry["node_url"]
            geocards = list(gstep.geocards)
            kind = "card-skill" if geocards else "skill-only"
            if geocards:
                # Resolve each referenced card to its owning node (the step
                # executes where the card lives).
                owners = {
                    cid: self._registry.get(cid).get("node_url")
                    for cid in geocards
                    if (self._registry.get(cid) or {}).get("node_url")
                }
                if owners:
                    node_url = next(iter(owners.values()))
            label = gstep.label or f"{gstep.skill}"
            steps.append(
                PlanStep(
                    step_id=index,
                    kind=kind,
                    skill=gstep.skill,
                    node_url=node_url,
                    geocards=geocards,
                    spatial=goal.spatial or None,
                    temporal=gstep.temporal,
                    params=dict(gstep.params),
                    description=label,
                    depends_on=list(gstep.depends_on),
                )
            )
        logger.info("Planned pipeline of %d step(s) for goal '%s'", len(steps), goal.label)
        return Plan(
            goal=goal.model_dump(exclude_none=True),
            registry_url=self.registry_url,
            steps=steps,
        )


def _topo_order(steps: list[tuple[int, list[int]]]) -> list[int]:
    """Return a valid execution order for (id, depends_on) pairs."""
    by_id = {step_id: deps for step_id, deps in steps}
    order: list[int] = []
    visited: set[int] = set()
    visiting: set[int] = set()

    def visit(step_id: int) -> None:
        if step_id in visiting:
            raise ExecutionError(f"cycle involving step {step_id}")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dep in by_id.get(step_id, []):
            if dep in by_id:
                visit(dep)
        visiting.discard(step_id)
        visited.add(step_id)
        order.append(step_id)

    for step_id, _ in steps:
        visit(step_id)
    return order


_TEMPLATE_RE = re.compile(r"\$\{step(\d+)\.outputs\.([A-Za-z0-9_]+)\}")


def _resolve_templates(value: Any, by_id: dict[int, PlanStep]) -> Any:
    """Resolve ``${stepN.outputs.key}`` templates against completed steps.

    Applies recursively to strings inside dicts/lists. A template referencing
    a missing output leaves the string untouched and records a warning.
    """
    if isinstance(value, str):

        def _sub(match: re.Match[str]) -> str:
            step_id = int(match.group(1))
            key = match.group(2)
            step = by_id.get(step_id)
            if step is None or step.result is None:
                return match.group(0)
            return str(step.result.get("outputs", {}).get(key, match.group(0)))

        return _TEMPLATE_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_templates(v, by_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_templates(v, by_id) for v in value]
    return value


class PlanExecutor:
    """Executes a plan step-by-step via federated pushdown.

    Steps with dependencies run after their dependencies; ``${stepN.outputs.key}``
    templates in step params are resolved from earlier steps' results.
    """

    def __init__(self, registry_url: str, timeout: float = 30.0) -> None:
        self.client = FederatedGeoMCPClient(registry_url, timeout=timeout)

    def run(self, plan: Plan) -> Plan:
        by_id = {s.step_id: s for s in plan.steps}
        try:
            order = _topo_order([(s.step_id, s.depends_on) for s in plan.steps])
        except ExecutionError:
            order = [s.step_id for s in plan.steps]
        for step_id in order:
            step = by_id[step_id]
            if any(by_id[d].status != "done" for d in step.depends_on if d in by_id):
                step.status = "failed"
                step.error = "a dependency step failed"
                continue
            params = _resolve_templates(step.params, by_id)
            try:
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
            except FederatedExecutionError as exc:
                logger.warning("Plan step %d failed: %s", step.step_id, exc)
                step.status = "failed"
                step.error = str(exc)
        return plan

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> PlanExecutor:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def run_goal(goal: Goal, registry_url: str, timeout: float = 30.0) -> Plan:
    """Plan a goal (capability-based or declarative pipeline) and execute it,
    returning the completed plan."""
    planner = GeoAgentPlanner(registry_url, timeout=timeout)
    plan = planner.plan_pipeline(goal) if goal.steps else planner.plan(goal)
    with PlanExecutor(registry_url, timeout=timeout) as executor:
        return executor.run(plan)
