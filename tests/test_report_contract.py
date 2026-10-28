"""批 2 的治理层：GAAG 报告合约 + 可见性继承。

两件事都在回答同一个问题：**一个组合出来的结论，凭什么可以被别人当结论引用？**
报告合约回答"它的来源是否完整且可核验"，可见性继承回答"它是不是泄露了组成里
本不该被看到的东西"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from geonexus.gaag import (
    CONTRACT_SCHEMAS,
    REPORT_ASSET_TYPE,
    REPORT_SOURCE_FIELDS,
    GAAGRegistry,
    ReportContractSource,
    contract_coverage,
    detect_asset_type,
    register_report_contract,
    report_card_from_run,
    report_contract,
    scan_report,
    validate_report,
)
from geonexus.geocard import (
    GeoCardBuilder,
    compose_visibility,
    effective_visibility,
    invisible_components,
    normalize_visibility,
)
from geonexus.recipe import load_recipe, materialize

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sdg_15_3_1" / "recipe.yaml"


@pytest.fixture()
def report_file(tmp_path) -> Path:
    path = tmp_path / "report.html"
    path.write_text(
        "<html><body><h1>SDG 15.3.1 土地退化评估</h1>"
        "<h2>方法</h2><p>口径</p><h2>结果</h2><p>退化 3.2%</p></body></html>",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def sdg_plan():
    return materialize(load_recipe(EXAMPLE), {"year": 2019, "region": "墨西哥"}, run_id="run-42")


def _source(plan, recipe) -> ReportContractSource:
    return ReportContractSource(
        source_run=plan.run_id,
        recipe=recipe.id,
        recipe_version=recipe.version,
        params=plan.params,
        data_snapshot=dict(recipe.data_snapshot),
        deliverables=recipe.deliverable_names(),
    )


# --------------------------------------------------------------------------- #
# 报告产物扫描
# --------------------------------------------------------------------------- #
def test_scan_report_captures_identity_not_quality(report_file) -> None:
    """扫描器只给**可核验**的特征：格式、指纹、标题、章节。不打分。"""
    meta = scan_report(report_file)
    assert meta["format"] == "html"
    assert meta["media_type"] == "text/html"
    assert meta["title"] == "SDG 15.3.1 土地退化评估"
    assert meta["sections"] == ["方法", "结果"]
    assert meta["checksum"].startswith("sha256:")
    assert meta["file_size"] > 0
    assert meta["produced_at"]
    assert "score" not in meta and "quality" not in meta


def test_scan_report_detects_a_changed_conclusion(report_file, tmp_path) -> None:
    """指纹是这份合约的用处之一：报告被改过必须能被发现。"""
    first = scan_report(report_file)["checksum"]
    report_file.write_text("<h1>x</h1><p>改过的结论</p>", encoding="utf-8")
    assert scan_report(report_file)["checksum"] != first


def test_scan_report_tolerates_a_missing_file(tmp_path) -> None:
    meta = scan_report(tmp_path / "nope.html")
    assert meta["exists"] is False
    assert meta["checksum"] is None
    assert meta["file_size"] == 0


def test_report_is_detected_before_plain_text() -> None:
    """``.html`` 是报告；``.md``/``.txt`` 仍是文本。顺序有意义，不能反过来。"""
    assert detect_asset_type("a.html") == "report"
    assert detect_asset_type("a.htm") == "report"
    assert detect_asset_type("a.md") == "text"
    assert detect_asset_type("a.tif") == "raster"
    assert REPORT_ASSET_TYPE in CONTRACT_SCHEMAS
    assert contract_coverage()["total_types"] >= 8


# --------------------------------------------------------------------------- #
# 报告卡片：结论带着来源
# --------------------------------------------------------------------------- #
def test_report_card_carries_its_whole_lineage(sdg_plan, report_file) -> None:
    card = report_card_from_run(
        sdg_plan, report_path=report_file, title="墨西哥土地退化 2019", license="CC-BY-4.0"
    )
    card.validate()
    assert card.type == "knowledge"
    assert card.interface.type == "report"
    assert card.recipe_ref.recipe_id == "recipe://geonexus/sdg-15-3-1@1.0.0"
    assert card.recipe_ref.data_snapshot == {"geokg": "2026.09.1", "lc08": "2024.12"}
    assert card.provenance.source == "run://run-42"
    assert "recipe://geonexus/sdg-15-3-1@1.0.0" in card.provenance.lineage
    assert card.access.endpoint == str(report_file)
    assert card.access.format == "text/html"

    # 组成 = 用了什么：两份数据 + 算子，角色与步骤都能对上
    components = {c.id: c for c in card.composition.components}
    assert components["geocard.packed.menggu"].role == "data"
    assert components["geocard.oge.lc08-l2"].role == "data"
    assert components["2026.9.16 SDG15.3.1"].role == "skill"
    assert components["geocard.packed.menggu"].step == "s1"
    # 同一个资产不会因为"既被 requires 声明又被某步使用"出现两次
    assert len(card.composition.components) == len(components)

    # 参数契约照抄来源方案，但取值是**这一次**的
    params = {p.name: p for p in card.params.params}
    assert params["year"].default == 2019
    assert params["region"].default == "墨西哥"
    assert params["baseline"].scope == "fixed"


def test_report_card_needs_a_recipe_source(sdg_plan) -> None:
    """没有方案来源的"报告"无法被追溯，直接拒绝，而不是生成一份残缺合约。"""

    class _NoRecipe:
        run_id = "r"
        params = {}
        outputs = []

    with pytest.raises(TypeError, match="无法被追溯"):
        report_card_from_run(_NoRecipe())


def test_report_card_picks_the_knowledge_deliverable(sdg_plan, report_file) -> None:
    """默认交付物是 ``role: knowledge`` 的那一项，不是"第一项"。"""
    card = report_card_from_run(sdg_plan, report_path=report_file)
    assert card.name.startswith("SDG 15.3.1")
    # 显式指定别的交付物也允许
    other = report_card_from_run(sdg_plan, deliverable="degradation_map", report_path=report_file)
    assert other.id != card.id, "同一次运行的不同交付物必须各有各的 id"
    assert other.id.endswith("degradation_map.run-42")
    with pytest.raises(ValueError, match="no deliverable named"):
        report_card_from_run(sdg_plan, deliverable="nope")


# --------------------------------------------------------------------------- #
# 报告合约：先校验后登记
# --------------------------------------------------------------------------- #
def test_report_contract_satisfied_with_a_complete_source(sdg_plan, report_file) -> None:
    recipe = load_recipe(EXAMPLE)
    card = report_card_from_run(sdg_plan, report_path=report_file, title="完整来源")
    contract = report_contract(card, source=_source(sdg_plan, recipe), report_path=report_file)
    result = validate_report(contract)
    assert result.satisfied, result.reasons
    assert contract.asset_type == REPORT_ASSET_TYPE
    for field in REPORT_SOURCE_FIELDS:
        assert contract.scanned_meta.get(field) not in (None, "", [], {})
    assert contract.scanned_meta["recipe_version"] == "1.0.0"
    assert contract.scanned_meta["deliverables"] == [
        "degradation_map",
        "transition_table",
        "report",
    ]


def test_report_contract_refuses_a_conclusion_without_a_source(sdg_plan, report_file) -> None:
    """来源不全的报告不能入册：否则之后每个人都会把它当"已验证的结论"引用。"""
    card = report_card_from_run(sdg_plan, title="缺来源")
    # 抹掉卡片里的来源，模拟一份"只有结论没有过程"的报告
    stripped = card.to_dict()
    stripped.pop("recipe_ref", None)
    from geonexus.geocard import GeoCard

    bare = GeoCard.model_validate(stripped)
    contract = report_contract(bare, report_path=report_file)
    result = validate_report(contract)
    assert not result.satisfied
    assert "source_run" in result.missing_fields
    assert any("无法被复核" in reason for reason in result.reasons)

    registry = GAAGRegistry()
    contract, result = register_report_contract(registry, bare, report_path=report_file)
    assert not result.satisfied
    assert registry.list() == [], "校验没过就不该写进注册中心"


def test_report_contract_reads_the_source_from_the_card_when_not_given(
    sdg_plan, report_file
) -> None:
    """来源也能从卡片自身推出（recipe_ref + params），不必强制调用方重复一遍。"""
    card = report_card_from_run(sdg_plan, report_path=report_file)
    contract = report_contract(
        card, source=_source(sdg_plan, load_recipe(EXAMPLE)), report_path=report_file
    )
    assert contract.scanned_meta["recipe"] == "recipe://geonexus/sdg-15-3-1@1.0.0"
    assert contract.scanned_meta["data_snapshot"] == {"geokg": "2026.09.1", "lc08": "2024.12"}
    assert contract.scanned_meta["params"]["year"] == 2019


def test_validating_a_non_report_contract_says_so() -> None:
    card = GeoCardBuilder(id="x", type="data", name="X", description="d").build()
    contract = report_contract(card)
    contract.asset_type = "raster"
    result = validate_report(contract)
    assert not result.satisfied
    assert any("不是报告合约" in reason for reason in result.reasons)
    # dict 形态也要能用（平台从 JSON 传过来时就是这个形状）
    result = validate_report({"asset_type": "report", "scanned_meta": {"format": "html"}})
    assert not result.satisfied


def test_registered_report_contract_is_retrievable(sdg_plan, report_file) -> None:
    recipe = load_recipe(EXAMPLE)
    card = report_card_from_run(sdg_plan, report_path=report_file, title="可检索报告")
    registry = GAAGRegistry()
    contract, result = register_report_contract(
        registry, card, source=_source(sdg_plan, recipe), report_path=report_file
    )
    assert result.satisfied
    assert contract.contract_id in {c.contract_id for c in registry.list()}
    assert registry.get(contract.contract_id).asset_type == REPORT_ASSET_TYPE


# --------------------------------------------------------------------------- #
# 可见性继承
# --------------------------------------------------------------------------- #
def _card(card_id: str, sensitivity: str | None, *, type_: str = "data"):
    builder = GeoCardBuilder(id=card_id, type=type_, name=card_id, description="d")
    if sensitivity is not None:
        builder = builder.compliance(sensitivity=sensitivity)
    return builder.build()


def test_visibility_vocabulary_degrades_to_restricted() -> None:
    """不认识的取值一律降级为受限 —— 缺省放开是访问控制里最常见的事故。"""
    assert normalize_visibility(None) == "restricted"
    assert normalize_visibility("") == "restricted"
    assert normalize_visibility("internal") == "restricted"
    assert normalize_visibility("TOP-SECRET") == "secret"
    assert normalize_visibility("whatever") == "restricted"
    assert compose_visibility(["public", "restricted", "sensitive"]) == "sensitive"
    assert compose_visibility([]) == "restricted"


def test_composite_visibility_is_the_strictest_component() -> None:
    case = (
        GeoCardBuilder(id="case", type="workflow", name="C", description="d")
        .compliance(sensitivity="public")
        .component("data-a", role="data")
        .component("data-b", role="data")
        .component("skill-c", role="skill")
        .build()
    )
    library = {
        "case": case,
        "data-a": _card("data-a", "public"),
        "data-b": _card("data-b", "sensitive"),
        "skill-c": _card("skill-c", "public", type_="skill"),
    }
    report = effective_visibility(case, library.get)
    assert report.visibility == "sensitive"
    assert report.visible is True
    assert report.levels["data-b"] == "sensitive"
    # 案例自己标 public 也没用：min 由最严的那一项决定
    assert report.levels["case"] == "public"


def test_a_missing_or_invisible_component_hides_the_whole_case() -> None:
    """引用一个"看不到"的资产 == 引用一个"不存在"的资产，对外必须同一个回答。"""
    case = (
        GeoCardBuilder(id="case", type="workflow", name="C", description="d")
        .component("secret-data")
        .component("known-data")
        .build()
    )
    report = effective_visibility(case, {"known-data": _card("known-data", "public")}.get)
    assert report.visible is False
    assert report.missing == ["secret-data"]
    assert report.visibility == "secret", "确认不了的组成按最严处理"
    assert invisible_components(case, {"known-data": _card("known-data", "public")}.get) == [
        "secret-data"
    ]


def test_optional_components_do_not_gate_visibility() -> None:
    """作者显式声明 ``required: false`` 的组成项缺席，不影响可运行性，也不影响可见性。"""
    case = (
        GeoCardBuilder(id="case", type="workflow", name="C", description="d")
        .compliance(sensitivity="public")
        .component("core", role="data")
        .component("nice-to-have", role="data", required=False)
        .build()
    )
    library = {"core": _card("core", "public")}
    assert effective_visibility(case, library.get).visibility == "public"
    # 但显式要求把可选项也算进来时，它确实参与判定
    assert effective_visibility(case, library.get, include_optional=True).visible is False


def test_visibility_grant_must_cover_the_strictest_component() -> None:
    """ "够得着其中一项"不算够；要看得见就得够得着最严的那一项。"""
    case = (
        GeoCardBuilder(id="case", type="workflow", name="C", description="d")
        .component("a")
        .component("b")
        .build()
    )
    library = {"a": _card("a", "public"), "b": _card("b", "secret")}
    report = effective_visibility(case, library.get)
    assert report.is_visible_to(["public"]) is False
    assert report.is_visible_to(["restricted"]) is False
    assert report.is_visible_to(["secret"]) is True
    assert report.is_visible_to(["public", "secret"]) is True
    payload = report.to_dict()
    assert payload["visible"] is True and payload["visibility"] == "secret"


def test_plain_card_visibility_needs_no_lookup() -> None:
    """非组合卡片：只算自己，不需要解析任何东西。"""
    card = _card("solo", "sensitive")
    report = effective_visibility(card)
    assert report.visibility == "sensitive"
    assert report.visible is True
    assert report.levels == {"solo": "sensitive"}

    undecorated = GeoCardBuilder(id="plain", type="data", name="P", description="d").build()
    assert effective_visibility(undecorated).visibility == "restricted"


def test_visibility_report_is_json_serialisable() -> None:
    card = _card("solo", "public")
    payload = json.loads(json.dumps(effective_visibility(card).to_dict()))
    assert payload["visibility"] == "public"


def test_only_content_bearing_roles_gate_visibility() -> None:
    """算子与算力不参与可见性判定 —— 规则过严等于没有规则。

    算子常常只是一个名字（甚至不在目录里）。如果"某一步用了某个算子"就能把整案
    变成不可见，那每个案例都会不可见；而知道"某地用了某个算子"本身并不泄露内容。
    反过来，数据/模型/知识/方案必须参与：知道"某地存在某份数据"本身就可能敏感。
    """
    case = (
        GeoCardBuilder(id="case", type="workflow", name="C", description="d")
        .compliance(sensitivity="public")
        .component("data-a", role="data")
        .component("an-operator-name-with-spaces", role="skill")
        .component("gpu-cluster", role="compute")
        .build()
    )
    library = {"data-a": _card("data-a", "public")}
    report = effective_visibility(case, library.get)
    assert report.visible is True, "算子/算力不在目录里不该让整案不可见"
    assert report.visibility == "public"
    assert "an-operator-name-with-spaces" not in report.levels

    # 数据缺席仍然整案不可见
    data_case = (
        GeoCardBuilder(id="case2", type="workflow", name="C2", description="d")
        .component("missing-data", role="data")
        .component("op", role="skill")
        .build()
    )
    missing = effective_visibility(data_case, {}.get)
    assert missing.visible is False
    assert missing.missing == ["missing-data"]

    # 审计口径：要求所有角色都参与时，算子也能把案子挡住
    strict = effective_visibility(case, library.get, gating_only=False)
    assert strict.visible is False
    assert "an-operator-name-with-spaces" in strict.missing
