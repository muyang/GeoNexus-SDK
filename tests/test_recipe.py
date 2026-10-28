"""Recipe 契约测试：装载、参数契约、物化、目录与 fork。

最重要的一个用例是 :func:`test_acceptance_fork_change_params_rerun_report` ——
它把"复杂案例能被复用"这句话变成了可执行的断言：派生 → 改参数 → 重跑 → 拿到
与源案例同构但取值不同的交付物清单。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from geonexus.recipe import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Recipe,
    RecipeConflict,
    RecipeNotFound,
    RecipeParamError,
    RecipeRegistry,
    RecipeValidationError,
    deliverables,
    materialize,
    parse_recipe_address,
    resolve_params,
    save_recipe,
    version_key,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sdg_15_3_1" / "recipe.yaml"


def _recipe_dict() -> dict:
    """最小可跑的 2 数据 + 1 算子 + 1 报告配方。"""
    return {
        "recipe_version": "1.0",
        "id": "recipe://geonexus/sdg-15-3-1@1.0.0",
        "name": "SDG 15.3.1",
        "description": "2 data + 1 skill + 1 report",
        "params": [
            {"name": "year", "type": "integer", "required": True, "minimum": 2015, "maximum": 2024},
            {"name": "region", "type": "enum", "enum": ["蒙古", "墨西哥"], "default": "蒙古"},
            {"name": "baseline", "type": "integer", "scope": "fixed", "default": 2015},
        ],
        "steps": [
            {
                "id": "s1",
                "uses": "geocard.packed.menggu",
                "params": {"region": "${params.region}"},
                "outputs": {"aoi": "矢量边界", "mask": "掩膜"},
            },
            {
                "id": "s2",
                "uses": "2026.9.16 SDG15.3.1",
                "depends_on": ["s1"],
                "params": {"year": "${params.year}", "baseline": "${params.baseline}"},
                "inputs": {"mask": "step://s1/mask"},
                "outputs": {"degradation": "退化栅格", "transition": "转移矩阵"},
                "max_attempts": 1,
                "resources": {"cpu": 2, "memory_mb": 4096},
            },
            {
                "id": "s3",
                "uses": "report.sdg1531.mng.2019",
                "depends_on": ["s2"],
                "inputs": {"degradation": "step://s2/degradation"},
                "outputs": {"report": "报告"},
            },
        ],
        "entrypoint": "s1",
        "outputs": [
            {"name": "degradation_map", "from": "s2.degradation", "role": "data"},
            {"name": "report", "from": "s3.report", "role": "knowledge"},
        ],
    }


# --------------------------------------------------------------------------- #
# 地址与装载
# --------------------------------------------------------------------------- #
def test_address_parsing() -> None:
    assert parse_recipe_address("recipe://geonexus/sdg-15-3-1@1.0.0") == (
        "geonexus",
        "sdg-15-3-1",
        "1.0.0",
    )
    assert parse_recipe_address("recipe://ns/name") == ("ns", "name", None)
    for bad in ("geonexus/sdg", "recipe://", "recipe://ns", "recipe://ns/", "http://x/y"):
        with pytest.raises(RecipeValidationError):
            parse_recipe_address(bad)


def test_example_recipe_loads_and_round_trips(tmp_path) -> None:
    from geonexus.recipe import load_recipe

    recipe = load_recipe(EXAMPLE)
    assert recipe.id == "recipe://geonexus/sdg-15-3-1@1.0.0"
    assert recipe.namespace == "geonexus"
    assert recipe.slug == "sdg-15-3-1"
    assert recipe.version == "1.0.0"
    assert recipe.entry() == "s1"
    assert recipe.reusable_param_names() == ["year", "region", "report_format"]
    assert recipe.fixed_param_names() == ["baseline"]
    assert recipe.uses() == [
        "geocard.packed.menggu",
        "2026.9.16 SDG15.3.1",
        "report.sdg1531.mng.2019",
    ]
    assert recipe.deliverable_names() == ["degradation_map", "transition_table", "report"]

    # YAML 与 JSON 两种落盘都要能原样读回。
    yaml_path = save_recipe(recipe, tmp_path / "recipe.yaml")
    json_path = save_recipe(recipe, tmp_path / "recipe.json")
    assert load_recipe(yaml_path).to_dict() == recipe.to_dict()
    assert load_recipe(json_path).to_dict() == recipe.to_dict()


def test_recipe_version_family_is_enforced() -> None:
    bad = _recipe_dict()
    bad["recipe_version"] = "2.0"
    with pytest.raises(RecipeValidationError, match="not supported"):
        Recipe.from_dict(bad)


def test_graph_validation_rejects_broken_dags() -> None:
    cases = {
        "duplicate step id": lambda d: d["steps"].append(dict(d["steps"][0])),
        "depends on unknown step": lambda d: d["steps"][1]["depends_on"].append("nope"),
        "depends on itself": lambda d: d["steps"][1]["depends_on"].append("s2"),
    }
    for match, mutate in cases.items():
        bad = _recipe_dict()
        mutate(bad)
        with pytest.raises((RecipeValidationError, ValidationError), match=match):
            Recipe.from_dict(bad)


def test_graph_validation_rejects_cycles() -> None:
    bad = _recipe_dict()
    bad["steps"][0]["depends_on"] = ["s3"]
    with pytest.raises((RecipeValidationError, ValidationError), match="cycle"):
        Recipe.from_dict(bad)


def test_deliverable_must_point_at_a_real_output() -> None:
    bad = _recipe_dict()
    bad["outputs"][0]["from"] = "s9.degradation"
    with pytest.raises((RecipeValidationError, ValidationError), match="unknown step"):
        Recipe.from_dict(bad)

    bad2 = _recipe_dict()
    bad2["outputs"][0]["from"] = "s2.nonexistent"
    with pytest.raises((RecipeValidationError, ValidationError), match="does not declare output"):
        Recipe.from_dict(bad2)


def test_upstream_reference_must_exist() -> None:
    bad = _recipe_dict()
    bad["steps"][1]["inputs"] = {"mask": "step://nope/mask"}
    with pytest.raises((RecipeValidationError, ValidationError), match="unknown step"):
        Recipe.from_dict(bad)


def test_composite_step_must_nest_a_recipe() -> None:
    bad = _recipe_dict()
    bad["steps"][0]["kind"] = "composite"
    with pytest.raises((RecipeValidationError, ValidationError), match="requires uses"):
        Recipe.from_dict(bad)

    bad2 = _recipe_dict()
    bad2["steps"][0]["uses"] = "recipe://geonexus/inner@1.0.0"
    with pytest.raises((RecipeValidationError, ValidationError), match="kind must be 'composite'"):
        Recipe.from_dict(bad2)

    ok = _recipe_dict()
    ok["steps"][0]["uses"] = "recipe://geonexus/inner@1.0.0"
    ok["steps"][0]["kind"] = "composite"
    assert Recipe.from_dict(ok).steps[0].kind == "composite"


def test_entrypoint_must_be_a_step() -> None:
    bad = _recipe_dict()
    bad["entrypoint"] = "s9"
    with pytest.raises((RecipeValidationError, ValidationError), match="entrypoint"):
        Recipe.from_dict(bad)


# --------------------------------------------------------------------------- #
# 参数契约
# --------------------------------------------------------------------------- #
def test_params_fill_defaults_and_pin_fixed() -> None:
    recipe = Recipe.from_dict(_recipe_dict())
    resolved = resolve_params(recipe, {"year": 2019})
    assert resolved == {"year": 2019, "region": "蒙古", "baseline": 2015}
    # 重复提交同一个 fixed 取值是允许的：前端回填就会这么做。
    assert resolve_params(recipe, {"year": 2019, "baseline": 2015})["baseline"] == 2015


def test_params_reject_each_violation() -> None:
    recipe = Recipe.from_dict(_recipe_dict())

    with pytest.raises(RecipeParamError) as missing:
        resolve_params(recipe, {})
    assert (missing.value.field, missing.value.code) == ("year", "param_missing")

    with pytest.raises(RecipeParamError) as unknown:
        resolve_params(recipe, {"year": 2019, "crs": "EPSG:4326"})
    assert unknown.value.code == "param_unknown"

    with pytest.raises(RecipeParamError) as low:
        resolve_params(recipe, {"year": 1999})
    assert (low.value.field, low.value.code) == ("year", "param_out_of_range")

    with pytest.raises(RecipeParamError) as high:
        resolve_params(recipe, {"year": 2099})
    assert high.value.code == "param_out_of_range"

    with pytest.raises(RecipeParamError) as enum:
        resolve_params(recipe, {"year": 2019, "region": "秘鲁"})
    assert (enum.value.field, enum.value.code) == ("region", "param_invalid_enum")

    with pytest.raises(RecipeParamError) as badint:
        resolve_params(recipe, {"year": "两千一十九"})
    assert (badint.value.field, badint.value.code) == ("year", "param_invalid_type")

    with pytest.raises(RecipeParamError) as fixed:
        resolve_params(recipe, {"year": 2019, "baseline": 2010})
    assert (fixed.value.field, fixed.value.code) == ("baseline", "param_fixed")

    payload = missing.value.to_dict()
    assert payload == {
        "field": "year",
        "code": "param_missing",
        "message": "required parameter 'year' is missing",
    }


def test_params_coerce_unambiguous_strings() -> None:
    """表单与命令行会把数字送成字符串；无歧义时要收下，有歧义时才拒绝。"""
    recipe = Recipe.from_dict(_recipe_dict())
    assert resolve_params(recipe, {"year": "2019"})["year"] == 2019
    assert isinstance(resolve_params(recipe, {"year": "2019"})["year"], int)


# --------------------------------------------------------------------------- #
# 物化
# --------------------------------------------------------------------------- #
def test_materialize_builds_a_runnable_graph() -> None:
    recipe = Recipe.from_dict(_recipe_dict())
    plan = materialize(recipe, {"year": 2021, "region": "墨西哥"}, run_id="run-42")

    assert plan.graph.topological_order() == ["run-42:s1", "run-42:s2", "run-42:s3"]
    assert plan.params["year"] == 2021

    s2 = plan.graph.task("run-42:s2")
    assert s2.depends_on == ["run-42:s1"]
    assert s2.spec["params"] == {"year": 2021, "baseline": 2015}
    assert s2.spec["uses"] == "2026.9.16 SDG15.3.1"
    assert s2.inputs["mask"].kind == "task_output"
    assert (s2.inputs["mask"].from_task, s2.inputs["mask"].field) == ("s1", "mask")
    # 预约单位是 MB，不再做 GiB 换算。
    assert s2.resources.memory_mb == 4096
    assert s2.resources.cpu == 2.0
    # max_attempts 是额外次数，RetryPolicy 数的是总次数。
    assert s2.retry.max_attempts == 2
    assert plan.graph.task("run-42:s3").retry.max_attempts == 1

    assert s2.provenance["origin"] == "recipe"
    assert s2.provenance["source"] == recipe.id
    assert s2.provenance["params"]["year"] == 2021
    assert s2.labels["recipe_step"] == "s2"

    assert plan.output_refs() == {
        "degradation_map": "step://s2/degradation",
        "report": "step://s3/report",
    }


def test_materialize_substitutes_whole_values_and_fragments() -> None:
    recipe = Recipe.from_dict(_recipe_dict())
    recipe.steps[2].params["title"] = "SDG ${params.year} / ${params.region}"
    plan = materialize(recipe, {"year": 2020}, run_id="r")
    # 整串是占位符时保留原始类型（int 不会变成 "2020"）……
    assert plan.graph.task("r:s2").spec["params"]["year"] == 2020
    # ……而夹杂在文本里时按文本插值。
    assert plan.graph.task("r:s3").spec["params"]["title"] == "SDG 2020 / 蒙古"


def test_materialize_rejects_undeclared_templates() -> None:
    raw = _recipe_dict()
    raw["steps"][1]["params"]["year"] = "${params.whatever}"
    recipe = Recipe.from_dict(raw)
    with pytest.raises(Exception, match="undeclared param"):
        materialize(recipe, {"year": 2019}, run_id="r")


def test_materialize_resolve_hook_decouples_deployment() -> None:
    recipe = Recipe.from_dict(_recipe_dict())
    plan = materialize(
        recipe,
        {"year": 2019},
        run_id="r",
        resolve=lambda uses: f"asset://local/{uses}",
    )
    task = plan.graph.task("r:s1")
    assert task.spec["uses"] == "asset://local/geocard.packed.menggu"
    assert task.spec["declared_uses"] == "geocard.packed.menggu"


def test_materialize_requires_a_run_id() -> None:
    recipe = Recipe.from_dict(_recipe_dict())
    with pytest.raises(Exception, match="run_id"):
        materialize(recipe, {"year": 2019}, run_id="  ")


def test_deliverables_carry_role_and_ref() -> None:
    recipe = Recipe.from_dict(_recipe_dict())
    plan = materialize(recipe, {"year": 2019}, run_id="r")
    listed = deliverables(plan)
    assert listed["report"]["role"] == "knowledge"
    assert listed["report"]["ref"] == "step://s3/report"
    assert listed["degradation_map"]["role"] == "data"


# --------------------------------------------------------------------------- #
# 目录
# --------------------------------------------------------------------------- #
def test_registry_registers_and_conflicts() -> None:
    registry = RecipeRegistry()
    recipe = Recipe.from_dict(_recipe_dict())
    entry = registry.register(recipe, provider_url="http://node-a")
    assert entry.status == STATUS_APPROVED
    assert len(registry) == 1

    with pytest.raises(RecipeConflict):
        registry.register(recipe)

    registry.register(recipe, replace=True, status=STATUS_PENDING)
    assert registry.get(recipe.id).status == STATUS_PENDING


def test_registry_resolves_unversioned_address_to_latest_approved() -> None:
    registry = RecipeRegistry()
    for version, status in (
        ("1.0.0", STATUS_APPROVED),
        ("1.2.0", STATUS_APPROVED),
        ("2.0.0", STATUS_PENDING),
    ):
        data = _recipe_dict()
        data["id"] = f"recipe://geonexus/sdg-15-3-1@{version}"
        registry.register(Recipe.from_dict(data), status=status)

    assert registry.resolve("recipe://geonexus/sdg-15-3-1@1.0.0").recipe.version == "1.0.0"
    # 1.10.0 > 1.9.0：字典序会选错，version_key 不会。
    assert registry.resolve("recipe://geonexus/sdg-15-3-1").recipe.version == "1.2.0"
    assert registry.resolve("recipe://geonexus/sdg-15-3-1@2.0.0") is None
    assert registry.resolve("recipe://geonexus/sdg-15-3-1@2.0.0", approved_only=False) is not None
    assert [e.recipe.version for e in registry.versions("geonexus", "sdg-15-3-1")] == [
        "1.0.0",
        "1.2.0",
        "2.0.0",
    ]


def test_registry_review_history_is_append_only() -> None:
    registry = RecipeRegistry()
    recipe = Recipe.from_dict(_recipe_dict())
    registry.register(recipe, status=STATUS_PENDING, submitted_by="alice")
    registry.approve(recipe.id, by="bob", note="口径清楚")
    entry = registry.reject(recipe.id, by="carol", note="数据快照过期")
    assert entry.status == STATUS_REJECTED
    assert [h["action"] for h in entry.review_history] == ["registered", "approved", "rejected"]
    assert entry.review_note == "数据快照过期"
    with pytest.raises(RecipeNotFound):
        registry.require(recipe.id, approved_only=True)


def test_registry_search_filters_by_contract_not_just_text() -> None:
    registry = RecipeRegistry()
    recipe = Recipe.from_dict(_recipe_dict())
    recipe.tags = ["sdg", "land"]
    registry.register(recipe)

    assert registry.search("sdg") and registry.search("SDG 15.3.1")
    assert registry.search("nothing-matches") == []
    assert registry.search(param="year")
    assert registry.search(param="crs") == []
    assert registry.search(output_role="knowledge")
    assert registry.search(output_role="model") == []
    assert registry.search(tag="land")
    assert registry.search(namespace="geonexus")
    assert registry.search(namespace="other") == []
    assert registry.search(approved_only=True) == [registry.get(recipe.id)]

    hit = registry.search("year")[0].summary()
    assert hit["steps"] == 3
    assert hit["reusable_params"] == ["year", "region"]
    assert hit["outputs"] == ["degradation_map", "report"]


def test_registry_persists_to_disk(tmp_path) -> None:
    path = tmp_path / "recipes.json"
    registry = RecipeRegistry(path)
    recipe = Recipe.from_dict(_recipe_dict())
    registry.register(recipe, provider_url="http://node-a", status=STATUS_PENDING)
    registry.approve(recipe.id, by="bob")

    reloaded = RecipeRegistry(path)
    entry = reloaded.require(recipe.id)
    assert entry.status == STATUS_APPROVED
    assert entry.provider_url == "http://node-a"
    assert reloaded.get(recipe.id).recipe.to_dict() == recipe.to_dict()
    assert json.loads(path.read_text("utf-8"))["version"] == 1


def test_version_key_handles_numeric_not_lexicographic_order() -> None:
    assert version_key("1.10.0") > version_key("1.9.0")
    assert version_key("2.0.0") > version_key("1.99.99")
    assert version_key(None) < version_key("0.0.1")
    assert version_key("1.0") == version_key("1.0.0")


# --------------------------------------------------------------------------- #
# fork
# --------------------------------------------------------------------------- #
def test_fork_creates_a_new_independently_approved_recipe() -> None:
    registry = RecipeRegistry()
    source = Recipe.from_dict(_recipe_dict())
    registry.register(source, provider_url="http://node-a")

    forked = registry.fork(
        "recipe://geonexus/sdg-15-3-1@1.0.0",
        namespace="muyang",
        name="sdg-15-3-1-mexico",
        version="1.0.0",
        params={"region": "墨西哥"},
        fixed={"year": 2019},
        title="SDG 15.3.1 墨西哥 2019",
    )

    assert forked.id == "recipe://muyang/sdg-15-3-1-mexico@1.0.0"
    assert forked.name == "SDG 15.3.1 墨西哥 2019"
    assert forked.param("region").default == "墨西哥"
    assert forked.param("year").scope == "fixed"
    assert forked.param("year").default == 2019
    # 源配方一个字节都没变。
    assert registry.get(source.id).recipe.param("region").default == "蒙古"
    assert registry.get(source.id).recipe.param("year").scope == "reusable"
    # 派生件默认进入待审。
    assert registry.get(forked.id).status == STATUS_PENDING
    assert registry.get(forked.id).review_note == f"forked from {source.id}"


def test_fork_coerces_injected_defaults_through_the_param_contract() -> None:
    """``--set year=2021`` 从命令行来的是字符串，落进配方前必须先收敛成整数。"""
    registry = RecipeRegistry()
    registry.register(Recipe.from_dict(_recipe_dict()))
    forked = registry.fork(
        "recipe://geonexus/sdg-15-3-1",
        register=False,
        params={"year": "2021"},
        fixed={"baseline": "2010"},
    )
    assert forked.param("year").default == 2021
    assert forked.param("baseline").default == 2010
    # 落盘再读回也还是整数。
    assert Recipe.from_dict(forked.to_dict()).param("year").default == 2021
    # 派生件因此可以直接物化，不需要调用方再补参数。
    plan = materialize(forked, {}, run_id="r")
    assert plan.params["year"] == 2021


def test_fork_defaults_and_unknown_params(tmp_path) -> None:
    registry = RecipeRegistry()
    registry.register(Recipe.from_dict(_recipe_dict()))

    forked = registry.fork("recipe://geonexus/sdg-15-3-1", register=False)
    assert forked.id == "recipe://geonexus/sdg-15-3-1-fork@1.0.0"

    with pytest.raises(Exception, match="unknown parameter"):
        registry.fork("recipe://geonexus/sdg-15-3-1", params={"crs": "EPSG:4326"}, register=False)

    with pytest.raises(RecipeNotFound):
        registry.fork("recipe://geonexus/nope", register=False)


def test_fork_requires_an_approved_source_by_default() -> None:
    registry = RecipeRegistry()
    registry.register(Recipe.from_dict(_recipe_dict()), status=STATUS_PENDING)
    with pytest.raises(RecipeNotFound):
        registry.fork("recipe://geonexus/sdg-15-3-1", register=False)
    assert registry.fork(
        "recipe://geonexus/sdg-15-3-1", register=False, approved_only=False
    ).id.startswith("recipe://geonexus/")


# --------------------------------------------------------------------------- #
# 验收：一个复杂案例可以被复用
# --------------------------------------------------------------------------- #
def test_acceptance_fork_change_params_rerun_report() -> None:
    """2 数据 + 1 算子 + 1 报告：派生 → 改参数 → 重跑 → 交付物齐备。

    这条链是"知识产品可被复用"的最小可执行证据：复用者不需要读懂算子内部，
    只需要知道哪些参数能动、动完会拿到什么。
    """
    registry = RecipeRegistry()
    registry.register(Recipe.from_dict(_recipe_dict()))
    source_id = "recipe://geonexus/sdg-15-3-1@1.0.0"

    # 1) 复用者看到的是"可动参数"，不是"作者的参数取值"。
    source_plan = materialize(registry.require(source_id).recipe, {"year": 2019}, run_id="r1")
    assert registry.require(source_id).recipe.reusable_param_names() == ["year", "region"]
    assert "report" in source_plan.output_refs()

    # 2) 派生一份自己的配方，把区域换成墨西哥。
    forked = registry.fork(source_id, name="sdg-15-3-1-mexico", params={"region": "墨西哥"})
    registry.approve(forked.id, by="reviewer")

    # 3) 改参数重跑：同一套步骤，不同的取值与结果引用。
    plan = materialize(
        registry.require(forked.id, approved_only=True).recipe, {"year": 2021}, run_id="r2"
    )
    assert plan.recipe_id == forked.id
    assert plan.params["region"] == "墨西哥"
    assert plan.params["year"] == 2021
    assert plan.graph.topological_order() == ["r2:s1", "r2:s2", "r2:s3"]
    assert plan.graph.task("r2:s2").provenance["params"]["region"] == "墨西哥"

    # 4) 交付物清单：两个数据 + 一份报告，且角色与来源都可核对。
    listed = deliverables(plan)
    assert set(listed) == {"degradation_map", "report"}
    assert listed["report"]["role"] == "knowledge"
    assert listed["report"]["from"] == "s3.report"
    assert listed["degradation_map"]["from"] == "s2.degradation"

    # 5) 两次运行互不干扰：任务 id 带 run_id，图各自独立。
    assert source_plan.task_ids() == ["r1:s1", "r1:s2", "r1:s3"]
    assert plan.task_ids() == ["r2:s1", "r2:s2", "r2:s3"]
