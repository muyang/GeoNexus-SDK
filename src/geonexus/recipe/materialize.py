"""物化：``Recipe + 参数`` → :class:`~geonexus.geotask.graph.TaskGraph`。

这是"复用"真正发生的地方。配方是方法，参数是取值，``run_id`` 是这一次运行的
身份；三者合起来才是一个可以调度、可以重放、可以追责的任务图。

三条硬规则：

1. **参数过契约**：先按 :class:`~geonexus.geocard.model.ParamSpec` 校验，再求值。
   不合格的参数在物化阶段就失败（``RecipeParamError``），不会等到跑了半小时才炸。
2. **模板不求值两次**：``${params.x}`` 在物化时替换成字面量；配方文件本身永远保持
   参数化，否则"复用"就变成"改别人的文件"。
3. **任务是幂等键**：``task_id = f"{run_id}:{step_id}"``，同一个 run 重放不会重复执行，
   不同 run 互不干扰（契约见 ``docs/GEOTASK.md`` §5.4）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..geotask.graph import TaskGraph
from ..geotask.model import (
    GeoTask,
    GpuRequirement,
    ResourceRequirement,
    RetryPolicy,
    TaskIO,
    TaskKind,
)
from .model import Recipe, RecipeError, RecipeOutput, RecipeParamError, RecipeStep

__all__ = [
    "PARAM_TEMPLATE_RE",
    "resolve_params",
    "coerce_param_value",
    "materialize",
    "deliverables",
    "RunPlan",
]

#: ``${params.year}``（允许 ``${ params.year }`` 这种空白）。
PARAM_TEMPLATE_RE = re.compile(r"\$\{\s*params\.([A-Za-z_][A-Za-z0-9_]*)\s*\}")

_KIND_TO_TASK_KIND = {
    "skill": TaskKind.SKILL,
    "model": TaskKind.MODEL,
    "batch": TaskKind.BATCH,
    "composite": TaskKind.COMPOSITE,
}


# --------------------------------------------------------------------------- #
# 1. 参数契约
# --------------------------------------------------------------------------- #
def coerce_param_value(name: str, spec_type: str, value: Any, enum: list[Any]) -> Any:
    """把外部传进来的值收成契约声明的类型。

    只做**无歧义**的转换（``"2019"`` → ``2019``）：JSON 表单和命令行都会把数字
    送成字符串，直接拒绝会把人逼疯；但 ``"abc"`` → ``int`` 这种必须拒绝，因为它
    说明调用方理解错了这个参数。
    """
    if spec_type == "integer":
        if isinstance(value, bool):
            raise RecipeParamError(name, "param_invalid_type", f"param '{name}' expects an integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                pass
        raise RecipeParamError(name, "param_invalid_type", f"param '{name}' expects an integer")
    if spec_type == "number":
        if isinstance(value, bool):
            raise RecipeParamError(name, "param_invalid_type", f"param '{name}' expects a number")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                pass
        raise RecipeParamError(name, "param_invalid_type", f"param '{name}' expects a number")
    if spec_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        raise RecipeParamError(name, "param_invalid_type", f"param '{name}' expects a boolean")
    if spec_type == "enum":
        if value not in enum:
            raise RecipeParamError(
                name, "param_invalid_enum", f"param '{name}' must be one of {enum}, got {value!r}"
            )
        return value
    if spec_type in ("bbox", "geometry"):
        if not isinstance(value, (list, tuple, dict)):
            raise RecipeParamError(
                name, "param_invalid_type", f"param '{name}' expects a {spec_type}"
            )
        return list(value) if isinstance(value, (list, tuple)) else value
    if spec_type == "ref":
        if not isinstance(value, str) or not value.strip():
            raise RecipeParamError(
                name, "param_invalid_type", f"param '{name}' expects a non-empty reference"
            )
        return value
    # string / time / 未知类型：原样透传（时间不做解析，避免替调用方猜时区）。
    if isinstance(value, (dict, list)):
        raise RecipeParamError(name, "param_invalid_type", f"param '{name}' expects a string")
    return value


def resolve_params(recipe: Recipe, supplied: dict[str, Any] | None = None) -> dict[str, Any]:
    """按参数契约把调用方给的值补齐、收敛、校验，返回**最终取值**。

    返回的字典一定包含全部参数（含作者写死的 ``fixed``），因此下游步骤不必再
    判断"这个参数会不会没传"。
    """
    supplied = dict(supplied or {})
    known = {p.name: p for p in recipe.params}

    unknown = sorted(set(supplied) - set(known))
    if unknown:
        raise RecipeParamError(
            unknown[0],
            "param_unknown",
            f"recipe {recipe.id} has no parameter(s): {', '.join(unknown)}",
        )

    resolved: dict[str, Any] = {}
    for spec in recipe.params:
        if spec.scope == "fixed":
            # 作者把这一步钉死了：允许重复提交同一个值（前端回填会这么做），
            # 但不允许改成别的 —— 否则"可复现"就是空话。
            if spec.name in supplied and supplied[spec.name] != spec.default:
                raise RecipeParamError(
                    spec.name,
                    "param_fixed",
                    f"param '{spec.name}' is fixed by the author at {spec.default!r}",
                )
            resolved[spec.name] = spec.default
            continue

        if spec.name not in supplied or supplied[spec.name] is None:
            if spec.default is not None:
                resolved[spec.name] = spec.default
                continue
            if spec.required:
                raise RecipeParamError(
                    spec.name, "param_missing", f"required parameter '{spec.name}' is missing"
                )
            resolved[spec.name] = None
            continue

        value = coerce_param_value(spec.name, spec.type, supplied[spec.name], list(spec.enum))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if spec.minimum is not None and value < spec.minimum:
                raise RecipeParamError(
                    spec.name,
                    "param_out_of_range",
                    f"param '{spec.name}' must be >= {spec.minimum}, got {value}",
                )
            if spec.maximum is not None and value > spec.maximum:
                raise RecipeParamError(
                    spec.name,
                    "param_out_of_range",
                    f"param '{spec.name}' must be <= {spec.maximum}, got {value}",
                )
        if spec.enum and value not in spec.enum:
            raise RecipeParamError(
                spec.name,
                "param_invalid_enum",
                f"param '{spec.name}' must be one of {list(spec.enum)}, got {value!r}",
            )
        resolved[spec.name] = value
    return resolved


# --------------------------------------------------------------------------- #
# 2. 模板求值
# --------------------------------------------------------------------------- #
def _substitute(value: Any, params: dict[str, Any], step_id: str) -> Any:
    """递归把 ``${params.x}`` 换成取值。整串就是一个占位符时保留原始类型。"""
    if isinstance(value, str):
        whole = PARAM_TEMPLATE_RE.fullmatch(value.strip())
        if whole is not None:
            name = whole.group(1)
            if name not in params:
                raise RecipeError(f"step '{step_id}' references undeclared param '{name}'")
            return params[name]
        return PARAM_TEMPLATE_RE.sub(lambda m: _as_text(params, m.group(1), step_id), value)
    if isinstance(value, dict):
        return {k: _substitute(v, params, step_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params, step_id) for v in value]
    return value


def _as_text(params: dict[str, Any], name: str, step_id: str) -> str:
    if name not in params:
        raise RecipeError(f"step '{step_id}' references undeclared param '{name}'")
    value = params[name]
    return "" if value is None else str(value)


def _unknown_templates(value: Any) -> set[str]:
    """找出值里出现的所有 ``${params.x}`` 名字（用于提前报错）。"""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(PARAM_TEMPLATE_RE.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            found |= _unknown_templates(item)
    elif isinstance(value, list):
        for item in value:
            found |= _unknown_templates(item)
    return found


# --------------------------------------------------------------------------- #
# 3. 物化
# --------------------------------------------------------------------------- #
def _task_io(ref: str, step_ids: set[str]) -> TaskIO:
    """把引用串变成 :class:`TaskIO`：``step://x/y`` 是上游产物，其余是资产。"""
    if ref.startswith("step://"):
        target, _, field = ref[len("step://") :].partition("/")
        return TaskIO(ref=ref, kind="task_output", from_task=target, field=field or None)
    return TaskIO(ref=ref, kind="asset")


def _retry_policy(step: RecipeStep) -> RetryPolicy:
    """``step.max_attempts`` 是**额外**尝试次数，``RetryPolicy`` 数的是总次数。

    这个 ±1 必须在这里转换，不能让两处各按自己的理解解释同一个数字。
    """
    return RetryPolicy(max_attempts=1 + step.max_attempts)


def _resources(step: RecipeStep) -> ResourceRequirement:
    """配方写的是**预约**（调度器按它算容量），单位与 GEOTASK §2.2 一致：MB。

    这里直接构造而不走 ``from_geocard_runtime``：那条路把 ``memory`` 当 GiB 字符串
    解析，而配方里的 ``memory_mb`` 已经是整数字节量，再换算一次就会差 1024 倍。
    """
    spec = step.resources or {}
    if not spec:
        return ResourceRequirement()

    cpu_raw = spec.get("cpu")
    try:
        cpu = float(cpu_raw) if cpu_raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        cpu = 0.0

    memory_raw = spec.get("memory_mb", spec.get("memory"))
    try:
        memory_mb = int(memory_raw) if memory_raw not in (None, "") else 0
    except (TypeError, ValueError):
        memory_mb = 0

    gpu_spec = spec.get("gpu")
    gpu = None
    if isinstance(gpu_spec, dict):
        kind = gpu_spec.get("kind", "cuda")
        if kind and str(kind).lower() not in ("none", "false", "0"):
            gpu = GpuRequirement(
                kind=str(kind),
                count=int(gpu_spec.get("count", 1) or 1),
                memory_mb=(
                    int(gpu_spec["memory_mb"]) if gpu_spec.get("memory_mb") is not None else None
                ),
            )
    elif gpu_spec and str(gpu_spec).lower() not in ("none", "false", "0"):
        gpu = GpuRequirement(kind=str(gpu_spec))

    return ResourceRequirement(cpu=cpu, memory_mb=memory_mb, gpu=gpu)


class RunPlan:
    """物化结果：任务图 + 这一次运行的取值与交付物。

    交付物解析需要知道"哪一步的哪个输出对应哪个文件"，那是执行期才知道的事，
    所以这里只保留**指向**（``step://``），由调用方在运行结束后解析成 URL。
    """

    def __init__(
        self,
        *,
        recipe: Recipe,
        run_id: str,
        graph: TaskGraph,
        params: dict[str, Any],
        outputs: list[RecipeOutput],
    ) -> None:
        # 计划必须记得自己是**从哪份方案**物化出来的：报告合约、血缘、复现都靠它。
        # 只存 id 的话，事后想拿参数契约或数据快照还得再去查一次目录。
        self.recipe = recipe
        self.run_id = run_id
        self.graph = graph
        self.params = params
        self.outputs = outputs

    @property
    def recipe_id(self) -> str:
        return self.recipe.id

    def task_ids(self) -> list[str]:
        return self.graph.topological_order()

    def output_refs(self) -> dict[str, str]:
        """交付物名字 → ``step://<step>/<output>``。"""
        return {o.name: f"step://{o.parts()[0]}/{o.parts()[1]}" for o in self.outputs}

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "run_id": self.run_id,
            "params": self.params,
            "tasks": self.task_ids(),
            "outputs": {
                o.name: {
                    "from": o.from_,
                    "role": o.role,
                    "media_type": o.media_type,
                }
                for o in self.outputs
            },
        }


def materialize(
    recipe: Recipe,
    params: dict[str, Any] | None = None,
    *,
    run_id: str = "run",
    resolve: Callable[[str], str] | None = None,
    extra_labels: dict[str, str] | None = None,
) -> RunPlan:
    """把配方与参数物化成一次可调度、可重放的运行。

    Args:
        recipe: 已校验的配方。
        params: 调用方给的取值；缺省项回落到作者的默认值。
        run_id: 本次运行的身份，进入每个 ``task_id``。
        resolve: 可选的资产解析钩子。收到步骤声明的 ``uses``，返回实际引用
            （例如把 GeoCard id 换成 ``s3://...`` 或平台内的 ``asset://`` URL）。
            不提供时原样保留，好处是配方与部署解耦。
        extra_labels: 附加到每个任务上的标签（平台用它挂 case/tenant）。
    """
    if not run_id or not run_id.strip():
        raise RecipeError("run_id must not be empty")

    resolved = resolve_params(recipe, params)

    # 提前检查所有模板都能求值：缺参数要在建图之前报错，而不是建到一半。
    declared = {p.name for p in recipe.params}
    for step in recipe.steps:
        for blob in (step.params, step.inputs, step.outputs):
            missing = _unknown_templates(blob) - declared
            if missing:
                raise RecipeError(
                    f"step '{step.id}' references undeclared param(s): {', '.join(sorted(missing))}"
                )

    step_ids = {s.id for s in recipe.steps}
    labels = dict(extra_labels or {})

    tasks: list[GeoTask] = []
    for step in recipe.steps:
        task_id = f"{run_id}:{step.id}"
        uses = resolve(step.uses) if resolve is not None else step.uses
        task = GeoTask(
            task_id=task_id,
            kind=_KIND_TO_TASK_KIND[step.kind],
            spec={
                "uses": uses,
                "declared_uses": step.uses,
                "params": _substitute(step.params, resolved, step.id),
                "description": step.description,
            },
            inputs={
                name: _task_io(ref, step_ids)
                for name, ref in (
                    (k, _substitute(v, resolved, step.id)) for k, v in step.inputs.items()
                )
            },
            outputs={
                name: TaskIO(ref=f"step://{step.id}/{name}", kind="asset") for name in step.outputs
            },
            depends_on=[f"{run_id}:{dep}" for dep in step.depends_on],
            description=step.description,
            retry=_retry_policy(step),
            resources=_resources(step),
            labels={
                **labels,
                "recipe": recipe.id,
                "recipe_step": step.id,
            },
            provenance={
                "origin": "recipe",
                "source": recipe.id,
                "recipe_version": recipe.version or recipe.recipe_version,
                "run_id": run_id,
                "uses": uses,
                "params": {k: v for k, v in resolved.items() if v is not None},
                "data_snapshot": dict(recipe.data_snapshot),
            },
        )
        if step.timeout_seconds is not None:
            task.timeout_seconds = step.timeout_seconds
        tasks.append(task)

    return RunPlan(
        recipe=recipe,
        run_id=run_id,
        graph=TaskGraph(tasks),
        params=resolved,
        outputs=list(recipe.outputs),
    )


def deliverables(plan: RunPlan) -> dict[str, dict[str, Any]]:
    """交付物清单（含指向），交给平台登记成"可下载产物"。

    这里刻意不产生 URL：**产物的位置由运行环境决定**（本地文件、对象存储、
    平台托管），SDK 只保证"哪个步骤的哪个输出是这次交付的结论"。
    """
    return {
        out.name: {
            "from": out.from_,
            "role": out.role,
            "media_type": out.media_type,
            "description": out.description,
            "ref": f"step://{out.parts()[0]}/{out.parts()[1]}",
        }
        for out in plan.outputs
    }
