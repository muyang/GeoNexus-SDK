"""V2 合约绑定：geo.execute(contract_binding=...) 必须过 ContractGate 双重验证。

钉住三条纪律：
  · 验证不过 → 拒绝（2007），不是降级放行；
  · 验证不了（无 GAAG 注册中心 / 查无此约）→ 同样拒绝 —— 授权声明不能默默失效；
  · capabilities 广告 v2.contract_binding，客户端按协商用，不靠猜。
"""

from __future__ import annotations

import pytest

from geonexus.gaag import GAAGContract, GAAGRegistry, embed_text
from geonexus.geocard import GeoCardBuilder
from geonexus.geomcp import CONTRACT_BINDING_REJECTED
from geonexus.geomcp.models import ExecuteParams
from geonexus.geonode import GeoNode


def _card():
    return (
        GeoCardBuilder(
            id="gaag-asset",
            type="data",
            name="ndvi analysis vegetation",
            description="NDVI analysis vegetation product.",
        )
        .spatial(bbox=[120.0, 30.0, 121.0, 31.0], crs="EPSG:4326")
        .build()
    )


def _registry(contract_id="gaag.ndvi", text="ndvi analysis vegetation"):
    reg = GAAGRegistry()
    reg.register(
        GAAGContract(
            contract_id=contract_id,
            asset_type="raster",
            card=_card(),
            semantic_embedding=embed_text(text),
            provenance="test",
        )
    )
    return reg


def _node(tmp_path, gaag_registry=None):
    node = GeoNode(name="v2-node", port=0, workdir=str(tmp_path), gaag_registry=gaag_registry)
    node.register_skill(
        name="ndvi-analysis",
        handler=lambda params, context: {"ndvi_mean": 0.42},
        description="NDVI analysis",
        input_schema={"required": []},
    )
    return node


def _exec(node, **kw):
    params = {"skill": "ndvi-analysis", "geocards": [], "params": {"region": "vegetation"}}
    params.update(kw)
    return node.execute(ExecuteParams(**params))


def test_matching_binding_passes_both_checks(tmp_path):
    node = _node(tmp_path, _registry())
    out = _exec(
        node,
        contract_binding="gaag.ndvi",
        spatial={"bbox": [120.2, 30.2, 120.8, 30.8], "crs": "EPSG:4326"},
    )
    assert out["status"] == "ok"
    assert out["outputs"] == {"ndvi_mean": 0.42}


def test_semantic_mismatch_is_rejected(tmp_path):
    node = _node(tmp_path, _registry(text="quarterly finance report of unrelated holdings"))
    with pytest.raises(Exception) as exc:
        _exec(
            node,
            contract_binding="gaag.ndvi",
            spatial={"bbox": [120.2, 30.2, 120.8, 30.8], "crs": "EPSG:4326"},
        )
    err = exc.value
    assert err.code == CONTRACT_BINDING_REJECTED
    assert err.data["semantic_ok"] is False


def test_contract_inconsistency_is_rejected(tmp_path):
    """语义对得上也不行：请求范围超出合约卡片 extent → 合约一致性不过。"""
    node = _node(tmp_path, _registry())
    with pytest.raises(Exception) as exc:
        _exec(
            node,
            contract_binding="gaag.ndvi",
            spatial={"bbox": [100.0, 10.0, 105.0, 15.0], "crs": "EPSG:4326"},
        )
    assert exc.value.code == CONTRACT_BINDING_REJECTED
    assert exc.value.data["contract_ok"] is False


def test_unknown_contract_is_rejected_not_ignored(tmp_path):
    node = _node(tmp_path, _registry())
    with pytest.raises(Exception) as exc:
        _exec(node, contract_binding="gaag.does-not-exist")
    assert exc.value.code == CONTRACT_BINDING_REJECTED
    assert exc.value.data["reason"] == "contract_not_found"


def test_binding_without_registry_is_rejected(tmp_path):
    """节点没挂 GAAG 注册中心时，声明了 binding 也必须拒绝 —— 查无对证 ≠ 放行。"""
    node = _node(tmp_path, gaag_registry=None)
    with pytest.raises(Exception) as exc:
        _exec(node, contract_binding="gaag.ndvi")
    assert exc.value.code == CONTRACT_BINDING_REJECTED
    assert exc.value.data["reason"] == "no_gaag_registry"


def test_capabilities_advertise_v2(tmp_path):
    # v2 广告是协商面：每加一个 V2 特性就在这里钉住它的形状
    assert _node(tmp_path, _registry()).server.capabilities()["v2"] == {
        "contract_binding": True,
        "pushdown": True,
        "federation": True,
    }
    assert _node(tmp_path, None).server.capabilities()["v2"] == {
        "contract_binding": False,
        "pushdown": True,
        "federation": True,
    }
