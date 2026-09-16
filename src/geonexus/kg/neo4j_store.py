"""GeoKG Neo4j 后端 — 可插拔图数据库存储。

当配置 NEO4J_URI 环境变量后，KnowledgeGraph 可切换为 Neo4j 后端。
默认仍为内存图（零依赖）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class Neo4jStore:
    """GeoKG 的 Neo4j 图数据库后端。

    使用方式:
        store = Neo4jStore.from_env()
        if store:
            kg = KnowledgeGraph(backend=store)
    """

    def __init__(self, uri: str, user: str, password: str) -> None:
        self.uri = uri
        self.user = user
        self.password = password
        self._driver = None

    @classmethod
    def from_env(cls) -> Neo4jStore | None:
        uri = os.environ.get("NEO4J_URI")
        if not uri:
            return None
        return cls(
            uri=uri,
            user=os.environ.get("NEO4J_USER", "neo4j"),
            password=os.environ.get("NEO4J_PASSWORD", "neo4j"),
        )

    @property
    def available(self) -> bool:
        return bool(self.uri)

    def _get_driver(self):
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
                self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            except ImportError:
                raise ImportError(
                    "neo4j package required: pip install neo4j"
                ) from None
            except Exception as exc:
                logger.warning("Neo4j connection failed: %s", exc)
                self._driver = None
                raise RuntimeError(f"Neo4j connection failed: {exc}") from exc
        return self._driver

    def ensure_schema(self) -> None:
        """创建索引和约束。"""
        driver = self._get_driver()
        if driver is None:
            return
        with driver.session() as session:
            session.run("CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.id)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.type)")
            logger.info("Neo4j schema ensured")

    def add_entity(
        self, entity_id: str, entity_type: str,
        properties: dict[str, Any] | None = None,
        labels: list[str] | None = None,
    ) -> None:
        driver = self._get_driver()
        if driver is None:
            return
        extra_labels = ":".join(labels or []) + ":" if labels else ""
        with driver.session() as session:
            session.run(
                f"MERGE (e:{extra_labels}Entity {{id: $id}}) "
                "SET e.type = $type, e += $props",
                id=entity_id, type=entity_type,
                props=properties or {},
            )

    def add_relation(
        self, source_id: str, target_id: str, relation: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        driver = self._get_driver()
        if driver is None:
            return
        with driver.session() as session:
            session.run(
                f"MATCH (a:Entity {{id: $source}}), (b:Entity {{id: $target}}) "
                f"MERGE (a)-[r:{relation}]->(b) SET r += $props",
                source=source_id, target=target_id, props=properties or {},
            )

    def search(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        try:
            driver = self._get_driver()
        except ImportError:
            return []
        if driver is None:
            return []
        with driver.session() as session:
            result = session.run(
                "MATCH (e:Entity) WHERE e.id CONTAINS $kw OR e.type CONTAINS $kw "
                "RETURN e.id, e.type, e LIMIT $limit",
                kw=keyword, limit=limit,
            )
            return [{"id": r["e.id"], "type": r["e.type"], **r["e"]} for r in result]

    def close(self) -> None:
        if self._driver:
            self._driver.close()