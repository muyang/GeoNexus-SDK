"""V2 联邦：federated_sources 跨注册中心解析。

钉住的纪律：
  · 一跳、去重、只认 approved；解析失败一律 2009，不静默忽略；
  · 联邦卡参与合同门控（它的 bbox 就是合约的一部分）；
  · 溯源写进 result["federated"]（哪个注册中心、谁的节点）；
  · 联邦属主并入 pushdown 的 owner 表 —— push 能真推到远域节点。
"""

from __future__ import annotations

import pytest

from geonexus.geocard import GeoCardBuilder
from geonexus.geomcp import FEDERATION_UNRESOLVED
from geonexus.geomcp.models import ExecuteParams
from geonexus.geonode import GeoNode
from geonexus.registry.server import RegistryServer


def _card():
    return (
        GeoCardBuilder(
            id="fed-card",
            type="data",
            name="Federated dataset",
            description="Lives in another registry domain.",
        )
        .spatial(bbox=[100.0, 0.0, 110.0, 10.0], crs="EPSG:4326")
        .build()
    )


@pytest.fixture()
def federation(tmp_path):
    """A 域：registry-A + 属主节点（持卡 + 技能）；B 域：registry-B + 本地节点。"""
    reg_a = RegistryServer()
    run_a = reg_a.start_in_thread(port=0)
    run_a.wait_until_ready()
    owner = GeoNode(name="owner", port=0, workdir=str(tmp_path / "owner"))
    owner.register_geocard(_card())
    owner.register_skill(
        name="work",
        handler=lambda p, c: {"ran_on": "owner"},
        description="w",
        input_schema={"required": []},
    )
    run_owner = owner.start_in_thread(port=0)
    run_owner.wait_until_ready()
    owner_url = f"http://127.0.0.1:{run_owner.port}"
    reg_a.register(_card().to_dict(), owner_url, status="approved")

    reg_b = RegistryServer()
    run_b = reg_b.start_in_thread(port=0)
    run_b.wait_until_ready()
    local = GeoNode(
        name="local",
        port=0,
        workdir=str(tmp_path / "local"),
        registry_url=f"http://127.0.0.1:{run_b.port}",
    )
    local.register_skill(
        name="work",
        handler=lambda p, c: {"ran_on": "local"},
        description="w",
        input_schema={"required": []},
    )
    yield {
        "reg_a": f"http://127.0.0.1:{run_a.port}",
        "reg_a_server": reg_a,
        "owner_url": owner_url,
        "local": local,
    }
    run_owner.stop()
    run_a.stop()
    run_b.stop()


def _exec(node, sources, **kw):
    return node.execute(
        ExecuteParams(skill="work", geocards=[], params={}, federated_sources=sources, **kw)
    )


def test_federated_card_resolves_and_provenance_recorded(federation):
    src = f"{federation['reg_a']}#fed-card"
    out = _exec(federation["local"], [src], spatial={"bbox": [101, 1, 102, 2], "crs": "EPSG:4326"})
    assert out["outputs"] == {"ran_on": "local"}
    assert out["federated"] == [
        {
            "source": src,
            "card": "fed-card",
            "owner": federation["owner_url"],
            "registry": federation["reg_a"],
        }
    ]


def test_federated_card_participates_in_contract_gate(federation):
    with pytest.raises(Exception) as exc:  # 超出联邦卡 extent → 2000
        _exec(
            federation["local"],
            [f"{federation['reg_a']}#fed-card"],
            spatial={"bbox": [0, 0, 1, 1], "crs": "EPSG:4326"},
        )
    assert exc.value.code == 2000


def test_duplicate_sources_are_deduped(federation):
    src = f"{federation['reg_a']}#fed-card"
    out = _exec(federation["local"], [src, src])
    assert len(out["federated"]) == 1


def test_malformed_source_is_2009(federation):
    with pytest.raises(Exception) as exc:
        _exec(federation["local"], ["garbage-without-hash"])
    assert exc.value.code == FEDERATION_UNRESOLVED
    assert exc.value.data["reason"] == "malformed_source"


def test_unreachable_registry_is_2009(federation):
    with pytest.raises(Exception) as exc:
        _exec(federation["local"], ["http://127.0.0.1:1#fed-card"])
    assert exc.value.code == FEDERATION_UNRESOLVED
    assert exc.value.data["reason"] == "registry_unreachable"


def test_pending_entry_is_2009(federation):
    federation["reg_a_server"].register(
        GeoCardBuilder(id="pending-card", type="data", name="P", description="d")
        .spatial(bbox=[0, 0, 1, 1], crs="EPSG:4326")
        .build()
        .to_dict(),
        federation["owner_url"],
        status="pending",
    )
    with pytest.raises(Exception) as exc:
        _exec(federation["local"], [f"{federation['reg_a']}#pending-card"])
    assert exc.value.code == FEDERATION_UNRESOLVED
    assert exc.value.data["reason"] == "not_approved"


def test_pushdown_push_uses_federated_owner(federation):
    """联邦 + 下推组合：push 把执行真推到远域属主节点。"""
    out = _exec(
        federation["local"],
        [f"{federation['reg_a']}#fed-card"],
        spatial={"bbox": [101, 1, 102, 2], "crs": "EPSG:4326"},
        pushdown_spec={"strategy": "push"},
    )
    assert out["outputs"] == {"ran_on": "owner"}
    assert out["pushdown"]["owner"] == federation["owner_url"]
    assert out["federated"][0]["card"] == "fed-card"
