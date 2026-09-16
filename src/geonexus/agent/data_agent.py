"""Data Agent — 就近数据发现 + 合约绑定 + 计算下推决策。

Data Agent 职责（对应设计文档"Data Agent原型"）：
1. 接收自然语言意图 → 语义搜索 GAAG 注册中心
2. 合约绑定：通过 ContractGate 双重验证（语义 + 合约一致性）
3. 计算下推决策：判断"本地计算" vs "数据迁移"（基于数据位置与网络成本）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..gaag import ContractGate, GAAGContract, GAAGRegistry

logger = logging.getLogger(__name__)


@dataclass
class DataAgentDecision:
    """Data Agent 对一次意图的完整决策结果。"""

    intent: str
    contracts: list[GAAGContract] = field(default_factory=list)
    bound_contract: GAAGContract | None = None
    gate_passed: bool = False
    compute_decision: str = "none"  # local | migrate | none
    explanation: str = ""
    match_scores: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "bound_contract": self.bound_contract.contract_id if self.bound_contract else None,
            "gate_passed": self.gate_passed,
            "compute_decision": self.compute_decision,
            "explanation": self.explanation,
            "matches": [
                {
                    "contract_id": c.contract_id,
                    "asset_type": c.asset_type,
                }
                for c in self.contracts[:5]
            ],
        }


class DataAgent:
    """就近数据发现 + 合约绑定 + 计算下推决策。

    Args:
        registry: GAAG 合约注册中心（语义检索数据源）。
        gate: ContractGate（合约满足度门控）。
        node_id: 本 Data Agent 所在节点（用于本地性判断）。
    """

    def __init__(
        self,
        registry: GAAGRegistry,
        gate: ContractGate | None = None,
        node_id: str = "local",
    ) -> None:
        self.registry = registry
        self.gate = gate or ContractGate()
        self.node_id = node_id

    # ------------------------------------------------------------------ #
    def discover(self, intent: str, k: int = 5) -> list[GAAGContract]:
        """Step 1 — 语义搜索：从注册中心发现候选合约。"""
        results = self.registry.search_semantic(intent, k=k)
        return [contract for contract, _score in results]

    def bind(
        self,
        intent: str,
        *,
        bbox: list[float] | None = None,
        crs: str | None = None,
        required_bands: list[str] | None = None,
    ) -> DataAgentDecision:
        """Step 2 — 合约绑定：对候选合约逐一做合约门控，取第一个通过的。"""
        candidates = self.discover(intent)
        decision = DataAgentDecision(intent=intent, contracts=candidates)

        if not candidates:
            decision.explanation = "注册中心未发现匹配合约"
            return decision

        for contract in candidates:
            result = self.gate.gate(
                contract,
                intent,
                bbox=bbox,
                crs=crs,
                required_bands=required_bands,
            )
            decision.match_scores.append(
                {
                    "contract_id": contract.contract_id,
                    "similarity": round(result.semantic_similarity, 4),
                    "semantic_ok": result.semantic_ok,
                    "contract_ok": result.contract_ok,
                }
            )
            if result.passed:
                decision.bound_contract = contract
                decision.gate_passed = True
                decision.compute_decision = self._compute_decision(contract)
                decision.explanation = (
                    f"绑定合约 {contract.contract_id}（语义 {result.semantic_similarity:.3f}）; "
                    f"计算决策: {decision.compute_decision}"
                )
                break

        if decision.bound_contract is None:
            decision.explanation = "候选合约均未通过合约门控"
        return decision

    def decide_pushdown(self, contract: GAAGContract, data_owner_node: str | None = None) -> str:
        """Step 3 — 计算下推决策: local vs migrate. 简化：数据在哪就在哪算。"""
        return self._compute_decision(contract, data_owner_node)

    def _compute_decision(self, contract: GAAGContract, owner_node: str | None = None) -> str:
        """本地性判断：数据所在节点 == 本节点 → local，否则 migrate。

        扩展点：可引入网络延迟/算力余量/合规策略的多目标优化。
        """
        owner = owner_node or contract.scanned_meta.get("node_id") or "remote"
        # 简化规则：合约 provenance 用 "local:" 前缀表示本地数据
        if owner == self.node_id or contract.provenance.startswith("local:"):
            return "local"
        return "migrate"  # 计算下推（任务到数据端执行），而非数据迁移


# 简洁别名
discover = DataAgent  # type: ignore[assignment]