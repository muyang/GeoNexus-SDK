"""任务依赖图 —— 契约 §5.3 与路线图 P1 的"就绪集并行"。

存在理由：现有 ``agent.Plan`` 已有 ``depends_on`` 与拓扑排序，但 ``PlanExecutor``
是 ``for step_id in order:`` ——**严格串行**，DAG 里可并行的部分被浪费。这个模块的
核心就是 ``ready()``：一次给出**所有**当前可跑的节点，让执行层能真的并行。

## 与 store 的关系（重要）

状态有**唯一**归属：:class:`~geonexus.geotask.store.TaskStore`。图本身只是调度
**视图**。若图和 store 各持一份 ``GeoTask`` 副本，就会出现"图里标记了 done、store
里还是 created"这类双写问题——第一版正是这么写的，表现为同一个任务被提交两次。

因此提供 :meth:`TaskGraph.bind`：把图节点**换成 store 里的同一个对象**，之后
``mark_*`` 全部通过 store 迁移（校验与事件都由 store 统一产生）。不绑定时（纯图
算法、单元测试）``mark_*`` 直接改内存对象，行为一致。

失败级联是 **fail-fast**：上游 ``failed``/``cancelled`` 后，下游递归转
``failed(upstream_failed)``；``skipped`` **不**级联——跳过是策略决定，下游可能仍然可跑。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .machine import IllegalTransition, assert_transition
from .model import GeoTask, TaskError, TaskState

if TYPE_CHECKING:  # pragma: no cover
    from .store import TaskStore

__all__ = ["TaskGraph", "TaskGraphError"]

#: 依赖被满足的判定：这两者都算"上游已经不再挡路"。
_SATISFIED = frozenset({TaskState.SUCCEEDED, TaskState.SKIPPED})

#: ``ready()`` 认为"可跑"的状态：
#:
#: - ``created``  还没交出去
#: - ``queued``   交出去了但当前没有执行者在跑（重试、让出、调度器退回都会落到这里）
#: - ``blocked``  **被调度器按资源卡住**的任务（P4）。它必须重新进就绪集，否则容量
#:   释放后没人会再试它——那是永久排队。阻塞型 ``blocked``（依赖失败）随后立刻转
#:   ``failed``，不会留在就绪集里，所以两者不冲突。
#:
#: ``running`` 不算可跑：它已经在执行者手里。
_RUNNABLE = frozenset({TaskState.CREATED, TaskState.QUEUED, TaskState.BLOCKED})


class TaskGraphError(Exception):
    """图结构本身有问题：重复 id、依赖不存在、有环、未知节点。"""


class TaskGraph:
    """一个任务 DAG。节点是 :class:`GeoTask`，边是 ``depends_on``。"""

    def __init__(self, tasks: Iterable[GeoTask]) -> None:
        self._tasks: dict[str, GeoTask] = {}
        self._order: list[str] = []
        self._errors: dict[str, TaskError] = {}
        self._store: TaskStore | None = None

        for task in tasks:
            if task.task_id in self._tasks:
                raise TaskGraphError(f"重复的 task_id: {task.task_id}")
            self._tasks[task.task_id] = task
            self._order.append(task.task_id)

        for task in self._tasks.values():
            for dep in task.depends_on:
                if dep not in self._tasks:
                    raise TaskGraphError(f"任务 {task.task_id} 依赖不存在的 {dep}")
                if dep == task.task_id:
                    raise TaskGraphError(f"任务 {task.task_id} 依赖自己")

        self._topo = self._topological_order()  # 顺便检环

    # ------------------------------------------------------------------ #
    @classmethod
    def from_tasks(cls, tasks: Iterable[GeoTask]) -> TaskGraph:
        return cls(tasks)

    @classmethod
    def from_store(cls, store: TaskStore, task_ids: Iterable[str]) -> TaskGraph:
        """按 id 从 store 取任务建图（任务必须已登记）。"""
        tasks = []
        for tid in task_ids:
            task = store.get(tid)
            if task is None:
                raise TaskGraphError(f"store 里没有 {tid}")
            tasks.append(task)
        return cls(tasks).bind(store)

    def bind(self, store: TaskStore) -> TaskGraph:
        """把图节点换成 store 里的对象，并让 ``mark_*`` 走 store 迁移。

        store 里没有的任务会被登记进去（``put`` 是幂等的），因此调用方不必先把
        任务逐个 put 一遍——少一个"忘了 put 就报 TaskNotFound"的坑。
        """
        self._store = store
        for tid in self._order:
            stored = store.get(tid)
            if stored is None:
                stored = store.put(self._tasks[tid])
            self._tasks[tid] = stored
        return self

    def _topological_order(self) -> list[str]:
        """Kahn 算法；有环则抛 :class:`TaskGraphError`。"""
        indegree = {tid: len(t.depends_on) for tid, t in self._tasks.items()}
        ready = [tid for tid in self._order if indegree[tid] == 0]
        order: list[str] = []
        while ready:
            tid = ready.pop(0)
            order.append(tid)
            for other in self._order:
                if tid in self._tasks[other].depends_on:
                    indegree[other] -= 1
                    if indegree[other] == 0:
                        ready.append(other)
        if len(order) != len(self._tasks):
            stuck = [t for t in self._tasks if t not in order]
            raise TaskGraphError(f"任务图有环，涉及: {stuck}")
        return order

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def task(self, task_id: str) -> GeoTask:
        return self._task(task_id)

    def topological_order(self) -> list[str]:
        return list(self._topo)

    def state_of(self, task_id: str) -> TaskState:
        return self._task(task_id).state

    def error_of(self, task_id: str) -> TaskError | None:
        """取失败原因（含级联出来的 ``upstream_failed``）。"""
        if task_id in self._errors:
            return self._errors[task_id]
        return self._task(task_id).error

    def ready(self) -> list[str]:
        """当前**可同时执行**的任务 id。

        这就是"并行就绪集"：无依赖的节点一起返回，而不是只返回拓扑序里的下一个。
        """
        out = []
        for tid in self._order:
            task = self._tasks[tid]
            if task.state not in _RUNNABLE:
                continue
            if all(self._tasks[d].state in _SATISFIED for d in task.depends_on):
                out.append(tid)
        return out

    def in_flight(self) -> list[str]:
        return [
            tid
            for tid in self._order
            if self._tasks[tid].state in (TaskState.QUEUED, TaskState.SCHEDULED, TaskState.RUNNING)
        ]

    def pending(self) -> list[str]:
        return [tid for tid in self._order if not self._tasks[tid].is_terminal()]

    def is_complete(self) -> bool:
        return all(self._tasks[tid].is_terminal() for tid in self._order)

    def dependents_of(self, task_id: str) -> list[str]:
        return [tid for tid in self._order if task_id in self._tasks[tid].depends_on]

    # ------------------------------------------------------------------ #
    # 迁移
    # ------------------------------------------------------------------ #
    def mark_running(self, task_id: str) -> None:
        """提交给执行者后立刻调用。

        这一步不是记账而是**防止重复提交**：``ready()`` 只看 CREATED/QUEUED，
        提交后必须把节点推到 RUNNING，否则下一轮循环会再提交同一个任务。
        """
        self._ensure_queued(task_id)
        self._move(task_id, TaskState.SCHEDULED)
        self._move(task_id, TaskState.RUNNING)

    def mark_retry(self, task_id: str) -> None:
        """可重试失败后把节点退回 ``queued``，让它重新进就绪集
        （状态机允许 ``running → queued``）。"""
        self._move(task_id, TaskState.QUEUED)

    def mark_done(self, task_id: str, result: Any = None) -> None:
        if self._task(task_id).state is not TaskState.RUNNING:
            self._ensure_queued(task_id)
            self._move(task_id, TaskState.SCHEDULED)
            self._move(task_id, TaskState.RUNNING)
        self._move(task_id, TaskState.SUCCEEDED, result=result)

    def mark_failed(self, task_id: str, code: str = "execution_error", message: str = "") -> None:
        if self._task(task_id).state is not TaskState.RUNNING:
            # 没跑过的任务不该被"补跑"成 RUNNING（那是假的）。按状态机允许的路径
            # 走到能失败的位置：created/queued → blocked → failed。
            self._ensure_blocked(task_id)
        self._errors[task_id] = TaskError.of(code, message)
        self._move(task_id, TaskState.FAILED, error=self._errors[task_id])
        self._cascade(task_id)

    def mark_blocked(
        self, task_id: str, reasons: list[str] | str = "", code: str = "resource_unavailable"
    ) -> None:
        """把任务标成"被调度器卡住"，并把理由落到事件流与 ``message``。

        理由**必须**可读：``blocked`` 不带理由是运维最讨厌的状态——看得见卡住，
        看不见缺什么。已经在 ``blocked`` 的任务只更新理由，不重复迁移。
        """
        text = "；".join(reasons) if isinstance(reasons, (list, tuple)) else str(reasons)
        message = f"{code}: {text}" if text else code
        task = self._task(task_id)
        if task.state is TaskState.BLOCKED:
            # 已经在 blocked：只更新理由。这不算状态变化，事件里不该多一条。
            task.message = message
            return
        if task.state is TaskState.CREATED:
            self._move(task_id, TaskState.QUEUED, reason=message)
        if task.state is TaskState.QUEUED:
            # 理由随 state_changed 事件走（契约 §4 的事件类型表不变）
            self._move(task_id, TaskState.BLOCKED, reason=message)
        task.message = message

    def mark_skipped(self, task_id: str) -> None:
        self._ensure_blocked(task_id)
        self._move(task_id, TaskState.SKIPPED)

    def mark_cancelled(self, task_id: str) -> None:
        task = self._task(task_id)
        if task.is_terminal():
            return
        if task.state in (TaskState.CREATED, TaskState.QUEUED):
            self._ensure_blocked(task_id)
        self._move(task_id, TaskState.CANCELLED)
        self._cascade(task_id)

    # ------------------------------------------------------------------ #
    def _task(self, task_id: str) -> GeoTask:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise TaskGraphError(f"未知 task_id: {task_id}") from None

    def _ensure_queued(self, task_id: str) -> None:
        """推进到 ``queued``。

        ``blocked → queued`` 是资源卡住之后"容量有了、重新参选"的回归路径
        （状态机里本来就有这条边，P4 才真正用上）。
        """
        state = self._task(task_id).state
        if state in (TaskState.CREATED, TaskState.BLOCKED):
            self._move(task_id, TaskState.QUEUED)

    def _ensure_blocked(self, task_id: str) -> None:
        """推进到 ``blocked``。

        状态机里 ``created``/``queued`` 都**不能**直接到 ``failed``/``skipped``
        （契约 §3.1），必须先经过 ``blocked``。这不是绕路：任务确实是被"卡住"
        （依赖失败）才走到那一步的，``blocked`` 正是这个语义的落点。
        """
        if self._task(task_id).state is TaskState.BLOCKED:
            return  # 已经在 blocked，别做 queued→blocked 的往返
        self._ensure_queued(task_id)
        if self._task(task_id).state is TaskState.QUEUED:
            self._move(task_id, TaskState.BLOCKED)

    def _move(
        self,
        task_id: str,
        to: TaskState,
        error: TaskError | None = None,
        result: Any = None,
        reason: str = "",
    ) -> None:
        task = self._task(task_id)
        if task.state is to:
            return
        if self._store is not None:
            # 绑定了 store：迁移（校验 + 事件）统一由 store 负责
            try:
                self._store.transition(task_id, to, reason=reason, error=error, result=result)
            except IllegalTransition:
                if task.is_terminal():
                    return
                raise
            return
        try:
            assert_transition(task.state, to)
        except IllegalTransition:
            # 图内部驱动时，已进入终态的任务不该被回退；跳过而不是炸掉
            if task.is_terminal():
                return
            raise
        task.state = to
        if error is not None:
            task.error = error
        if reason:
            task.message = reason

    def _cascade(self, source_id: str) -> None:
        """fail-fast：递归把下游标成 ``failed(upstream_failed)``。"""
        queue = list(self.dependents_of(source_id))
        seen: set[str] = set()
        while queue:
            tid = queue.pop(0)
            if tid in seen:
                continue
            seen.add(tid)
            task = self._tasks[tid]
            if task.state in _SATISFIED:
                # 已经成功/跳过的下游不回滚：它已经产生过产物
                continue
            if not task.is_terminal():
                self._ensure_blocked(tid)
                self._errors[tid] = TaskError.of("upstream_failed", f"上游 {source_id} 失败/取消")
                self._move(tid, TaskState.FAILED, error=self._errors[tid])
            queue.extend(self.dependents_of(tid))

    def _error_view(self, task_id: str) -> dict[str, Any] | None:
        """任务错误的可序列化视图；没有错误就是 ``None``。"""
        error = self.error_of(task_id)
        return error.to_dict() if error is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.topological_order(),
            "ready": self.ready(),
            "nodes": [
                {
                    "task_id": tid,
                    "state": self._tasks[tid].state.value,
                    "depends_on": list(self._tasks[tid].depends_on),
                    "kind": self._tasks[tid].kind.value,
                    # 只取一次：两次调用之间状态可变，第二次可能返回 None
                    "error": self._error_view(tid),
                }
                for tid in self._order
            ],
        }
