"""Tests for the spatial legal ontology (10 jurisdictions) + SecurityGateway integration.

2026 年度考核：覆盖 ≥10 个国家测绘法规的合规引擎。
"""

from __future__ import annotations

from geonexus.security import (
    JURISDICTIONS,
    REVIEW_STATUS,
    SecurityGateway,
    check_compliance,
    get_jurisdiction,
    legal_coverage_report,
    list_jurisdictions,
    to_policy_rules,
)

# --------------------------------------------------------------------------- #
# 覆盖率
# --------------------------------------------------------------------------- #

class TestCoverage:
    def test_at_least_ten_jurisdictions(self):
        """考核要求 ≥10 国。"""
        report = legal_coverage_report()
        assert report["jurisdictions"] >= 10
        assert report["met"] is True

    def test_expected_countries_present(self):
        expected = {"CHN", "EU", "USA", "BRA", "IND", "ZAF", "KEN", "NGA", "IDN", "VNM"}
        assert expected.issubset(set(JURISDICTIONS))

    def test_every_rule_has_legal_basis(self):
        for code, rule in JURISDICTIONS.items():
            assert rule.legal_basis, f"{code} 缺法规依据"
            assert rule.name, f"{code} 缺名称"

    def test_review_status_flagged(self):
        """本体库必须标注待法务复核。"""
        assert "review" in REVIEW_STATUS

    def test_cross_border_classification(self):
        report = legal_coverage_report()
        by_cb = report["by_cross_border"]
        # 中国与印尼属限制类；美国属开放类
        assert "CHN" in by_cb["conditional"]
        assert "USA" in by_cb["open"]


# --------------------------------------------------------------------------- #
# 查询接口
# --------------------------------------------------------------------------- #

class TestQueries:
    def test_list_jurisdictions_sorted(self):
        codes = list_jurisdictions()
        assert codes == sorted(codes)
        assert len(codes) >= 10

    def test_get_jurisdiction(self):
        rule = get_jurisdiction("CHN")
        assert rule is not None
        assert rule.requires_license is True
        assert rule.sovereignty_required is True

    def test_get_jurisdiction_case_insensitive(self):
        assert get_jurisdiction("chn") is get_jurisdiction("CHN")

    def test_get_unknown_jurisdiction(self):
        assert get_jurisdiction("XXX") is None


# --------------------------------------------------------------------------- #
# 合规核验
# --------------------------------------------------------------------------- #

class TestComplianceCheck:
    def test_china_export_sensitive_data_blocked(self):
        """中国：导出高精度地形数据应被拒绝。"""
        finding = check_compliance(
            "CHN", action="export",
            data_category="高精度地形数据",
        )
        assert not finding.compliant
        assert finding.violations

    def test_china_resolution_ceiling(self):
        """中国：请求精度优于 10m 上限应被拒绝。"""
        finding = check_compliance(
            "CHN", action="export", resolution_m=1.0,
        )
        assert not finding.compliant
        assert any("精度" in v for v in finding.violations)

    def test_china_within_resolution_allowed(self):
        """中国：精度在限制内可通过。"""
        finding = check_compliance(
            "CHN", action="read", resolution_m=30.0,
        )
        assert finding.compliant

    def test_china_license_required(self):
        """中国：无资质导出应被拒绝。"""
        finding = check_compliance(
            "CHN", action="export", is_licensed=False,
        )
        assert not finding.compliant
        assert any("资质" in v for v in finding.violations)

    def test_china_licensed_passes(self):
        finding = check_compliance(
            "CHN", action="export", is_licensed=True,
        )
        assert finding.compliant

    def test_usa_open_transfer(self):
        """美国：一般不限制跨境。"""
        finding = check_compliance(
            "USA", action="export", requester_region="EU",
        )
        assert finding.compliant

    def test_indonesia_sovereignty(self):
        """印尼：跨境写入需数据本地化。"""
        finding = check_compliance(
            "IDN", action="write",
            requester_region="USA", is_licensed=True,
        )
        assert finding.compliant
        assert any("本地化" in c for c in finding.conditions)

    def test_kenya_notification(self):
        """肯尼亚：跨境需备案。"""
        finding = check_compliance(
            "KEN", action="read",
            requester_region="USA",
        )
        assert finding.compliant
        assert any("备案" in c for c in finding.conditions)

    def test_unknown_jurisdiction_rejected(self):
        finding = check_compliance("ZZZ", action="read")
        assert not finding.compliant
        assert "未收录管辖区" in finding.violations[0]

    def test_legal_basis_attached(self):
        finding = check_compliance("CHN", action="read")
        assert finding.legal_basis
        assert any("测绘法" in b for b in finding.legal_basis)


# --------------------------------------------------------------------------- #
# SecurityGateway 集成
# --------------------------------------------------------------------------- #

class TestGatewayIntegration:
    def test_check_legal_compliance_pass(self):
        gw = SecurityGateway()
        decision = gw.check_legal_compliance("CHN", action="read", resolution_m=30.0)
        assert decision.allowed
        assert "CHN" in decision.rule_name

    def test_check_legal_compliance_block(self):
        gw = SecurityGateway()
        decision = gw.check_legal_compliance(
            "CHN", action="export", data_category="高精度地形数据",
        )
        assert not decision.allowed
        assert decision.rule_name == "legal_CHN"

    def test_register_legal_rules(self):
        gw = SecurityGateway()
        gw.register_legal_rules()
        assert gw.legal_rule_count > 10

    def test_exported_rules_shape(self):
        rules = to_policy_rules()
        assert len(rules) > 10
        for r in rules:
            assert "name" in r
            assert "jurisdiction" in r
            assert "legal_basis" in r
            assert r["action"] in ("deny", "require_approval", "notify")

    def test_china_rules_exported(self):
        rules = to_policy_rules()
        chn = [r for r in rules if r["jurisdiction"] == "CHN"]
        names = {r["name"] for r in chn}
        assert "CHN_cross_border" in names
        assert "CHN_resolution_ceiling" in names
        assert "CHN_license_required" in names

    def test_existing_abac_rules_still_work(self):
        """回归：原有 ABAC 规则不受影响。"""
        gw = SecurityGateway()
        assert gw.check("read", subject="u", sensitivity="public").allowed
        assert not gw.check("write", subject="anonymous").allowed