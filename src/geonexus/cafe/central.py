"""CAFE CentralServer — task routing to optimal workers.

Central Server 职责：
1. 维护 Worker 注册表（数据本地性、算力余量、合规策略）
2. 根据任务的输入数据位置，选择最优 Worker（数据本地性优先）
3. 分发任务并聚合结果
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .protocol import PushdownRequest, PushdownResult
from .worker import WorkerNode

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Result of routing a task to workers."""

    chosen_worker: str | None
    candidates: list[str]
    reason: str


class TaskRouter:
    """Backward-compatible alias for CentralServer (router behaviour)."""

    def __init__(self, central: CentralServer) -> None:
        self.central = central

    def route(self, request: PushdownRequest) -> RoutingDecision:
        return self.central.route(request)


class CentralServer:
    """Central Server for CAFE pushdown tasks.

    Attributes:
        server_id: Unique identifier.
        workers: Dict of worker_id -> WorkerNode.
    """

    def __init__(self, server_id: str = "central") -> None:
        self.server_id = server_id
        self.workers: dict[str, WorkerNode] = {}

    # ------------------------------------------------------------------ #
    # Worker management
    # ------------------------------------------------------------------ #
    def register_worker(self, worker: WorkerNode) -> CentralServer:
        """Register a WorkerNode so tasks can be routed to it."""
        self.workers[worker.worker_id] = worker
        logger.info("Central registered worker %s (%d assets)",
                    worker.worker_id, len(worker.data_assets))
        return self

    def available_workers(self) -> list[WorkerNode]:
        return list(self.workers.values())

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #
    def route(self, request: PushdownRequest) -> RoutingDecision:
        """Choose the best worker for a pushdown request.

        Strategy (多目标优化的简版):
        1. 数据本地性优先 — 能解析最多输入数据的 Worker 优先
        2. 次选: 满足 metadata 中 region 标签的 Worker
        3. 兜底: 第一个可用 Worker
        """
        region_hint = request.metadata.get("region")
        candidates: list[str] = []

        scored: list[tuple[int, str]] = []
        for worker in self.workers.values():
            score = 0
            for value in request.inputs.values():
                if worker.resolve_input(value) is not None:
                    score += 1
            scored.append((score, worker.worker_id))

        # Data locality: highest input resolution count first.
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and scored[0][0] > 0:
            candidates = [wid for _, wid in scored]
            chosen = scored[0][1]
            return RoutingDecision(chosen, candidates, f"data locality ({scored[0][0]} inputs local)")

        # Region tag fallback.
        region_workers = [
            wid for wid, w in self.workers.items()
            if w.tags.get("region") == region_hint
        ]
        if region_workers:
            candidates = region_workers
            return RoutingDecision(region_workers[0], region_workers, f"region tag {region_hint}")

        # Any worker.
        ids = list(self.workers.keys())
        if ids:
            return RoutingDecision(ids[0], ids, "fallback (no locality hints)")
        return RoutingDecision(None, [], "no workers registered")

    # ------------------------------------------------------------------ #
    # Task dispatch
    # ------------------------------------------------------------------ #
    def dispatch(self, request: PushdownRequest) -> PushdownResult:
        """Route and execute a pushdown task on the chosen worker."""
        decision = self.route(request)
        if decision.chosen_worker is None:
            return PushdownResult(
                task_id=request.task_id,
                status="failed",
                error="no worker available",
            )
        worker = self.workers[decision.chosen_worker]
        logger.info("Central dispatching %s -> %s (%s)",
                    request.task_id, worker.worker_id, decision.reason)
        return worker.execute(request)

    def dispatch_to(self, worker_id: str, request: PushdownRequest) -> PushdownResult:
        """Dispatch a task to an explicitly named worker."""
        worker = self.workers.get(worker_id)
        if worker is None:
            return PushdownResult(
                task_id=request.task_id,
                status="failed",
                error=f"worker not found: {worker_id}",
            )
        return worker.execute(request)

    # ------------------------------------------------------------------ #
    # Health & info
    # ------------------------------------------------------------------ #
    def health(self) -> dict[str, Any]:
        return {
            "central": self.server_id,
            "workers": [
                {
                    "worker_id": w.worker_id,
                    "assets": list(w.data_assets.keys()),
                    "tags": w.tags,
                }
                for w in self.workers.values()
            ],
        }