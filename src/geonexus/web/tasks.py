"""Asynchronous task management for the Web layer.

:class:`TaskManager` runs blocking SDK calls (``run_goal``, GeoMCP
``execute``, registry search) in a background thread pool so HTTP requests
return immediately with a ``task_id``; clients poll ``GET /api/tasks/{id}``
or stream progress over SSE (``GET /api/tasks/{id}/stream``).

The state machine is::

    queued -> running -> done | failed | cancelled

State is held in memory (optionally persisted via a callback). For a
horizontally scaled deployment the manager lives in the Web backend process;
a shared store (Redis / Postgres) can be plugged in by subclassing.
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from inspect import signature
from typing import Any

# Task states (stable strings for the API surface).
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

_TERMINAL = {DONE, FAILED, CANCELLED}


class TaskNotFoundError(Exception):
    """Raised when a task id is unknown."""


class TaskNotCancellableError(Exception):
    """Raised when a task is already terminal."""


@dataclass
class Task:
    """A tracked background execution."""

    id: str
    status: str = QUEUED
    created_at: float = field(default_factory=lambda: time.time())
    started_at: float | None = None
    finished_at: float | None = None
    progress: float = 0.0  # 0..1
    message: str = ""
    result: Any = None
    error: str | None = None
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at) if self.started_at else None,
            "finished_at": _iso(self.finished_at) if self.finished_at else None,
            "progress": self.progress,
            "message": self.message,
            "cancelled": self.cancelled,
            "error": self.error,
            # Omit `result` here: it is included by `get()` on request so
            # list endpoints stay light.
        }


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class TaskManager:
    """Run blocking work in the background and track its state.

    Args:
        max_workers: Thread pool size (default: CPU count, capped at 8).
        persist: Optional ``callable(task_dict)`` invoked on every state
            change, for pluggable persistence (JSONL / Redis / …).

    Guarantees for ``persist``: every state transition of a task is reported
    exactly once (a snapshot is taken inside the same critical section that
    performs the transition, so a fast task cannot skip ``queued``). Calls are
    made outside ``_lock``, so a callback may call back into the manager.
    Ordering across concurrent transitions of the *same* task is not
    guaranteed; callbacks that need strict order should sort on
    ``created_at``/``started_at``/``finished_at``.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        persist: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        workers = max_workers or min(8, max(2, (threading.active_count() + 2)))
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="geonode-task"
        )
        self._tasks: dict[str, Task] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._lock = threading.RLock()
        self._persist = persist

    # ------------------------------------------------------------------ #
    # Submission
    # ------------------------------------------------------------------ #
    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        task_id: str | None = None,
        message: str = "",
        **kwargs: Any,
    ) -> str:
        """Schedule ``fn(*args, **kwargs)`` and return its task id.

        When ``fn`` declares a first parameter named ``task_id``, the task id
        is injected as the first positional argument — the canonical way for
        a worker to report progress via :meth:`update_progress` /
        :meth:`should_cancel`.
        """
        tid = task_id or uuid.uuid4().hex
        params = list(signature(fn).parameters)
        inject = bool(params and params[0] == "task_id")
        with self._lock:
            self._tasks[tid] = Task(id=tid, message=message)
            future = self._pool.submit(
                self._run, tid, fn, (tid, *args) if inject else args, kwargs
            )
            self._futures[tid] = future
            # Snapshot QUEUED while still holding the lock: the worker cannot
            # have started yet, so this transition can never be missed.
            snapshot = self._snapshot(tid)
        self._persist_snapshot(snapshot)
        return tid

    def _run(self, tid: str, fn: Callable[..., Any], args: tuple, kwargs: dict) -> Any:
        task = self._tasks[tid]
        with self._lock:
            task.status = RUNNING
            task.started_at = time.time()
            snapshot = self._snapshot(tid)
        self._persist_snapshot(snapshot)
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the API
            with self._lock:
                task.status = FAILED
                task.finished_at = time.time()
                task.error = str(exc)
                snapshot = self._snapshot(tid)
            self._persist_snapshot(snapshot)
            return None
        with self._lock:
            if task.cancelled:
                task.status = CANCELLED
                task.result = result  # cooperative workers may return a value
            else:
                task.status = DONE
                task.result = result
            task.finished_at = time.time()
            snapshot = self._snapshot(tid)
        self._persist_snapshot(snapshot)
        return result

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get(self, task_id: str, include_result: bool = True) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError(f"Unknown task: {task_id}")
            data = task.to_dict()
            if include_result and task.status in (DONE, CANCELLED):
                data["result"] = task.result
        return data

    def list(self, include_result: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            tasks = [t.to_dict() for t in self._tasks.values()]
        tasks.sort(key=lambda t: t["created_at"], reverse=True)
        if not include_result:
            return tasks
        with self._lock:
            for data in tasks:
                task = self._tasks[data["id"]]
                if task.status in (DONE, CANCELLED):
                    data["result"] = task.result
        return tasks

    def cancel(self, task_id: str) -> dict[str, Any]:
        """Request cancellation of a queued/running task.

        The running callable cooperates by checking
        :meth:`should_cancel` (the manager cannot interrupt a thread).
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFoundError(f"Unknown task: {task_id}")
            if task.status in _TERMINAL:
                raise TaskNotCancellableError(
                    f"Task {task_id} already {task.status}"
                )
            task.cancelled = True
            if task.status == QUEUED:
                task.status = CANCELLED
                task.finished_at = time.time()
            snapshot = self._snapshot(task_id)
            result = task.to_dict()
        # Persist outside the lock so a callback that touches the manager
        # cannot deadlock, and so no user code runs under `_lock`.
        self._persist_snapshot(snapshot)
        return result

    def should_cancel(self, task_id: str) -> bool:
        """Cooperatation hook for running callables."""
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.cancelled)

    def update_progress(self, task_id: str, progress: float, message: str = "") -> None:
        """Allow a running callable to report progress (0..1)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != RUNNING:
                return
            task.progress = max(0.0, min(1.0, progress))
            if message:
                task.message = message
            snapshot = self._snapshot(task_id)
        self._persist_snapshot(snapshot)

    def wait(self, task_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Block until the task reaches a terminal state."""
        with self._lock:
            future = self._futures.get(task_id)
        if future is None:
            raise TaskNotFoundError(f"Unknown task: {task_id}")
        future.result(timeout=timeout)  # raises on worker failure; state already set
        return self.get(task_id)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #
    def _snapshot(self, task_id: str) -> dict[str, Any]:
        """Build the persistence payload for ``task_id``.

        Caller must hold ``_lock``. Taking the snapshot inside the same
        critical section as the state change is what guarantees every
        transition is reported: a fast task can otherwise advance to a later
        state before a caller that re-reads the state gets to look at it.
        """
        task = self._tasks[task_id]
        data = task.to_dict()
        if task.status in (DONE, CANCELLED):
            data["result"] = task.result
        return data

    def _persist_snapshot(self, data: dict[str, Any]) -> None:
        """Hand a snapshot to the persist callback; never raises."""
        if self._persist is None:
            return
        # Persistence must never break the task it is reporting on.
        with contextlib.suppress(Exception):
            self._persist(data)

    def close(self) -> None:
        """Shut the worker pool down (waits for running tasks)."""
        self._pool.shutdown(wait=True)

    def __enter__(self) -> TaskManager:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
