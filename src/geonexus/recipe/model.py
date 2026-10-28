"""Recipe —— 可执行方案（DAG）的契约。

分工（与 GeoCard 的边界，见 ``docs/RECIPE.md``）：

* **GeoCard** 描述"这是什么、能做什么、由哪些资产组成"（元数据契约，可能不可执行）。
* **Recipe** 描述"怎么跑"：步骤、依赖、参数落到哪（执行契约，必须可执行）。
* **TaskGraph** 是一次**运行**：``Recipe + 参数 + run_id`` 物化出来的实例。

因此本模块只放数据结构与取值校验；物化在 :mod:`geonexus.recipe.materialize`，
存放/审批在 :mod:`geonexus.recipe.registry`。

参数契约**复用** :class:`geonexus.geocard.model.ParamSpec`：同一个字段名、同一套
取值范围，避免"卡片说 reusable、配方说 locked"这种自相矛盾。参数在这里**声明**，
在卡片里**被引用**——只有一份定义。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..geocard.model import COMPONENT_ROLES, ParamSpec

__all__ = [
    "RECIPE_VERSION",
    "STEP_KINDS",
    "RECIPE_ADDRESS_RE",
    "RecipeError",
    "RecipeValidationError",
    "RecipeParamError",
    "parse_recipe_address",
    "format_recipe_address",
    "RecipeStep",
    "RecipeOutput",
    "Recipe",
]

#: 配方文档版本。与 GeoCard 一样，1.x 内只允许**新增**。
RECIPE_VERSION = "1.0"

#: 步骤种类 —— 与 :class:`geonexus.geotask.model.TaskKind` 一一对应。
STEP_KINDS = ("skill", "model", "batch", "composite")

#: ``recipe://<namespace>/<name>[@<version>]``。版本可省，但一旦写了就是身份的一部分。
RECIPE_ADDRESS_RE = re.compile(
    r"^recipe://(?P<namespace>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"/(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:@(?P<version>[A-Za-z0-9][A-Za-z0-9.+_-]*))?$"
)


class RecipeError(Exception):
    """配方层的基类异常。"""


class RecipeValidationError(RecipeError):
    """配方文档本身不合法（结构/引用/环）。"""

    def __init__(self, errors: list[str] | str) -> None:
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


class RecipeParamError(RecipeError):
    """调用方给的参数不满足参数契约。

    ``field`` 是出错参数的机器可读名字，平台据此返回 400 并高亮表单字段；
    ``code`` 说明**为什么**，便于前端区分"没填"和"填错"。
    """

    #: 参数缺失。 ``param_missing``
    #: 未知参数。 ``param_unknown``
    #: 值不在 ``enum`` 内。 ``param_invalid_enum``
    #: 数值越界。 ``param_out_of_range``
    #: 类型不符。 ``param_invalid_type``
    #: 试图覆盖 ``scope: fixed`` 的参数。 ``param_fixed``
    def __init__(self, field: str, code: str, message: str) -> None:
        self.field = field
        self.code = code
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": str(self)}


def parse_recipe_address(address: str) -> tuple[str, str, str | None]:
    """把 ``recipe://ns/name@1.0.0`` 拆成 ``(namespace, name, version)``。

    非法地址抛 :class:`RecipeValidationError` —— 地址是身份，不能"看起来像"就行。
    """
    match = RECIPE_ADDRESS_RE.match(address or "")
    if match is None:
        raise RecipeValidationError(
            f"recipe id must look like 'recipe://<namespace>/<name>[@<version>]', got {address!r}"
        )
    return match.group("namespace"), match.group("name"), match.group("version")


def format_recipe_address(namespace: str, name: str, version: str | None = None) -> str:
    """``(namespace, name, version)`` → ``recipe://ns/name@version``。"""
    base = f"recipe://{namespace}/{name}"
    return f"{base}@{version}" if version else base


class RecipeStep(BaseModel):
    """配方里的一步。

    ``params`` 与 ``inputs`` 里的值可以是字面量，也可以是 ``${params.x}`` 模板：
    配方写的是**方法**，调用方给的才是**取值**。物化时才求值
    （见 :func:`geonexus.recipe.materialize.materialize`），因此同一个配方能跑出
    无限多个具体案例。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Step id, unique within the recipe; used in depends_on.")
    uses: str = Field(
        description=(
            "What runs here: a GeoCard id, a registered skill name, or a nested "
            "'recipe://...' for a composite step."
        )
    )
    kind: str = Field(default="skill", description="One of: " + ", ".join(STEP_KINDS) + ".")
    depends_on: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Literal values or '${params.<name>}' templates handed to the step.",
    )
    inputs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Named inputs: an asset ref, a '${params.<name>}' template, or "
            "'step://<step>/<output>' to consume an upstream result."
        ),
    )
    outputs: dict[str, str] = Field(
        default_factory=dict,
        description="Declared outputs: name → the ref it will be published under.",
    )
    resources: dict[str, Any] = Field(
        default_factory=dict, description="cpu / memory_mb / gpu, as a reservation."
    )
    timeout_seconds: float | None = Field(default=None, description="Per-step timeout.")
    max_attempts: int = Field(
        default=0, ge=0, description="Extra attempts after a retryable failure."
    )
    description: str = ""

    @field_validator("id", "uses")
    @classmethod
    def _not_blank(cls, value: str, info: Any) -> str:
        if not value or not value.strip():
            raise ValueError(f"steps[].{info.field_name} must not be empty")
        return value

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in STEP_KINDS:
            raise ValueError(f"steps[].kind must be one of: {', '.join(STEP_KINDS)}")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _positive_timeout(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("steps[].timeout_seconds must be positive")
        return value

    @model_validator(mode="after")
    def _composite_uses_a_recipe(self) -> RecipeStep:
        """A composite step must nest a recipe, and only a composite step may."""
        nested = self.uses.startswith("recipe://")
        if self.kind == "composite" and not nested:
            raise ValueError(
                f"step '{self.id}': kind 'composite' requires uses to be a 'recipe://' address"
            )
        if nested and self.kind != "composite":
            raise ValueError(
                f"step '{self.id}': uses '{self.uses}' is a recipe, so kind must be 'composite'"
            )
        if nested:
            parse_recipe_address(self.uses)  # 地址本身也要合法
        return self


class RecipeOutput(BaseModel):
    """这个配方对外**交付**什么。

    交付物不是步骤的副产品：它是方案的结论。``role`` 用 GeoCard 的角色词表
    （``data`` / ``knowledge`` / ...），这样平台不必再造一套分类。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(description="Deliverable name, unique within the recipe.")
    from_: str = Field(
        alias="from",
        description="Where it comes from, e.g. 's3.report' (step id . output name).",
    )
    role: str = Field(
        default="knowledge", description="One of: " + ", ".join(COMPONENT_ROLES) + "."
    )
    media_type: str | None = None
    description: str = ""

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("outputs[].name must not be empty")
        return value

    @field_validator("role")
    @classmethod
    def _known_role(cls, value: str) -> str:
        if value not in COMPONENT_ROLES:
            raise ValueError(f"outputs[].role must be one of: {', '.join(COMPONENT_ROLES)}")
        return value

    @model_validator(mode="after")
    def _from_is_step_output(self) -> RecipeOutput:
        if "." not in self.from_:
            raise ValueError(
                f"output '{self.name}': 'from' must be '<step>.<output>', got {self.from_!r}"
            )
        return self

    def parts(self) -> tuple[str, str]:
        """``'s3.report'`` → ``('s3', 'report')``。"""
        step, _, output = self.from_.partition(".")
        return step, output


class Recipe(BaseModel):
    """一个可执行、可复用、可审批的方案。"""

    model_config = ConfigDict(extra="allow")

    recipe_version: str = Field(default=RECIPE_VERSION)
    id: str = Field(description="'recipe://<namespace>/<name>[@<version>]'.")
    name: str = ""
    description: str = ""
    params: list[ParamSpec] = Field(
        default_factory=list,
        description="The reusable parameter contract — the same shape a GeoCard declares.",
    )
    steps: list[RecipeStep] = Field(default_factory=list)
    entrypoint: str | None = Field(
        default=None, description="Step to start from; defaults to the unique root step."
    )
    outputs: list[RecipeOutput] = Field(default_factory=list)
    requires: list[str] = Field(
        default_factory=list,
        description="GeoCard ids this recipe expects to resolve, for pre-flight checks.",
    )
    data_snapshot: dict[str, str] = Field(
        default_factory=dict,
        description="Data versions this recipe was authored against.",
    )
    license: str | None = None
    tags: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    @field_validator("recipe_version")
    @classmethod
    def _supported_version(cls, value: str) -> str:
        if value.split(".")[0] != RECIPE_VERSION.split(".")[0]:
            raise ValueError(
                f"recipe_version {value!r} is not supported; this SDK implements {RECIPE_VERSION}.x"
            )
        return value

    @field_validator("id")
    @classmethod
    def _address_is_valid(cls, value: str) -> str:
        parse_recipe_address(value)
        return value

    @field_validator("params")
    @classmethod
    def _unique_params(cls, value: list[ParamSpec]) -> list[ParamSpec]:
        seen: set[str] = set()
        for item in value:
            if not item.name or not item.name.strip():
                raise ValueError("params[].name must not be empty")
            if item.name in seen:
                raise ValueError(f"duplicate param: {item.name}")
            seen.add(item.name)
        return value

    @model_validator(mode="after")
    def _graph_is_sound(self) -> Recipe:
        """步骤 id 唯一、依赖存在、无环、交付物指向真实输出。"""
        problems: list[str] = []

        if not self.steps:
            problems.append("a recipe must have at least one step")

        by_id: dict[str, RecipeStep] = {}
        for step in self.steps:
            if step.id in by_id:
                problems.append(f"duplicate step id: {step.id}")
            by_id[step.id] = step

        for step in self.steps:
            for dep in step.depends_on:
                if dep == step.id:
                    problems.append(f"step '{step.id}' depends on itself")
                elif dep not in by_id:
                    problems.append(f"step '{step.id}' depends on unknown step '{dep}'")

        # 环：Kahn 拓扑排序，剩下的就是环里的点。
        indegree = {sid: len(s.depends_on) for sid, s in by_id.items()}
        ready = [sid for sid in by_id if indegree[sid] == 0]
        order: list[str] = []
        while ready:
            sid = ready.pop(0)
            order.append(sid)
            for other in by_id.values():
                if sid in other.depends_on:
                    indegree[other.id] -= 1
                    if indegree[other.id] == 0:
                        ready.append(other.id)
        if len(order) != len(by_id):
            cyclic = sorted(set(by_id) - set(order))
            problems.append(f"dependency cycle among steps: {', '.join(cyclic)}")

        if self.entrypoint is not None and self.entrypoint not in by_id:
            problems.append(f"entrypoint '{self.entrypoint}' is not a step")

        # 交付物必须真的存在：否则"交付"只是愿望。
        seen_outputs: set[str] = set()
        for out in self.outputs:
            if out.name in seen_outputs:
                problems.append(f"duplicate output name: {out.name}")
            seen_outputs.add(out.name)
            step_id, output = out.parts()
            source_step = by_id.get(step_id)
            if source_step is None:
                problems.append(f"output '{out.name}' comes from unknown step '{step_id}'")
            elif source_step.outputs and output not in source_step.outputs:
                problems.append(
                    f"output '{out.name}': step '{step_id}' does not declare output '{output}'"
                )

        # 上游引用也要存在（step://x/y）。
        for step in self.steps:
            for name, ref in step.inputs.items():
                if not ref.startswith("step://"):
                    continue
                target, _, field = ref[len("step://") :].partition("/")
                if target not in by_id:
                    problems.append(
                        f"step '{step.id}' input '{name}' reads unknown step '{target}'"
                    )
                elif field and by_id[target].outputs and field not in by_id[target].outputs:
                    problems.append(
                        f"step '{step.id}' input '{name}': step '{target}' "
                        f"does not declare output '{field}'"
                    )

        if problems:
            raise ValueError("; ".join(problems))
        return self

    # ------------------------------------------------------------------ #
    # 取值/查询
    # ------------------------------------------------------------------ #
    @property
    def namespace(self) -> str:
        return parse_recipe_address(self.id)[0]

    @property
    def slug(self) -> str:
        return parse_recipe_address(self.id)[1]

    @property
    def version(self) -> str | None:
        """版本只存在于地址里 —— 单一事实来源，不会出现两个版本号打架。"""
        return parse_recipe_address(self.id)[2]

    def step(self, step_id: str) -> RecipeStep:
        for item in self.steps:
            if item.id == step_id:
                return item
        raise RecipeError(f"recipe {self.id} has no step {step_id!r}")

    def roots(self) -> list[str]:
        """没有依赖的步骤 —— 默认从它们开始。"""
        return [s.id for s in self.steps if not s.depends_on]

    def entry(self) -> str:
        """``entrypoint`` 或（唯一的）根步骤。"""
        if self.entrypoint:
            return self.entrypoint
        roots = self.roots()
        if len(roots) == 1:
            return roots[0]
        raise RecipeError(
            f"recipe {self.id} has {len(roots)} root steps, so it must declare an entrypoint"
        )

    def reusable_param_names(self) -> list[str]:
        return [p.name for p in self.params if p.scope == "reusable"]

    def fixed_param_names(self) -> list[str]:
        return [p.name for p in self.params if p.scope == "fixed"]

    def param(self, name: str) -> ParamSpec | None:
        for item in self.params:
            if item.name == name:
                return item
        return None

    def defaults(self) -> dict[str, Any]:
        """作者写下的默认取值（含 ``fixed`` 参数）。"""
        return {p.name: p.default for p in self.params if p.default is not None}

    def uses(self) -> list[str]:
        """配方会碰到的所有资产/技能/子配方，去重后按出现顺序。"""
        out: list[str] = []
        for step in self.steps:
            if step.uses not in out:
                out.append(step.uses)
        return out

    def deliverable_names(self) -> list[str]:
        return [o.name for o in self.outputs]

    def to_dict(self) -> dict[str, Any]:
        """序列化 —— 写回磁盘、走 GeoMCP、被平台存库都用这一份。"""
        data = self.model_dump(mode="json", exclude_none=True, by_alias=True)
        # 段顺序固定，diff 才稳定；extra 字段按字母序跟在后面。
        ordered: dict[str, Any] = {
            "recipe_version": data.pop("recipe_version"),
            "id": data.pop("id"),
        }
        for key in (
            "name",
            "description",
            "license",
            "tags",
            "params",
            "steps",
            "entrypoint",
            "outputs",
            "requires",
            "data_snapshot",
        ):
            if key in data:
                ordered[key] = data.pop(key)
        ordered.update({k: data[k] for k in sorted(data)})
        return ordered

    def to_yaml(self) -> str:
        import yaml

        return yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)

    # pydantic 的 BaseModel.validate 是已弃用的类方法；这里是实例方法，语义不同，
    # 保留该名字（多处调用方依赖）并显式声明覆盖，避免被当成同签名实现。
    def validate(self) -> Recipe:  # type: ignore[override]
        """重新跑一遍全部校验，失败抛 :class:`RecipeValidationError`。"""
        try:
            return Recipe.model_validate(self.to_dict())
        except Exception as exc:  # pydantic ValidationError → 本层的错误类型
            raise RecipeValidationError(str(exc)) from exc

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recipe:
        try:
            return cls.model_validate(data)
        except RecipeError:
            raise
        except Exception as exc:
            raise RecipeValidationError(str(exc)) from exc
