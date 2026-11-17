"""Opt-in, lightweight GeoNode admission and evidence-based readiness tooling.

Public declarations cannot carry approvals. Platform owns identity, observations,
review authority, task authorization and actual execution.
"""

from .models import (
    AccessContext,
    Capacity,
    Evidence,
    NodeAnnouncement,
    NodeDeclaration,
    Principal,
    Publication,
    ResourceDeclaration,
    ToolBinding,
)
from .node import from_node
from .rating import Rating, RatingPolicy, contract_issue, rate_node, rate_resource, readiness
from .service import AdmissionDecision, AdmissionService
from .transport import AdmissionClient, create_admission_app

__all__ = [
    "AccessContext",
    "AdmissionClient",
    "AdmissionDecision",
    "AdmissionService",
    "Capacity",
    "Evidence",
    "NodeAnnouncement",
    "NodeDeclaration",
    "Principal",
    "Publication",
    "Rating",
    "RatingPolicy",
    "ResourceDeclaration",
    "ToolBinding",
    "contract_issue",
    "create_admission_app",
    "from_node",
    "rate_node",
    "rate_resource",
    "readiness",
]
