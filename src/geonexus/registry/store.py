"""In-memory, thread-safe store backing the shared GeoCard Registry."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from ..geocard.validator import ContractResult, ContractValidator
from .models import (
    REVIEW_STATUSES,
    STATUS_APPROVED,
    STATUS_REJECTED,
    RegistryEntry,
    RegistrySearchResult,
    SkillEntry,
)


class RegistryStoreError(Exception):
    """Base error for the registry store."""


class RegistryEntryConflict(RegistryStoreError):
    """Raised when registering a card id that already exists."""


class RegistryEntryNotFound(RegistryStoreError):
    """Raised when a card id is not present."""


class SkillEntryConflict(RegistryStoreError):
    """Raised when registering a skill name that already exists."""


class SkillEntryNotFound(RegistryStoreError):
    """Raised when a skill name is not present."""


class RegistryStore:
    """Thread-safe store for cards and skills.

    Holds :class:`RegistryEntry` objects (card + owning node endpoint) and
    :class:`SkillEntry` objects (skill description + offering node endpoint).
    Card search applies the GeoCard :class:`ContractValidator` as a coarse
    pre-filter; the owning node re-checks the contract authoritatively before
    execution.

    When ``persist_path`` is set, every mutation is saved to a JSON file and
    the store reloads it on startup (V1.0 persistence).
    """

    def __init__(self, persist_path: str | os.PathLike | None = None) -> None:
        self._entries: dict[str, RegistryEntry] = {}
        self._skills: dict[str, SkillEntry] = {}
        self._lock = threading.RLock()
        self.contract_validator = ContractValidator()
        self.persist_path = str(persist_path) if persist_path else None
        if self.persist_path:
            self._load()

    # ------------------------------------------------------------------ #
    # Persistence (V1.0)
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        import json

        assert self.persist_path is not None
        path = Path(self.persist_path)
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            raise RegistryStoreError(f"Failed to load registry file {path}: {exc}") from exc
        for raw in data.get("cards", []):
            card_entry = RegistryEntry.model_validate(raw)
            self._entries[card_entry.card.id] = card_entry
        for raw in data.get("skills", []):
            skill_entry = SkillEntry.model_validate(raw)
            self._skills[skill_entry.skill.name] = skill_entry

    def _save(self) -> None:
        if self.persist_path is None:
            return
        import json

        path = Path(self.persist_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cards": [e.to_dict() for e in self._entries.values()],
            "skills": [e.to_dict() for e in self._skills.values()],
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        tmp.replace(path)

    # ------------------------------------------------------------------ #
    def register(self, entry: RegistryEntry) -> None:
        """Register a card. Raises :class:`RegistryEntryConflict` on duplicates."""
        with self._lock:
            if entry.card.id in self._entries:
                raise RegistryEntryConflict(f"Card already registered: {entry.card.id}")
            self._entries[entry.card.id] = entry
            self._save()

    def register_or_update(self, entry: RegistryEntry) -> bool:
        """Register a card, replacing an existing one. Returns True if added."""
        with self._lock:
            added = entry.card.id not in self._entries
            self._entries[entry.card.id] = entry
            self._save()
            return added

    def unregister(self, card_id: str) -> RegistryEntry:
        """Remove and return an entry; raises :class:`RegistryEntryNotFound`."""
        with self._lock:
            if card_id not in self._entries:
                raise RegistryEntryNotFound(f"Card not registered: {card_id}")
            entry = self._entries.pop(card_id)
            self._save()
            return entry

    def get(self, card_id: str) -> RegistryEntry | None:
        """Return the entry for a card id, or None."""
        with self._lock:
            return self._entries.get(card_id)

    def list_entries(self, status: str | None = None) -> list[RegistryEntry]:
        """All entries (insertion order), optionally filtered by review status."""
        with self._lock:
            entries = list(self._entries.values())
        if status is not None:
            entries = [e for e in entries if e.status == status]
        return entries

    def set_status(
        self, card_id: str, status: str, note: str | None = None, by: str | None = None
    ) -> RegistryEntry:
        """Set a card's review status (pending/approved/rejected) and record
        the action in ``review_history``.

        When the status changes to approved or rejected, the GeoCard's
        ``provenance`` is updated with the review decision so the card itself
        carries the audit trail.

        Raises :class:`RegistryEntryNotFound` when the card is unknown.
        """
        if status not in REVIEW_STATUSES:
            raise ValueError(f"invalid status {status!r}; expected one of {REVIEW_STATUSES}")
        with self._lock:
            entry = self._entries.get(card_id)
            if entry is None:
                raise RegistryEntryNotFound(f"Card not registered: {card_id}")
            now = datetime.now(timezone.utc).isoformat()
            history_entry = {
                "action": status,
                "by": by or "unknown",
                "at": now,
                "note": note or "",
            }
            history = list(entry.review_history) + [history_entry]
            # Update the GeoCard's provenance with the review decision.
            card = entry.card.model_copy(deep=True)
            review_line = f"[{status}] by {by or 'unknown'} at {now}"
            if note:
                review_line += f" — {note}"
            if card.provenance is not None:
                updated_lineage = (card.provenance.lineage or "") + " | " + review_line
                card.provenance.lineage = updated_lineage
            updated = entry.model_copy(
                update={
                    "status": status,
                    "review_note": note,
                    "review_history": history,
                    "card": card,
                }
            )
            self._entries[card_id] = updated
            self._save()
            return updated

    def approve(
        self, card_id: str, note: str | None = None, by: str | None = None
    ) -> RegistryEntry:
        """Approve a pending card (makes it discoverable)."""
        return self.set_status(card_id, STATUS_APPROVED, note, by=by)

    def reject(
        self, card_id: str, note: str | None = None, by: str | None = None
    ) -> RegistryEntry:
        """Reject a pending card (removes it from discovery)."""
        return self.set_status(card_id, STATUS_REJECTED, note, by=by)

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def search(
        self,
        capability: str | None = None,
        type: str | None = None,
        tags: list[str] | None = None,
        bbox: list[float] | None = None,
        crs: str | None = None,
        start: str | None = None,
        end: str | None = None,
        required_bands: list[str] | None = None,
        required_resolution: float | None = None,
        contract_gate: bool = True,
        status: str | None = STATUS_APPROVED,
    ) -> list[RegistrySearchResult]:
        """Search cards, optionally gated by contract satisfaction.

        By default only **approved** cards are returned (review workflow,
        v1.1); pass ``status=None`` to include every state (admin view) or a
        specific state (``"pending"``) for review queues.

        When any contract constraint (bbox/crs/start/end/required_bands/
        required_resolution) is provided and ``contract_gate`` is True, only
        cards that satisfy the request are returned.
        """
        with self._lock:
            candidates = list(self._entries.values())
        if status is not None:
            candidates = [e for e in candidates if e.status == status]

        results: list[RegistrySearchResult] = []
        for entry in candidates:
            card = entry.card
            if capability is not None and capability not in card.capability_names():
                continue
            if type is not None and card.type != type:
                continue
            if tags:
                tag_set: set[str] = set(tags)
                if not tag_set.issubset(set(card.tags)):
                    continue

            contract: ContractResult | None = None
            has_constraint = any(
                v is not None for v in (bbox, crs, start, end, required_bands, required_resolution)
            )
            if has_constraint:
                contract = self.contract_validator.check(
                    card,
                    bbox=bbox,
                    crs=crs,
                    start=start,
                    end=end,
                    required_bands=required_bands,
                    required_resolution=required_resolution,
                )
                if contract_gate and not contract.satisfied:
                    continue
            results.append(RegistrySearchResult(entry=entry, contract=contract))
        return results

    def search_by_id(self, card_id: str) -> RegistryEntry | None:
        """Convenience lookup (equivalent to :meth:`get`)."""
        return self.get(card_id)

    def register_many(self, entries: Iterable[RegistryEntry]) -> None:
        for entry in entries:
            self.register(entry)

    # ------------------------------------------------------------------ #
    # Skills (V0.5: shared GeoSkill registry)
    # ------------------------------------------------------------------ #
    def register_skill(self, entry: SkillEntry) -> None:
        """Register a skill. Raises :class:`SkillEntryConflict` on duplicates."""
        with self._lock:
            if entry.skill.name in self._skills:
                raise SkillEntryConflict(f"Skill already registered: {entry.skill.name}")
            self._skills[entry.skill.name] = entry
            self._save()

    def register_skill_or_update(self, entry: SkillEntry) -> bool:
        """Register a skill, replacing an existing one. Returns True if added."""
        with self._lock:
            added = entry.skill.name not in self._skills
            self._skills[entry.skill.name] = entry
            self._save()
            return added

    def unregister_skill(self, name: str) -> SkillEntry:
        """Remove and return a skill entry; raises :class:`SkillEntryNotFound`."""
        with self._lock:
            if name not in self._skills:
                raise SkillEntryNotFound(f"Skill not registered: {name}")
            entry = self._skills.pop(name)
            self._save()
            return entry

    def get_skill(self, name: str) -> SkillEntry | None:
        """Return the skill entry for a name, or None."""
        with self._lock:
            return self._skills.get(name)

    def list_skills(self) -> list[SkillEntry]:
        """All skill entries (insertion order)."""
        with self._lock:
            return list(self._skills.values())

    def search_skills(
        self,
        capability: str | None = None,
        name: str | None = None,
        node_url: str | None = None,
    ) -> list[SkillEntry]:
        """Filter skills by capability, exact name and/or offering node."""
        with self._lock:
            results = list(self._skills.values())
        if capability is not None:
            results = [e for e in results if capability in e.skill.capabilities]
        if name is not None:
            results = [e for e in results if e.skill.name == name]
        if node_url is not None:
            results = [e for e in results if e.node_url == node_url]
        return results

    def count_skills(self) -> int:
        with self._lock:
            return len(self._skills)
