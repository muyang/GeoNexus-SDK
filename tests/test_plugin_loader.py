"""插件加载器测试：两种插件形态、坏插件隔离、health 面可见。"""

from __future__ import annotations

from types import SimpleNamespace

from geonexus.geonode import GeoNode, Skill, load_plugins


def _ep(name, obj=None, exc=None, version="0.1.0"):
    def load():
        if exc is not None:
            raise exc
        return obj

    return SimpleNamespace(name=name, load=load, dist=SimpleNamespace(version=version))


class _Declarative:
    """声明式插件：cards() / skills()。"""

    name = "decl"
    version = "1.0.0"

    @staticmethod
    def skills():
        return [Skill(name="decl-skill", description="d", handler=lambda p, c: {"ok": True})]

    @staticmethod
    def cards():
        return []


class _Imperative:
    """命令式插件：register(node)。"""

    @staticmethod
    def register(node):
        node.register_skill(
            name="imp-skill",
            handler=lambda p, c: {"ok": 1},
            description="i",
            input_schema={"required": []},
        )


def test_both_plugin_shapes_load(tmp_path):
    node = GeoNode(name="plug-node", port=0, workdir=str(tmp_path))
    status = load_plugins(node, entry_points=[_ep("decl", _Declarative), _ep("imp", _Imperative)])
    assert [s["name"] for s in status] == ["decl", "imp"]
    assert all(s["ok"] for s in status)
    assert node.server.skill_registry.has("decl-skill")
    assert node.server.skill_registry.has("imp-skill")


def test_broken_plugin_is_isolated_and_visible(tmp_path):
    node = GeoNode(name="plug-node", port=0, workdir=str(tmp_path))
    status = load_plugins(
        node,
        entry_points=[
            _ep("good", _Declarative),
            _ep("bad", exc=RuntimeError("boom in third-party code")),
        ],
    )
    good, bad = status
    assert good["ok"] is True
    assert bad["ok"] is False and "boom" in bad["error"]
    # 坏插件不拖垮节点：好插件的技能仍在，health 面把两者都报出来
    assert node.server.skill_registry.has("decl-skill")
    node.server.plugin_status = status
    names = {p["name"]: p["ok"] for p in node.health()["plugins"]}
    assert names == {"good": True, "bad": False}


def test_version_comes_from_distribution(tmp_path):
    node = GeoNode(name="plug-node", port=0, workdir=str(tmp_path))
    status = load_plugins(node, entry_points=[_ep("decl", _Declarative, version="2.3.4")])
    assert status[0]["version"] == "2.3.4"


def test_no_entry_points_is_a_noop(tmp_path, monkeypatch):
    # monkeypatch 掉发现函数：测试的通过与否不能取决于 venv 里恰好装了/没装什么插件
    monkeypatch.setattr("geonexus.geonode.node.discover_plugin_entry_points", lambda: [])
    node = GeoNode(name="plug-node", port=0, workdir=str(tmp_path))
    assert load_plugins(node, entry_points=[]) == []
    assert node.health()["plugins"] == []
