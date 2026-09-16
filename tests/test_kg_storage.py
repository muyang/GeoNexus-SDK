"""Tests for GeoKG storage layer (gzip + NDJSON incremental append)."""

from __future__ import annotations

import gzip
import json
import os

import pytest

from geonexus.kg import KGEntity, KnowledgeGraph
from geonexus.kg.storage import (
    FORMAT_VERSION,
    append_graph,
    detect_format,
    iter_entities,
    load_graph,
    save_graph,
    storage_stats,
)


def _small_graph(name: str = "test", n: int = 50) -> KnowledgeGraph:
    kg = KnowledgeGraph(name)
    for i in range(n):
        kg.add_entity(KGEntity(f"e.{i}", "Thing", {"i": i}, ["test"]))
    for i in range(n - 1):
        kg.add_relation(f"e.{i}", f"e.{i+1}", "NEXT")
    return kg


# --------------------------------------------------------------------------- #
# 格式探测
# --------------------------------------------------------------------------- #

class TestDetectFormat:
    @pytest.mark.parametrize("path,expected", [
        ("a.json", ("json", False)),
        ("a.json.gz", ("json", True)),
        ("a.ndjson", ("ndjson", False)),
        ("a.ndjson.gz", ("ndjson", True)),
        ("dir/b.nn.json", ("json", False)),
        ("DIR/B.NDJSON.GZ", ("ndjson", True)),
    ])
    def test_detection(self, path, expected):
        assert detect_format(path) == expected


# --------------------------------------------------------------------------- #
# 全量保存 / 加载
# --------------------------------------------------------------------------- #

class TestRoundTrip:
    @pytest.mark.parametrize("filename", [
        "g.json", "g.json.gz", "g.ndjson", "g.ndjson.gz",
    ])
    def test_roundtrip_all_formats(self, tmp_path, filename):
        kg = _small_graph()
        p = str(tmp_path / filename)
        kg.save(p)
        kg2 = KnowledgeGraph.load(p)
        assert kg2.entity_count() == kg.entity_count()
        assert kg2.relation_count() == kg.relation_count()
        assert kg2.get_entity("e.0") is not None
        assert kg2.get_entity("e.0").properties["i"] == 0

    def test_name_preserved(self, tmp_path):
        kg = _small_graph("my-graph")
        p = str(tmp_path / "g.ndjson.gz")
        kg.save(p)
        assert KnowledgeGraph.load(p).name == "my-graph"

    def test_relations_preserved(self, tmp_path):
        kg = _small_graph(n=10)
        p = str(tmp_path / "g.ndjson.gz")
        kg.save(p)
        kg2 = KnowledgeGraph.load(p)
        neighbors = kg2.neighbors("e.0", "NEXT")
        assert len(neighbors) == 1
        assert neighbors[0][0].id == "e.1"

    def test_creates_parent_dirs(self, tmp_path):
        kg = _small_graph(n=3)
        p = str(tmp_path / "a" / "b" / "g.ndjson.gz")
        kg.save(p)
        assert os.path.exists(p)


# --------------------------------------------------------------------------- #
# 压缩效果
# --------------------------------------------------------------------------- #

class TestCompression:
    def test_gzip_smaller_than_plain(self, tmp_path):
        kg = _small_graph(n=300)
        plain = str(tmp_path / "g.ndjson")
        gz = str(tmp_path / "g.ndjson.gz")
        kg.save(plain)
        kg.save(gz)
        assert os.path.getsize(gz) < os.path.getsize(plain)

    def test_gzip_json_smaller(self, tmp_path):
        kg = _small_graph(n=300)
        plain = str(tmp_path / "g.json")
        gz = str(tmp_path / "g.json.gz")
        kg.save(plain)
        kg.save(gz)
        assert os.path.getsize(gz) < os.path.getsize(plain)

    def test_gzip_readable_by_stdlib(self, tmp_path):
        """gzip 文件应能被标准库直接解压。"""
        kg = _small_graph(n=10)
        gz = str(tmp_path / "g.json.gz")
        kg.save(gz)
        with gzip.open(gz, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["name"] == "test"
        assert len(data["entities"]) == 10


# --------------------------------------------------------------------------- #
# 增量追加
# --------------------------------------------------------------------------- #

class TestIncrementalAppend:
    def test_append_only_new_entities(self, tmp_path):
        """append() 只写入新增实体，不重写全文件。"""
        kg = _small_graph(n=10)
        p = str(tmp_path / "g.ndjson.gz")
        kg.save(p)
        size_after_save = os.path.getsize(p)

        for i in range(10, 15):
            kg.add_entity(KGEntity(f"e.{i}", "Thing", {"i": i}))
        info = kg.append(p)

        assert info["appended_entities"] == 5, "只应追加 5 个新实体"
        assert os.path.getsize(p) > size_after_save

        kg2 = KnowledgeGraph.load(p)
        assert kg2.entity_count() == 15

    def test_append_twice_accumulates(self, tmp_path):
        kg = _small_graph(n=5)
        p = str(tmp_path / "g.ndjson.gz")
        kg.save(p)

        kg.add_entity(KGEntity("x.1", "Thing", {}))
        kg.append(p)
        kg.add_entity(KGEntity("x.2", "Thing", {}))
        kg.append(p)

        kg2 = KnowledgeGraph.load(p)
        assert kg2.entity_count() == 7
        assert kg2.get_entity("x.1") is not None
        assert kg2.get_entity("x.2") is not None

    def test_append_without_prior_save(self, tmp_path):
        """未 save 直接 append 应写入全部内容并建表头。"""
        kg = _small_graph(n=4)
        p = str(tmp_path / "g.ndjson.gz")
        info = kg.append(p)
        assert info["appended_entities"] == 4
        kg2 = KnowledgeGraph.load(p)
        assert kg2.entity_count() == 4

    def test_append_rejects_json_format(self, tmp_path):
        """增量追加仅支持 NDJSON。"""
        kg = _small_graph(n=3)
        p = str(tmp_path / "g.json")
        with pytest.raises(ValueError, match="NDJSON"):
            kg.append(p)

    def test_append_relations_too(self, tmp_path):
        kg = _small_graph(n=3)
        p = str(tmp_path / "g.ndjson.gz")
        kg.save(p)
        kg.add_entity(KGEntity("z.1", "Thing", {}))
        kg.add_relation("z.1", "e.0", "LINKS")
        info = kg.append(p)
        assert info["appended_entities"] == 1
        assert info["appended_relations"] == 1
        kg2 = KnowledgeGraph.load(p)
        assert kg2.relation_count() == kg.relation_count()

    def test_load_after_append_keeps_name(self, tmp_path):
        kg = _small_graph("named-graph", n=3)
        p = str(tmp_path / "g.ndjson.gz")
        kg.save(p)
        kg.add_entity(KGEntity("n.1", "Thing", {}))
        kg.append(p)
        assert KnowledgeGraph.load(p).name == "named-graph"


# --------------------------------------------------------------------------- #
# 流式与统计
# --------------------------------------------------------------------------- #

class TestStreamingAndStats:
    def test_iter_entities_streams(self, tmp_path):
        kg = _small_graph(n=20)
        p = str(tmp_path / "g.ndjson.gz")
        kg.save(p)
        ids = [e["id"] for e in iter_entities(p)]
        assert len(ids) == 20
        assert "e.0" in ids

    def test_iter_entities_on_json_fallback(self, tmp_path):
        kg = _small_graph(n=5)
        p = str(tmp_path / "g.json")
        kg.save(p)
        assert len(list(iter_entities(p))) == 5

    def test_storage_stats(self, tmp_path):
        kg = _small_graph(n=10)
        p = str(tmp_path / "g.ndjson.gz")
        kg.save(p)
        st = storage_stats(p)
        assert st["exists"] is True
        assert st["format"] == "ndjson"
        assert st["compressed"] is True
        assert st["bytes"] > 0
        assert st["megabytes"] >= 0

    def test_storage_stats_missing(self, tmp_path):
        assert storage_stats(str(tmp_path / "nope.json"))["exists"] is False

    def test_save_returns_info(self, tmp_path):
        kg = _small_graph(n=7)
        p = str(tmp_path / "g.ndjson.gz")
        info = kg.save(p)
        assert info["entities"] == 7
        assert info["relations"] == 6
        assert info["format"] == "ndjson"
        assert info["compressed"] is True


# --------------------------------------------------------------------------- #
# 低层 API
# --------------------------------------------------------------------------- #

class TestLowLevelApi:
    def test_save_graph_and_load_graph(self, tmp_path):
        p = str(tmp_path / "g.ndjson.gz")
        info = save_graph(
            p, name="low",
            entities=[{"id": "a", "type": "T", "properties": {}, "labels": []}],
            relations=[{"source": "a", "target": "a", "relation": "SELF", "properties": {}}],
        )
        assert info["entities"] == 1
        name, ents, rels = load_graph(p)
        assert name == "low"
        assert len(ents) == 1
        assert len(rels) == 1

    def test_append_graph_creates_header(self, tmp_path):
        p = str(tmp_path / "h.ndjson")
        info = append_graph(p, entities=[{"id": "a", "type": "T",
                                          "properties": {}, "labels": []}])
        assert info["appended_entities"] == 1
        with open(p, encoding="utf-8") as fh:
            first = json.loads(fh.readline())
        assert first["_type"] == "header"
        assert first["version"] == FORMAT_VERSION

    def test_append_graph_rejects_json(self, tmp_path):
        with pytest.raises(ValueError, match="NDJSON"):
            append_graph(str(tmp_path / "x.json"), entities=[])

    def test_corrupt_line_skipped(self, tmp_path):
        """损坏行应被跳过而非抛异常。"""
        p = str(tmp_path / "c.ndjson")
        kg = _small_graph(n=3)
        kg.save(p)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write("this is not json\n")
            fh.write(json.dumps({"_type": "entity", "id": "ok", "type": "T",
                                 "properties": {}, "labels": []}) + "\n")
        kg2 = KnowledgeGraph.load(p)
        assert kg2.entity_count() == 4
        assert kg2.get_entity("ok") is not None