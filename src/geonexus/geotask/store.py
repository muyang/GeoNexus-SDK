"""任务存储 —— 状态与事件的唯一入口。

两条不可让步的规则（契约 §6）：

1. ``transition()`` 是**唯一**能改状态的方法，它走
   :func:`~geonexus.geotask.machine.assert_transition`。绕过它改 ``task.state``
   就等于把状态机变成建议。
2. ``reconcile()`` 保证**重启后不撒谎**：没有存活执行者的任务绝不允许继续报
   ``running``。租约（``scheduled_to`` + ``lease_expires_at``）是这条的前提——
   没有租约就没有"对账"可言。
"""

from __future__ import annotations

import builtins
import json
import os
import threading
import time
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .events import (
    TASK_CANCELLED,
    TASK_CREATED,
    TASK_FAILED,
    TASK_PROGRESS,
    TASK_STATE_CHANGED,
    build_event,
)
from .machine import TERMINAL_STATES, IllegalTransition, assert_transition
from .model import GeoTask, TaskError, TaskResult, TaskState

__all__ = ["TaskStore", "JsonTaskStore", "EventHistoryGone", "TaskNotFound"]

#: 每个任务保留的事件条数上限。超出后 ``events_since`` 对过老的 ``since`` 抛
#: :class:`EventHistoryGone`（HTTP 层映射成 410），**不允许静默从头开始**——
#: 那会让客户端重复处理已经处理过的事件。
DEFAULT_EVENT_RETENTION = 1000


class TaskNotFound(KeyError):
    """未知 task_id。"""


class EventHistoryGone(Exception):
    """请求的事件已被保留窗口裁掉。"""

    def __init__(self, task_id: str, requested: int, oldest: int) -> None:
        super().__init__(
            f"任务 {task_id} 的事件历史已过期：请求 since={requested}，最早可用 seq={oldest}"
        )
        self.task_id = task_id
        self.requested = requested
        self.oldest = oldest


class TaskStore:
    """内存实现（``TaskStore.memory()``）。真源，JSON 后端只是它的持久化版本。"""

    def __init__(self, *, event_retention: int = DEFAULT_EVENT_RETENTION) -> None:
        self._tasks: dict[str, GeoTask] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._seq: dict[str, int] = {}
        self._lock = threading.RLock()
        self.event_retention = event_retention

    @classmethod
    def memory(cls, **kwargs: Any) -> TaskStore:
        """显式地要一个内存 store（与 :class:`JsonTaskStore` 对称，读起来更清楚）。"""
        return cls(**kwargs)

    # ------------------------------------------------------------------ #
    # 任务
    # ------------------------------------------------------------------ #
    def put(self, task: GeoTask) -> GeoTask:
        """登记任务。**``task_id`` 是幂等键**：已存在则原样返回既有任务，
        不新建、不重启、不覆盖状态（契约 §5.4）。

        幂等是跨节点重试与网络重投递不产生重复执行的唯一保证。
        """
        with self._lock:
            existing = self._tasks.get(task.task_id)
            if existing is not None:
                return existing
            self._tasks[task.task_id] = task
            self._events.setdefault(task.task_id, [])
            self._seq.setdefault(task.task_id, 0)
            self.append_event(task.task_id, TASK_CREATED, {"kind": task.kind.value})
            return task

    def get(self, task_id: str) -> GeoTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def require(self, task_id: str) -> GeoTask:
        task = self.get(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        return task

    def list(
        self,
        *,
        states: Iterable[TaskState] | None = None,
        owner: str | None = None,
        tenant: str | None = None,
        limit: int = 100,
    ) -> builtins.list[GeoTask]:
        wanted = set(states) if states is not None else None
        with self._lock:
            out = [
                t
                for t in self._tasks.values()
                if (wanted is None or t.state in wanted)
                and (owner is None or t.owner == owner)
                and (tenant is None or t.tenant == tenant)
            ]
        out.sort(key=lambda t: (-t.priority, t.created_at))
        return out[:limit]

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)
            self._events.pop(task_id, None)
            self._seq.pop(task_id, None)

    # ------------------------------------------------------------------ #
    # 状态迁移（唯一入口）
    # ------------------------------------------------------------------ #
    def transition(
        self,
        task_id: str,
        to: TaskState,
        *,
        reason: str = "",
        error: TaskError | None = None,
        result: TaskResult | None = None,
    ) -> GeoTask:
        """把任务迁到 ``to``。非法迁移抛 :class:`IllegalTransition`。"""
        with self._lock:
            task = self.require(task_id)
            src = task.state
            assert_transition(src, to)

            task.state = to
            if to is TaskState.RUNNING and task.started_at is None:
                task.started_at = time.time()
            if to in TERMINAL_STATES:
                task.finished_at = time.time()
            if error is not None:
                task.error = error
            if result is not None:
                task.result = result
            if to is TaskState.QUEUED and src is TaskState.RUNNING:
                # 重试：清掉上一次的失败痕迹，否则观测面会显示"排队中但已失败"
                task.error = None
                task.started_at = None
                task.cancel_requested = False

            data: dict[str, Any] = {"from": src.value, "to": to.value}
            if reason:
                data["reason"] = reason
            self.append_event(task_id, TASK_STATE_CHANGED, data)
            if to is TaskState.FAILED:
                self.append_event(
                    task_id,
                    TASK_FAILED,
                    {
                        "code": task.error.code if task.error else "execution_error",
                        "message": task.error.message if task.error else "",
                        "retryable": task.error.retryable if task.error else True,
                        "from": src.value,
                    },
                )
            elif to is TaskState.CANCELLED:
                self.append_event(
                    task_id,
                    TASK_CANCELLED,
                    {"from": src.value, "reason": reason or "user_requested"},
                )
            return task

    def set_lease(self, task_id: str, *, expires_at: float, executor: str | None = None) -> GeoTask:
        """写入/续租。``scheduled_to`` 与 ``lease_expires_at`` 一起构成"谁在执行"。"""
        with self._lock:
            task = self.require(task_id)
            task.lease_expires_at = expires_at
            if executor is not None:
                task.scheduled_to = executor
            return task

    def report_progress(self, task_id: str, progress: float, message: str = "") -> None:
        with self._lock:
            task = self.require(task_id)
            task.progress = max(0.0, min(1.0, float(progress)))
            if message:
                task.message = message
            self.append_event(
                task_id,
                TASK_PROGRESS,
                {"progress": task.progress, "message": task.message},
                attempt=task.attempt,
            )

    def request_cancel(self, task_id: str) -> GeoTask:
        """请求取消。

        **只置标志，不改状态**：执行体是不可中断的（下推出去的子进程杀不掉），
        所以状态转为 ``cancelled`` 由执行体在**步骤边界**自行结束之后落定。
        尚未真正执行的任务（created/queued/blocked）立即生效。
        """
        with self._lock:
            task = self.require(task_id)
            if task.is_terminal():
                raise IllegalTransition(task.state, TaskState.CANCELLED)
            task.cancel_requested = True
            if task.state in (TaskState.CREATED, TaskState.QUEUED, TaskState.BLOCKED):
                self.transition(task_id, TaskState.CANCELLED, reason="cancelled_before_start")
            return task

    def reconcile(self, *, now: float | None = None) -> dict[str, int]:
        """重启对账（契约 §6.2）：没有存活执行者的任务不许继续报 ``running``。

        规则：``scheduled``/``running`` 且租约过期（或无租约）→ 可重试则退回
        ``queued`` 并 ``attempt += 1``，否则 ``failed(executor_lost)``。
        终态不动；``created``/``queued``/``blocked`` 交回调度器。
        """
        now = time.time() if now is None else now
        report = {"requeued": 0, "failed": 0, "checked": 0}
        with self._lock:
            for task in list(self._tasks.values()):
                if task.state not in (TaskState.SCHEDULED, TaskState.RUNNING):
                    continue
                report["checked"] += 1
                lease = task.lease_expires_at
                expired = lease is None or lease <= now
                if not expired:
                    continue
                if task.retry.allows_another_attempt(task.attempt):
                    task.attempt += 1
                    self.transition(task.task_id, TaskState.QUEUED, reason="lease_expired")
                    report["requeued"] += 1
                else:
                    self.transition(
                        task.task_id,
                        TaskState.FAILED,
                        reason="lease_expired",
                        error=TaskError.of(
                            "executor_lost", "执行者租约过期：重启后没有存活的执行者认领该任务"
                        ),
                    )
                    report["failed"] += 1
        return report

    # ------------------------------------------------------------------ #
    # 事件
    # ------------------------------------------------------------------ #
    def append_event(self, task_id: str, event: str, data: dict[str, Any], attempt: int = 0) -> int:
        """追加事件并返回它的 ``seq``（每任务内从 1 起严格递增）。"""
        with self._lock:
            seq = self._seq.get(task_id, 0) + 1
            self._seq[task_id] = seq
            task = self._tasks.get(task_id)
            payload = build_event(
                task_id, seq, event, data, attempt=attempt or (task.attempt if task else 0)
            )
            history = self._events.setdefault(task_id, [])
            history.append(payload)
            if len(history) > self.event_retention:
                del history[: len(history) - self.event_retention]
            if task is not None:
                self._on_event(task_id, payload)
            return seq

    def _on_event(self, task_id: str, payload: dict[str, Any]) -> None:
        """持久化钩子（:class:`JsonTaskStore` 覆写）。"""

    def events_since(
        self, task_id: str, since: int = 0
    ) -> tuple[builtins.list[dict[str, Any]], int]:
        """取 ``seq > since`` 的事件，返回 ``(events, latest_seq)``。

        历史被裁掉时抛 :class:`EventHistoryGone`——**不允许静默从头开始**。
        """
        with self._lock:
            history = self._events.get(task_id, [])
            latest = self._seq.get(task_id, 0)
            if history and since and since < history[0]["seq"] - 1:
                raise EventHistoryGone(task_id, since, history[0]["seq"])
            return [e for e in history if e["seq"] > since], latest

    def subscribe(self, task_id: str, since: int = 0) -> builtins.list[dict[str, Any]]:
        """便捷方法：只要事件不要 latest。"""
        return self.events_since(task_id, since)[0]


class JsonTaskStore(TaskStore):
    """把任务与事件落到一个 JSON 文件；构造时载入。

    用法（单节点部署）：

        store = JsonTaskStore("var/geotask.json")
        store.reconcile()      # **载入后必须对账**，否则会把死任务报成 running
    """

    def __init__(
        self, path: str | os.PathLike[str], *, event_retention: int = DEFAULT_EVENT_RETENTION
    ) -> None:
        super().__init__(event_retention=event_retention)
        self.path = Path(path)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        with self._lock:
            for item in raw.get("tasks", []):
                task = GeoTask.from_dict(item)
                self._tasks[task.task_id] = task
            for task_id, events in (raw.get("events") or {}).items():
                self._events[task_id] = list(events)
                self._seq[task_id] = events[-1]["seq"] if events else 0

    def _flush(self) -> None:
        payload = {
            "format": 1,
            "tasks": [t.to_dict() for t in self._tasks.values()],
            "events": {k: list(v) for k, v in self._events.items()},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # 先写临时文件再原子替换：进程在写一半时被杀，不会留下半个 JSON
        os.replace(tmp, self.path)

    def _on_event(self, task_id: str, payload: dict[str, Any]) -> None:
        self._flush()

    def put(self, task: GeoTask) -> GeoTask:
        stored = super().put(task)
        self._flush()
        return stored

    def transition(self, task_id: str, to: TaskState, **kwargs: Any) -> GeoTask:
        result = super().transition(task_id, to, **kwargs)
        self._flush()
        return result

    def set_lease(self, task_id: str, **kwargs: Any) -> GeoTask:
        result = super().set_lease(task_id, **kwargs)
        self._flush()
        return result


def _replace_state(task: GeoTask, **changes: Any) -> GeoTask:
    """给未来的不可变实现留的口子（当前 store 用可变对象）。"""
    return replace(task, **changes)
