"""GeoSkill — the reusable geospatial capability unit of GeoNexus.

A Skill bundles a name, description, input/output schemas, an executable
handler and (optionally) its own GeoCard describing it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..geocard.model import GeoCard

# Handler contract: ``handler(params: dict, context: SkillContext) -> dict``
Handler = Callable[[dict[str, Any], "SkillContext"], dict[str, Any]]


@dataclass
class SkillContext:
    """Execution context handed to a skill handler."""

    request_id: str | None = None
    spatial: Any | None = None
    temporal: Any | None = None
    geocards: list[GeoCard] = field(default_factory=list)
    workdir: str | None = None
    node_name: str = "local-node"


@dataclass
class Skill:
    """A reusable geospatial capability.

    Attributes:
        name: Unique skill name, e.g. ``ndvi-analysis``.
        description: Human-readable description.
        input_schema: JSON-schema-like declaration of inputs.
        output_schema: JSON-schema-like declaration of outputs.
        handler: Callable ``(params, context) -> dict`` performing the work.
        geocard: Optional GeoCard describing this skill as an asset.
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    handler: Handler | None = None
    geocard: GeoCard | None = None

    def describe(self) -> dict[str, Any]:
        """Serialize the skill for GeoMCP ``geo.describe`` / capabilities."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "geocard_id": self.geocard.id if self.geocard else None,
        }

    def validate_inputs(self, params: dict[str, Any]) -> list[str]:
        """Return a list of missing required input keys (empty when valid)."""
        required = self.input_schema.get("required", []) if self.input_schema else []
        return [key for key in required if key not in params]
