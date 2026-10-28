"""执行后端 —— 契约 §7。

调度层不关心任务在哪里跑，只认这个接口。四个实现分四期落地；Phase 1 只做
:class:`LocalBackend`（进程内线程池），其余三个是路线图 Phase 2/3 的工作。

``capabilities()`` 不是装饰：**不是所有后端都支持取消或进度**。调度器据此决定
是报错还是降级，而不是假装支持然后静默失效。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .model import GeoTask, TaskError, TaskResult, TaskState

__all__ = [
    "BackendCapabilities",
    "ExecutionContext",
    "TaskOutcome",
    "Handle",
    "ExecutionBackend",
    "LocalBackend",
    "Handler",
]

#: 执行体签名：``handler(task, ctx) -> dict[str, Any]``（返回值即产物）。
Handler = Callable[[GeoTask, "ExecutionContext"], dict[str, Any]]


@dataclass
class BackendCapabilities:
    name: str
    kinds: frozenset[str] = frozenset()
    supports_cancel: bool = False
    supports_progress: bool = True
    resource_dims: tuple[str, ...] = ()


@dataclass
class TaskOutcome:
    """执行体的一次汇报。"""

    state: TaskState
    progress: float = 0.0
    message: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    error: TaskError | None = None

    @classmethod
    def success(cls, outputs: dict[str, Any] | None = None, message: str = "") -> TaskOutcome:
        return cls(state=TaskState.SUCCEEDED, progress=1.0, message=message, outputs=outputs or {})

    @classmethod
    def failure(cls, code: str, message: str = "") -> TaskOutcome:
        return cls(state=TaskState.FAILED, error=TaskError.of(code, message))

    @classmethod
    def cancelled(cls, message: str = "已取消") -> TaskOutcome:
        return cls(state=TaskState.CANCELLED, message=message)


@dataclass
class Handle:
    task_id: str
    backend: str
    token: Any = None


@dataclass
class ExecutionContext:
    """交给执行体的上下文：进度上报与协作式取消的唯一通道。"""

    task: GeoTask
    _on_progress: Callable[[float, str], None] | None = None
    _should_cancel: Callable[[], bool] | None = None

    def report_progress(self, progress: float, message: str = "") -> None:
        if self._on_progress is not None:
            self._on_progress(progress, message)

    def should_cancel(self) -> bool:
        """执行体应**在步骤边界**检查它；下推出去的子进程杀不掉。"""
        return bool(self._should_cancel and self._should_cancel())


@runtime_checkable
class ExecutionBackend(Protocol):
    name: str

    def capabilities(self) -> BackendCapabilities: ...
    def submit(self, task: GeoTask, ctx: ExecutionContext) -> Handle: ...
    def poll(self, handle: Handle) -> TaskOutcome: ...
    def cancel(self, handle: Handle) -> bool: ...


class LocalBackend:
    """进程内线程池执行。

    任务执行什么由 ``handlers`` 决定：按 ``spec["skill"]``（或 ``spec["key"]``）
    查表。没有注册处理器的任务会以 ``invalid_spec`` 失败——**明确失败**，不是静默
    成功。
    """

    name = "local"

    def __init__(self, handlers: dict[str, Handler] | None = None, max_workers: int = 4) -> None:
        self._handlers: dict[str, Handler] = dict(handlers or {})
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="geotask")
        self._futures: dict[str, Future[Any]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def register(self, key: str, handler: Handler) -> LocalBackend:
        self._handlers[key] = handler
        return self

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name=self.name,
            kinds=frozenset({"skill", "composite"}),
            supports_cancel=False,  # 线程不可中断：取消只能协作式，见 ctx
            supports_progress=True,
            resource_dims=("cpu", "memory_mb"),
        )

    def _handler_for(self, task: GeoTask) -> Handler | None:
        """按 ``spec`` 里的处理器键查表。

        查找顺序是 ``handler`` → ``skill`` → ``key``。``handler`` 放在最前是**刻意**
        的：``skill`` 既是路由键又是业务字段（技能名），agent 的计划步骤就同时带着
        "用哪个处理器执行"和"执行哪个技能"两件事，放在同一个键上会撞——第一版正是
        这么写的，症状是"没有为 ... 注册执行体"。
        """
        for key in (task.spec.get("handler"), task.spec.get("skill"), task.spec.get("key")):
            if key and key in self._handlers:
                return self._handlers[key]
        return None

    def submit(self, task: GeoTask, ctx: ExecutionContext) -> Handle:
        handler = self._handler_for(task)
        if handler is None:
            future: Future[Any] = Future()
            future.set_result(
                TaskOutcome.failure(
                    "invalid_spec",
                    f"没有为 {task.spec!r} 注册执行体（可用: {sorted(self._handlers)}）",
                )
            )
            with self._lock:
                self._futures[task.task_id] = future
            return Handle(task_id=task.task_id, backend=self.name, token=future)

        with self._lock:
            future = self._pool.submit(self._run, handler, task, ctx)
            self._futures[task.task_id] = future
        return Handle(task_id=task.task_id, backend=self.name, token=future)

    def _run(self, handler: Handler, task: GeoTask, ctx: ExecutionContext) -> TaskOutcome:
        try:
            outputs = handler(task, ctx) or {}
        except Exception as exc:  # noqa: BLE001 - 任何执行体异常都变成任务失败
            return TaskOutcome.failure("execution_error", f"{type(exc).__name__}: {exc}")
        if ctx.should_cancel():
            return TaskOutcome.cancelled("在结束前收到取消请求")
        return TaskOutcome.success(outputs)

    def poll(self, handle: Handle) -> TaskOutcome:
        future: Future[Any] | None = handle.token
        if future is None or not future.done():
            return TaskOutcome(state=TaskState.RUNNING)
        return future.result()

    def cancel(self, handle: Handle) -> bool:
        """本地线程不可中断，所以取消**由执行体协作完成**。

        这里返回 ``False`` 是有话要说的：告诉调度器"我没有能力撤销它"，
        而不是假装成功。真正的取消走 ``ExecutionContext.should_cancel()``。
        """
        return False

    def wait(self, task_id: str, timeout: float | None = None) -> TaskOutcome | None:
        with self._lock:
            future = self._futures.get(task_id)
        if future is None:
            return None
        return future.result(timeout=timeout)

    def close(self) -> None:
        self._pool.shutdown(wait=True)

    def __enter__(self) -> LocalBackend:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def outcome_to_result(outcome: TaskOutcome) -> TaskResult | None:
    if outcome.state is not TaskState.SUCCEEDED:
        return None
    return TaskResult(outputs=dict(outcome.outputs))
