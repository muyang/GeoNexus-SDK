"""Security Gateway v0.1 — OPA 风格策略引擎 + 零信任规则。

实现:
- 基于属性的访问控制 (ABAC): subject/action/resource/context
- 规则引擎: 内存条件匹配 + 可扩展 OPA 集成
- 空间法律规则: 地理区域 × 数据敏感度 × 操作类型
- 默认拒绝原则 (zero-trust)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AccessRequest:
    """访问请求: subject/action/resource/context。"""

    subject: str = "anonymous"
    action: str = "read"  # read | write | execute
    resource: str = ""    # contract_id / node_id
    resource_type: str = "GeoCard"
    region: str = "global"
    sensitivity: str = "public"  # public | restricted | sensitive | secret
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyDecision:
    """策略决策结果。"""

    allowed: bool
    reason: str = ""
    rule_name: str = ""
    warnings: list[str] = field(default_factory=list)


class SecurityGateway:
    """零信任安全网关 — OPA 风格策略引擎。

    策略规则:
    - deny_anonymous_write: 匿名用户不可写
    - deny_cross_region_secret: 跨区域访问敏感数据需审批
    - allow_public_read: 公开数据允许读
    - default_deny: 默认拒绝（zero-trust）
    """

    def __init__(self) -> None:
        self._rules: list[tuple[str, Callable[[AccessRequest], PolicyDecision | None]]] = []
        self._register_defaults()

    def _register_defaults(self) -> None:
        """注册默认安全规则。"""
        # 1. 匿名用户不可写
        self.add_rule("deny_anonymous_write", lambda req: (
            PolicyDecision(False, "匿名用户不可执行写操作", "deny_anonymous_write")
            if req.subject == "anonymous" and req.action in ("write", "execute")
            else None
        ))
        # 2. 跨区域敏感数据拒绝
        self.add_rule("deny_cross_region_secret", lambda req: (
            PolicyDecision(False, "跨区域敏感数据访问被拒绝", "deny_cross_region_secret")
            if req.sensitivity == "secret" and req.region not in ("admin", req.metadata.get("user_region", ""))
            else None
        ))
        # 3. 公开数据允许读
        self.add_rule("allow_public_read", lambda req: (
            PolicyDecision(True, "公开数据读允许", "allow_public_read")
            if req.action == "read" and req.sensitivity == "public"
            else None
        ))
        # 4. 内部区域允许操作
        self.add_rule("allow_internal_region", lambda req: (
            PolicyDecision(True, "内部区域操作允许", "allow_internal_region")
            if req.metadata.get("user_region") == req.region == "admin"
            else None
        ))

    def add_rule(
        self,
        name: str,
        rule: Callable[[AccessRequest], PolicyDecision | None],
    ) -> SecurityGateway:
        self._rules.append((name, rule))
        return self

    def evaluate(self, request: AccessRequest) -> PolicyDecision:
        """按顺序评估规则，返回第一个匹配的决策。

        默认: 拒绝（zero-trust 原则）。
        """
        for name, rule in self._rules:
            decision = rule(request)
            if decision is not None:
                logger.debug("Security rule matched: %s → %s", name, decision.allowed)
                return decision
        return PolicyDecision(False, "无匹配策略，默认拒绝 (zero-trust)", "default_deny")

    def is_allowed(self, request: AccessRequest) -> bool:
        return self.evaluate(request).allowed

    def check(
        self,
        action: str,
        resource: str = "",
        subject: str = "anonymous",
        **kwargs,
    ) -> PolicyDecision:
        return self.evaluate(AccessRequest(
            subject=subject, action=action, resource=resource, **kwargs,
        ))

    # ------------------------------------------------------------------ #
    # 空间法规合规核验
    # ------------------------------------------------------------------ #
    def check_legal_compliance(
        self,
        jurisdiction: str,
        *,
        action: str = "read",
        data_category: str = "",
        requester_region: str = "",
        resolution_m: float | None = None,
        is_licensed: bool = False,
    ) -> PolicyDecision:
        """基于 :mod:`geonexus.security.legal_ontology` 做管辖区合规核验。

        与 :meth:`evaluate` 的分工：evaluate 处理通用 ABAC 规则，
        本方法处理具体国家的测绘法规约束。
        """
        from .legal_ontology import check_compliance

        finding = check_compliance(
            jurisdiction,
            action=action,
            data_category=data_category,
            requester_region=requester_region,
            resolution_m=resolution_m,
            is_licensed=is_licensed,
        )
        if finding.violations:
            return PolicyDecision(
                allowed=False,
                reason="; ".join(finding.violations),
                rule_name=f"legal_{finding.jurisdiction}",
                warnings=finding.conditions,
            )
        return PolicyDecision(
            allowed=True,
            reason=f"{finding.jurisdiction} 合规核验通过",
            rule_name=f"legal_{finding.jurisdiction}",
            warnings=finding.conditions,
        )

    def register_legal_rules(self) -> SecurityGateway:
        """将法规本体库导出为策略规则（记录用途，供审计/OPA 导出）。"""
        from .legal_ontology import to_policy_rules

        self._exported_legal_rules = to_policy_rules()
        logger.info("SecurityGateway loaded %d legal policy rules", len(self._exported_legal_rules))
        return self

    @property
    def legal_rule_count(self) -> int:
        return len(getattr(self, "_exported_legal_rules", []))


# 法规本体库再导出（置于文件末尾以避免与 SecurityGateway 的循环导入）
from .legal_ontology import (  # noqa: E402
    JURISDICTIONS,
    REVIEW_STATUS,
    ComplianceFinding,
    JurisdictionRule,
    check_compliance,
    get_jurisdiction,
    list_jurisdictions,
    to_policy_rules,
)
from .legal_ontology import (  # noqa: E402  (底部再导出，避免循环导入)
    coverage_report as legal_coverage_report,
)

__all__ = [
    "SecurityGateway",
    "AccessRequest",
    "PolicyDecision",
    "JurisdictionRule",
    "JURISDICTIONS",
    "ComplianceFinding",
    "check_compliance",
    "get_jurisdiction",
    "list_jurisdictions",
    "legal_coverage_report",
    "to_policy_rules",
    "REVIEW_STATUS",
]