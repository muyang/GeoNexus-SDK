"""CAFE pushdown protocol — task negotiation, code distribution, result return."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class PushdownRequest:
    """A task to be pushed down to a Worker for local execution.

    Attributes:
        task_id: Unique task identifier.
        code: Python source code executed on the worker (the "pushdown").
        inputs: Local data references (paths or URIs the worker resolves locally).
        returns: The name of the variable the code must assign as its result.
        timeout_seconds: Execution timeout on the worker.
        metadata: Optional routing hints (e.g. {"region": "mekong"}).
    """

    code: str
    inputs: dict[str, Any] = field(default_factory=dict)
    returns: str = "result"
    task_id: str = field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    timeout_seconds: float = 60.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "code": self.code,
            "inputs": self.inputs,
            "returns": self.returns,
            "timeout_seconds": self.timeout_seconds,
            "metadata": self.metadata,
        }


@dataclass
class PushdownResult:
    """Outcome of a pushdown task executed on a worker."""

    task_id: str
    status: TaskStatus
    result: Any = None
    error: str | None = None
    worker_id: str | None = None
    started_at: float = field(default_factory=time.time)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "worker_id": self.worker_id,
            "elapsed_seconds": round(self.elapsed_seconds, 4),
        }


#: Signature of a pushdown function: (inputs: dict) -> Any
PushdownFunction = Callable[[dict[str, Any]], Any]