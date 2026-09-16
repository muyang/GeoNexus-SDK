"""空间法律规则本体库 v0.1 — 10 国测绘与地理信息法规形式化。

2026 年度考核：覆盖 ≥10 个国家测绘法规的合规引擎。

本模块将各国法规中与地理数据流通相关的**约束条款**形式化为机器可读规则，
供 :class:`~geonexus.security.SecurityGateway` 在协议层做自动合规核验。

    ⚠️ 重要说明
    本本体库为**工程原型**，条款归纳自公开法规文本的高层要点，
    用于打通"法规 → 策略 → 拦截器"的技术链路。
    正式生产部署前必须由法务对每条规则逐项复核（见 REVIEW_STATUS）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: 复核状态：本版本全部为 pending（待法务复核）
REVIEW_STATUS = "pending-legal-review"


# --------------------------------------------------------------------------- #
# 规则模型
# --------------------------------------------------------------------------- #
@dataclass
class JurisdictionRule:
    """单个司法管辖区的空间数据规则。

    Attributes:
        code: ISO 3166-1 alpha-3 或区域码（如 EU）。
        name: 中文/英文名称。
        legal_basis: 主要法规依据。
        cross_border_transfer: 跨境传输限制等级。
        sensitive_categories: 受管制的地理数据类别。
        requires_license: 是否需要测绘资质/许可。
        resolution_ceiling_m: 允许对外提供的最高精度（米），None 表示无限制。
        sovereignty_required: 是否强制数据本地化。
        notes: 补充说明。
    """

    code: str
    name: str
    legal_basis: list[str]
    cross_border_transfer: Literal["prohibited", "conditional", "notification", "open"]
    sensitive_categories: list[str] = field(default_factory=list)
    requires_license: bool = False
    resolution_ceiling_m: float | None = None
    sovereignty_required: bool = False
    notes: str = ""


# --------------------------------------------------------------------------- #
# 10 国规则库
# --------------------------------------------------------------------------- #
JURISDICTIONS: dict[str, JurisdictionRule] = {
    "CHN": JurisdictionRule(
        code="CHN",
        name="中国",
        legal_basis=["《中华人民共和国测绘法》", "《地图管理条例》", "《地理信息安全管理办法》"],
        cross_border_transfer="conditional",
        sensitive_categories=[
            "高精度地形数据", "军事设施周边矢量", "重要地理信息数据",
            "卫星导航定位基准站数据", "大于等于一定精度的DEM",
        ],
        requires_license=True,
        resolution_ceiling_m=10.0,
        sovereignty_required=True,
        notes=(
            "重要地理信息数据须经测绘地理信息主管部门审核；"
            "对外提供需依法履行审批程序；"
            "互联网地图服务须取得相应测绘资质。"
        ),
    ),
    "EU": JurisdictionRule(
        code="EU",
        name="欧洲联盟",
        legal_basis=["INSPIRE Directive 2007/2/EC", "GDPR (EU) 2016/679", "Open Data Directive (EU) 2019/1024"],
        cross_border_transfer="conditional",
        sensitive_categories=["个人位置数据", "关键基础设施位置", "环境敏感区精确坐标"],
        requires_license=False,
        resolution_ceiling_m=None,
        sovereignty_required=False,
        notes=(
            "INSPIRE 推动公共部门空间数据共享与互操作；"
            "个人位置数据受 GDPR 约束，跨境传输需充分性认定或适当保障措施；"
            "高价值数据集应开放。"
        ),
    ),
    "USA": JurisdictionRule(
        code="USA",
        name="美国",
        legal_basis=["Geospatial Data Act of 2018", "OMB Circular A-16", "Executive Order 12906"],
        cross_border_transfer="open",
        sensitive_categories=["关键基础设施细节", "受限军事区域高精度影像"],
        requires_license=False,
        resolution_ceiling_m=None,
        sovereignty_required=False,
        notes=(
            "联邦地理数据以开放共享为原则（FGDC 协调）；"
            "敏感基础设施数据由各部门自定限制；"
            "无统一跨境限制。"
        ),
    ),
    "BRA": JurisdictionRule(
        code="BRA",
        name="巴西",
        legal_basis=["Lei 12.651/2012 (Código Florestal)", "Lei 13.709/2018 (LGPD)", "Decreto 6.666/2008 (SINDE)"],
        cross_border_transfer="conditional",
        sensitive_categories=["亚马逊雨林原始数据", "原住民领地边界", "国防区域数据"],
        requires_license=False,
        resolution_ceiling_m=None,
        sovereignty_required=False,
        notes=(
            "森林法典要求农村环境登记（CAR）数据公开；"
            "原住民领地数据受特别保护；"
            "LGPD 规范个人数据跨境。"
        ),
    ),
    "IND": JurisdictionRule(
        code="IND",
        name="印度",
        legal_basis=["Geospatial Information Regulation Bill", "National Data Sharing and Accessibility Policy"],
        cross_border_transfer="conditional",
        sensitive_categories=["高精度地形", "边境地区数据", "沿海敏感设施"],
        requires_license=True,
        resolution_ceiling_m=None,
        sovereignty_required=True,
        notes=(
            "地理空间数据采集与发布须经授权；"
            "边境与沿海敏感区数据受严格管制；"
            "近年逐步放宽部分公开数据限制。"
        ),
    ),
    "ZAF": JurisdictionRule(
        code="ZAF",
        name="南非",
        legal_basis=["Spatial Data Infrastructure Act 54 of 2003", "POPIA (Act 4 of 2013)"],
        cross_border_transfer="notification",
        sensitive_categories=["个人位置数据", "关键基础设施"],
        requires_license=False,
        resolution_ceiling_m=None,
        sovereignty_required=False,
        notes=(
            "SDI Act 建立国家空间信息基础设施与元数据目录；"
            "POPIA 要求跨境传输个人数据需满足条件并通知监管机构。"
        ),
    ),
    "KEN": JurisdictionRule(
        code="KEN",
        name="肯尼亚",
        legal_basis=["Survey Act (Cap 299)", "Data Protection Act 2019", "Physical and Land Use Planning Act 2019"],
        cross_border_transfer="notification",
        sensitive_categories=["地籍数据", "个人位置数据", "边境区域测绘"],
        requires_license=True,
        resolution_ceiling_m=None,
        sovereignty_required=False,
        notes=(
            "测绘执业须持牌；"
            "地籍数据受 Survey Act 管制；"
            "个人数据跨境需符合 Data Protection Act 要求。"
        ),
    ),
    "NGA": JurisdictionRule(
        code="NGA",
        name="尼日利亚",
        legal_basis=["Survey Co-ordination Act", "Nigeria Data Protection Act 2023", "NOSDRA 相关条例"],
        cross_border_transfer="notification",
        sensitive_categories=["油气设施位置", "边境测绘数据", "个人位置数据"],
        requires_license=True,
        resolution_ceiling_m=None,
        sovereignty_required=False,
        notes=(
            "测绘由测量师委员会与测绘协调法规范；"
            "油气基础设施数据受安全考量限制；"
            "2023 年数据保护法建立跨境传输框架。"
        ),
    ),
    "IDN": JurisdictionRule(
        code="IDN",
        name="印度尼西亚",
        legal_basis=["UU No. 4/2011 Geospatial Information", "UU No. 27/2022 Personal Data Protection", "Perpres 9/2016"],
        cross_border_transfer="conditional",
        sensitive_categories=["高精度地形", "海底地形", "边境岛屿数据", "关键基础设施"],
        requires_license=True,
        resolution_ceiling_m=5.0,
        sovereignty_required=True,
        notes=(
            "地理空间信息法确立 ONE MAP 政策与数据本地化要求；"
            "高精度地形与海底数据受严格管制；"
            "跨境传输需授权。"
        ),
    ),
    "VNM": JurisdictionRule(
        code="VNM",
        name="越南",
        legal_basis=["Luật Đo đạc và bản đồ 2018", "Nghị định 27/2019/NĐ-CP", "Decree 13/2023 on Personal Data"],
        cross_border_transfer="conditional",
        sensitive_categories=["国家边界数据", "高精度地形", "军事区域", "重要工程设施"],
        requires_license=True,
        resolution_ceiling_m=None,
        sovereignty_required=True,
        notes=(
            "测绘与地图法规范测绘活动与地图出版；"
            "国家边界与重要地理信息数据对外提供须审批；"
            "个人数据跨境需影响评估与备案。"
        ),
    ),
}


# --------------------------------------------------------------------------- #
# 查询与评估接口
# --------------------------------------------------------------------------- #
def list_jurisdictions() -> list[str]:
    """返回已收录的管辖区代码。"""
    return sorted(JURISDICTIONS)


def get_jurisdiction(code: str) -> JurisdictionRule | None:
    return JURISDICTIONS.get(code.upper())


def coverage_report() -> dict[str, Any]:
    """返回本体库覆盖统计（用于考核指标报告）。"""
    return {
        "jurisdictions": len(JURISDICTIONS),
        "target": 10,
        "met": len(JURISDICTIONS) >= 10,
        "review_status": REVIEW_STATUS,
        "codes": list_jurisdictions(),
        "by_cross_border": {
            level: [c for c, r in JURISDICTIONS.items() if r.cross_border_transfer == level]
            for level in ("prohibited", "conditional", "notification", "open")
        },
    }


@dataclass
class ComplianceFinding:
    """一次合规核验的结论。"""

    compliant: bool
    jurisdiction: str
    violations: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    legal_basis: list[str] = field(default_factory=list)


def check_compliance(
    jurisdiction: str,
    *,
    action: str = "read",
    data_category: str = "",
    requester_region: str = "",
    resolution_m: float | None = None,
    is_licensed: bool = False,
) -> ComplianceFinding:
    """对一次数据访问做管辖区合规核验。

    Args:
        jurisdiction: 数据所属管辖区代码。
        action: read | write | export。
        data_category: 数据类别（与 sensitive_categories 比对）。
        requester_region: 请求方所在区域（判断是否跨境）。
        resolution_m: 请求的数据精度（米）。
        is_licensed: 请求方是否具备相应测绘资质。

    Returns:
        ComplianceFinding，含违反项与附加条件。
    """
    rule = JURISDICTIONS.get(jurisdiction.upper())
    if rule is None:
        return ComplianceFinding(
            compliant=False,
            jurisdiction=jurisdiction,
            violations=[f"未收录管辖区: {jurisdiction}"],
        )

    violations: list[str] = []
    conditions: list[str] = []
    cross_border = bool(requester_region) and requester_region.upper() != jurisdiction.upper()

    # 1. 敏感类别导出
    is_sensitive = data_category and any(
        cat in data_category for cat in rule.sensitive_categories
    )
    if is_sensitive and action in ("export", "write"):
        if rule.cross_border_transfer in ("prohibited", "conditional"):
            violations.append(
                f"「{data_category}」属受管制类别，禁止/限制导出（依据：{rule.legal_basis[0]}）"
            )
        else:
            conditions.append(f"「{data_category}」导出需备案")

    # 2. 跨境传输
    if cross_border:
        if rule.cross_border_transfer == "prohibited":
            violations.append(f"{rule.name}禁止数据跨境传输")
        elif rule.cross_border_transfer == "conditional":
            conditions.append(f"跨境传输需事先审批（{rule.name}）")
        elif rule.cross_border_transfer == "notification":
            conditions.append(f"跨境传输需向监管机构备案（{rule.name}）")

    # 3. 测绘资质
    if rule.requires_license and action in ("write", "export") and not is_licensed:
        violations.append(f"{rule.name}要求测绘资质，请求方未提供")

    # 4. 精度上限
    if (
        rule.resolution_ceiling_m is not None
        and resolution_m is not None
        and resolution_m < rule.resolution_ceiling_m
    ):
        violations.append(
            f"请求精度 {resolution_m}m 优于 {rule.name} 对外上限 {rule.resolution_ceiling_m}m"
        )

    # 5. 数据本地化
    if rule.sovereignty_required and cross_border and action == "write":
        conditions.append(f"{rule.name}要求数据本地化存储")

    return ComplianceFinding(
        compliant=not violations,
        jurisdiction=jurisdiction.upper(),
        violations=violations,
        conditions=conditions,
        legal_basis=rule.legal_basis,
    )


def to_policy_rules() -> list[dict[str, Any]]:
    """将本体库导出为 OPA 风格策略规则（供 SecurityGateway 加载）。

    每条规则形如：
        {"name": "CHN_export_restriction", "jurisdiction": "CHN",
         "deny_if": {...}, "legal_basis": [...]}
    """
    rules: list[dict[str, Any]] = []
    for code, rule in JURISDICTIONS.items():
        rules.append({
            "name": f"{code}_cross_border",
            "jurisdiction": code,
            "deny_if": {
                "cross_border": True,
                "transfer_policy": rule.cross_border_transfer,
            },
            "action": "deny" if rule.cross_border_transfer == "prohibited" else "require_approval"
            if rule.cross_border_transfer == "conditional" else "notify",
            "legal_basis": rule.legal_basis,
        })
        if rule.resolution_ceiling_m is not None:
            rules.append({
                "name": f"{code}_resolution_ceiling",
                "jurisdiction": code,
                "deny_if": {
                    "resolution_m_lt": rule.resolution_ceiling_m,
                    "action": "export",
                },
                "action": "deny",
                "legal_basis": rule.legal_basis,
            })
        if rule.requires_license:
            rules.append({
                "name": f"{code}_license_required",
                "jurisdiction": code,
                "deny_if": {"is_licensed": False, "action_in": ["write", "export"]},
                "action": "deny",
                "legal_basis": rule.legal_basis,
            })
    return rules