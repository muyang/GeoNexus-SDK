"""GAAG pgvector 存储后端 — PostgreSQL + pgvector + PostGIS 三重存储。

替代默认的内存/SQLite 后端，提供：
- pgvector 向量索引（IVFFlat）加速语义搜索
- PostGIS 空间索引（GiST）加速 bbox 过滤
- 空间+语义组合查询（单条 SQL）
"""

from __future__ import annotations

import json
import logging
import os

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    psycopg2 = None  # type: ignore
    psycopg2.extras = None  # type: ignore
    HAS_PSYCOPG2 = False

from ..geocard import GeoCard
from .contract import GAAGContract

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 64

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS gaag_contracts (
    contract_id     TEXT PRIMARY KEY,
    asset_type      TEXT NOT NULL DEFAULT 'raster',
    card_json       JSONB NOT NULL,
    scanned_meta_json JSONB DEFAULT '{{}}',
    embedding       vector({EMBEDDING_DIM}),
    provenance      TEXT DEFAULT '',
    registered_at   TIMESTAMPTZ DEFAULT now(),
    bbox            GEOMETRY(POLYGON, 4326)
);

CREATE INDEX IF NOT EXISTS idx_gaag_embedding
    ON gaag_contracts USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);

CREATE INDEX IF NOT EXISTS idx_gaag_bbox
    ON gaag_contracts USING GIST (bbox);

CREATE INDEX IF NOT EXISTS idx_gaag_asset_type
    ON gaag_contracts (asset_type);
"""


class PgVectorStore:
    """GAAG 注册中心的 PostgreSQL + pgvector 存储后端。

    使用方式：
        store = PgVectorStore(dsn="postgresql://user:pass@host:5432/db")
        # 或从环境变量
        store = PgVectorStore.from_env()
    """

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("POSTGIS_DSN", "")
        if not self.dsn:
            raise ValueError("POSTGIS_DSN environment variable or dsn argument required")
        if not HAS_PSYCOPG2:
            raise ImportError("psycopg2 is required for pgvector backend. Install: pip install psycopg2-binary")
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = True
        psycopg2.extras.register_vector(self.conn)
        self._ensure_schema()

    @classmethod
    def from_env(cls) -> PgVectorStore | None:
        dsn = os.environ.get("POSTGIS_DSN")
        if not dsn:
            return None
        return cls(dsn=dsn)

    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #
    def _ensure_schema(self) -> None:
        with self.conn.cursor() as cur:
            # Execute each statement separately
            for stmt in SCHEMA_SQL.split(";"):
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        cur.execute(stmt)
                    except Exception as exc:
                        if "already exists" not in str(exc) and "duplicate" not in str(exc).lower():
                            logger.warning("Schema statement skipped: %s", exc)
            logger.info("pgvector schema ensured (dim=%d)", EMBEDDING_DIM)

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def register(self, contract: GAAGContract) -> GAAGContract:
        card_json = json.dumps(contract.card.to_dict())
        meta_json = json.dumps(contract.scanned_meta)
        emb = contract.semantic_embedding
        # Build PostGIS polygon from bbox [w,s,e,n]
        bbox = contract.scanned_meta.get("bbox")
        if bbox and len(bbox) >= 4:
            bbox_wkt = f"SRID=4326;POLYGON(({bbox[0]} {bbox[1]},{bbox[2]} {bbox[1]},{bbox[2]} {bbox[3]},{bbox[0]} {bbox[3]},{bbox[0]} {bbox[1]}))"
        else:
            bbox_wkt = None

        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO gaag_contracts
                   (contract_id, asset_type, card_json, scanned_meta_json,
                    embedding, provenance, registered_at, bbox)
                   VALUES (%s, %s, %s, %s, %s, %s, %s,
                           ST_GeomFromText(%s, 4326) IF %s IS NOT NULL ELSE NULL)""",
                (
                    contract.contract_id,
                    contract.asset_type,
                    card_json,
                    meta_json,
                    emb,
                    contract.provenance,
                    contract.registered_at,
                    bbox_wkt,
                    bbox_wkt,
                ),
            )
        return contract

    def get(self, contract_id: str) -> GAAGContract | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT contract_id, asset_type, card_json, scanned_meta_json, "
                "embedding, provenance, registered_at FROM gaag_contracts "
                "WHERE contract_id = %s",
                (contract_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_contract(row)

    def search_semantic(
        self,
        query_text: str,
        k: int = 5,
        bbox: list[float] | None = None,
    ) -> list[tuple[GAAGContract, float]]:
        from .embed import embed_text

        qvec = embed_text(query_text)
        if bbox and len(bbox) >= 4:
            # Combined spatial + semantic query
            with self.conn.cursor() as cur:
                cur.execute(
                    """SELECT contract_id, asset_type, card_json, scanned_meta_json,
                              embedding, provenance, registered_at,
                              1 - (embedding <=> %s) AS similarity
                       FROM gaag_contracts
                       WHERE bbox IS NOT NULL
                         AND ST_Intersects(bbox, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
                       ORDER BY embedding <=> %s
                       LIMIT %s""",
                    (qvec, bbox[0], bbox[1], bbox[2], bbox[3], qvec, k),
                )
                return [(_row_to_contract(row), float(row[7])) for row in cur.fetchall()]
        else:
            # Semantic only
            with self.conn.cursor() as cur:
                cur.execute(
                    """SELECT contract_id, asset_type, card_json, scanned_meta_json,
                              embedding, provenance, registered_at,
                              1 - (embedding <=> %s) AS similarity
                       FROM gaag_contracts
                       ORDER BY embedding <=> %s
                       LIMIT %s""",
                    (qvec, qvec, k),
                )
                return [(_row_to_contract(row), float(row[7])) for row in cur.fetchall()]

    def remove(self, contract_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM gaag_contracts WHERE contract_id = %s", (contract_id,))

    def count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM gaag_contracts")
            return cur.fetchone()[0]

    def list(self, asset_type: str | None = None) -> list[GAAGContract]:
        with self.conn.cursor() as cur:
            if asset_type:
                cur.execute(
                    "SELECT contract_id, asset_type, card_json, scanned_meta_json, "
                    "embedding, provenance, registered_at FROM gaag_contracts "
                    "WHERE asset_type = %s ORDER BY registered_at DESC",
                    (asset_type,),
                )
            else:
                cur.execute(
                    "SELECT contract_id, asset_type, card_json, scanned_meta_json, "
                    "embedding, provenance, registered_at FROM gaag_contracts "
                    "ORDER BY registered_at DESC"
                )
            return [_row_to_contract(row) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #
def _row_to_contract(row: tuple) -> GAAGContract:
    cid, atype, card_json, meta_json, emb, prov, at = row
    card = GeoCard.from_dict(json.loads(card_json) if isinstance(card_json, str) else card_json)
    return GAAGContract(
        contract_id=cid,
        asset_type=atype,
        card=card,
        scanned_meta=json.loads(meta_json) if isinstance(meta_json, str) else meta_json,
        semantic_embedding=list(emb) if emb else None,
        provenance=prov or "",
        registered_at=str(at) if at else "",
    )