"""可见性继承 —— 组合案例的"能看见"必须是**最严**的那一条。

问题很具体：一个案例由 2 份数据 + 1 个算子 + 1 份报告组成，其中一份数据是
``sensitive``。如果案例本身标 ``public`` 就照 public 展示，那么组合就成了绕过
访问控制的捷径 —— 用户不需要拿到那份数据，只要引用它，就能从案例页看到它的元数据、
空间范围，甚至通过重跑把结论算出来。

所以规则只有一条，且不能有例外：

    组合的可见性 = min(组成各方的可见性)
    任何一项不可见 ⇒ 整个组合不可见（平台返回 404，而不是"部分展示"或 403）

为什么是 404 而不是 403：403 等于承认"存在一个你看不到的案例"，这本身就是信息
泄露（能推断出某个敏感区域有研究）。不存在与不可见，对外必须是同一个回答。

本模块只提供**判定**；"谁有权看到什么"仍然由平台的身份层决定（SDK 不发明账号体系）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .model import COMPONENT_ROLES, GeoCard

__all__ = [
    "DEFAULT_VISIBILITY",
    "GATING_ROLES",
    "role_vocabulary",
    "VISIBILITY_LEVELS",
    "VISIBILITY_RANK",
    "normalize_visibility",
    "compose_visibility",
    "effective_visibility",
    "VisibilityReport",
    "invisible_components",
    "DEFAULT_VISIBILITY",
    "role_vocabulary",
]

#: 从最公开到最严格。顺序即排序，不允许自造中间档 —— 多一档就多一处解释分歧。
VISIBILITY_LEVELS: tuple[str, ...] = ("public", "restricted", "sensitive", "secret")

#: 数值化排序：越大越严。``min`` 在语义上就是"取最大 rank"。
VISIBILITY_RANK: dict[str, int] = {name: index for index, name in enumerate(VISIBILITY_LEVELS)}

#: 未声明时的默认档：**当有限制处理**。缺省放开是访问控制里最常见的事故。
DEFAULT_VISIBILITY = "restricted"

#: **会参与可见性判定**的角色：承载内容的那几类。
#:
#: 为什么不是"所有组成项"：算子（skill）与算力（compute）是**代码与资源**，
#: 它们不是内容，也不因为"某地用了某个算子"而泄露任何东西；而且算子常常只有一个
#: 名字（甚至不在目录里），把它算进来会让每个案例都变成不可见 —— 规则过严等于没有规则。
#: 数据/模型/知识/方案则不同：知道"某地存在某份数据"本身就可能敏感。
GATING_ROLES: tuple[str, ...] = ("data", "model", "knowledge", "workflow")

_ALIASES = {
    "": DEFAULT_VISIBILITY,
    "none": DEFAULT_VISIBILITY,
    "unknown": DEFAULT_VISIBILITY,
    "internal": "restricted",
    "private": "restricted",
    "confidential": "sensitive",
    "top-secret": "secret",
    "top_secret": "secret",
}


def normalize_visibility(value: str | None) -> str:
    """把外部写法收敛到四档之一；不认识的一律按 ``restricted``。

    "不认识就放开"是不可接受的默认值，所以未知取值降级为受限，而不是 public。
    """
    if value is None:
        return DEFAULT_VISIBILITY
    text = str(value).strip().lower()
    if text in VISIBILITY_RANK:
        return text
    return _ALIASES.get(text, DEFAULT_VISIBILITY)


def compose_visibility(levels: Iterable[str | None]) -> str:
    """取最严的一档（``min`` 语义）。空集合按默认档处理。"""
    normalized = [normalize_visibility(level) for level in levels]
    if not normalized:
        return DEFAULT_VISIBILITY
    return max(normalized, key=lambda name: VISIBILITY_RANK[name])


class VisibilityReport:
    """一次可见性判定的完整结果 —— 平台据此决定 404 与日志，前端据此解释原因。"""

    def __init__(
        self,
        visibility: str,
        *,
        card: str | None = None,
        levels: Mapping[str, str] | None = None,
        missing: list[str] | None = None,
    ) -> None:
        self.visibility = visibility
        self.card = card
        self.levels = dict(levels or {})
        self.missing = list(missing or [])

    @property
    def visible(self) -> bool:
        """组合里任何一项取不到 ⇒ 不完整 ⇒ 不可见。

        "有一项查不到"不能当成"那一项是公开的"：那正好是访问控制最容易被绕过的地方。
        """
        return not self.missing

    def is_visible_to(self, granted: Iterable[str]) -> bool:
        """``granted`` 是调用方**已获授权**的档位集合（平台身份层给的）。

        语义：要看得见，就得够得着**最严的那一项**。不是"够得着其中一项"。
        """
        allowed = {normalize_visibility(level) for level in granted}
        need = VISIBILITY_RANK[self.visibility]
        return any(VISIBILITY_RANK[level] >= need for level in allowed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "visibility": self.visibility,
            "visible": self.visible,
            "levels": self.levels,
            "missing": self.missing,
            "card": self.card,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return (
            f"VisibilityReport(visibility={self.visibility!r}, visible={self.visible}, "
            f"levels={self.levels!r}, missing={self.missing!r})"
        )


def _card_visibility(card: GeoCard | Mapping[str, Any] | None) -> str:
    if card is None:
        return DEFAULT_VISIBILITY
    if isinstance(card, Mapping):
        compliance = card.get("compliance") or {}
        return normalize_visibility(compliance.get("sensitivity"))
    compliance = getattr(card, "compliance", None)
    return normalize_visibility(getattr(compliance, "sensitivity", None))


def invisible_components(
    card: GeoCard,
    lookup: Callable[[str], GeoCard | Mapping[str, Any] | None],
    *,
    gating_only: bool = True,
) -> list[str]:
    """列出解析不到的组成项。

    ``lookup`` 返回 ``None`` 表示"这个 id 在调用方可见的范围内不存在" ——
    可能是真的没有，也可能是没权限。**两种情况在访问控制上必须同等对待**，
    否则"引用一个不存在的 id"和"引用一个无权的 id"会给出不同响应，
    等于提供了一个探测后者的接口。
    """
    missing: list[str] = []
    for component in card.composition.components if card.composition else []:
        if component.required is False:
            # 可选组成项：缺了照样跑（这是方案作者显式声明的），因此不影响可见性。
            continue
        if gating_only and component.role not in GATING_ROLES:
            continue
        if lookup(component.id) is None:
            missing.append(component.id)
    return missing


def effective_visibility(
    card: GeoCard,
    lookup: Callable[[str], GeoCard | Mapping[str, Any] | None] | None = None,
    *,
    own: str | None = None,
    include_optional: bool = False,
    gating_only: bool = True,
) -> VisibilityReport:
    """算出一张卡片**对外**的可见性。

    Args:
        card: 组合卡片（也可以是普通卡片 —— 那时只有它自己一项）。
        lookup: ``id -> 卡片`` 的解析函数（注册中心/资产目录）。不提供时只算自身，
            并把组成项记为 ``missing``（因为无法确认它们是否可见）。
        own: 覆盖卡片自身的档位（调用方已从别处拿到，例如平台镜像表）。
        include_optional: 是否把 ``required: false`` 的组成项也算进来。默认不算：
            可选依赖缺席不影响可运行性，也就不该影响可见性。
        gating_only: 只让**承载内容**的角色（见 :data:`GATING_ROLES`）参与判定。
            默认开启；传 ``False`` 时所有组成项都参与（更严，适合审计口径）。
    """
    self_level = normalize_visibility(own) if own is not None else _card_visibility(card)
    levels: dict[str, str] = {card.id: self_level}
    missing: list[str] = []

    if card.composition is None:
        return VisibilityReport(self_level, card=card.id, levels=levels)

    for component in card.composition.components:
        if component.required is False and not include_optional:
            continue
        if gating_only and component.role not in GATING_ROLES:
            continue
        resolved = lookup(component.id) if lookup is not None else None
        if resolved is None:
            missing.append(component.id)
            continue
        levels[component.id] = _card_visibility(resolved)

    if missing:
        # 有一项确认不了：整案按最严处理（不对外可见），并把缺谁记下来供运维排查。
        return VisibilityReport("secret", card=card.id, levels=levels, missing=missing)
    return VisibilityReport(compose_visibility(levels.values()), card=card.id, levels=levels)


def role_vocabulary() -> tuple[str, ...]:
    """组合项的角色词表（转出给平台，避免它自己维护一套）。"""
    return COMPONENT_ROLES
