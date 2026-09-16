"""GAAG registry — contract registration, semantic retrieval, contract gating.

包含:
- :class:`GAAGRegistry` — 内存合约注册中心（可选 SQLite 持久化），
  支持按 id/type 检索、语义 top-k 检索、一键注册扫描结果。
- :class:`ContractGate` — 合约满足度门控：语义相似度 + 合约一致性双重验证。

参考设计: ``docs/implementation-plan.md`` 子任务2 (GAAG 地理资产合约注册中心)。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..geocard import ContractValidator, GeoCard
from .contract import GAAGContract, GAAGError
from .embed import cosine_similarity
from .scanner import scan_asset_to_contract

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
class GAAGRegistry:
    """GAAG contract registry — 内存/SQLite/pgvector 可插拔后端。

    Supports the ``register → search → gate`` lifecycle of the GAAG design.

    Args:
        persist_path: 可选 SQLite 文件路径（内存注册时持久化）。
        backend: ``"memory"`` (默认) | ``"pgvector"``。
    """

    def __init__(
        self,
        persist_path: str | None = None,
        backend: str = "memory",
    ) -> None:
        self._contracts: dict[str, GAAGContract] = {}
        self._persist_path = persist_path
        self._backend = backend
        self._pgvector = None
        if backend == "pgvector":
            self._init_pgvector()
        elif persist_path:
            _ensure_sqlite(persist_path)
            self._load_from_sqlite()

    def _init_pgvector(self) -> None:
        try:
            from .pgvector_store import PgVectorStore
            self._pgvector = PgVectorStore.from_env()
            if self._pgvector is None:
                logger.warning("POSTGIS_DSN not set, falling back to memory store")
                self._backend = "memory"
            else:
                logger.info("GAAG using pgvector backend (dim=%d)", 64)
        except Exception as exc:
            logger.warning("pgvector init failed, falling back to memory: %s", exc)
            self._backend = "memory"
            self._pgvector = None

    # -- registration ------------------------------------------------------- #
    def register(self, contract: GAAGContract) -> GAAGContract:
        if self._pgvector:
            return self._pgvector.register(contract)
        if contract.contract_id in self._contracts:
            raise GAAGError(f"Contract already registered: {contract.contract_id}")
        self._contracts[contract.contract_id] = contract
        if self._persist_path:
            self._persist_one(contract)
        logger.info("GAAG registered %s (type=%s, emb=%s)",
                    contract.contract_id, contract.asset_type,
                    "yes" if contract.semantic_embedding else "no")
        return contract

    def register_asset(
        self,
        path: str | Path,
        *,
        contract_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        embedding: list[float] | None = None,
    ) -> GAAGContract:
        """One-shot: scan a file and register the resulting contract."""
        contract = scan_asset_to_contract(
            path,
            contract_id=contract_id,
            name=name,
            description=description,
            embedding=embedding,
        )
        return self.register(contract)

    def remove(self, contract_id: str) -> None:
        if self._pgvector:
            return self._pgvector.remove(contract_id)
        self._contracts.pop(contract_id, None)
        if self._persist_path:
            _sqlite(self._persist_path).execute(
                "DELETE FROM gaag_contracts WHERE contract_id = ?", (contract_id,)
            ).connection.commit()

    # -- query -------------------------------------------------------------- #
    def get(self, contract_id: str) -> GAAGContract | None:
        if self._pgvector:
            return self._pgvector.get(contract_id)
        return self._contracts.get(contract_id)

    def list(self, asset_type: str | None = None) -> list[GAAGContract]:
        if self._pgvector:
            return self._pgvector.list(asset_type)
        contracts = list(self._contracts.values())
        if asset_type:
            contracts = [c for c in contracts if c.asset_type == asset_type]
        return contracts

    def search_semantic(
        self, query_text: str, k: int = 5, bbox: list[float] | None = None
    ) -> list[tuple[GAAGContract, float]]:
        if self._pgvector:
            return self._pgvector.search_semantic(query_text, k=k, bbox=bbox)
        from .embed import embed_text

        qvec = embed_text(query_text)
        scored: list[tuple[GAAGContract, float]] = []
        for contract in self._contracts.values():
            emb = contract.semantic_embedding
            score = cosine_similarity(qvec, emb) if emb else 0.0
            # Simple bbox filter (memory)
            if bbox and contract.scanned_meta.get("bbox"):
                cb = contract.scanned_meta["bbox"]
                if len(cb) >= 4 and (
                    cb[2] < bbox[0] or cb[0] > bbox[2] or cb[3] < bbox[1] or cb[1] > bbox[3]
                ):
                    continue
            scored.append((contract, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]

    def count(self) -> int:
        if self._pgvector:
            return self._pgvector.count()
        return len(self._contracts)

    # -- persistence -------------------------------------------------------- #
    def _persist_one(self, contract: GAAGContract) -> None:
        conn = _sqlite(self._persist_path)
        conn.execute(
            """
            INSERT OR REPLACE INTO gaag_contracts
                (contract_id, asset_type, card_json, scanned_meta_json,
                 embedding_json, provenance, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract.contract_id,
                contract.asset_type,
                json.dumps(contract.card.to_dict()),
                json.dumps(contract.scanned_meta),
                json.dumps(contract.semantic_embedding),
                contract.provenance,
                contract.registered_at,
            ),
        )
        conn.commit()

    def _load_from_sqlite(self) -> None:
        rows = _sqlite(self._persist_path).execute(
            "SELECT contract_id, asset_type, card_json, scanned_meta_json, "
            "embedding_json, provenance, registered_at FROM gaag_contracts"
        ).fetchall()
        for (cid, atype, card_json, meta_json, emb_json, prov, at) in rows:
            card = GeoCard.from_dict(json.loads(card_json))
            self._contracts[cid] = GAAGContract(
                contract_id=cid,
                asset_type=atype,
                card=card,
                scanned_meta=json.loads(meta_json),
                semantic_embedding=json.loads(emb_json) if emb_json else None,
                provenance=prov,
                registered_at=at,
            )
        if rows:
            logger.info("GAAG loaded %d contracts from %s", len(rows), self._persist_path)


# --------------------------------------------------------------------------- #
# Contract gate
# --------------------------------------------------------------------------- #
@dataclass
class ContractGateResult:
    """Outcome of the GAAG contract gate (double verification)."""

    passed: bool
    contract_id: str
    semantic_similarity: float
    semantic_ok: bool
    contract_reasons: list[str] = field(default_factory=list)
    contract_ok: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "contract_id": self.contract_id,
            "semantic_similarity": round(self.semantic_similarity, 4),
            "semantic_ok": self.semantic_ok,
            "contract_ok": self.contract_ok,
            "contract_reasons": self.contract_reasons,
            "message": self.message,
        }


class ContractGate:
    """GAAG Contract Satisfaction Gating.

    双重验证:
    1. 语义相似度 — 请求文本与合约语义向量的 cosine similarity ≥ threshold
    2. 合约一致性 — ContractValidator 的 CRS/bbox/temporal/bands/resolution 检查
    """

    def __init__(self, semantic_threshold: float = 0.25) -> None:
        self.semantic_threshold = semantic_threshold
        self.validator = ContractValidator()

    def gate(
        self,
        contract: GAAGContract,
        query_text: str,
        *,
        bbox: list[float] | None = None,
        crs: str | None = None,
        start: str | None = None,
        end: str | None = None,
        required_bands: list[str] | None = None,
        required_resolution: float | None = None,
        semantic_threshold: float | None = None,
    ) -> ContractGateResult:
        threshold = semantic_threshold if semantic_threshold is not None else self.semantic_threshold

        # 1. Semantic similarity
        qvec = _embed(query_text)
        sim = cosine_similarity(qvec, contract.semantic_embedding) if contract.semantic_embedding else 0.0
        semantic_ok = sim >= threshold

        # 2. Contract consistency (ContractValidator)
        result = self.validator.check(
            contract.card,
            bbox=bbox,
            crs=crs,
            start=start,
            end=end,
            required_bands=required_bands,
            required_resolution=required_resolution,
        )
        passed = semantic_ok and result.satisfied

        message = ""
        if not semantic_ok:
            message = f"语义相似度 {sim:.3f} < 阈值 {threshold:.2f}"
        elif not result.satisfied:
            message = "合约一致性未满足: " + "; ".join(result.reasons)
        else:
            message = "合约门控通过"

        return ContractGateResult(
            passed=passed,
            contract_id=contract.contract_id,
            semantic_similarity=sim,
            semantic_ok=semantic_ok,
            contract_reasons=result.reasons,
            contract_ok=result.satisfied,
            message=message,
        )


def _embed(text: str) -> list[float]:
    from .embed import embed_text

    return embed_text(text)


# --------------------------------------------------------------------------- #
# SQLite helpers
# --------------------------------------------------------------------------- #
def _ensure_sqlite(path: str) -> None:
    conn = _sqlite(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gaag_contracts (
            contract_id TEXT PRIMARY KEY,
            asset_type TEXT NOT NULL,
            card_json TEXT NOT NULL,
            scanned_meta_json TEXT,
            embedding_json TEXT,
            provenance TEXT,
            registered_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _sqlite(path: str) -> sqlite3.Connection:
    if path == ":memory:":
        return sqlite3.connect(":memory:")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)