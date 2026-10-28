"""Recipe 目录：登记、检索、审批状态与 **fork**。

这一层刻意长得像 :mod:`geonexus.registry` 的卡片目录，因为要解决的是同一个问题：
"谁能用、用哪一版、谁批的"。**差别**在于审批的裁决权不在 SDK —— ``approve`` /
``reject`` 只是记录状态与历史，身份与权限由平台决定（见 ``docs/ARCHITECTURE.md``
的分工）。SDK 不引入自己的账号体系。

``fork`` 是"复用"这个词的可执行定义：拿到一个已批准的配方，改命名空间、改参数
默认值或固定值，**产生一份新的、可独立审批的配方**。原配方一个字节都不动。
"""

from __future__ import annotations

import builtins
import json
import os
import threading
from pathlib import Path
from typing import Any

from ..review import REVIEW_STATUSES, STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED
from .materialize import coerce_param_value
from .model import (
    Recipe,
    RecipeError,
    RecipeValidationError,
    format_recipe_address,
    parse_recipe_address,
)

__all__ = [
    "RecipeEntry",
    "RecipeConflict",
    "RecipeNotFound",
    "RecipeAmbiguous",
    "RecipeRegistry",
    "version_key",
]

#: 状态与卡片目录共用一套取值 —— 平台不必为"配方"再学一套审批词表。
STATUSES = REVIEW_STATUSES


def version_key(version: str | None) -> tuple[int, ...]:
    """语义版本的粗排序键；无法解析的版本排在能解析的后面。

    不追求完整的 semver 预发布规则：目录只需要"选一个最新的已批准版本"，把
    ``1.10.0 > 1.9.0`` 这类字典序陷阱处理掉就够了。
    """
    if not version:
        return (0,)
    core = version.split("+", 1)[0].split("-", 1)[0]
    parts: list[int] = []
    for chunk in core.split("."):
        parts.append(int(chunk) if chunk.isdigit() else -1)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


class RecipeConflict(RecipeError):
    """同一个地址已经登记过（且调用方没有要求覆盖）。"""


class RecipeNotFound(RecipeError):
    """目录里没有这个配方。"""


class RecipeAmbiguous(RecipeError):
    """地址不带版本，却匹配到多个版本 —— 拒绝替调用方猜。"""


class RecipeEntry:
    """目录里的一条：配方 + 提供者 + 审批状态 + 历史。"""

    def __init__(
        self,
        recipe: Recipe,
        *,
        provider_url: str = "",
        status: str = STATUS_APPROVED,
        submitted_by: str | None = None,
        review_note: str | None = None,
        review_history: list[dict[str, Any]] | None = None,
        created_at: str | None = None,
    ) -> None:
        self.recipe = recipe
        self.provider_url = provider_url
        self.status = status
        self.submitted_by = submitted_by
        self.review_note = review_note
        self.review_history = list(review_history or [])
        self.created_at = created_at or _now()

    @property
    def recipe_id(self) -> str:
        return self.recipe.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe": self.recipe.to_dict(),
            "provider_url": self.provider_url,
            "status": self.status,
            "submitted_by": self.submitted_by,
            "review_note": self.review_note,
            "review_history": self.review_history,
            "created_at": self.created_at,
        }

    def summary(self) -> dict[str, Any]:
        """检索结果用的轻量视图 —— 列表页不需要整张配方的每一步。"""
        return {
            "id": self.recipe.id,
            "name": self.recipe.name,
            "description": self.recipe.description,
            "version": self.recipe.version,
            "status": self.status,
            "provider_url": self.provider_url,
            "steps": len(self.recipe.steps),
            "params": [p.name for p in self.recipe.params],
            "reusable_params": self.recipe.reusable_param_names(),
            "outputs": self.recipe.deliverable_names(),
            "tags": list(self.recipe.tags),
            "license": self.recipe.license,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecipeEntry:
        return cls(
            Recipe.from_dict(data["recipe"]),
            provider_url=data.get("provider_url", ""),
            status=data.get("status", STATUS_APPROVED),
            submitted_by=data.get("submitted_by"),
            review_note=data.get("review_note"),
            review_history=data.get("review_history"),
            created_at=data.get("created_at"),
        )


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class RecipeRegistry:
    """线程安全的配方目录，可选落盘（JSON）。

    ``persist_path`` 给定时每次改动都会原子写回，重启后自动载入 —— 与
    :class:`geonexus.registry.store.RegistryStore` 同样的持久化承诺。
    """

    def __init__(self, persist_path: str | os.PathLike | None = None) -> None:
        self._entries: dict[str, RecipeEntry] = {}
        self._lock = threading.RLock()
        self.persist_path = str(persist_path) if persist_path else None
        self._stamp: tuple[float, int] | None = None
        if self.persist_path:
            self._load()
            self._stamp = self._file_stamp()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        assert self.persist_path is not None
        path = Path(self.persist_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RecipeError(f"failed to load recipe catalogue {path}: {exc}") from exc
        for raw in data.get("recipes", []):
            entry = RecipeEntry.from_dict(raw)
            self._entries[entry.recipe_id] = entry

    def _save(self) -> None:
        if self.persist_path is None:
            return
        path = Path(self.persist_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "recipes": [e.to_dict() for e in self._entries.values()],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        self._stamp = self._file_stamp()

    def _file_stamp(self) -> tuple[float, int] | None:
        if self.persist_path is None:
            return None
        try:
            stat = Path(self.persist_path).stat()
        except OSError:
            return None
        return (stat.st_mtime, stat.st_size)

    def reload_if_changed(self) -> bool:
        """文件被**别的进程**改过时重新载入（``recipe register --catalogue`` 就是那种）。

        目录是多进程共享的：节点启动时读一次就再也不看，会让"刚登记的方案在节点上
        不存在"。按 mtime+size 判断，改动过就整体重载 —— 目录很小，够用且简单。
        """
        if self.persist_path is None:
            return False
        stamp = self._file_stamp()
        if stamp is None or stamp == self._stamp:
            return False
        with self._lock:
            self._entries.clear()
            self._load()
            self._stamp = self._file_stamp()
        return True

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def register(
        self,
        recipe: Recipe | dict[str, Any],
        *,
        provider_url: str = "",
        status: str = STATUS_APPROVED,
        submitted_by: str | None = None,
        replace: bool = False,
        review_note: str | None = None,
    ) -> RecipeEntry:
        """登记一个配方。

        版本是身份的一部分：``@1.0.0`` 和 ``@1.1.0`` 是两条记录，可以并存 ——
        这正是"已有人在用 1.0 跑结果"时还能安全发新版的原因。同一个地址重复
        登记会冲突，除非显式 ``replace=True``（改历史要留痕，不能悄悄发生）。
        """
        if status not in STATUSES:
            raise RecipeValidationError(
                f"unknown review status {status!r}; expected one of {', '.join(STATUSES)}"
            )
        parsed = recipe if isinstance(recipe, Recipe) else Recipe.from_dict(recipe)
        parsed.validate()

        with self._lock:
            if parsed.id in self._entries and not replace:
                raise RecipeConflict(
                    f"recipe {parsed.id} is already registered "
                    f"(status={self._entries[parsed.id].status}); bump the version or pass replace"
                )
            entry = RecipeEntry(
                parsed,
                provider_url=provider_url,
                status=status,
                submitted_by=submitted_by,
                review_note=review_note,
            )
            entry.review_history.append(
                {
                    "action": "registered",
                    "by": submitted_by,
                    "at": entry.created_at,
                    "note": review_note,
                }
            )
            self._entries[parsed.id] = entry
            self._save()
            return entry

    def approve(self, recipe_id: str, *, by: str | None = None, note: str | None = None) -> RecipeEntry:
        return self._review(recipe_id, STATUS_APPROVED, by=by, note=note)

    def reject(self, recipe_id: str, *, by: str | None = None, note: str | None = None) -> RecipeEntry:
        return self._review(recipe_id, STATUS_REJECTED, by=by, note=note)

    def _entry_for_review(self, address: str) -> RecipeEntry:
        """审批必须落到**一条**记录上。

        地址不带版本时：只有一个版本就用它；有多个版本则拒绝猜测 —— 审批是治理
        动作，"我批准的是最新那版"这种推断一旦猜错，后果比报错严重得多。
        """
        exact = self._entries.get(address)
        if exact is not None:
            return exact

        try:
            namespace, name, version = parse_recipe_address(address)
        except RecipeValidationError as exc:
            raise RecipeNotFound(f"recipe {address} is not registered") from exc
        if version:
            raise RecipeNotFound(f"recipe {address} is not registered")

        matches = [
            e
            for e in self._entries.values()
            if e.recipe.namespace == namespace and e.recipe.slug == name
        ]
        if not matches:
            raise RecipeNotFound(f"recipe {address} is not registered")
        if len(matches) > 1:
            versions = ", ".join(sorted(e.recipe.version or "-" for e in matches))
            raise RecipeAmbiguous(
                f"recipe {address} has {len(matches)} versions ({versions}); "
                "include @<version> to say which one"
            )
        return matches[0]

    def _review(
        self, recipe_id: str, status: str, *, by: str | None, note: str | None
    ) -> RecipeEntry:
        with self._lock:
            entry = self._entry_for_review(recipe_id)
            entry.status = status
            entry.review_note = note
            entry.review_history.append(
                {"action": status, "by": by, "at": _now(), "note": note}
            )
            self._save()
            return entry

    def remove(self, recipe_id: str) -> None:
        with self._lock:
            entry = self._entry_for_review(recipe_id)
            del self._entries[entry.recipe_id]
            self._save()

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def get(self, recipe_id: str) -> RecipeEntry | None:
        return self._entries.get(recipe_id)

    def require(self, recipe_id: str, *, approved_only: bool = False) -> RecipeEntry:
        entry = self._entries.get(recipe_id)
        if entry is None:
            raise RecipeNotFound(f"recipe {recipe_id} is not registered")
        if approved_only and entry.status != STATUS_APPROVED:
            raise RecipeNotFound(f"recipe {recipe_id} is not approved (status={entry.status})")
        return entry

    def resolve(self, address: str, *, approved_only: bool = True) -> RecipeEntry | None:
        """把"可能没写版本"的地址解析成一条确定记录。

        解析顺序：精确命中 → 最新**已批准**版本 → 最新任意版本。平台展示"用哪个
        版本"时必须能复现，所以这里永远返回**一条具体记录**，不返回"一批"。
        """
        if address in self._entries:
            entry = self._entries[address]
            if approved_only and entry.status != STATUS_APPROVED:
                return None
            return entry

        namespace, name, version = parse_recipe_address(address)
        if version:
            return None
        candidates = [
            e
            for e in self._entries.values()
            if e.recipe.namespace == namespace and e.recipe.slug == name
            and (not approved_only or e.status == STATUS_APPROVED)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda e: version_key(e.recipe.version))

    def search(
        self,
        query: str = "",
        *,
        namespace: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        param: str | None = None,
        output_role: str | None = None,
        approved_only: bool = False,
        limit: int | None = None,
    ) -> list[RecipeEntry]:
        """按文本 + 结构检索。

        ``param`` 是这里的重点：调用方常问的是"**哪个配方能接受我要改的那个参数**"，
        而不是"名字里有没有这两个字"。
        """
        needle = (query or "").strip().lower()
        out: list[RecipeEntry] = []
        for entry in self._entries.values():
            if namespace and entry.recipe.namespace != namespace:
                continue
            if status and entry.status != status:
                continue
            if approved_only and entry.status != STATUS_APPROVED:
                continue
            if tag and tag not in entry.recipe.tags:
                continue
            if param and entry.recipe.param(param) is None:
                continue
            if output_role and not any(o.role == output_role for o in entry.recipe.outputs):
                continue
            if needle:
                haystack = " ".join(
                    [
                        entry.recipe.id,
                        entry.recipe.name,
                        entry.recipe.description,
                        " ".join(entry.recipe.tags),
                        " ".join(entry.recipe.uses()),
                        " ".join(p.name for p in entry.recipe.params),
                    ]
                ).lower()
                if needle not in haystack:
                    continue
            out.append(entry)
        out.sort(key=lambda e: (e.recipe.slug, version_key(e.recipe.version)))
        return out[:limit] if limit else out

    def list(self, *, status: str | None = None) -> builtins.list[RecipeEntry]:
        return self.search(status=status)

    def versions(self, namespace: str, name: str) -> builtins.list[RecipeEntry]:
        """同一个配方的所有版本，从旧到新 —— 版本列表页要用。"""
        found = [
            e
            for e in self._entries.values()
            if e.recipe.namespace == namespace and e.recipe.slug == name
        ]
        found.sort(key=lambda e: version_key(e.recipe.version))
        return found

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, recipe_id: object) -> bool:
        return recipe_id in self._entries

    # ------------------------------------------------------------------ #
    # fork —— "复用"的可执行定义
    # ------------------------------------------------------------------ #
    def fork(
        self,
        recipe_id: str,
        *,
        namespace: str | None = None,
        name: str | None = None,
        version: str | None = None,
        params: dict[str, Any] | None = None,
        fixed: dict[str, Any] | None = None,
        title: str | None = None,
        register: bool = True,
        approved_only: bool = True,
        provider_url: str = "",
    ) -> Recipe:
        """从一条已登记配方派生一个新的、可独立审批与迭代的配方。

        Args:
            recipe_id: 源地址；不给版本时取最新的已批准版本。
            namespace / name / version: 新地址的三段；缺省沿用源，``name`` 缺省时
                加 ``-fork`` 后缀，``version`` 缺省重置为 ``1.0.0``（派生是新的
                血统，不是源的新版本）。
            params: 覆盖**可复用**参数的默认值 —— 这就是"换一组参数再跑一遍"。
            fixed: 把某些可复用参数改为 ``fixed``（作者认为这次不能动）。
            register: 默认把派生结果登记回目录，状态 ``pending``：派生也要过审。

        源配方保持不变：fork 的语义是"分叉"，不是"改写别人的配方"。
        """
        entry = self.resolve(recipe_id, approved_only=approved_only)
        if entry is None:
            raise RecipeNotFound(
                f"no {'approved ' if approved_only else ''}recipe matches {recipe_id}"
            )
        source = entry.recipe
        data = source.to_dict()

        new_version = version or "1.0.0"
        new_name = name or f"{source.slug}-fork"
        new_namespace = namespace or source.namespace
        data["id"] = format_recipe_address(new_namespace, new_name, new_version)
        if title:
            data["name"] = title

        overrides = dict(params or {})
        pins = dict(fixed or {})
        known = {p.name for p in source.params}
        unknown = sorted((set(overrides) | set(pins)) - known)
        if unknown:
            raise RecipeError(
                f"cannot fork {source.id}: unknown parameter(s) {', '.join(unknown)}"
            )

        for spec in data.get("params", []):
            # 注入的默认值必须过一遍类型契约：``--set year=2021`` 从命令行来的是
            # 字符串，直接写进配方会让"整数参数"的默认值变成 "2021"。
            if spec["name"] in overrides:
                spec["default"] = coerce_param_value(
                    spec["name"], spec.get("type", "string"), overrides[spec["name"]],
                    list(spec.get("enum") or []),
                )
            if spec["name"] in pins:
                spec["scope"] = "fixed"
                spec["default"] = coerce_param_value(
                    spec["name"], spec.get("type", "string"), pins[spec["name"]],
                    list(spec.get("enum") or []),
                )

        forked = Recipe.from_dict(data)
        forked.validate()

        if register:
            self.register(
                forked,
                provider_url=provider_url or entry.provider_url,
                status=STATUS_PENDING,
                submitted_by=None,
                review_note=f"forked from {source.id}",
            )
        return forked
