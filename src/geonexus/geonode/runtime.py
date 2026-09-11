"""Local execution runtime for the GeoNode.

:class:`LocalRuntime` executes a registered GeoSkill for a ``geo.execute``
request: it resolves the skill, validates required inputs, builds a
:class:`SkillContext` and calls the handler. It is the *execution boundary*
of GeoNexus: all computation happens here, on the node that owns the data.
"""

from __future__ import annotations

import logging
from typing import Any

from ..geomcp.models import ExecuteParams
from ..geomcp.protocol import (
    EXECUTION_FAILED,
    INVALID_PARAMS,
    SKILL_NOT_FOUND,
    GeoMCPProtocolError,
)
from .registry import SkillRegistry
from .skill import SkillContext

logger = logging.getLogger(__name__)


class LocalRuntime:
    """Runs GeoSkills locally.

    Args:
        skill_registry: The SkillRegistry this runtime executes from.
        workdir: Optional working directory handed to skill handlers
            (defaults to the current working directory).
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        workdir: str | None = None,
        geocard_registry: Any | None = None,
        node_name: str = "local-node",
    ) -> None:
        self.skill_registry = skill_registry
        self.workdir = workdir
        self.geocard_registry = geocard_registry
        self.node_name = node_name

    # ------------------------------------------------------------------ #
    def execute(self, params: ExecuteParams) -> dict[str, Any]:
        """Execute a skill for a GeoMCP ``geo.execute`` request.

        Raises :class:`GeoMCPProtocolError` with JSON-RPC error codes on
        failure (SKILL_NOT_FOUND / INVALID_PARAMS / EXECUTION_FAILED).
        """
        skill = self.skill_registry.get(params.skill)
        if skill is None:
            raise GeoMCPProtocolError(
                SKILL_NOT_FOUND,
                f"Skill not found: {params.skill}",
                {"available": [s.name for s in self.skill_registry.list_skills()]},
            )
        if skill.handler is None:
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Skill '{skill.name}' has no handler",
            )

        missing = skill.validate_inputs(params.params)
        if missing:
            raise GeoMCPProtocolError(
                INVALID_PARAMS,
                f"Skill '{skill.name}' missing required input(s): {missing}",
                {"missing": missing},
            )

        resolved_cards = [self._resolve_card(card_id) for card_id in params.geocards]
        context = SkillContext(
            request_id=params.request_id,
            spatial=params.spatial,
            temporal=params.temporal,
            geocards=[c for c in resolved_cards if c is not None],
            workdir=self.workdir,
            node_name=self.node_name,
        )

        logger.info("Executing skill '%s' (request_id=%s)", skill.name, params.request_id)
        try:
            outputs = skill.handler(params.params, context)
        except GeoMCPProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001 - skill boundary
            logger.exception("Skill '%s' failed", skill.name)
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Skill '{skill.name}' failed: {exc}",
            ) from exc

        if not isinstance(outputs, dict):
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Skill '{skill.name}' must return a dict, got {type(outputs).__name__}",
            )

        return {
            "status": "ok",
            "skill": skill.name,
            "outputs": outputs,
            "geocards": params.geocards,
            "executed_by": "local-runtime",
            "request_id": params.request_id,
        }

    def _resolve_card(self, card_id: str) -> Any | None:
        """Resolve a GeoCard by id if a registry is attached."""
        registry = getattr(self, "geocard_registry", None)
        if registry is not None:
            return registry.get(card_id)
        return None

    def set_geocard_registry(self, registry: Any) -> None:
        """Attach a GeoCard registry so skills can resolve referenced cards."""
        self.geocard_registry = registry
