"""GeoAgent — deterministic, capability-based orchestration (V0.5+).

V1.0 additions: declarative **pipeline goals** (DAG / skill chaining) with
output templates, and an optional **LLM-assisted goal translator**
(:mod:`geonexus.agent.llm_planner`) — the LLM only translates natural
language into a structured :class:`Goal`; execution always stays on the
deterministic executor.
"""

from .llm_planner import LLMConfig, LLMGoalPlanner, plan_from_text
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
]
