"""v1.1.1 审查中修复缺陷的回归测试。

每个用例对应一处**真实失效**（不是风格问题），命名里带上失效表现，
避免以后被"重构"悄悄改回旧写法：

1. ``OgeExecutionError`` 没有 ``.message`` —— 失败路径二次抛 ``AttributeError``，
   把原始错误吞掉。
2. ``TaskGraph.to_dict()`` 对同一任务两次调用 ``error_of()``，第二次可能为 ``None``。
3. ``RecipeRegistry.list`` 方法名遮蔽内置 ``list``，使同模块注解无法求值。
4. ``_pushdown_request`` 不再读 dataclass 私有字段取默认工厂。
5. ``match_resource`` 收轻量 shim（Protocol 契约）。
"""

from __future__ import annotations

import typing
from typing import Any

import pytest

from geonexus.adapters.oge_client import OgeExecutionError
from geonexus.adapters.oge_executor import OgeExecutor
from geonexus.geotask.graph import TaskGraph
from geonexus.geotask.model import GeoTask, TaskKind, TaskState
from geonexus.recipe.registry import RecipeRegistry

# ── 1. OGE 失败路径不得二次抛错 ────────────────────────────────────────


def test_oge_execution_error_has_no_message_attribute() -> None:
    """前提：异常对象本身没有 ``.message``，所以 ``exc.message`` 必然炸。"""
    exc = OgeExecutionError("boom", status_code=500)
    assert not hasattr(exc, "message")
    assert str(exc) == "boom"


def test_executor_failure_path_reports_error_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """轮询抛 ``OgeExecutionError`` 时，execute() 必须返回 failed 结果而不是崩掉。"""

    class _Client:
        def execute_operator(self, operator: str, *, tk: str, params: dict[str, Any]):
            class _Resp:
                process_id = "proc-1"

            return _Resp()

    class _CredMgr:
        def ensure_app_key(self) -> str:
            return "token"

    class _Poller:
        def poll_until_done(self, process_id: str, *, tk: str, timeout: float) -> None:
            raise OgeExecutionError("operator failed upstream", status_code=502)

    executor = OgeExecutor()
    monkeypatch.setattr(executor, "_get_client", lambda: _Client())
    monkeypatch.setattr(executor, "_get_cred_mgr", lambda: _CredMgr())
    monkeypatch.setattr(executor, "_get_poller", lambda: _Poller())

    result = executor.execute("Coverage.terrSlope", params={})

    assert result.status == "failed"
    assert result.error_message == "operator failed upstream"
    assert result.process_id == "proc-1"


# ── 2. 图谱错误视图只取一次 ───────────────────────────────────────────


def test_graph_error_view_is_stable_across_repeated_calls() -> None:
    """``to_dict()`` 反复调用结果一致，且错误节点与无错误节点各就各位。"""
    graph = TaskGraph(
        [
            GeoTask(task_id="a", kind=TaskKind.SKILL, spec={"skill": "noop"}),
            GeoTask(
                task_id="b",
                kind=TaskKind.SKILL,
                spec={"skill": "noop"},
                depends_on=["a"],
            ),
        ]
    )
    graph.mark_failed("a", "boom", "炸了")

    first = graph.to_dict()
    second = graph.to_dict()

    assert first == second
    nodes = {n["task_id"]: n for n in first["nodes"]}
    assert nodes["a"]["error"]["code"] == "boom"
    # b 依赖失败的 a → 级联标记 upstream_failed（这是设计行为，不是漏网）
    assert nodes["b"]["error"]["code"] == "upstream_failed"


def test_graph_error_view_returns_none_without_error() -> None:
    graph = TaskGraph([GeoTask(task_id="only", kind=TaskKind.SKILL, spec={"skill": "noop"})])
    assert graph._error_view("only") is None


# ── 3. ``list`` 遮蔽不再破坏注解求值 ──────────────────────────────────


def test_recipe_registry_annotations_are_resolvable() -> None:
    """方法名 ``list`` 曾遮蔽内置 ``list``；注解必须能真正求值。"""
    hints = typing.get_type_hints(RecipeRegistry.list)
    assert "return" in hints
    assert typing.get_type_hints(RecipeRegistry.versions)["return"] is not None

    # 功能本身不受影响
    registry = RecipeRegistry()
    assert registry.list() == []


# ── 4. 下推请求不再依赖 dataclass 私有字段 ────────────────────────────


def test_pushdown_request_helper_uses_default_factory() -> None:
    from geonexus.federation.client import _pushdown_request

    explicit = _pushdown_request(skill="ndvi", request_id="req-42")
    assert explicit.task_id == "req-42"
    assert explicit.skill == "ndvi"

    generated = _pushdown_request(skill="ndvi")
    assert generated.task_id.startswith("task-")

    two = _pushdown_request(skill="ndvi")
    assert two.task_id != generated.task_id  # 默认工厂每次生成新 id


# ── 5. match_resource 接受轻量 shim ──────────────────────────────────


def test_match_resource_accepts_shim_with_id_and_runtime() -> None:
    """CAFE 路由传的是 shim；Protocol 契约让这条路径有类型保证。"""
    from dataclasses import dataclass

    from geonexus.resource import ComputeCapability, match_resource

    @dataclass
    class _Shim:
        id: str
        runtime: Any = None

    match = match_resource(
        _Shim(id="task-1"),
        [ComputeCapability(node_url="http://node-a", cpu=4, memory_gb=8.0)],
    )
    assert match.matched is True
    assert match.model_id == "task-1"


def test_task_state_enum_exposes_failed() -> None:
    """佐证上面对 ``TaskState`` 的依赖是稳定的（防止枚举改名后测试静默失效）。"""
    assert TaskState.FAILED in TaskState
