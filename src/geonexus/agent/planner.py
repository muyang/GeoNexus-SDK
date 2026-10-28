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
from ..geotask import (
    GeoTask,
    LocalBackend,
    TaskGraph,
    TaskKind,
    TaskRunner,
    TaskState,
    TaskStore,
)
from ..registry import RegistryClient, RegistryClientError

logger = logging.getLogger(__name__)

#: 执行后端里注册的处理器名。GeoTask 的 ``spec["skill"]`` 用它路由到"执行一个
#: 计划步骤"这件事本身（步骤真正要调用的技能名在 ``spec["step_skill"]`` 里）。
_AGENT_STEP_HANDLER = "__agent_plan_step__"


class ExecutionError(Exception):
    """Raised when a goal cannot be planned or executed."""


class _StepFailed(Exception):
    """执行体内部信号：该步骤失败。

    执行后端的 ``Handler`` 协议只有"返回=成功、抛异常=失败"两种结局，而
    ``_run_step`` 已经把失败原因写进 ``step.error``（保持与旧实现逐字一致），
    所以这里只做一个不携带信息的信号，投影阶段不改写 ``step.error``。
    """


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
    status: str = "pending"  # pending | done | failed | skipped
    result: dict[str, Any] | None = None
    error: str | None = None
    # Reflection history (populated by ReflectiveExecutor; empty otherwise).
    reflections: list[dict[str, Any]] = field(default_factory=list)

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
            "reflections": self.reflections,
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


# --------------------------------------------------------------------------- #
# PlanStep ↔ GeoTask 的桥接
# --------------------------------------------------------------------------- #
def _task_id(step_id: int) -> str:
    """PlanStep 的数字 id → GeoTask 的 task_id。

    ``PlanStep.step_id`` 是 1 起的整数（``${step1...}`` 模板也用它），而 GeoTask
    用字符串。前缀是为了让两者在同一张图里可读地对照。
    """
    return f"step-{step_id}"


def _template_deps(step: PlanStep) -> set[int]:
    """从参数模板里推断依赖：``${stepN.outputs.k}`` 隐含"必须先跑 N"。

    旧执行器是**串行拓扑序**，所以即使没写 ``depends_on``，只要步骤按顺序排列，
    模板也能取到值——隐式依赖"碰巧能用"。并行之后这条假设不成立（两个独立步骤会
    同时开跑），因此必须把隐式依赖变成显式的图边。

    这不是新增语义，而是把原本靠执行顺序维持的关系写进 DAG。
    """
    found: set[int] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.update(int(m.group(1)) for m in _TEMPLATE_RE.finditer(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(step.params)
    return found


def _effective_deps(step: PlanStep, by_id: dict[int, PlanStep]) -> list[int]:
    """显式 ``depends_on`` ∪ 模板推断出的依赖，去重并保持升序。"""
    deps = {d for d in step.depends_on if d in by_id}
    deps |= {d for d in _template_deps(step) if d in by_id and d != step.step_id}
    return sorted(deps)


def _to_geotask(step: PlanStep, by_id: dict[int, PlanStep]) -> GeoTask:
    return GeoTask(
        task_id=_task_id(step.step_id),
        kind=TaskKind.SKILL,
        # handler 是**路由键**（后端据此选执行体），step_skill 才是真正的技能名
        spec={"handler": _AGENT_STEP_HANDLER, "step_id": step.step_id, "step_skill": step.skill},
        depends_on=[_task_id(d) for d in _effective_deps(step, by_id)],
        description=step.description or step.skill,
        labels={"node_url": step.node_url} if step.node_url else {},
    )


def _preload_settled(graph: TaskGraph, plan: Plan, task_of: dict[int, str]) -> None:
    """把已经处于终态的步骤预先标记好。

    :class:`ReflectiveExecutor` 的反思循环会反复调用"执行所有可执行步骤"，此时
    上一轮已 ``done``/``skipped`` 的步骤必须作为**依赖已满足**参与，而不是重新跑。
    """
    for step in plan.steps:
        task_id = task_of[step.step_id]
        if step.status == "done":
            graph.mark_done(task_id)
        elif step.status == "skipped":
            graph.mark_skipped(task_id)


def _project_plan(
    plan: Plan, by_id: dict[int, PlanStep], graph: TaskGraph, task_of: dict[int, str]
) -> None:
    """把图/任务状态投影回 ``PlanStep.status``（agent 的四值词汇表）。

    映射（契约 §8.2 的反向）::

        succeeded → done      failed → failed
        skipped   → skipped   cancelled → failed（agent 词汇表里没有"已取消"，
                                           且该状态在今天不可达——没有取消入口）

    只有**被级联**（依赖失败而自己没跑）的步骤需要在这里补状态，而且错误文案逐字
    保留旧实现的 ``a dependency step failed``——避免按文案判断的下游代码失效。
    """
    for step in plan.steps:
        if step.status in ("done", "skipped"):
            continue
        state = graph.state_of(task_of[step.step_id])
        if state is TaskState.SUCCEEDED:
            step.status = "done"
            continue
        if state is TaskState.SKIPPED:
            step.status = "skipped"
            continue
        if state in (TaskState.FAILED, TaskState.CANCELLED):
            step.status = "failed"
            if not step.error:
                if state is TaskState.CANCELLED:
                    step.error = "cancelled"
                else:
                    error = graph.error_of(task_of[step.step_id])
                    step.error = (
                        "a dependency step failed"
                        if error is None or error.code == "upstream_failed"
                        else error.message or error.code
                    )
        # pending/created/queued/…（理论上不会出现在终态之后）：保持原样


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

    Independent steps run **in parallel** (ready-set execution); ``max_workers``
    bounds the concurrency, and ``max_workers=1`` restores the old serial order.
    """

    #: 哪些异常算"这一步失败"（而不是让整个执行器崩掉）。
    #:
    #: 基类只认协议层的 :class:`FederatedExecutionError`；:class:`ReflectiveExecutor`
    #: 把它放宽到所有异常——它的职责就是对"意料之外"的失败（自定义 client、序列化
    #: 问题等）也给出反思建议。两者原本各有一份 ``_run_step`` 来体现这个差别，
    #: 合并时若简单地取其一就会把语义悄悄改掉，所以留成显式的钩子。
    step_error_types: tuple[type[BaseException], ...] = (FederatedExecutionError,)

    def __init__(
        self,
        registry_url: str,
        timeout: float = 30.0,
        api_key: str | None = None,
        max_workers: int = 4,
    ) -> None:
        """Create an executor.

        Args:
            registry_url: Base URL of the shared GeoCard Registry.
            timeout: Request timeout for node calls.
            api_key: Optional ``X-API-Key`` forwarded on node calls — needed
                when nodes enforce authentication (server-side / BFF usage).
            max_workers: 同时执行多少步。步骤是网络下推（IO 密集），所以默认
                并发 4；设成 1 就退回旧的串行语义。
        """
        self.client = FederatedGeoMCPClient(registry_url, timeout=timeout, api_key=api_key)
        self.max_workers = max(1, max_workers)

    def run(self, plan: Plan) -> Plan:
        """执行计划（就绪集并行，见 :meth:`_execute_ready_set`）。"""
        return self._execute_ready_set(plan)

    def _execute_ready_set(self, plan: Plan) -> Plan:
        """就绪集并行执行：每一轮把所有"依赖已满足"的步骤一起提交。

        旧实现是 ``for step_id in order:`` —— 严格串行，DAG 里可并行的部分被浪费。
        独立步骤现在真的同时执行，由 ``tests/test_agent_parallel.py`` 用栅栏证明
        （不用计时断言）。

        语义与旧实现保持一致的两处关键点：

        1. ``${stepN.outputs.key}`` 模板在**提交时**解析。就绪集保证被引用的步骤
           已完成，所以并行不会让模板取到空值；
        2. 依赖失败的步骤仍然是 ``failed`` + ``a dependency step failed``
           （逐字保留，避免下游按文案判断的代码失效）。

        独立成方法而不是写在 ``run()`` 里，是因为 :class:`ReflectiveExecutor`
        覆盖了 ``run()``：它需要调用**这套执行逻辑**而不是（会递归的）``self.run``。
        """
        by_id = {s.step_id: s for s in plan.steps}
        store = TaskStore.memory()
        backend = LocalBackend(
            handlers={_AGENT_STEP_HANDLER: self._step_handler(by_id)},
            max_workers=self.max_workers,
        )
        task_of = {step.step_id: _task_id(step.step_id) for step in plan.steps}
        graph = TaskGraph.from_tasks([_to_geotask(step, by_id) for step in plan.steps])
        graph.bind(store)
        _preload_settled(graph, plan, task_of)
        try:
            TaskRunner(store, backend, executor_id="agent").run(graph)
        finally:
            backend.close()
        _project_plan(plan, by_id, graph, task_of)
        return plan

    # ------------------------------------------------------------------ #
    def _step_handler(self, by_id: dict[int, PlanStep]) -> Any:
        """执行后端的处理器：把一次任务执行映射成一个计划步骤。"""

        def handler(task: GeoTask, _ctx: Any) -> dict[str, Any]:
            step = by_id[int(task.spec["step_id"])]
            self._run_step(step, by_id)
            if step.status == "failed":
                # 失败原因已由 _run_step 写在 step.error 上，这里只是让后端知道
                # 该任务失败了（Handler 协议：返回=成功，抛异常=失败）
                raise _StepFailed(step.error or "step failed")
            return {"step_id": step.step_id, "status": step.status}

        return handler

    def _run_step(self, step: PlanStep, by_id: dict[int, PlanStep]) -> None:
        """执行单个步骤：解析模板 → 下推 → 记录状态。

        失败只写 ``step.status/error``，不抛异常——调用方（执行后端）据此决定
        任务结局；:class:`ReflectiveExecutor` 也需要它在失败后继续反思。
        """
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
        except self.step_error_types as exc:
            logger.warning("Plan step %d failed: %s", step.step_id, exc)
            step.status = "failed"
            step.error = str(exc)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> PlanExecutor:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def run_goal(
    goal: Goal,
    registry_url: str,
    timeout: float = 30.0,
    api_key: str | None = None,
) -> Plan:
    """Plan a goal (capability-based or declarative pipeline) and execute it,
    returning the completed plan. ``api_key`` is forwarded on node calls."""
    planner = GeoAgentPlanner(registry_url, timeout=timeout)
    plan = planner.plan_pipeline(goal) if goal.steps else planner.plan(goal)
    with PlanExecutor(registry_url, timeout=timeout, api_key=api_key) as executor:
        return executor.run(plan)
