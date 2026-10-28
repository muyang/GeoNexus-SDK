"""GAAG 报告合约 —— 让"结论"也成为一个可审计的资产。

数据资产有合约（CRS/波段/分辨率…），**报告同样需要**。没有合约的报告只是一份
文件：没人知道它按哪一版数据、用哪个算子、改过哪些参数算出来的。本模块给知识
产品补上这一层，并把结论与它的来源绑在一起：

    报告卡片（type: knowledge, interface.type: report）
      ├─ composition  → 用了哪些数据与算子（引用，不复制）
      ├─ params       → 这一次实际取了什么参数值
      ├─ recipe_ref   → 哪一份可执行方案、哪一版、哪个数据快照
      └─ provenance   → 哪一次运行（run_id）产生的

于是"报告能不能被信任"变成一个可以机械检查的问题：必填字段缺不缺、产出它的
那次运行还在不在、数据快照和现在差多少。这正是 GAAG 合约模板在做的事，只不过
对象从栅格换成了结论。

与 :func:`geonexus.gaag.scan_text` 的分工：那个扫的是**文本文件**的通用特征
（字数、语言、主题）；这里扫的是**报告作为交付物**的身份特征（格式、校验和、
章节、生成时间），并负责把运行上下文补进去。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..geocard.builder import GeoCardBuilder
from ..geocard.model import COMPONENT_ROLES, GeoCard
from .contract import GAAGContract
from .contract_types import ContractCheckResult, validate_contract

__all__ = [
    "REPORT_ASSET_TYPE",
    "REPORT_MEDIA_TYPES",
    "REPORT_SOURCE_FIELDS",
    "scan_report",
    "report_card_from_run",
    "report_contract",
    "validate_report",
    "register_report_contract",
]

#: 合约类型标识。写进 ``GAAGContract.asset_type``。
REPORT_ASSET_TYPE = "report"

#: 报告常见媒体类型 → 扩展名。用来从产物路径判断"这是不是一份报告"。
REPORT_MEDIA_TYPES: dict[str, str] = {
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".json": "application/json",
}

#: 报告合约的"来源四件套"：缺任何一项，报告就无法被追溯到它的结论。
REPORT_SOURCE_FIELDS = ("source_run", "recipe", "params", "data_snapshot")

_TITLE_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_TAG_RE = re.compile(r"<[^>]+>")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_html(value: str) -> str:
    return _TAG_RE.sub("", value).strip()


# --------------------------------------------------------------------------- #
# 产物扫描：报告这份文件本身
# --------------------------------------------------------------------------- #
def scan_report(path: str | Path) -> dict[str, Any]:
    """扫描一份报告产物，返回合约所需的**身份特征**。

    刻意只做能机械核验的事：格式、字节数、校验和、标题、章节标题、生成时间。
    "写得对不对"不是扫描器能回答的问题，硬要打分只会造出一个没人信的分数。
    """
    target = Path(path)
    suffix = target.suffix.lower()
    meta: dict[str, Any] = {
        "format": suffix.lstrip(".") or "unknown",
        "media_type": REPORT_MEDIA_TYPES.get(suffix, "application/octet-stream"),
        "file_size": target.stat().st_size if target.exists() else 0,
        "exists": target.exists(),
        "title": None,
        "sections": [],
    }
    if not target.exists():
        meta["checksum"] = None
        return meta

    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    meta["checksum"] = f"sha256:{digest.hexdigest()}"
    meta["produced_at"] = datetime.fromtimestamp(
        target.stat().st_mtime, tz=timezone.utc
    ).isoformat()

    if suffix in (".html", ".htm", ".md", ".txt"):
        try:
            text = target.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - 读到一半被删
            return meta
        if suffix in (".html", ".htm"):
            heading = _TITLE_RE.search(text)
            sections = re.findall(r"<h2[^>]*>(.*?)</h2>", text, re.IGNORECASE | re.DOTALL)
            meta["title"] = _strip_html(heading.group(1)) if heading else None
            meta["sections"] = [_strip_html(s) for s in sections][:20]
        else:
            headings = _HEADING_RE.findall(text)
            meta["title"] = headings[0][1].strip() if headings else None
            meta["sections"] = [h[1].strip() for h in headings[1:21]]
        meta["word_count"] = len(text.split())
    return meta


# --------------------------------------------------------------------------- #
# 从一次运行生成报告卡片
# --------------------------------------------------------------------------- #
def report_card_from_run(
    plan: Any,
    *,
    outputs: Mapping[str, str] | None = None,
    deliverable: str | None = None,
    title: str | None = None,
    description: str | None = None,
    license: str | None = None,
    owner: str | None = None,
    report_path: str | None = None,
) -> GeoCard:
    """把一次运行的**结论**变成一张可注册、可追溯的 GeoCard。

    Args:
        plan: :class:`geonexus.recipe.RunPlan`（或任何有 ``recipe_id`` /
            ``params`` / ``outputs`` / ``graph`` 的对象）。
        outputs: 交付物名 → 产物引用/路径。缺省时从 ``plan.output_refs()`` 取。
        deliverable: 指定哪一项交付物是这份报告；缺省取 ``role: knowledge`` 的第一项。
        report_path: 报告产物的落盘路径，用于扫描与校验和。

    这张卡片**不含任何数值**：它说的是"这份结论是怎么来的"。结论本身在产物文件里，
    两者通过 ``recipe_ref`` 与 provenance 关联 —— 卡片不是数据的副本。
    """
    recipe = _plan_recipe(plan)
    outputs = dict(outputs if outputs is not None else _plan_outputs(plan))
    spec = _pick_deliverable(plan, deliverable)

    name = title or f"{getattr(recipe, 'name', '') or '报告'}（{_short_run_id(plan)}）"
    builder = (
        GeoCardBuilder(
            # id 里同时带方案、交付物与运行：同一方案的不同交付物、以及不同运行，
            # 各自是**不同的**结论，不能共用一个 id。
            id=spec.get("card_id")
            or "report.{recipe}.{deliverable}.{run}".format(
                recipe=_slug(getattr(recipe, "slug", "report")),
                deliverable=_slug(spec.get("name") or "report"),
                run=_short_run_id(plan),
            ),
            type="knowledge",
            name=name,
            description=description
            or f"由方案 {getattr(recipe, 'id', '-')} 在运行 {getattr(plan, 'run_id', '-')} 中产生。",
        )
        .tag("gaag")
        .tag("report")
        .interface(type="report", version="1.0")
    )

    # 数据 + 算子：引用而不是复制。一个 id 只出现一次 —— 同一个资产既是"方案要求的
    # 资产"又是"某一步的 uses"时，重复登记只会让组成列表看起来有两个东西。
    for component_id, role, step in _plan_components(plan, recipe):
        builder = builder.component(component_id, role=role, step=step)

    for param in getattr(recipe, "params", []) or []:
        builder = builder.param(
            param.name,
            type=param.type,
            required=param.required,
            default=(plan.params or {}).get(param.name, param.default),
            enum=list(param.enum) or None,
            minimum=param.minimum,
            maximum=param.maximum,
            scope=param.scope,
            target=param.target,
            description=param.description,
        )

    builder = builder.recipe(
        getattr(recipe, "id", ""),
        version=getattr(recipe, "version", None),
        entrypoint=getattr(recipe, "entrypoint", None),
        data_snapshot=dict(getattr(recipe, "data_snapshot", {}) or {}),
    )

    snapshot = dict(getattr(recipe, "data_snapshot", {}) or {})
    lineage = " <- ".join(
        [
            f"run://{getattr(plan, 'run_id', 'run')}",
            getattr(recipe, "id", "-"),
            *(f"{k}@{v}" for k, v in sorted(snapshot.items())),
        ]
    )
    builder = builder.provenance(
        provider=owner or None,
        source=f"run://{getattr(plan, 'run_id', 'run')}",
        lineage=lineage,
    )
    if license:
        builder = builder.license(name=license)
    if report_path:
        scan = scan_report(report_path)
        # 产物路径与形态：``access`` 只认 protocol/endpoint/auth/format，
        # 媒体类型放 ``format``，不自己另造一个字段（否则 schema 会拒）。
        builder = builder.access(
            protocol="file",
            endpoint=str(report_path),
            format=scan.get("media_type") or spec.get("media_type"),
        )
    return builder.build()


def _plan_recipe(plan: Any) -> Any:
    recipe = getattr(plan, "recipe", None)
    if recipe is not None:
        return recipe
    raise TypeError(
        "report_card_from_run 需要一个带 .recipe 的运行计划（RunPlan）；"
        "只有参数与产物、没有方案来源的'报告'无法被追溯，因此拒绝生成合约"
    )


def _plan_outputs(plan: Any) -> dict[str, str]:
    refs = getattr(plan, "output_refs", None)
    if callable(refs):
        return dict(refs())
    return dict(getattr(plan, "outputs", {}) or {})


def _pick_deliverable(plan: Any, name: str | None) -> dict[str, Any]:
    outputs = list(getattr(plan, "outputs", []) or [])
    if name:
        for out in outputs:
            if getattr(out, "name", None) == name:
                return _deliverable_view(out)
        raise ValueError(f"run plan has no deliverable named {name!r}")
    for out in outputs:
        if getattr(out, "role", None) == "knowledge":
            return _deliverable_view(out)
    return _deliverable_view(outputs[0]) if outputs else {}


def _deliverable_view(out: Any) -> dict[str, Any]:
    step, _, output = str(getattr(out, "from_", "")).partition(".")
    return {
        "name": getattr(out, "name", None),
        "role": getattr(out, "role", "knowledge"),
        "media_type": getattr(out, "media_type", None),
        "from": getattr(out, "from_", None),
        "step": step or None,
        "output": output or None,
    }


def _plan_components(plan: Any, recipe: Any) -> list[tuple[str, str, str | None]]:
    """从任务图里抽出 ``(资产, 角色, 步骤)`` —— 报告用了什么，卡片就要写什么。

    角色规则（一条，可预测）：
      · 出现在方案 ``requires`` 里的 → ``data``（方案声明的依赖资产）；
      · 只在某一步 ``uses`` 里出现的 → ``skill``（真正干活的算子）。

    同一个 id 只登记一次，用 ``step`` 记住它在哪里被用到。
    """
    graph = getattr(plan, "graph", None)
    if graph is None:
        return []
    declared = {str(r) for r in (getattr(recipe, "requires", []) or [])}
    found: list[tuple[str, str, str | None]] = []
    seen: set[str] = set()
    for task_id in graph.topological_order():
        task = graph.task(task_id)
        spec = task.spec or {}
        uses = spec.get("declared_uses") or spec.get("uses")
        if not uses or uses in seen:
            continue
        seen.add(uses)
        kind = role_of_task(task)
        if uses in declared:
            role = "data"
        elif kind == "model":
            role = "model"
        elif kind == "composite":
            role = "workflow"
        else:
            role = "skill"
        found.append((uses, role, (task.labels or {}).get("recipe_step") or task_id))
    # 声明了但没有任何步骤用到的依赖，也要出现在组成里（否则"用了什么"是漏的）
    for ref in sorted(declared - seen):
        found.append((ref, "data", None))
    return found


def role_of_task(task: Any) -> str:
    kind = getattr(task, "kind", None)
    return getattr(kind, "value", str(kind or "skill"))


def _short_run_id(plan: Any) -> str:
    run_id = str(getattr(plan, "run_id", "run"))
    return run_id.split(":")[-1]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "report")).strip("-")
    return cleaned or "report"


# --------------------------------------------------------------------------- #
# 合约
# --------------------------------------------------------------------------- #
@dataclass
class ReportContractSource:
    """报告合约里"来源"部分的取值。

    单独一个结构，是因为这四样东西**必须一起出现**：只写 run_id 不写数据快照，
    半年后没人能判断"结论变了"是因为重跑过还是因为数据换了。
    """

    source_run: str
    recipe: str
    params: dict[str, Any] = field(default_factory=dict)
    data_snapshot: dict[str, str] = field(default_factory=dict)
    deliverables: list[str] = field(default_factory=list)
    recipe_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_run": self.source_run,
            "recipe": self.recipe,
            "recipe_version": self.recipe_version,
            "params": self.params,
            "data_snapshot": self.data_snapshot,
            "deliverables": self.deliverables,
        }


def report_contract(
    card: GeoCard,
    *,
    source: ReportContractSource | None = None,
    report_path: str | Path | None = None,
    registered_at: str | None = None,
) -> GAAGContract:
    """把报告卡片 + 运行来源 + 产物扫描合成一份 GAAG 合约。

    合约的 ``scanned_meta`` 就是后续 :func:`validate_report` 的输入，所以这里
    不做任何"补默认值"的动作：缺什么就缺什么，让校验去报错。
    """
    scan = scan_report(report_path) if report_path else {}
    meta: dict[str, Any] = dict(scan)
    if source is not None:
        meta.update(source.to_dict())
    if card.recipe_ref is not None:
        meta.setdefault("recipe", card.recipe_ref.recipe_id)
        meta.setdefault("recipe_version", card.recipe_ref.version)
        meta.setdefault("data_snapshot", dict(card.recipe_ref.data_snapshot))
    meta.setdefault(
        "params", {p.name: p.default for p in (card.params.params if card.params else [])}
    )
    if card.access is not None and card.access.endpoint:
        meta.setdefault("deliverable_ref", card.access.endpoint)

    contract = GAAGContract(
        contract_id=card.id,
        asset_type=REPORT_ASSET_TYPE,
        card=card,
        scanned_meta=meta,
        provenance=(
            f"report contract for run {meta.get('source_run', '-')} "
            f"produced by {meta.get('recipe', '-')}"
        ),
        registered_at=registered_at or _now(),
    )
    return contract


def validate_report(contract: GAAGContract | dict[str, Any]) -> ContractCheckResult:
    """校验报告合约：产物特征 + 来源四件套是否齐全。"""
    if isinstance(contract, GAAGContract):
        asset_type = contract.asset_type
        meta = contract.scanned_meta
    else:
        asset_type = contract.get("asset_type", REPORT_ASSET_TYPE)
        meta = contract.get("scanned_meta", {}) or {}
    if asset_type != REPORT_ASSET_TYPE:
        return ContractCheckResult(
            satisfied=False,
            asset_type=asset_type,
            reasons=[f"不是报告合约（asset_type={asset_type}）"],
        )
    result = validate_contract(REPORT_ASSET_TYPE, meta)
    missing_source = [f for f in REPORT_SOURCE_FIELDS if f in result.missing_fields]
    if missing_source:
        result.reasons.append(
            "报告缺少来源字段（" + ", ".join(missing_source) + "）："
            "没有来源的结论无法被复核，不能作为交付物"
        )
    return result


def register_report_contract(
    registry: Any,
    card: GeoCard,
    *,
    source: ReportContractSource | None = None,
    report_path: str | Path | None = None,
) -> tuple[GAAGContract, ContractCheckResult]:
    """校验通过才登记。返回 ``(合约, 校验结果)``，未通过时不写注册中心。

    先校验后登记，是这份合约存在的意义：一份来源不全的报告进了注册中心，之后
    每个人都会把它当"已验证的结论"引用。
    """
    contract = report_contract(card, source=source, report_path=report_path)
    result = validate_report(contract)
    if not result.satisfied:
        return contract, result
    register = getattr(registry, "register", None)
    if callable(register):
        register(contract)
    return contract, result


#: 供需要"哪些角色算数据、哪些算算子"的调用方复用（与 GeoCard 同一套词表）。
DATA_COMPONENT_ROLES = tuple(r for r in COMPONENT_ROLES if r in ("data", "knowledge"))
