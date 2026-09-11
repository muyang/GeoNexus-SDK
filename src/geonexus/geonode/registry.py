"""Registries for the Local GeoNode.

- :class:`GeoCardRegistry` — the node's GeoCard registry (discovery layer).
- :class:`SkillRegistry` — the node's Skill registry (capability layer).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

from ..geocard.model import GeoCard
from .skill import Skill


class GeoNodeError(Exception):
    """Base error for GeoNode components."""


class DuplicateEntryError(GeoNodeError):
    """Raised when registering an entry that already exists."""


class EntryNotFoundError(GeoNodeError):
    """Raised when a requested entry is not present in a registry."""


class GeoCardRegistry:
    """Thread-safe registry of GeoCards (the node's discovery layer)."""

    def __init__(self) -> None:
        self._cards: dict[str, GeoCard] = {}
        self._lock = threading.RLock()

    def register(self, card: GeoCard) -> None:
        """Register a GeoCard. Raises :class:`DuplicateEntryError` on a
        duplicate id unless ``replace`` is used."""
        with self._lock:
            if card.id in self._cards:
                raise DuplicateEntryError(f"GeoCard already registered: {card.id}")
            self._cards[card.id] = card

    def register_many(self, cards: Iterable[GeoCard]) -> None:
        """Register several GeoCards."""
        for card in cards:
            self.register(card)

    def get(self, card_id: str) -> GeoCard | None:
        """Return the card with the given id, or None."""
        with self._lock:
            return self._cards.get(card_id)

    def require(self, card_id: str) -> GeoCard:
        """Return the card with the given id or raise :class:`EntryNotFoundError`."""
        card = self.get(card_id)
        if card is None:
            raise EntryNotFoundError(f"GeoCard not found: {card_id}")
        return card

    def list_cards(self) -> list[GeoCard]:
        """Return all registered cards (insertion order)."""
        with self._lock:
            return list(self._cards.values())

    def search(
        self,
        capability: str | None = None,
        type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[GeoCard]:
        """Filter cards by capability, type and/or tags."""
        with self._lock:
            results = list(self._cards.values())
        if capability is not None:
            results = [c for c in results if capability in c.capability_names()]
        if type is not None:
            results = [c for c in results if c.type == type]
        if tags:
            tag_set: set[str] = set(tags)
            results = [c for c in results if tag_set.issubset(set(c.tags))]
        return results

    def count(self) -> int:
        """Number of registered cards."""
        with self._lock:
            return len(self._cards)

    def __contains__(self, card_id: str) -> bool:
        return self.get(card_id) is not None


class SkillRegistry:
    """Thread-safe registry of Skills (the node's capability layer)."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._lock = threading.RLock()

    def register(self, skill: Skill) -> None:
        """Register a Skill. Raises :class:`DuplicateEntryError` on duplicates."""
        with self._lock:
            if skill.name in self._skills:
                raise DuplicateEntryError(f"Skill already registered: {skill.name}")
            self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """Return the skill with the given name, or None."""
        with self._lock:
            return self._skills.get(name)

    def require(self, name: str) -> Skill:
        """Return the skill or raise :class:`EntryNotFoundError`."""
        skill = self.get(name)
        if skill is None:
            raise EntryNotFoundError(f"Skill not found: {name}")
        return skill

    def has(self, name: str) -> bool:
        """Whether a skill with this name is registered."""
        with self._lock:
            return name in self._skills

    def list_skills(self) -> list[Skill]:
        """Return all registered skills (insertion order)."""
        with self._lock:
            return list(self._skills.values())

    def unregister(self, name: str) -> None:
        """Remove a skill by name (no-op if absent)."""
        with self._lock:
            self._skills.pop(name, None)

    def count(self) -> int:
        """Number of registered skills."""
        with self._lock:
            return len(self._skills)
