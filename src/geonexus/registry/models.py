"""Data models of the shared GeoCard Registry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ..geocard.model import GeoCard
from ..geocard.validator import ContractResult

# Registry review states (v1.1).
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
REVIEW_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED)


class RegistryEntry(BaseModel):
    """A card registered at a registry, bound to the node that owns it.

    ``status`` is the review state (v1.1): ``pending`` (submitted, awaiting
    review), ``approved`` (visible in search — the default, so existing
    registrations stay compatible), or ``rejected`` (with ``review_note``).

    ``review_history`` (v1.1) records every review action::

        [{"action": "approved", "by": "admin", "at": "2026-01-01T00:00:00Z", "note": "metadata OK"}]
    """

    card: GeoCard
    node_url: str = Field(description="Endpoint of the node that owns the asset.")
    registered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = Field(default="approved", description="pending | approved | rejected")
    review_note: str | None = Field(
        default=None, description="Reviewer note (rejection reason or approval remark)."
    )
    review_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Chronological review actions: [{action, by, at, note}]",
    )
    submitted_by: str | None = Field(
        default=None, description="Username who submitted the card for review."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "card": self.card.to_dict(),
            "node_url": self.node_url,
            "registered_at": self.registered_at,
            "status": self.status,
            "review_note": self.review_note,
            "review_history": self.review_history,
            "submitted_by": self.submitted_by,
        }


class RegistrySearchResult(BaseModel):
    """A registry search hit: the entry plus its contract evaluation."""

    entry: RegistryEntry
    contract: ContractResult | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"entry": self.entry.to_dict()}
        if self.contract is not None:
            data["contract"] = {
                "satisfied": self.contract.satisfied,
                "reasons": self.contract.reasons,
                "warnings": self.contract.warnings,
            }
        return data


class SkillDescriptor(BaseModel):
    """A lightweight description of a GeoSkill for shared discovery."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "capabilities": self.capabilities,
        }


class SkillEntry(BaseModel):
    """A skill registered at a registry, bound to the node offering it."""

    skill: SkillDescriptor
    node_url: str = Field(description="Endpoint of the node offering the skill.")
    registered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill.to_dict(),
            "node_url": self.node_url,
            "registered_at": self.registered_at,
        }
