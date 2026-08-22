# GeoAgent (V0.5 → V1.0)

> GeoAgent orchestrates capabilities. The core is a **deterministic,
> capability-based planner**; V1.0 adds **pipeline goals (DAG / skill
> chaining)** and an **LLM-assisted goal translator**.

## What it does

Given a **goal** (a capability, an optional spatial context, one or more
temporal windows, required bands and skill parameters), the GeoAgent:

1. **Discovers** skills and GeoCards at a shared GeoCard Registry
   (capability matching + contract pre-filtering).
2. **Plans** one pushdown execution step per (skill × card × temporal
   window) — each step is executed **on the node that owns the data**.
3. **Executes** the plan step-by-step via `FederatedGeoMCPClient` and
   records per-step status (`pending | done | failed`) with results/errors.
4. Returns the completed plan; callers can compose results.

## Pipeline goals (V1.0: DAG / skill chaining)

A goal can instead declare a **pipeline** (`Goal.steps`): ordered steps with
explicit dependencies (`depends_on`), where a step's params may reference
the outputs of earlier steps via `${stepN.outputs.key}` templates. The
executor runs steps in topological order, resolves templates from completed
results, and skips steps whose dependencies failed.

Pipeline steps can be **any registry-discoverable skill** — including
**remote OGC API - Processes skills** registered via
`register_ogc_process_skill` (see `docs/OGC.md`). Local outputs flow into
OGC process inputs through the same templates:

```python
Goal(
    capability="note",
    steps=[
        GoalStep(skill="local-note", label="local", temporal={...}),        # local GeoSkill
        GoalStep(skill="echo", label="OGC",
                 params={"message": "${step1.outputs.note}"},
                 depends_on=[1]),                                            # remote OGC process
    ],
)
```

Run it: `geonexus demo ogc-pipeline` (offline mock) — verified with
`run_goal` in `tests/test_ogc_pipeline.py`.

```python
from geonexus.agent import Goal, GoalStep, run_goal

goal = Goal(
    capability="ndvi",
    spatial={"bbox": [-73.9, -15.0, -44.0, 5.0], "crs": "EPSG:4326"},
    steps=[
        GoalStep(skill="ndvi-analysis", label="NDVI 2015",
                 temporal={"start": "2015-01-01", "end": "2015-12-31"}),
        GoalStep(skill="ndvi-analysis", label="NDVI 2025",
                 temporal={"start": "2025-01-01", "end": "2025-12-31"}),
        GoalStep(
            skill="ndvi-change", label="Change 2025-2015",
            params={
                "ndvi_a": "${step1.outputs.ndvi_raster}",
                "ndvi_b": "${step2.outputs.ndvi_raster}",
            },
            depends_on=[1, 2],
        ),
    ],
    label="vegetation-change-amazon-pipeline",
)
plan = run_goal(goal, "http://127.0.0.1:8790")
```

Dependencies must point at *earlier* step ids; cycles are rejected at plan
time (`ExecutionError`).

## LLM-assisted goal translation (V1.0)

[`LLMGoalPlanner`](MCP.md) turns a **natural-language request** into a
structured `Goal` via any **OpenAI-compatible** `/chat/completions` endpoint
(OpenAI, DeepSeek, local vLLM/Ollama-compatible servers). The LLM only
translates: the output is schema-validated into a `Goal` (with one repair
retry), and execution always runs on the deterministic executor.

```bash
export GEONEXUS_LLM_API_KEY=sk-...            # required
export GEONEXUS_LLM_BASE_URL=https://api.openai.com/v1   # optional
export GEONEXUS_LLM_MODEL=gpt-4o-mini         # optional

geonexus agent ask "分析亚马逊雨林 2015 与 2025 的植被变化" \
    --registry http://127.0.0.1:8790
```

The CLI grounds the LLM with the registry's actual capabilities and skills,
then plans and executes the translated goal. Without `GEONEXUS_LLM_API_KEY`
(or `--llm-api-key`), the planner refuses with a clear error — it never
silently skips the LLM step.

## Honest scope

- The planner is **rule-based**: capability → skill discovery → contract
  match → step enumeration. It has no LLM, no natural language and no
  free-form reasoning.
- This is the orchestration foundation a future (V1.x) GeoAgent planner —
  possibly LLM-assisted — will build on. The `Goal` / `Plan` / `PlanStep`
  structures are deliberately plain JSON so any planner can drive them.

## Reflective execution (V1.1: LLM-assisted repair)

V1.1 adds **LLM-assisted reflection** on top of the deterministic executor.
The LLM never executes — it only *advises*; all execution stays on
`PlanExecutor`:

- **Registry-grounded planning** — `plan_from_text_with_registry(text,
  registry_url)` discovers the skills that actually exist at a registry and
  grounds the LLM translation in them (no hallucinated skill names):

  ```python
  from geonexus.agent import plan_from_text_with_registry
  goal = plan_from_text_with_registry("分析亚马逊 2015 与 2025 植被变化",
                                      "http://127.0.0.1:8790")
  ```

- **PlanReflector** — given the goal, a failed step, the error and the
  *context of already-completed steps* (multi-turn), an LLM proposes a
  repair: `retry` (same step), `replace` (new skill/params), `skip` (drop
  the step) or `abort` (give up).

- **ReflectiveExecutor** — runs a plan like `PlanExecutor`; on failure it
  reflects and retries per the advice, bounded by `max_reflections`
  (default 3). Each step records its `reflections` history (observable via
  `step.to_dict()`):

  ```python
  from geonexus.agent import PlanReflector, ReflectiveExecutor, Goal, GeoAgentPlanner
  plan = GeoAgentPlanner("http://127.0.0.1:8790").plan(goal)
  with PlanReflector() as reflector:
      plan = ReflectiveExecutor("http://127.0.0.1:8790", reflector).run(plan)
  for step in plan.steps:
      print(step.step_id, step.status, step.reflections)
  ```

- **Result self-assessment** — `evaluate_plan(plan, reflector=...)` asks the
  LLM to review the finished plan against the goal and returns
  `{"satisfied": bool, "score": 0..100, "notes": str}`.

## API

```python
from geonexus.agent import Goal, run_goal

goal = Goal(
    capability="ndvi",
    spatial={"bbox": [-73.9, -15.0, -44.0, 5.0], "crs": "EPSG:4326"},
    temporal_steps=[
        {"start": "2015-01-01", "end": "2015-12-31"},
        {"start": "2025-01-01", "end": "2025-12-31"},
    ],
    required_bands=["B04", "B08"],
    params={"red": "red.tif", "nir": "nir.tif"},   # skill arguments
    label="vegetation-change-amazon",
)

plan = run_goal(goal, "http://127.0.0.1:8790")
for step in plan.steps:
    print(step.step_id, step.description, step.status)
```

Components: `Goal` (pydantic), `GeoAgentPlanner.plan(goal) -> Plan`,
`PlanExecutor.run(plan) -> Plan`, `run_goal(goal, registry_url) -> Plan`
(all in `src/geonexus/agent/planner.py`).

## CLI

```bash
# Plan + execute a goal against a live registry:
geonexus agent run --registry http://127.0.0.1:8790 \
    --capability ndvi --bbox=-73.9,-15,-44,5 --bands B04,B08 \
    --window 2015-01-01/2015-12-31 --window 2025-01-01/2025-12-31 \
    --param red=/abs/red.tif --param nir=/abs/nir.tif \
    --label vegetation-change-amazon

# Natural-language goal via an OpenAI-compatible LLM (V1.0):
geonexus agent ask "分析亚马逊雨林 2015 与 2025 的植被变化" \
    --registry http://127.0.0.1:8790

# Bundled demo (registry + data node + 3-step pipeline):
geonexus demo agent
```

## Demo

`geonexus demo agent` starts a registry and a data node, advertises the
`sentinel-2-amazon` card plus the `ndvi-analysis` and `ndvi-change` skills,
generates synthetic scenes, then plans and executes the **pipeline** goal
"vegetation-change-amazon": NDVI 2015 → NDVI 2025 → change (step 3 consumes
steps 1–2 outputs via `${stepN.outputs.ndvi_raster}` templates).

Output (reference run):

```
step 1: NDVI 2015 -> done
step 2: NDVI 2025 -> done
step 3: Change 2025-2015 -> done
NDVI 2015 mean=0.749 | 2025 mean=0.320 | change=-0.430
```

## Relationship to the roadmap

- V0.5: deterministic planner, GeoSkill registry, MCP bridge (stdio).
- V1.0: pipeline goals (DAG/skill chaining), LLM-assisted goal translation
  (OpenAI-compatible), MCP Streamable HTTP, Registry persistence + auth.
- V1.x: plan repair/retry, cross-node cost-aware scheduling, LLM-native
  agent loops.
