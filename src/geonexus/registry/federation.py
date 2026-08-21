"""Registry federation (V1.0+): registry-to-registry sync.

:class:`RegistryFederator` implements **pull-based** sync: a registry imports
the card and skill catalogs of its peer registries, so discovery through any
one registry sees the whole federation. Data and node endpoints never move —
only descriptions, exactly like the registry's own contract.

Sync semantics:
- cards and skills are imported as-is, keeping the **owning node's endpoint**
  (``node_url``) from the peer, so routing/pushdown keeps working.
- duplicate ids are skipped by default or updated in place
  (``update_existing=True``), so sync is idempotent.
- a peer that is unreachable or errors is recorded in the report; other
  peers are still synced.
"""

from __future__ import annotations

import logging
from typing import Any

from .client import RegistryClient, RegistryClientError
from .models import RegistryEntry, SkillEntry
from .store import RegistryEntryConflict, SkillEntryConflict

logger = logging.getLogger(__name__)


class RegistryFederator:
    """Pull-based sync between a local registry and its peers."""

    def __init__(
        self,
        local_store: Any,
        peers: list[str],
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.local_store = local_store
        self.peers = list(peers)
        self.api_key = api_key
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    def sync(self, update_existing: bool = True) -> dict[str, Any]:
        """Import cards and skills from every peer.

        Returns a report:
        ``{"peers": n, "cards_added": n, "cards_updated": n,
        "cards_skipped": n, "skills_added": n, "skills_updated": n,
        "skills_skipped": n, "errors": {peer: msg}}``
        """
        report: dict[str, Any] = {
            "peers": len(self.peers),
            "cards_added": 0,
            "cards_updated": 0,
            "cards_skipped": 0,
            "skills_added": 0,
            "skills_updated": 0,
            "skills_skipped": 0,
            "errors": {},
        }
        for peer in self.peers:
            try:
                self._sync_peer(peer, update_existing, report)
            except RegistryClientError as exc:
                logger.warning("Registry sync with %s failed: %s", peer, exc)
                report["errors"][peer] = str(exc)
        return report

    def _sync_peer(
        self,
        peer: str,
        update_existing: bool,
        report: dict[str, Any],
    ) -> None:
        with RegistryClient(peer, timeout=self.timeout, api_key=self.api_key) as client:
            for card in client.list_cards():
                card_entry = RegistryEntry.model_validate(
                    {
                        "card": card["card"],
                        "node_url": card.get("node_url", "unknown"),
                        "registered_at": card.get("registered_at"),
                    }
                )
                outcome = self._import_card(card_entry, update_existing)
                report[f"cards_{outcome}"] += 1
            for skill in client.list_skills():
                skill_entry = SkillEntry.model_validate(
                    {
                        "skill": skill["skill"],
                        "node_url": skill.get("node_url", "unknown"),
                        "registered_at": skill.get("registered_at"),
                    }
                )
                outcome = self._import_skill(skill_entry, update_existing)
                report[f"skills_{outcome}"] += 1
        logger.info("Registry sync with %s completed", peer)

    def _import_card(self, entry: RegistryEntry, update_existing: bool) -> str:
        """Return ``added``, ``updated`` or ``skipped``."""
        try:
            self.local_store.register(entry)
            return "added"
        except RegistryEntryConflict:
            if update_existing:
                self.local_store.register_or_update(entry)
                return "updated"
            return "skipped"

    def _import_skill(self, entry: SkillEntry, update_existing: bool) -> str:
        """Return ``added``, ``updated`` or ``skipped``."""
        try:
            self.local_store.register_skill(entry)
            return "added"
        except SkillEntryConflict:
            if update_existing:
                self.local_store.register_skill_or_update(entry)
                return "updated"
            return "skipped"
