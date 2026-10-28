"""RFI 优先湿地案例：契约、五步链路与可见性分层的测试。

这个案例的价值在于它按**论文真正的方法**跑（框架比较 → 数据汇集 → PC1 取舍 →
刻画与 χ² → 报告），数据是论文公开的站点级结果。测试不 mock 算子：直接跑，
断言"结论与原文公开数字一致"，以及契约层面的规则（参数镜像、fixed 参数、可见性继承）。

这里同时锁住两件容易被"顺手改好"的事：
  · 原文两份公开材料之间的不一致（补充材料粗体 117 处 vs 正文 Table 4 的 108 处；
    2 处我国站点低于阈值却仍在名单内）必须**照原样报出来**，不许悄悄对齐；
  · 受限的逐笔计数数据必须仍然让整案对访客不可见（可见性继承）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from geonexus.geocard import load_geocard
from geonexus.recipe import (
    RecipeParamError,
    load_recipe,
    materialize,
)

CASE_DIR = Path(__file__).resolve().parent.parent / "examples" / "flightway_wetland"
CARD = CASE_DIR / "case.card.yaml"
RECIPE = CASE_DIR / "recipe.yaml"
DATA = CASE_DIR / "data" / "rfi"
EXTENSION_DIR = CASE_DIR / "extensions" / "habitat_change"


def _load_module(name: str, directory: Path = CASE_DIR):
    """导入案例目录里的脚本（它们不是包，只是可执行示例）。"""
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    path = directory / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"flightway_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rfi_skills():
    return _load_module("rfi_skills")


@pytest.fixture(scope="module")
def executed_case(tmp_path_factory, rfi_skills) -> dict:
    """按方案真跑一遍，返回运行清单（与 `run_case.py --json` 同一形状）。"""
    import shutil

    run_case = _load_module("run_case")
    workdir = tmp_path_factory.mktemp("rfi-run")
    shutil.copytree(DATA, workdir / "data" / "rfi")

    card = load_geocard(CARD)
    recipe = load_recipe(RECIPE)
    params = {
        "data_dir": "data/rfi",
        "threshold_prc_mongolia": 10.0,
        "threshold_others": 1.0,
        "include_expert_exceptions": True,
        "include_published_sites": True,
        "one_percent_rule": True,
        "counts": "",
        "report_format": "html",
    }
    plan = materialize(recipe, params, run_id="test-run")
    execution = run_case.run_plan(plan, workdir)
    deliverables = run_case.deliverables_of(plan, execution["results"], workdir)
    return {
        "workdir": workdir,
        "plan": plan,
        "execution": execution,
        "deliverables": deliverables,
        "card": card,
        "recipe": recipe,
        "run_case": run_case,
    }


# --------------------------------------------------------------------------- #
# 契约
# --------------------------------------------------------------------------- #
def test_card_and_recipe_validate() -> None:
    card = load_geocard(CARD)
    card.validate()
    assert card.type == "workflow"
    assert card.recipe_ref.recipe_id == "recipe://geonexus/eaaf-wetland-priority@2.0.0"
    assert card.recipe_ref.version == "2.0.0"
    assert card.recipe_ref.data_snapshot  # 没有数据快照就无法区分"重跑"与"数据变了"
    # 出处是论文本身（DOI），不再是科普文章 —— 案例要能指回一手来源
    assert card.provenance.source == "https://doi.org/10.1038/s41598-025-31727-2"
    recipe = load_recipe(RECIPE)
    recipe.validate()
    assert recipe.entry() == "s1"
    assert recipe.deliverable_names() == [
        "priority_sites",
        "priority_selection",
        "country_characteristics",
        "report",
    ]


def test_card_params_mirror_the_recipe_params() -> None:
    """卡片与方案必须是**同一份**参数定义，不是两套说法。"""
    card = load_geocard(CARD)
    recipe = load_recipe(RECIPE)
    card_params = {p.name: p for p in card.params.params}
    recipe_params = {p.name: p for p in recipe.params}
    assert set(card_params) == set(recipe_params)
    for name, spec in recipe_params.items():
        mirror = card_params[name]
        assert mirror.type == spec.type, name
        assert mirror.scope == spec.scope, name
        assert mirror.default == spec.default, name
        assert mirror.minimum == spec.minimum and mirror.maximum == spec.maximum, name
        assert list(mirror.enum) == list(spec.enum), name


def test_the_one_percent_rule_is_a_definition_not_a_knob() -> None:
    """1% 规则是定义：`scope: fixed`；改它必须被拒。"""
    recipe = load_recipe(RECIPE)
    fixed = {p.name: p for p in recipe.params}["one_percent_rule"]
    assert fixed.scope == "fixed"
    with pytest.raises(RecipeParamError):
        materialize(recipe, {"one_percent_rule": False})


def test_the_regional_calibration_is_a_knob() -> None:
    """区域校准阈值（中国/蒙古 10、其余八国 1）是作者的方法学选择，不是定义 —— 必须可改。"""
    recipe = load_recipe(RECIPE)
    params = {p.name: p for p in recipe.params}
    assert params["threshold_prc_mongolia"].scope != "fixed"
    assert params["threshold_prc_mongolia"].default == 10.0
    assert params["threshold_others"].default == 1.0
    plan = materialize(recipe, {"threshold_prc_mongolia": 1.0})
    assert plan.run_id  # 能物化就说明参数没被 fixed 挡住


# --------------------------------------------------------------------------- #
# 数据：与原文公开数字逐项对照
# --------------------------------------------------------------------------- #
def test_site_dataset_matches_the_published_tables() -> None:
    """147 处 · 10 国 · 沿海 91 / 内陆 56 —— 与论文 Table 4 完全一致。"""
    import csv

    with open(DATA / "sites.csv", encoding="utf-8") as handle:
        sites = list(csv.DictReader(handle))
    with open(DATA / "country_summary.csv", encoding="utf-8") as handle:
        countries = list(csv.DictReader(handle))

    assert len(sites) == 147
    assert sum(1 for s in sites if s["wetland_type"] == "coastal") == 91
    assert sum(1 for s in sites if s["wetland_type"] == "inland") == 56
    expected = {
        "Bangladesh": 8,
        "Cambodia": 9,
        "PRC": 60,
        "Indonesia": 17,
        "Lao PDR": 3,
        "Malaysia": 6,
        "Mongolia": 11,
        "Philippines": 12,
        "Thailand": 12,
        "Viet Nam": 9,
    }
    for row in countries:
        got = sum(1 for s in sites if s["country"] == row["country"])
        assert got == int(row["priority_sites"]) == expected[row["country"]], row["country"]
    # 每处站点都有公开的中心坐标（否则地图上画不出来）
    assert all(s["lat"] and s["lon"] for s in sites)


def test_the_papers_two_published_protection_numbers_are_both_kept() -> None:
    """补充材料粗体 117 处 vs 正文 Table 4 的 108 处：不许悄悄对齐成同一个数。"""
    import csv

    with open(DATA / "sites.csv", encoding="utf-8") as handle:
        sites = list(csv.DictReader(handle))
    with open(DATA / "country_summary.csv", encoding="utf-8") as handle:
        countries = list(csv.DictReader(handle))
    bold = sum(1 for s in sites if s["overlaps_protected_bold"] == "True")
    table4 = sum(int(c["protected_or_partial"]) for c in countries)
    assert bold == 117 and table4 == 108 and bold != table4
    provenance = (DATA / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "117" in provenance and "108" in provenance  # 差异必须写在数据说明里


# --------------------------------------------------------------------------- #
# 五步链路
# --------------------------------------------------------------------------- #
def test_every_step_succeeds(executed_case) -> None:
    runs = executed_case["execution"]["runs"]
    assert [r["step"] for r in runs] == ["s1", "s2", "s3", "s4", "s5"]
    assert all(r["status"] == "ok" for r in runs), runs
    assert [r["skill"] for r in runs] == [
        "rfi-framework-review",
        "rfi-site-data",
        "rfi-pc1-select",
        "rfi-characterise",
        "rfi-report",
    ]


def test_framework_review_counts_match_the_paper(executed_case) -> None:
    """三套框架在 10 国的站点数：Ramsar 148、EAAFP 网络 49、湿地 IBA/KBA 465（原文 Table 1）。"""
    totals = executed_case["execution"]["results"]["s1"]["totals"]
    assert totals == {"ramsar": 148, "flyway_network": 49, "wetland_iba_kba": 465}


def test_site_data_step_validates_against_the_paper(executed_case) -> None:
    s2 = executed_case["execution"]["results"]["s2"]
    assert s2["validated"] is True and not s2["problems"]
    funnel = s2["funnel"]
    assert funnel["priority_sites"] == 147
    assert funnel["candidate_sites"] == 467 and funnel["assessed_sites_min"] == 270
    assert funnel["coastal"] == 91 and funnel["inland"] == 56
    assert funnel["species_rows"] > 900  # 963 条"某站点达到 1% 的物种"记录


def test_pc1_selection_reproduces_the_published_list(executed_case) -> None:
    """按公开分值严格套用区域校准阈值得到 145 处；补上原文已列入的 2 处得 147 处。"""
    s3 = executed_case["execution"]["results"]["s3"]
    assert s3["selected_count"] == 147
    assert s3["rule_only_selected_count"] == 145
    discrepancies = s3["threshold_discrepancies"]
    assert {d["site"] for d in discrepancies} == {"Danzhou-Lingao coast", "Fangcheng coast"}
    assert all(d["pc1"] < d["threshold"] for d in discrepancies)


def test_expert_exceptions_and_calibration_change_the_result(tmp_path) -> None:
    """关掉专家例外、或把中国与蒙古的阈值降到 1，结果必须随之变化（阈值是真在用的）。"""
    import shutil

    rfi_skills = _load_module("rfi_skills")

    class _Ctx:
        def __init__(self, workdir):
            self.workdir = workdir

    def run(**overrides):
        workdir = tmp_path / f"run-{len(list(tmp_path.iterdir()))}"
        shutil.copytree(DATA, workdir / "data" / "rfi")
        params = {
            "data_dir": "data/rfi",
            "threshold_prc_mongolia": 10.0,
            "threshold_others": 1.0,
            "include_expert_exceptions": True,
            "include_published_sites": True,
        }
        params.update(overrides)
        out = rfi_skills.pc1_select_handler(params, _Ctx(workdir))
        return json.loads(Path(out["selection_json"]).read_text(encoding="utf-8"))

    strict = run(include_published_sites=False)
    assert strict["selected_count"] == 145  # 纯规则：2 处我国站点落选
    relaxed = run(threshold_prc_mongolia=1.0, include_published_sites=False)
    assert relaxed["selected_count"] > 145  # 取消区域校准会纳入更多北方站点
    no_exceptions = run(include_expert_exceptions=False)
    assert len(no_exceptions["exceptions_used"]) == 0


def test_characterisation_reproduces_the_paper_chi_squares(executed_case) -> None:
    """复算原文的三个 χ²：8.33（沿海/内陆）、32.39（保护状态）、72.53（HDI 三档）。"""
    payload = json.loads(
        Path(executed_case["execution"]["results"]["s4"]["characterisation_json"]).read_text(
            encoding="utf-8"
        )
    )
    stats = payload["statistics"]
    assert stats["coastal_vs_inland"]["chi2"] == 8.33
    assert stats["protection_status"]["chi2"] == 32.39
    assert stats["hdi_tiers"]["chi2"] == 72.53
    assert stats["coastal_vs_inland"]["df"] == 1
    assert stats["hdi_tiers"]["df"] == 2
    # 观测值与原文一致
    assert stats["coastal_vs_inland"]["observed"] == [91, 56]
    assert stats["protection_status"]["observed"] == [108, 39]
    assert payload["hdi_counts"] == {"Very high": 18, "High": 97, "Medium": 32}


def test_deliverables_are_the_declared_conclusions(executed_case) -> None:
    deliverables = executed_case["deliverables"]
    assert [d["name"] for d in deliverables] == [
        "priority_sites",
        "priority_selection",
        "country_characteristics",
        "report",
    ]
    for item in deliverables:
        assert item["exists"], item
        assert item["size_bytes"] and item["size_bytes"] > 0
        assert item["checksum"] and item["checksum"].startswith("sha256:")
    roles = {d["name"]: d["role"] for d in deliverables}
    assert roles["report"] == "knowledge"
    assert set(roles.values()) == {"data", "knowledge"}


def test_report_carries_method_limits_and_data_availability(executed_case) -> None:
    path = next(d["path"] for d in executed_case["deliverables"] if d["name"] == "report")
    html = Path(path).read_text(encoding="utf-8")
    for needle in [
        "10.1038/s41598-025-31727-2",  # 出处
        "方法（原文五步）",  # 方法
        "数据可用性",  # 数据边界
        "没有数据",  # 原文承认的局限
        "栖息地质量",  # PC1 不含什么
        "117",
        "108",
    ]:  # 两个保护状态数字都在
        assert needle in html, needle
    # 报告必须明说 PC1 不是本报告重算的
    assert "不重算" in html or "不复算" in html


# --------------------------------------------------------------------------- #
# 可见性分层
# --------------------------------------------------------------------------- #
def test_visibility_inherits_from_the_restricted_counts(executed_case) -> None:
    """逐笔计数数据受限 ⇒ 拿不到它的人，整案不可见（平台应返回 404，不是 403）。"""
    card = executed_case["card"]
    report = executed_case["run_case"].visibility_report(card, restricted_hidden=True)
    assert report["visible"] is False
    assert "geocard.eaaf.waterbird-counts-restricted" in report["missing"]
    author = executed_case["run_case"].visibility_report(card, restricted_hidden=False)
    assert author["visible"] is True
    assert author["visibility"] == "restricted"


def test_the_restricted_component_is_required() -> None:
    card = load_geocard(CARD)
    restricted = [
        c for c in card.composition.components if c.id == "geocard.eaaf.waterbird-counts-restricted"
    ]
    assert restricted and restricted[0].required is True
    # 公开的站点表不应被误标成受限：论文的补充材料本来就把经度纬度公开了
    public = [
        c
        for c in card.composition.components
        if c.id in ("geocard.eaaf.rfi-priority-sites", "geocard.eaaf.rfi-country-summary")
    ]
    assert len(public) == 2


# --------------------------------------------------------------------------- #
# 扩展：原文建议的后续方向（NDWI 栖息地变化）单独存放、仍然自证为合成数据
# --------------------------------------------------------------------------- #
def test_the_habitat_change_extension_is_kept_but_marked_as_not_the_paper_method() -> None:
    recipe = load_recipe(EXTENSION_DIR / "recipe.yaml")
    card = load_geocard(EXTENSION_DIR / "case.card.yaml")
    recipe.validate()
    card.validate()
    assert recipe.id == "recipe://geonexus/eaaf-habitat-change@1.0.0"
    readme = (EXTENSION_DIR / "README.md").read_text(encoding="utf-8")
    assert "不是" in readme and "论文" in readme  # 必须写明它不是论文方法
    # 案例主体的组成项里不能再出现合成影像/示意计数
    main_card = load_geocard(CARD)
    ids = {c.id for c in main_card.composition.components}
    assert "geocard.eaaf.wetland-imagery-sample" not in ids
    assert "geocard.eaaf.waterbird-counts-sample" not in ids


def test_extracted_dataset_declares_its_provenance_and_licence() -> None:
    text = (DATA / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "10.1038/s41598-025-31727-2" in text
    assert "CC-BY" in text
    assert "Supplementary Table 3" in text
    assert "wetland_type" in text  # 推断字段要说明
