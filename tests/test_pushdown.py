"""V2 下推引擎：local vs push 的成本决策，可解释、可复盘。

钉住的纪律：
  · auto 估不出数字就**回退 local 并写理由**，不编数字做决策；
  · push 解析不到属主 = 2008（auto 不报这个错，它回退）；
  · 决策与两侧估算写进执行结果（result["pushdown"]）；
  · push 真落地 = 复用一跳委托，结果来自属主节点。
"""

from __future__ import annotations

import pytest

from geonexus.geocard import GeoCardBuilder
from geonexus.geomcp import PUSHDOWN_UNRESOLVED
from geonexus.geomcp.models import ExecuteParams
from geonexus.geonode import GeoNode
from geonexus.registry.server import RegistryServer


def _card():
    return (
        GeoCardBuilder(
            id="ds", type="data", name="Dataset", description="Pushdown fixture dataset."
        )
        .spatial(bbox=[100.0, 0.0, 110.0, 10.0], crs="EPSG:4326")
        .build()
    )


def _node(tmp_path, name, marker, registry_url=None):
    node = GeoNode(name=name, port=0, workdir=str(tmp_path), registry_url=registry_url)
    node.register_geocard(_card())
    node.register_skill(
        name="work",
        handler=lambda p, c: {"ran_on": marker},
        description="w",
        input_schema={"required": []},
    )
    return node


def _run(node, spec):
    return node.execute(ExecuteParams(skill="work", geocards=["ds"], params={}, pushdown_spec=spec))


def test_auto_picks_local_when_pull_is_cheaper(tmp_path):
    node = _node(tmp_path, "decider", "decider")
    out = _run(
        node,
        {"strategy": "auto", "data_bytes": 1_000, "bandwidth_bps": 1_000_000, "remote_queue_s": 30},
    )
    assert out["outputs"] == {"ran_on": "decider"}
    pd = out["pushdown"]
    assert pd["decision"] == "local"
    assert pd["estimates"]["pull_data_s"] == 0.001
    assert pd["estimates"]["push_exec_s"] == 30.0
    assert any("auto" in r for r in pd["reasons"])


def test_auto_push_delegates_to_owner(tmp_path):
    reg = RegistryServer()
    running = reg.start_in_thread(port=0)
    running.wait_until_ready()
    owner = _node(tmp_path, "owner", "owner")
    owner_running = owner.start_in_thread(port=0)
    owner_running.wait_until_ready()
    owner_url = f"http://127.0.0.1:{owner_running.port}"
    reg.register(_card().to_dict(), owner_url, status="approved")
    try:
        decider = _node(
            tmp_path, "decider", "decider", registry_url=f"http://127.0.0.1:{running.port}"
        )
        out = _run(
            decider,
            {
                "strategy": "auto",
                "data_bytes": 10_000_000_000,
                "bandwidth_bps": 1_000,
                "remote_queue_s": 5,
            },
        )
        assert out["outputs"] == {"ran_on": "owner"}, "push 决策必须真落到属主"
        assert out["pushdown"]["decision"] == "push"
        assert out["pushdown"]["owner"] == owner_url, "决策记录要写明属主是谁"
    finally:
        owner_running.stop()
        running.stop()


def test_auto_missing_estimates_falls_back_local_with_reason(tmp_path):
    node = _node(tmp_path, "decider", "decider")
    out = _run(node, {"strategy": "auto"})
    pd = out["pushdown"]
    assert pd["decision"] == "local"
    assert "missing-estimates" in pd["reasons"]
    assert out["outputs"] == {"ran_on": "decider"}


def test_local_forced_ignores_terrible_estimates(tmp_path):
    node = _node(tmp_path, "decider", "decider")
    out = _run(
        node,
        {
            "strategy": "local",
            "data_bytes": 10_000_000_000,
            "bandwidth_bps": 1,
            "remote_queue_s": 0.1,
        },
    )
    assert out["pushdown"]["decision"] == "local"
    assert out["outputs"] == {"ran_on": "decider"}


def test_migrate_is_alias_of_local(tmp_path):
    node = _node(tmp_path, "decider", "decider")
    out = _run(
        node, {"strategy": "migrate", "data_bytes": 1, "bandwidth_bps": 1, "remote_queue_s": 0.1}
    )
    assert out["pushdown"]["requested"] == "migrate"
    assert out["pushdown"]["decision"] == "local"


def test_push_without_registry_is_2008(tmp_path):
    node = _node(tmp_path, "decider", "decider")
    with pytest.raises(Exception) as exc:
        _run(node, {"strategy": "push"})
    assert exc.value.code == PUSHDOWN_UNRESOLVED
    assert exc.value.data["reason"] == "no_registry_binding"


def test_push_with_unknown_owner_is_2008(tmp_path):
    reg = RegistryServer()
    running = reg.start_in_thread(port=0)
    running.wait_until_ready()
    try:
        node = _node(
            tmp_path, "decider", "decider", registry_url=f"http://127.0.0.1:{running.port}"
        )
        with pytest.raises(Exception) as exc:
            _run(node, {"strategy": "push"})
        assert exc.value.code == PUSHDOWN_UNRESOLVED
        assert exc.value.data["reason"] == "owner_unknown"
    finally:
        running.stop()


def test_bad_strategy_is_invalid_argument(tmp_path):
    node = _node(tmp_path, "decider", "decider")
    with pytest.raises(Exception) as exc:
        _run(node, {"strategy": "teleport"})
    assert exc.value.code == 2004
    assert exc.value.data["field"] == "pushdown_spec.strategy"


def test_no_spec_means_no_pushdown_record(tmp_path):
    node = _node(tmp_path, "decider", "decider")
    assert "pushdown" not in _run(node, None)
