"""GeoAgent — deterministic, capability-based orchestration (V0.5+).

V1.0 additions: declarative **pipeline goals** (DAG / skill chaining) with
output templates, and an optional **LLM-assisted goal translator**
(:mod:`geonexus.agent.llm_planner`) — the LLM only translates natural
language into a structured :class:`Goal`; execution always stays on the
deterministic executor.

V1.1 additions: **registry-grounded planning** (:func:`plan_from_text_with_registry`)
and **reflective execution** (:mod:`geonexus.agent.reflective`) — an LLM
diagnoses failed steps and proposes repairs (retry/replace/skip/abort), and
a finished plan can be self-assessed (:func:`evaluate_plan`).
"""

from .llm_planner import (
    LLMConfig,
    LLMGoalPlanner,
    plan_from_text,
    plan_from_text_with_registry,
)
from .planner import (
    ExecutionError,
    GeoAgentPlanner,
    Goal,
    GoalStep,
    Plan,
    PlanExecutor,
    PlanStep,
    run_goal,
)
from .reflective import (
    ACTION_ABORT,
    ACTION_REPLACE,
    ACTION_RETRY,
    ACTION_SKIP,
    DEFAULT_MAX_REFLECTIONS,
    PlanReflector,
    ReflectionAdvice,
    ReflectionError,
    ReflectiveExecutor,
    evaluate_plan,
)

__all__ = [
    "Goal",
    "GoalStep",
    "Plan",
    "PlanStep",
    "GeoAgentPlanner",
    "PlanExecutor",
    "run_goal",
    "ExecutionError",
    "LLMConfig",
    "LLMGoalPlanner",
    "plan_from_text",
    "plan_from_text_with_registry",
    "PlanReflector",
    "ReflectiveExecutor",
    "ReflectionAdvice",
    "ReflectionError",
    "evaluate_plan",
    "ACTION_RETRY",
    "ACTION_REPLACE",
    "ACTION_SKIP",
    "ACTION_ABORT",
    "DEFAULT_MAX_REFLECTIONS",
]
