"""GeoMCP server.

:class:`GeoMCPServer` provides the GeoMCP capability surface (tools,
resources, GeoCards) on top of the transport-independent
:class:`~geonexus.geomcp.protocol.GeoMCPDispatcher`. A FastAPI HTTP transport
is provided by :meth:`GeoMCPServer.create_app`.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..geocard.model import GeoCard
from ..geocard.validator import ContractValidator
from .models import DescribeParams, ExecuteParams, InvokeParams, PlanParams
from .protocol import (
    CONTRACT_BINDING_REJECTED,
    CONTRACT_NOT_SATISFIED,
    EXECUTION_FAILED,
    FEDERATION_UNRESOLVED,
    GEOCARD_NOT_FOUND,
    INVALID_ARGUMENT,
    INVALID_PARAMS,
    PUSHDOWN_UNRESOLVED,
    RECIPE_NOT_FOUND,
    RECIPE_PARAM_INVALID,
    SKILL_NOT_FOUND,
    WRITE_METHODS,
    GeoMCPDispatcher,
    GeoMCPProtocolError,
)

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "1.0.0"


class _RegistryRecipe:
    """本地目录没有、从注册中心取回的那条记录的最小外壳。

    ``geo.plan`` 只用到 ``.recipe``，所以不为了一个字段去重建 RecipeEntry
    （那会连带审核历史等本地概念一起造假）。
    """

    __slots__ = ("recipe",)

    def __init__(self, recipe: Any) -> None:
        self.recipe = recipe


def _recipe_view(entry: Any) -> dict[str, Any]:
    """目录条目 → 线上视图。

    协议里给的是**摘要 + 契约**，不是整张配方：``geo.describe`` 可能一次返回几十
    个配方，把每个步骤的取值都塞进去会让响应大到没人愿意读。要看全文用
    ``geo.plan``（它返回 ``recipe`` 全量）。
    """
    summary = getattr(entry, "summary", None)
    if callable(summary):
        return summary()
    recipe = getattr(entry, "recipe", None)
    if recipe is not None:
        return {
            "id": recipe.id,
            "name": recipe.name,
            "description": recipe.description,
            "version": recipe.version,
            "params": [p.name for p in recipe.params],
            "reusable_params": recipe.reusable_param_names(),
            "outputs": recipe.deliverable_names(),
        }
    return dict(entry) if isinstance(entry, dict) else {"recipe": str(entry)}

# Internal marker preventing delegation loops (never reaches skill handlers).
DELEGATE_MARKER = "__geonode_delegate"


class ExecutionEngine(Protocol):
    """Interface implemented by anything that can execute a GeoMCP request.

    The Local GeoNode runtime implements this protocol; a custom engine can
    be provided to the server for standalone use.
    """

    def execute(self, params: ExecuteParams) -> dict[str, Any]: ...


class _Tool:
    """A registered GeoMCP tool."""

    def __init__(
        self,
        name: str,
        handler: Any,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.handler = handler
        self.description = description or ""
        self.input_schema = input_schema or {}
        self.output_schema = output_schema or {}

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }



def _json_safe(value: Any) -> Any:
    """传输层防御：把 Path / set 这类"能算出来但不能上线"的值转成 JSON 安全形态。

    技能 handler 应该自己返回可序列化的值；但第三方 handler 忘了是常态 ——
    与其让整个节点在 json.dumps 上抛 500（调用方连 JSON-RPC 错误都拿不到），
    不如在这里转换一次，并在文档里写明：输出走线请用字符串路径。
    """
    if isinstance(value, os.PathLike):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    return value
class GeoMCPServer:
    """GeoMCP capability server.

    Args:
        name: Node / server display name.
        engine: Optional :class:`ExecutionEngine` that runs ``geo.execute``.
        geocard_registry: Optional registry of GeoCards (must expose
            ``get(id)``, ``list()``). Defaults to an internal dict-backed
            registry.
        skill_registry: Optional registry of skills (must expose
            ``get(name)``, ``list()``, ``has(name)``).
        contract_validator: Contract checker used to gate ``geo.execute``.
        registry_url: Optional shared GeoCard Registry endpoint. When set,
            requests referencing GeoCards this node does not own are
            **delegated** to the owning node (V1.0+ node-level pushdown).
        forward_timeout: Timeout for delegated execution requests.
        api_keys: Optional set of ``X-API-Key`` values. When non-empty, the
            write method ``geo.execute`` requires a matching header; read
            methods (``geo.capabilities``, ``geo.describe``, ``geo.health``)
            stay open for discovery (mirrors the registry's auth policy).
        forward_api_key: Optional ``X-API-Key`` sent on **outbound delegated**
            requests to other nodes. Independent of the inbound ``api_keys``:
            a node may require its own key while presenting a different
            credential to peers (per-node / cross-domain deployments).
    """

    def __init__(
        self,
        name: str = "geonexus-node",
        engine: ExecutionEngine | None = None,
        geocard_registry: Any | None = None,
        skill_registry: Any | None = None,
        contract_validator: ContractValidator | None = None,
        registry_url: str | None = None,
        forward_timeout: float = 60.0,
        api_keys: set[str] | None = None,
        forward_api_key: str | None = None,
        model_backend: Any | None = None,
        recipe_registry: Any | None = None,
        artifact_roots: list[str] | None = None,
        gaag_registry: Any | None = None,
        pushdown_defaults: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.engine = engine
        self.registry_url = registry_url
        self.forward_timeout = forward_timeout
        self.api_keys = set(api_keys or ())
        self.forward_api_key = forward_api_key
        # 产物文件服务（GET /artifact）的白名单根（GeoNode 会传自己的 workdir）
        self.artifact_roots = [str(r) for r in (artifact_roots or [])]
        # 插件加载状态（由 GeoNode 经 entry-points 发现后写入），health 面暴露
        self.plugin_status: list[dict[str, Any]] = []
        # V2 GAAG：合约注册中心（可选）。有它，contract_binding 才可验证。
        self.gaag_registry = gaag_registry
        self._contract_gate: Any | None = None
        # V2 下推成本模型的缺省估算（data_bytes/bandwidth_bps/remote_queue_s），
        # 请求的 pushdown_spec 可覆盖；都没有时 auto 老实回退 local 并说明理由
        self.pushdown_defaults = dict(pushdown_defaults or {})
        self.geocard_registry = geocard_registry
        self.skill_registry = skill_registry
        #: ``geonexus.model.ModelBackend``（或任何有 ``invoke_card`` 的对象）。
        #: 为 ``None`` 时 ``geo.invoke`` **明确拒绝**而不是假装可用——见 capabilities()。
        self.model_backend = model_backend
        #: ``geonexus.recipe.RecipeRegistry``（或任何满足 ``resolve``/``get`` 的对象）。
        #: 未绑定时 ``geo.plan`` **明确拒绝**，而不是返回一张空图——见 capabilities()。
        self.recipe_registry = recipe_registry
        self.contract_validator = contract_validator or ContractValidator()

        self._tools: dict[str, _Tool] = {}
        self._resources: dict[str, dict[str, Any]] = {}
        self._cards: dict[str, GeoCard] = {}

        self._dispatcher = GeoMCPDispatcher(
            capabilities_fn=self._handle_capabilities,
            describe_fn=self._handle_describe,
            execute_fn=self._handle_execute,
            invoke_fn=self._handle_invoke,
            plan_fn=self._handle_plan,
            health_fn=self._handle_health,
        )

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register_tool(
        self,
        name: str,
        handler: Any,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a tool that can be advertised and executed."""
        self._tools[name] = _Tool(name, handler, description, input_schema, output_schema)
        logger.debug("Registered GeoMCP tool '%s'", name)

    def register_resource(self, name: str, **attributes: Any) -> None:
        """Register a resource (a named, addressable piece of data/service)."""
        self._resources[name] = {"name": name, **attributes}
        logger.debug("Registered GeoMCP resource '%s'", name)

    def register_geocard(self, card: GeoCard) -> None:
        """Register a GeoCard with this server (and its registry when set)."""
        self._cards[card.id] = card
        if self.geocard_registry is not None:
            self.geocard_registry.register(card)
        logger.debug("Registered GeoCard '%s'", card.id)

    # ------------------------------------------------------------------ #
    # Protocol handlers
    # ------------------------------------------------------------------ #
    def _handle_capabilities(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.capabilities()

    def _handle_health(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.health()

    def _handle_describe(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.describe(DescribeParams(**params))

    def _handle_execute(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            execute_params = ExecuteParams(**params)
        except Exception as exc:
            raise GeoMCPProtocolError(INVALID_PARAMS, f"Invalid geo.execute params: {exc}") from exc
        return self.execute(execute_params)

    def _handle_invoke(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            invoke_params = InvokeParams(**params)
        except Exception as exc:
            raise GeoMCPProtocolError(INVALID_PARAMS, f"Invalid geo.invoke params: {exc}") from exc
        return self.invoke(invoke_params)

    def _handle_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            plan_params = PlanParams(**params)
        except Exception as exc:
            raise GeoMCPProtocolError(INVALID_PARAMS, f"Invalid geo.plan params: {exc}") from exc
        return self.plan(plan_params)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def capabilities(self) -> dict[str, Any]:
        """Advertise the server's protocol surface."""
        cards = (
            [c.to_dict() for c in self.geocard_registry.list_cards()]
            if self.geocard_registry is not None
            else [c.to_dict() for c in self._cards.values()]
        )
        skills = (
            [s.describe() for s in self.skill_registry.list_skills()]
            if self.skill_registry is not None
            else []
        )
        return {
            "protocol": "geomcp",
            "version": PROTOCOL_VERSION,
            "node": self.name,
            "methods": self._dispatcher.methods(),
            "tools": [t.describe() for t in self._tools.values()],
            "resources": list(self._resources.keys()),
            "skills": skills,
            # V2 特性广告：客户端按 capabilities 协商，不靠猜（协议冻结纪律）
            "v2": {"contract_binding": self.gaag_registry is not None,
                   "pushdown": True, "federation": True},
            "geocards": [c["id"] for c in cards],
            "geocard_ids": [c["id"] for c in cards],
            "recipes": self._recipe_summaries(),
            # 加法式扩展：老客户端读不认识的键会忽略它；认识它的客户端据此判断
            # 这个节点能不能真的执行模型调用，而不是发出去才知道。
            "model_invocation": self.model_invocation_capability(),
            "recipe_planning": self.recipe_planning_capability(),
        }

    def _recipe_entries(self) -> list[Any]:
        """目录里登记的配方（已批准优先）——没有目录就是空列表。"""
        registry = self.recipe_registry
        if registry is None:
            return []
        search = getattr(registry, "search", None)
        if callable(search):
            return list(search(approved_only=True))
        lister = getattr(registry, "list", None)
        return list(lister()) if callable(lister) else []

    def _recipe_summaries(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for entry in self._recipe_entries():
            summary = getattr(entry, "summary", None)
            out.append(summary() if callable(summary) else getattr(entry, "recipe", entry))
        return out

    def recipe_planning_capability(self) -> dict[str, Any]:
        """``geo.plan`` 的可用性声明 —— 与 ``geo.invoke`` 同样的"先声明再调用"。"""
        if self.recipe_registry is None:
            return {
                "enabled": False,
                "method": "geo.plan",
                "reason": "此节点未绑定 recipe_registry，geo.plan 会返回 recipe_not_found",
            }
        return {
            "enabled": True,
            "method": "geo.plan",
            "read_only": True,
            "recipes": len(self._recipe_entries()),
        }

    def model_invocation_capability(self) -> dict[str, Any]:
        """``geo.invoke`` 的可用性声明。"""
        if self.model_backend is None:
            return {
                "enabled": False,
                "method": "geo.invoke",
                "reason": "此节点未配置 model_backend，geo.invoke 会返回 invalid_argument",
            }
        names = getattr(self.model_backend, "provider_names", None)
        return {
            "enabled": True,
            "method": "geo.invoke",
            "compute_gate": True,
            "providers": sorted(names() if callable(names) else (names or [])),
            "accepted_cards": ["model"],
        }

    def invoke(self, params: InvokeParams) -> dict[str, Any]:
        """Invoke a ``type: model`` GeoCard (``geo.invoke``).

        Synchronous by design: the caller gets the full
        :class:`~geonexus.model.ModelOutcome` -- including ``error_code``,
        ``retryable`` and the compute-gate ``reasons`` -- as the JSON-RPC
        result. Scheduling it as a task is a separate concern and belongs to
        ``geonexus.geotask`` + ``ModelBackend``.
        """
        if self.model_backend is None:
            raise GeoMCPProtocolError(
                INVALID_ARGUMENT,
                "此节点未配置模型调用能力（model_backend），无法执行 geo.invoke",
                {"capability": self.model_invocation_capability()},
            )

        card = self._resolve_card(params.model)
        if card is None:
            raise GeoMCPProtocolError(
                GEOCARD_NOT_FOUND,
                f"GeoCard not found: {params.model}",
                {"missing": params.model},
            )
        card_type = getattr(card, "type", None)
        card_type = getattr(card_type, "value", card_type)
        if card_type != "model":
            raise GeoMCPProtocolError(
                INVALID_ARGUMENT,
                f"geo.invoke 只接受 type=model 的卡片，{params.model} 是 {card_type!r}",
                {"model": params.model, "type": card_type},
            )

        timeout = params.timeout
        if timeout is None:
            timeout = float(getattr(card, "timeout_seconds", None) or 300.0)
        outcome = self.model_backend.invoke_card(
            card, params.inputs, params=params.params, timeout=timeout
        )
        return outcome.to_dict()

    def describe(self, params: DescribeParams | None = None) -> dict[str, Any]:
        """Describe registered GeoCards and/or skills."""
        params = params or DescribeParams()
        if self.geocard_registry is not None:
            cards = self.geocard_registry.list_cards()
        else:
            cards = list(self._cards.values())
        if params.geocards is not None:
            wanted_cards = set(params.geocards)
            cards = [c for c in cards if c.id in wanted_cards]
            missing_cards: list[str] = sorted(wanted_cards - {c.id for c in cards})
            if missing_cards:
                raise GeoMCPProtocolError(
                    GEOCARD_NOT_FOUND,
                    f"GeoCard(s) not found: {missing_cards}",
                    {"missing": missing_cards},
                )
        if self.skill_registry is not None:
            skills = self.skill_registry.list_skills()
        else:
            skills = []
        if params.skills is not None:
            wanted_skills = set(params.skills)
            skills = [s for s in skills if s.name in wanted_skills]
            missing_skills: list[str] = sorted(wanted_skills - {s.name for s in skills})
            if missing_skills:
                raise GeoMCPProtocolError(
                    SKILL_NOT_FOUND,
                    f"Skill(s) not found: {missing_skills}",
                    {"missing": missing_skills},
                )
        recipes = self._recipe_entries()
        if params.recipes is not None:
            wanted_recipes = list(params.recipes)
            selected: list[Any] = []
            missing_recipes: list[str] = []
            for address in wanted_recipes:
                found = self._resolve_recipe(address)
                if found is None:
                    missing_recipes.append(address)
                else:
                    selected.append(found)
            if missing_recipes:
                raise GeoMCPProtocolError(
                    RECIPE_NOT_FOUND,
                    f"Recipe(s) not found: {missing_recipes}",
                    {"missing": missing_recipes},
                )
            recipes = selected
        return {
            "geocards": [c.to_dict() for c in cards],
            "skills": [s.describe() for s in skills],
            "recipes": [_recipe_view(e) for e in recipes],
        }

    # ------------------------------------------------------------------ #
    # geo.plan (V1.1) —— 物化，不执行
    # ------------------------------------------------------------------ #
    def _resolve_recipe(self, address: str) -> Any | None:
        """把一个（可能没写版本的）地址解析成一条登记记录。

        解析分三步，因为目录是**共享**的、而节点的副本可能已经过时：

        1. 本地目录若被别的进程改过（``recipe register --catalogue`` 那种），先重载；
        2. 本地命中就直接用；
        3. 本地没有、但节点绑定了注册中心 —— 去注册中心问一次。共享目录才是权威，
           不能因为"我启动时它还不存在"就告诉调用方方案不存在。
        """
        registry = self.recipe_registry
        if registry is not None:
            reloader = getattr(registry, "reload_if_changed", None)
            if callable(reloader):
                try:
                    reloader()
                except Exception:  # noqa: BLE001 - 目录坏了不该让物化变成 500
                    logger.warning("Recipe catalogue reload failed", exc_info=True)
            resolve = getattr(registry, "resolve", None)
            if callable(resolve):
                found = resolve(address, approved_only=True)
            else:
                # 兼容只实现 get 的目录：**自己**把审批状态也检查一遍。
                # 不能因为"resolve 没找到"就退回不带审批条件的 get —— 那等于
                # 让未过审的方案悄悄变得可运行。
                get = getattr(registry, "get", None)
                found = get(address) if callable(get) else None
                status = getattr(found, "status", None)
                if found is not None and status is not None and status != "approved":
                    found = None
            if found is not None:
                return found
        return self._recipe_from_registry(address)

    def _recipe_from_registry(self, address: str) -> Any | None:
        """向绑定的注册中心查一条已批准的方案（本地没有时）。"""
        if not self.registry_url:
            return None
        try:
            import httpx

            from ..recipe import Recipe

            url = f"{self.registry_url.rstrip('/')}/recipes/{address}"
            response = httpx.get(url, params={"approved_only": "true"}, timeout=5.0)
            if response.status_code != 200:
                return None
            payload = response.json()
            raw = ((payload or {}).get("entry") or {}).get("recipe")
            if not isinstance(raw, dict):
                return None
            return _RegistryRecipe(Recipe.from_dict(raw))
        except Exception:  # noqa: BLE001 - 注册中心不可达 = 本地也没有
            logger.debug("Recipe lookup at %s failed for %s", self.registry_url, address, exc_info=True)
            return None

    def plan(self, params: PlanParams) -> dict[str, Any]:
        """Materialise a recipe into a run (``geo.plan``).

        **只读**：返回任务图与交付物清单，不执行任何步骤、不落任何产物。平台的
        参数表单据此在"运行"之前就报出契约错误，用户不必等到任务失败才发现
        参数写错了。
        """
        if self.recipe_registry is None:
            raise GeoMCPProtocolError(
                RECIPE_NOT_FOUND,
                "此节点未绑定 recipe_registry，无法物化配方",
                {"reason": "recipe_registry_not_configured"},
            )
        entry = self._resolve_recipe(params.recipe)
        if entry is None:
            raise GeoMCPProtocolError(
                RECIPE_NOT_FOUND,
                f"Recipe not found or not approved: {params.recipe}",
                {"recipe": params.recipe},
            )
        recipe = getattr(entry, "recipe", entry)

        from ..recipe.materialize import deliverables as _deliverables
        from ..recipe.materialize import materialize
        from ..recipe.model import RecipeParamError

        resolver = self._resolve_plan_asset if params.resolve else None
        try:
            run = materialize(recipe, params.params, run_id=params.run_id, resolve=resolver)
        except RecipeParamError as exc:
            # 参数契约失败是**调用方**的错误，必须能被机器读懂（field/code），
            # 而不是包成一句人话让前端去猜哪个输入框红了。
            raise GeoMCPProtocolError(RECIPE_PARAM_INVALID, str(exc), exc.to_dict()) from exc

        tasks = []
        for task_id in run.task_ids():
            task = run.graph.task(task_id)
            tasks.append(
                {
                    "task_id": task_id,
                    "step": task.labels.get("recipe_step"),
                    "kind": task.kind.value,
                    "uses": task.spec.get("uses"),
                    "declared_uses": task.spec.get("declared_uses"),
                    "depends_on": list(task.depends_on),
                    "params": task.spec.get("params", {}),
                    "inputs": {name: io.ref for name, io in task.inputs.items()},
                    "outputs": {name: io.ref for name, io in task.outputs.items()},
                }
            )

        return {
            "recipe": recipe.to_dict(),
            "recipe_id": recipe.id,
            "run_id": run.run_id,
            "params": run.params,
            "reusable_params": recipe.reusable_param_names(),
            "fixed_params": recipe.fixed_param_names(),
            "tasks": tasks,
            "deliverables": _deliverables(run),
        }

    def _resolve_plan_asset(self, uses: str) -> str:
        """把步骤声明的 ``uses`` 解析成实际引用（仅当调用方要求 resolve 时）。

        解析不到就保留原样：物化不该因为"这个节点的资产目录还没同步"而失败 ——
        它描述的是**计划**，不是**此刻的库存**。
        """
        card = self._resolve_card(uses)
        if card is None:
            return uses
        access = getattr(card, "access", None)
        endpoint = getattr(access, "endpoint", None) if access is not None else None
        return endpoint or uses

    def execute(self, params: ExecuteParams) -> dict[str, Any]:
        """Execute a skill via the engine, gated by contract validation.

        When this node is bound to a shared registry (``registry_url``) and
        the request references GeoCards this node does not own, the request
        is **delegated** to the owning node (V1.0+ node-level pushdown) and
        the owner's result is relayed back. A single internal marker
        (``__geonode_delegate``) prevents delegation loops.
        """
        # 0. Delegation: unknown cards + registry bound + not already delegated.
        if params.geocards and self.registry_url and not params.params.get(DELEGATE_MARKER):
            unknown = [c for c in params.geocards if self._resolve_card(c) is None]
            if unknown:
                logger.info(
                    "Delegating geo.execute (cards %s) to owning node via %s",
                    unknown,
                    self.registry_url,
                )
                return self._delegate(params, unknown)

        # 1. Resolve the GeoCards referenced by the request.
        resolved_cards: list[GeoCard] = []
        if params.geocards:
            for card_id in params.geocards:
                card = self._resolve_card(card_id)
                if card is None:
                    raise GeoMCPProtocolError(
                        GEOCARD_NOT_FOUND,
                        f"GeoCard not found: {card_id}",
                        {"missing": card_id},
                    )
                resolved_cards.append(card)

        # 1b. V2 federation: resolve cross-registry card references.
        federated_meta: list[dict[str, Any]] = []
        federated_owners: dict[str, str] = {}
        if params.federated_sources:
            known = {c.id for c in resolved_cards}
            for src in dict.fromkeys(params.federated_sources):   # 去重且保序
                card, owner, reg_url = self._resolve_federated(src)
                if card.id not in known:
                    resolved_cards.append(card)
                    known.add(card.id)
                federated_meta.append({"source": src, "card": card.id,
                                       "owner": owner, "registry": reg_url})
                federated_owners[card.id] = owner

        # 2. Contract satisfaction gating (registry / discovery -> contract).
        #    联邦解析来的卡同样受门控：它的 bbox 就是合约的一部分。
        if resolved_cards:
            spatial = params.spatial
            temporal = params.temporal
            for card in resolved_cards:
                check = self.contract_validator.check(
                    card,
                    bbox=spatial.bbox if spatial else None,
                    crs=spatial.crs if spatial else None,
                    start=temporal.start if temporal else None,
                    end=temporal.end if temporal else None,
                )
                if not check.satisfied:
                    raise GeoMCPProtocolError(
                        CONTRACT_NOT_SATISFIED,
                        f"GeoCard '{card.id}' does not satisfy the request contract",
                        {
                            "geocard": card.id,
                            "reasons": check.reasons,
                            "warnings": check.warnings,
                        },
                    )

        # 2a. V2 pushdown: decide WHERE this runs before spending anything.
        pushdown_decision: dict[str, Any] | None = None
        if params.pushdown_spec:
            pushdown_decision = self._decide_pushdown(params, resolved_cards)
            if pushdown_decision["decision"] == "push":
                delegated, owner_url = self._delegate_to_owner(
                    params, resolved_cards, known_owners=federated_owners)
                pushdown_decision["owner"] = owner_url
                delegated["pushdown"] = pushdown_decision
                if federated_meta:      # 委托结果也要带联邦溯源，不能半路丢
                    delegated["federated"] = federated_meta
                return delegated

        # 2b. V2 GAAG contract binding: when declared, it MUST verify —
        #     semantic similarity + contract consistency, both or nothing.
        if params.contract_binding:
            self._enforce_contract_binding(params)

        # 3. Execute via the engine (Local GeoNode runtime) or a registered tool.
        #    The delegation marker is internal and must not reach handlers.
        clean_params = params.model_copy(deep=True)
        clean_params.params.pop(DELEGATE_MARKER, None)
        result: dict[str, Any]
        if self.engine is not None:
            result = self.engine.execute(clean_params)
        else:
            tool = self._tools.get(clean_params.skill)
            if tool is None:
                raise GeoMCPProtocolError(
                    SKILL_NOT_FOUND,
                    f"Skill not found: {clean_params.skill}",
                    {"available": list(self._tools.keys())},
                )
            result = self._run_tool(tool, clean_params)
        if isinstance(result, dict):
            if pushdown_decision is not None:
                # 决策要可解释：两侧估算都带上
                result["pushdown"] = pushdown_decision
            if federated_meta:
                # 联邦溯源：这张卡是从哪个注册中心、谁的节点解析来的，必须留痕
                result["federated"] = federated_meta
        return result

    # ------------------------------------------------------------------ #
    # V2 下推：在哪里算，先算一笔账
    # ------------------------------------------------------------------ #
    def _decide_pushdown(self, params: ExecuteParams,
                         resolved_cards: list[GeoCard]) -> dict[str, Any]:
        """local（数据拉来本节点算）vs push（推到数据属主节点算）的成本决策。

        估算全部显式：请求的 pushdown_spec 覆盖节点缺省，缺省覆盖内置保守值；
        **估不出来就老实回退 local 并写理由**，不编数字做决策。决策与两侧估算
        一并写进执行结果，事后可复盘（"为什么这次在远端跑"必须有答案）。
        """
        spec = dict(params.pushdown_spec or {})
        strategy = str(spec.get("strategy", "auto")).lower()
        if strategy == "migrate":          # 方案文档用词：migrate = 数据迁来算
            strategy = "local"
        if strategy not in ("local", "push", "auto"):
            raise GeoMCPProtocolError(
                INVALID_ARGUMENT,
                f"pushdown_spec.strategy 只认 local|migrate|push|auto，收到 {strategy!r}",
                {"field": "pushdown_spec.strategy"},
            )
        est = {**self.pushdown_defaults, **{k: v for k, v in spec.items()
                                            if k != "strategy" and v is not None}}
        data_bytes = est.get("data_bytes")
        bandwidth = est.get("bandwidth_bps")
        remote_queue = est.get("remote_queue_s")
        reasons: list[str] = []

        pull_s = push_s = None
        if data_bytes is not None and bandwidth:
            pull_s = round(float(data_bytes) / float(bandwidth), 3)   # 数据拉来本节点
        if remote_queue is not None:
            push_s = round(float(remote_queue), 3)                    # 属主排队+远端执行开销

        if strategy == "local":
            decision, why = "local", "strategy=local（调用方指定，数据迁移成本自担）"
        elif strategy == "push":
            decision, why = "push", "strategy=push（调用方指定）"
        else:  # auto
            if pull_s is None or push_s is None:
                decision = "local"
                why = ("auto 回退 local：估算不全（缺 data_bytes/bandwidth_bps/"
                       "remote_queue_s 之一），不做无依据的决策")
                reasons.append("missing-estimates")
            elif push_s < pull_s:
                decision, why = "push", f"auto：push 估算 {push_s}s < 拉数据 {pull_s}s"
            else:
                decision, why = "local", f"auto：拉数据 {pull_s}s ≤ push 估算 {push_s}s"
        return {
            "requested": str(spec.get("strategy", "auto")),
            "decision": decision,
            "owner": None,                       # _delegate_to_owner 解析后回填
            "estimates": {"pull_data_s": pull_s, "push_exec_s": push_s,
                          "data_bytes": data_bytes, "bandwidth_bps": bandwidth,
                          "remote_queue_s": remote_queue},
            "reasons": reasons + [why],
        }

    def _delegate_to_owner(self, params: ExecuteParams,
                           resolved_cards: list[GeoCard],
                           known_owners: dict[str, str] | None = None
                           ) -> tuple[dict[str, Any], str]:
        """push 决策落地：解析属主节点并委托（复用一跳委托语义）。"""
        if not self.registry_url:
            raise GeoMCPProtocolError(
                PUSHDOWN_UNRESOLVED,
                "pushdown 决策为 push，但本节点未绑定 registry，无法解析数据属主",
                {"reason": "no_registry_binding"},
            )
        owners: dict[str, str] = dict(known_owners or {})
        from ..registry import RegistryClient, RegistryClientError

        try:
            with RegistryClient(self.registry_url) as registry:
                for card in resolved_cards:
                    try:
                        entry = registry.get(card.id)
                    except RegistryClientError as miss:
                        if miss.code == 404:
                            continue          # 查无此卡 → 走 owner_unknown，不是查询故障
                        raise
                    if isinstance(entry, dict) and entry.get("node_url"):
                        owners[card.id] = entry["node_url"]
        except RegistryClientError as exc:
            raise GeoMCPProtocolError(
                PUSHDOWN_UNRESOLVED,
                f"pushdown 属主解析失败：registry 查询出错：{exc}",
                {"reason": "registry_lookup_failed"},
            ) from exc
        missing = [c.id for c in resolved_cards if c.id not in owners]
        if missing:
            raise GeoMCPProtocolError(
                PUSHDOWN_UNRESOLVED,
                f"pushdown 决策为 push，但以下 GeoCard 的属主解析不到：{missing}",
                {"reason": "owner_unknown", "cards": missing},
            )
        target = sorted(set(owners.values()))
        if len(target) > 1:
            raise GeoMCPProtocolError(
                PUSHDOWN_UNRESOLVED,
                f"pushdown 暂不支持多属主拆分执行：{target}",
                {"reason": "multi_owner", "owners": target},
            )
        unknown = [c.id for c in resolved_cards if self._resolve_card(c.id) is None]
        return (self._delegate(params, unknown or [c.id for c in resolved_cards],
                               extra_owners=known_owners), target[0])

    def _resolve_federated(self, source: str) -> tuple[GeoCard, str, str]:
        """解析一条跨注册中心引用：``<registry_url>#<card_id>``。

        一跳、只认 approved：远端注册中心直接给出卡片与属主节点，不再递归
        （递归链路由"注册中心互相同步"承担，执行面不做无限跳）；解析失败一律
        2009 —— 联邦引用是调用方的显式声明，静默忽略等于偷偷换语义。
        """
        from ..registry import RegistryClient, RegistryClientError

        reg_url, sep, card_id = source.rpartition("#")
        if not sep or not reg_url.startswith(("http://", "https://")) or not card_id:
            raise GeoMCPProtocolError(
                FEDERATION_UNRESOLVED,
                f"federated source 格式应为 <registry_url>#<card_id>：{source!r}",
                {"reason": "malformed_source", "source": source},
            )
        try:
            with RegistryClient(reg_url) as remote:
                entry = remote.get(card_id)
        except RegistryClientError as exc:
            reason = "card_not_found" if exc.code == 404 else "registry_unreachable"
            raise GeoMCPProtocolError(
                FEDERATION_UNRESOLVED,
                f"联邦解析失败（{reason}）：{source}（{exc}）",
                {"reason": reason, "source": source},
            ) from exc
        if not isinstance(entry, dict) or entry.get("status") != "approved":
            raise GeoMCPProtocolError(
                FEDERATION_UNRESOLVED,
                f"联邦条目未批准，拒绝引用：{source}",
                {"reason": "not_approved", "source": source,
                 "status": entry.get("status") if isinstance(entry, dict) else None},
            )
        try:
            card = GeoCard.model_validate(entry["card"])
        except Exception as exc:
            raise GeoMCPProtocolError(
                FEDERATION_UNRESOLVED,
                f"联邦卡片 schema 校验失败：{source}（{exc}）",
                {"reason": "bad_card", "source": source},
            ) from None
        return card, str(entry.get("node_url") or ""), reg_url

    def _enforce_contract_binding(self, params: ExecuteParams) -> None:
        """V2: 声明 contract_binding 的执行必须过 ContractGate 双重验证。

        验证不了（没有 GAAG 注册中心 / 合约不存在）与验证不过**同样拒绝**：
        合约绑定是授权声明，"查无此约"不能默默放行。
        """
        from ..gaag import ContractGate

        binding = params.contract_binding
        if self.gaag_registry is None:
            raise GeoMCPProtocolError(
                CONTRACT_BINDING_REJECTED,
                f"contract_binding '{binding}' declared but this node has no GAAG registry",
                {"contract_id": binding, "reason": "no_gaag_registry"},
            )
        contract = self.gaag_registry.get(binding)
        if contract is None:
            raise GeoMCPProtocolError(
                CONTRACT_BINDING_REJECTED,
                f"GAAG contract not found: {binding}",
                {"contract_id": binding, "reason": "contract_not_found"},
            )
        if self._contract_gate is None:
            self._contract_gate = ContractGate()
        # 意图文本 = 技能名 + 参数值（词面相似度对确定性嵌入是可复现的）
        query_text = " ".join(
            [params.skill, *(str(v) for v in list(params.params.values())[:8])]
        )
        spatial = params.spatial
        temporal = params.temporal
        result = self._contract_gate.gate(
            contract,
            query_text,
            bbox=spatial.bbox if spatial else None,
            crs=spatial.crs if spatial else None,
            start=temporal.start if temporal else None,
            end=temporal.end if temporal else None,
        )
        if not result.passed:
            raise GeoMCPProtocolError(
                CONTRACT_BINDING_REJECTED,
                f"GAAG contract '{binding}' rejected this execution: {result.message}",
                result.to_dict(),
            )

    def health(self) -> dict[str, Any]:
        """Health check payload."""
        return {
            "status": "ok",
            "node": self.name,
            "protocol": "geomcp",
            "version": PROTOCOL_VERSION,
            "time": datetime.now(timezone.utc).isoformat(),
            "plugins": self.plugin_status,
        }

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def set_registry(self, registry_url: str | None) -> None:
        """Bind (or unbind) this server to a shared GeoCard Registry.

        When bound, requests referencing unknown GeoCards are delegated to
        their owning node.
        """
        self.registry_url = registry_url
        logger.info("GeoMCP server '%s' registry binding: %s", self.name, registry_url)

    def _delegate(self, params: ExecuteParams, unknown: list[str],
                  extra_owners: dict[str, str] | None = None) -> dict[str, Any]:
        """Forward a request to the node that owns the first unknown card.

        One-hop semantics: the forwarded request carries the delegation
        marker, so the owner never re-delegates. The owner's result is
        relayed unchanged.
        """
        from ..registry import RegistryClient, RegistryClientError

        registry_url = self.registry_url
        assert registry_url is not None  # delegation is only entered when bound
        owners: dict[str, str] = dict(extra_owners or {})
        to_lookup = [c for c in unknown if c not in owners]
        try:
            with RegistryClient(registry_url) as registry:
                for card_id in to_lookup:
                    entry = registry.get(card_id)
                    if isinstance(entry, dict) and entry.get("node_url"):
                        owners[card_id] = entry["node_url"]
        except RegistryClientError as exc:
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Delegation failed: registry lookup error: {exc}",
            ) from exc
        missing = [c for c in unknown if c not in owners]
        if missing:
            raise GeoMCPProtocolError(
                GEOCARD_NOT_FOUND,
                f"GeoCard(s) not found at registry {registry_url}: {missing}",
                {"missing": missing},
            )
        owner = owners[unknown[0]]
        forward_params = dict(params.params)
        forward_params[DELEGATE_MARKER] = True
        try:
            from .client import GeoMCPClient, GeoMCPClientError

            # Present this node's outbound credential to the owner so the
            # delegated execution authenticates (per-node key model).
            forward_key = self.forward_api_key
            with GeoMCPClient(owner, timeout=self.forward_timeout, api_key=forward_key) as client:
                return client.execute(
                    skill=params.skill,
                    geocards=params.geocards,
                    spatial=(
                        params.spatial.model_dump(exclude_none=True) if params.spatial else None
                    ),
                    temporal=(
                        params.temporal.model_dump(exclude_none=True) if params.temporal else None
                    ),
                    params=forward_params,
                    request_id=params.request_id,
                )
        except GeoMCPClientError as exc:
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Delegation to {owner} failed: {exc}",
                {"owner": owner, "code": exc.code},
            ) from exc

    def _resolve_card(self, card_id: str) -> GeoCard | None:
        if self.geocard_registry is not None:
            return self.geocard_registry.get(card_id)
        return self._cards.get(card_id)

    def _run_tool(self, tool: _Tool, params: ExecuteParams) -> dict[str, Any]:
        try:
            result = tool.handler(params.params)
        except GeoMCPProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001 - tool boundary
            logger.exception("Tool '%s' failed", tool.name)
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Tool '{tool.name}' failed: {exc}",
            ) from exc
        if not isinstance(result, dict):
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Tool '{tool.name}' must return a dict, got {type(result).__name__}",
            )
        return {
            "status": "ok",
            "skill": params.skill,
            "outputs": result,
            "geocards": params.geocards,
            "executed_by": "tool",
            "request_id": params.request_id,
        }

    # ------------------------------------------------------------------ #
    # HTTP transport
    # ------------------------------------------------------------------ #
    def create_app(self) -> FastAPI:
        """Create a FastAPI application exposing the GeoMCP HTTP transport.

        Endpoints:
          - ``POST /geomcp``        — JSON-RPC dispatch
          - ``GET  /health``        — health check
          - ``GET  /capabilities``  — protocol surface

        When :attr:`api_keys` is non-empty, write methods (``geo.execute``,
        ``geo.invoke``) require an ``X-API-Key`` header; discovery methods
        stay open.
        """
        app = FastAPI(title=f"GeoMCP server: {self.name}", version=PROTOCOL_VERSION)

        # CORS：默认**不开**。浏览器客户端（client/ SPA）直连跨域节点时，
        # 运维显式设 GEONODE_CORS_ORIGINS="http://localhost:5175,..." 才放行 ——
        # 无鉴权节点默认对全网浏览器开放执行是事故，不是便利。
        cors_env = os.environ.get("GEONODE_CORS_ORIGINS", "").strip()
        if cors_env:
            from fastapi.middleware.cors import CORSMiddleware

            app.add_middleware(
                CORSMiddleware,
                allow_origins=[o.strip() for o in cors_env.split(",") if o.strip()],
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Content-Type", "X-API-Key", "Authorization"],
            )

        def _require_execute_key(request: Request, payload: dict[str, Any]) -> None:
            if not self.api_keys:
                return
            method = payload.get("method")
            # 两个都是**写**方法：会占用算力、可能产生费用，不能匿名开放
            if method not in WRITE_METHODS:
                return
            supplied = request.headers.get("X-API-Key")
            if supplied not in self.api_keys:
                raise GeoMCPProtocolError(
                    INVALID_PARAMS,
                    f"{method} requires a valid X-API-Key header",
                )

        @app.post("/geomcp")
        async def geomcp_endpoint(request: Request) -> JSONResponse:
            try:
                payload = await request.json()
            except Exception:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": "Parse error: body must be valid JSON",
                        },
                    },
                    status_code=400,
                )
            try:
                _require_execute_key(request, payload)
            except GeoMCPProtocolError as exc:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "error": {
                            "code": exc.code,
                            "message": str(exc),
                            "data": exc.data,
                        },
                    },
                    status_code=401,
                )
            response = self._dispatcher.dispatch(payload)
            status = 200
            if response.get("error") and response["error"].get("code") in (
                -32700,
                -32600,
            ):
                status = 400
            return JSONResponse(_json_safe(response), status_code=status)

        @app.get("/artifact")
        async def artifact_endpoint(request: Request, path: str):
            """受控读取节点本地产物 —— 只开放白名单根之下的普通文件。

            为什么需要：执行面把产物写在自己机器上（"数据留在原地"），平台要展示
            远端节点跑出来的报告/CSV，就必须有个受控出口，否则永远 404。
            白名单 = 构造参数（GeoNode 传 workdir）+ GEONODE_ARTIFACT_ROOTS + 进程 CWD；
            配置了 api_keys 的节点，取产物同样要带 X-API-Key —— 结果文件可能含敏感内容。
            """
            import mimetypes
            import os as _os

            from fastapi.responses import FileResponse as _FileResponse

            if self.api_keys and request.headers.get("X-API-Key") not in self.api_keys:
                return JSONResponse({"error": "artifact requires a valid X-API-Key header"}, status_code=401)
            roots = list(self.artifact_roots)
            roots += [r for r in _os.environ.get("GEONODE_ARTIFACT_ROOTS", "").split(_os.pathsep) if r]
            roots.append(_os.getcwd())
            target = _os.path.realpath(_os.path.abspath(path))
            allowed = any(target == r or target.startswith(r + _os.sep)
                          for r in (_os.path.realpath(x) for x in roots))
            if not allowed:
                return JSONResponse({"error": "artifact path is outside the node's allowed roots"}, status_code=403)
            if not _os.path.isfile(target):
                return JSONResponse({"error": "artifact not found"}, status_code=404)
            media = mimetypes.guess_type(target)[0] or "application/octet-stream"
            if media.startswith("text/"):
                media += "; charset=utf-8"
            return _FileResponse(target, media_type=media, filename=_os.path.basename(target))

        @app.get("/health")
        async def health_endpoint() -> dict[str, Any]:
            return self.health()

        @app.get("/capabilities")
        async def capabilities_endpoint() -> dict[str, Any]:
            return self.capabilities()

        return app

    def run(self, host: str = "127.0.0.1", port: int = 8787, log_level: str = "info") -> None:
        """Run the FastAPI transport with uvicorn (blocking)."""
        import uvicorn

        uvicorn.run(self.create_app(), host=host, port=port, log_level=log_level)
