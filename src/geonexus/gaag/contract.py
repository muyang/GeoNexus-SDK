"""GAAG contract data model.

:class:`GAAGContract` is the registry entry: a GeoCard plus the contract
fields mandated by the GAAG design (asset_type, scanned metadata, semantic
embedding, provenance, registration timestamp).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..geocard import GeoCard
from ..geocard.builder import GeoCardBuilder


class GAAGError(Exception):
    """Base error for GAAG module."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GAAGContractSpec:
    """Input parameters used to build a GAAG contract.

    Mirrors the GeoCard identifier/description fields plus an optional
    explicit semantic embedding (when the caller computed one off-line).
    """

    id: str
    name: str
    description: str
    type: str = "data"
    embedding: list[float] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GAAGContract:
    """A registered Geo Asset Contract.

    Attributes:
        contract_id: Registry-unique contract identifier (equals card.id).
        asset_type: One of raster | vector | stac | model | skill | other.
        card: The underlying GeoCard (CRS/bbox/bands/temporal/provenance...).
        scanned_meta: Raw metadata captured at scan time.
        semantic_embedding: Dense vector used for semantic retrieval.
        provenance: Provenance note (e.g. "scanned from <path>").
        registered_at: ISO timestamp of registration.
    """

    contract_id: str
    asset_type: str
    card: GeoCard
    scanned_meta: dict[str, Any] = field(default_factory=dict)
    semantic_embedding: list[float] | None = None
    provenance: str = ""
    registered_at: str = field(default_factory=_now)

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """Return a plain JSON-compatible dictionary."""
        return {
            "contract_id": self.contract_id,
            "asset_type": self.asset_type,
            "card": self.card.to_dict(),
            "scanned_meta": self.scanned_meta,
            "semantic_embedding": self.semantic_embedding,
            "provenance": self.provenance,
            "registered_at": self.registered_at,
        }


def contract_to_card(spec_or_contract: GAAGContractSpec | dict[str, Any]) -> GeoCard:
    """Build a GeoCard from a spec or raw dict."""
    if isinstance(spec_or_contract, GAAGContractSpec):
        spec = spec_or_contract
        builder = (
            GeoCardBuilder(
                id=spec.id,
                type=spec.type,
                name=spec.name,
                description=spec.description,
            )
            .tag("gaag")
        )
        return builder.build()
    # raw dict
    return GeoCard.from_dict(spec_or_contract)