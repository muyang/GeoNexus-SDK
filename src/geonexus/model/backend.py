"""ModelBackend —— 把"调用模型"接到 GeoTask 调度契约上。

到这里三块拼成了一条完整链路::

    GeoTask(kind=model)  →  ModelBackend  →  invoke_model  →  算力门禁 → provider

为什么要做成 ``ExecutionBackend`` 而不是一个独立函数：模型调用要和技能执行**同一套**
状态机、租约、重试与观测。否则模型任务在平台上是"另一种东西"——没有 attempt、没有
错误码、取消语义另说一套，运维就得学两套。``TaskKind.MODEL``/``BATCH`` 早就留好了
位置，这里把它填上。

**取消说明**：不支持。HTTP 请求发出后，本地无法撤销远端计算；谎报"已取消"比说
"做不到"更有害，所以 ``capabilities().supports_cancel`` 是 ``False``，取消只在
提交前生效（见 ``_run`` 里的前置检查）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..geotask.backend import (
    BackendCapabilities,
    ExecutionContext,
    Handle,
    TaskOutcome,
)
from ..geotask.model import GeoTask, TaskError, TaskState
from ..resource import ComputeCapability
from .invocation import ModelOutcome, ModelProvider, invoke_model

logger = logging.getLogger(__name__)

__all__ = ["ModelBackend", "CardResolver"]

#: 从 ``spec["model"]`` 拿到 model GeoCard。返回 ``None`` 表示找不到。
CardResolver = Callable[[str], Any]


class ModelBackend:
    """按 GeoCard 调用模型的执行后端。

    ``spec`` 约定（宽松，给多少用多少）::

        {"model": "clip-vit-b32",          # 或 "model_id" / "card"（直接给卡片对象）
         "inputs": {...},                  # 覆盖/补充 task.inputs
         "params": {"top_k": 5}}

    ``task.inputs`` 里的 ``TaskIO`` 会被摊平成入参：``kind=literal`` 时取字面值，
    其余取 ``ref``（资产引用/路径）。**引用不在这里解引用**——解引用是资产层的
    职责，模型服务要的是路径或句柄。
    """

    name = "model"

    def __init__(
        self,
        cards: dict[str, Any] | None = None,
        *,
        resolver: CardResolver | None = None,
        providers: dict[str, ModelProvider] | None = None,
        capabilities: list[ComputeCapability] | None = None,
        registry_url: str | None = None,
        require_compute: bool = True,
        max_workers: int = 4,
    ) -> None:
        self._cards: dict[str, Any] = dict(cards or {})
        self._resolver = resolver
        self._providers = dict(providers or {})
        self._capabilities = capabilities
        self._registry_url = registry_url
        self._require_compute = require_compute
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="geomodel")
        self._futures: dict[str, Future[Any]] = {}
        self._last: dict[str, ModelOutcome] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def register(self, model_id: str, card: Any) -> ModelBackend:
        self._cards[model_id] = card
        return self

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name=self.name,
            kinds=frozenset({"model", "batch"}),
            supports_cancel=False,  # 请求已发出，远端计算撤不回来
            supports_progress=False,  # 一次请求一次响应，没有中间进度
            resource_dims=("cpu", "memory_mb", "gpu"),
        )

    def resolve_card(self, task: GeoTask) -> Any | None:
        """``spec["card"]`` > ``spec["model"]``/``model_id`` 查表 > ``resolver``。"""
        inline = task.spec.get("card")
        if inline is not None:
            return inline
        model_id = task.spec.get("model") or task.spec.get("model_id")
        if not model_id:
            return None
        if model_id in self._cards:
            return self._cards[model_id]
        if self._resolver is not None:
            try:
                return self._resolver(model_id)
            except Exception as exc:  # noqa: BLE001 - 解析失败要变成可读的拒绝
                logger.warning("解析模型 %s 失败：%s", model_id, exc)
                return None
        return None

    def collect_inputs(self, task: GeoTask) -> dict[str, Any]:
        """``task.inputs``（引用）打底，``spec["inputs"]`` 覆盖。"""
        inputs: dict[str, Any] = {}
        for name, io in (task.inputs or {}).items():
            inputs[name] = io.ref if io.kind != "literal" else io.ref
        inputs.update(task.spec.get("inputs") or {})
        return inputs

    # ------------------------------------------------------------------ #
    def submit(self, task: GeoTask, ctx: ExecutionContext) -> Handle:
        with self._lock:
            future = self._pool.submit(self._run, task, ctx)
            self._futures[task.task_id] = future
        return Handle(task_id=task.task_id, backend=self.name, token=future)

    def invoke_card(
        self,
        card: Any,
        inputs: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 300.0,
    ) -> ModelOutcome:
        """直接（同步）调用一张模型卡片。

        ``geo.invoke`` 走的是这条路：JSON-RPC 调用方要的是**立即**拿到结果，
        不是排一个任务。两条路共用同一份门禁/校验/provider 逻辑，差别只在谁来
        驱动（调度器 vs 调用方）。
        """
        outcome = invoke_model(
            card,
            inputs or {},
            params=params or {},
            capabilities=self._capabilities,
            registry_url=self._registry_url,
            providers=self._providers or None,
            timeout=timeout,
            require_compute=self._require_compute,
        )
        if not outcome.model_id:
            outcome.model_id = getattr(card, "id", None)
        with self._lock:
            key = outcome.request_id or getattr(card, "id", None) or ""
            self._last[key] = outcome
        return outcome

    def provider_names(self) -> list[str]:
        """已注册的 provider 名字（供 ``geo.capabilities`` 声明）。"""
        return sorted(self._providers)

    def _run(self, task: GeoTask, ctx: ExecutionContext) -> TaskOutcome:
        if ctx.should_cancel():
            return TaskOutcome.cancelled("提交前收到取消请求")

        card = self.resolve_card(task)
        if card is None:
            return TaskOutcome.failure(
                "invalid_spec",
                f"找不到模型卡片：spec={task.spec!r}（已注册: {sorted(self._cards)}）",
            )

        timeout = float(task.spec.get("timeout") or task.timeout_seconds or 300.0)
        outcome = self.invoke_card(
            card, self.collect_inputs(task), params=task.spec.get("params") or {}, timeout=timeout
        )
        with self._lock:
            self._last[task.task_id] = outcome

        if outcome.ok:
            return TaskOutcome.success(outcome.outputs)
        return TaskOutcome(
            state=TaskState.FAILED,
            error=TaskError.of(
                outcome.error_code or "execution_error",
                outcome.error or "模型调用失败",
                retryable=outcome.retryable,
            ),
        )

    def poll(self, handle: Handle) -> TaskOutcome:
        future: Future[Any] | None = handle.token
        if future is None or not future.done():
            return TaskOutcome(state=TaskState.RUNNING)
        return future.result()

    def cancel(self, handle: Handle) -> bool:
        """**做不到就是做不到**：见模块文档。"""
        return False

    # ------------------------------------------------------------------ #
    def last_outcome(self, task_id: str) -> ModelOutcome | None:
        """最近一次调用的完整结果（含 reasons / compute_node）——观测用。

        刻意不放进 ``TaskOutcome``：调度契约里的产物是 ``dict``，而这些是**诊断**
        信息，混进产物会让"结果"随实现细节漂移。
        """
        with self._lock:
            return self._last.get(task_id)

    def wait(self, task_id: str, timeout: float | None = None) -> TaskOutcome | None:
        with self._lock:
            future = self._futures.get(task_id)
        if future is None:
            return None
        return future.result(timeout=timeout)

    def close(self) -> None:
        self._pool.shutdown(wait=True)
        for provider in set(self._providers.values()):
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> ModelBackend:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
