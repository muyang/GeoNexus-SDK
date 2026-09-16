"""geonexus.kg — 通用地理空间知识图谱原语。

只提供**图引擎**，不含任何 GeoNexus 领域内容：

- 实体/关系类型: :class:`KGEntity`, :class:`KGRelation`
- 内存图存储: :class:`KnowledgeGraph`（CRUD / 邻居 / 类型与标签检索 / 统计）
- 持久化: ``geonexus.kg.storage``（JSON / NDJSON，支持 gzip 与增量 append）
- 可选 Neo4j 后端: ``geonexus.kg.neo4j_store``
- 从 GAAG 合约导入实体: :meth:`KnowledgeGraph.import_from_gaag`

领域内容（SDG 框架、国家、卫星、行政区划、概念及其摄入管道）位于**独立的
``GeoKG`` 包**，单向依赖本模块::

    geokg ──依赖──▶ geonexus.kg

本模块不导入 ``geokg``，因此 SDK 可以在没有领域数据集的情况下独立安装。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 实体类型
ENTITY_TYPES = {
    "DataProduct", "Skill", "GeoNode", "Region",
    "SDG_Indicator", "Concept", "Organization",
}

# 关系类型
RELATION_TYPES = {
    "DEPENDS_ON", "PRODUCES", "LOCATED_IN", "COVERS",
    "RELATES_TO", "MEASURES", "OWNS", "IMPLEMENTS",
}


@dataclass
class KGEntity:
    """知识图谱节点。"""

    id: str
    type: str  # DataProduct | Skill | GeoNode | ...
    properties: dict[str, Any] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "properties": self.properties,
            "labels": self.labels,
        }


@dataclass
class KGRelation:
    """知识图谱边。"""

    source_id: str
    target_id: str
    relation: str  # DEPENDS_ON | PRODUCES | ...
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_id,
            "target": self.target_id,
            "relation": self.relation,
            "properties": self.properties,
        }


class KnowledgeGraph:
    """内存知识图谱存储。

    可扩展 Neo4j 后端：子类化并覆盖 _add_entity/_add_relation/query 方法。
    """

    def __init__(self, name: str = "geokg") -> None:
        self.name = name
        self._entities: dict[str, KGEntity] = {}
        self._relations: list[KGRelation] = []
        self._outgoing: dict[str, list[KGRelation]] = defaultdict(list)
        self._incoming: dict[str, list[KGRelation]] = defaultdict(list)
        # 增量追加游标：已落盘的实体/关系数量
        self._synced_entities: int = 0
        self._synced_relations: int = 0

    # -- CRUD -------------------------------------------------------------- #
    def add_entity(self, entity: KGEntity) -> KGEntity:
        self._entities[entity.id] = entity
        logger.debug("KG add entity: %s (%s)", entity.id, entity.type)
        return entity

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        **properties,
    ) -> KGRelation:
        rel = KGRelation(source_id, target_id, relation, properties)
        self._relations.append(rel)
        self._outgoing[source_id].append(rel)
        self._incoming[target_id].append(rel)
        return rel

    def get_entity(self, entity_id: str) -> KGEntity | None:
        return self._entities.get(entity_id)

    # -- Query ------------------------------------------------------------- #
    def neighbors(self, entity_id: str, relation: str | None = None) -> list[tuple[KGEntity, KGRelation]]:
        """查询所有邻居（出边）。"""
        results: list[tuple[KGEntity, KGRelation]] = []
        for rel in self._outgoing.get(entity_id, []):
            if relation and rel.relation != relation:
                continue
            target = self._entities.get(rel.target_id)
            if target:
                results.append((target, rel))
        return results

    def search_by_type(self, entity_type: str) -> list[KGEntity]:
        return [e for e in self._entities.values() if e.type == entity_type]

    def search_by_label(self, label: str) -> list[KGEntity]:
        return [e for e in self._entities.values() if label in e.labels]

    def search(self, keyword: str) -> list[KGEntity]:
        kw = keyword.lower()
        return [
            e for e in self._entities.values()
            if kw in e.id.lower()
            or kw in str(e.properties).lower()
            or kw in " ".join(e.labels).lower()
        ]

    # -- Stats ------------------------------------------------------------- #
    def entity_count(self) -> int:
        return len(self._entities)

    def relation_count(self) -> int:
        return len(self._relations)

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        for e in self._entities.values():
            by_type[e.type] += 1
        return {
            "name": self.name,
            "entities": self.entity_count(),
            "relations": self.relation_count(),
            "by_type": dict(by_type),
        }

    # -- Persistence ------------------------------------------------------- #
    def save(self, path: str) -> dict[str, Any]:
        """保存图谱。格式按扩展名自动选择：

            geokg.json          紧凑 JSON
            geokg.json.gz       gzip 压缩 JSON
            geokg.ndjson        NDJSON（可用 append 增量追加）
            geokg.ndjson.gz     gzip 压缩 NDJSON（推荐）

        Returns: 存储统计字典。
        """
        from .storage import save_graph

        info = save_graph(
            path,
            name=self.name,
            entities=(e.to_dict() for e in self._entities.values()),
            relations=(r.to_dict() for r in self._relations),
        )
        # 全量落盘后重置增量游标，避免后续 append() 重复写入
        self._synced_entities = len(self._entities)
        self._synced_relations = len(self._relations)
        logger.info("KG saved: %d entities ← %s", len(self._entities), path)
        return info

    def append(self, path: str) -> dict[str, Any]:
        """增量追加到 NDJSON 文件（仅追加新增部分，不重写全文件）。

        需要记录上次保存位置：调用方可通过 ``mark_synced()`` 标记。
        简化用法：``kg.save_ndjson(path)`` 首存，之后 ``kg.append_ndjson(path)``。
        """
        from .storage import append_graph

        n_ent_before = self._synced_entities
        n_rel_before = self._synced_relations
        ents = list(self._entities.values())[n_ent_before:]
        rels = self._relations[n_rel_before:]

        info = append_graph(
            path,
            entities=(e.to_dict() for e in ents),
            relations=(r.to_dict() for r in rels),
        )
        self._synced_entities = len(self._entities)
        self._synced_relations = len(self._relations)
        return info

    @classmethod
    def load(cls, path: str) -> KnowledgeGraph:
        """加载图谱（自动识别 json / json.gz / ndjson / ndjson.gz）。"""
        from .storage import load_graph

        name, entities, relations = load_graph(path)
        kg = cls(name)
        for e in entities:
            kg.add_entity(KGEntity(
                id=e["id"], type=e["type"],
                properties=e.get("properties", {}),
                labels=e.get("labels", []),
            ))
        for r in relations:
            kg.add_relation(r["source"], r["target"], r["relation"],
                            **r.get("properties", {}))
        kg._synced_entities = len(kg._entities)
        kg._synced_relations = len(kg._relations)
        logger.info("KG loaded: %d entities ← %s", kg.entity_count(), path)
        return kg

    # -- Import ------------------------------------------------------------ #
    def import_from_gaag(self, registry: Any) -> int:
        """从 GAAG 注册中心导入 DataProduct 实体和关系。

        每个合约 → DataProduct 实体
        合约依赖 → DEPENDS_ON 关系
        """
        count = 0
        for contract in registry.list():
            entity = KGEntity(
                id=contract.contract_id,
                type="DataProduct",
                properties={
                    "asset_type": contract.asset_type,
                    "description": contract.card.description,
                    "provenance": contract.provenance,
                    "tags": contract.card.tags,
                },
                labels=[contract.asset_type, "gaag"],
            )
            self.add_entity(entity)
            count += 1

            # 如果有依赖，创建关系
            if contract.scanned_meta.get("dependencies"):
                for dep_id in contract.scanned_meta["dependencies"]:
                    if dep_id in self._entities:
                        self.add_relation(
                            contract.contract_id, dep_id, "DEPENDS_ON",
                        )
        logger.info("KG imported %d entities from GAAG", count)
        return count

    def seed_demo(self) -> KnowledgeGraph:
        """注入演示实体：区域、SDG 指标、技能。"""
        # Regions
        for rid, name in [
            ("region.mekong", "Mekong Delta"),
            ("region.amazon", "Amazon Basin"),
            ("region.nairobi", "Nairobi"),
        ]:
            self.add_entity(KGEntity(rid, "Region", {"name": name}, ["southeast-asia" if "mekong" in rid else "global"]))

        # SDG Indicators
        sdgs = [
            ("sdg.6.6.1", "Water-related ecosystems extent", "SDG_Indicator"),
            ("sdg.11.3.1", "Land consumption rate", "SDG_Indicator"),
            ("sdg.15.1.1", "Forest area proportion", "SDG_Indicator"),
        ]
        for sid, name, etype in sdgs:
            self.add_entity(KGEntity(sid, etype, {"name": name}, ["sdg"]))

        # Skills
        skills = [
            ("skill.ndvi-analysis", "NDVI Analysis"),
            ("skill.flood-impact-analysis", "Flood Impact Analysis"),
            ("skill.ndwi-analysis", "Water Body Detection"),
        ]
        for sid, name in skills:
            self.add_entity(KGEntity(sid, "Skill", {"name": name}, ["analysis"]))

        # Relations
        self.add_relation("region.mekong", "sdg.6.6.1", "MEASURES")
        self.add_relation("skill.flood-impact-analysis", "region.mekong", "LOCATED_IN")
        self.add_relation("skill.flood-impact-analysis", "skill.ndwi-analysis", "DEPENDS_ON")

        return self