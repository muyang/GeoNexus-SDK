"""Build a content-addressed announcement from a real GeoNode's registries."""

from __future__ import annotations

from datetime import timedelta

from .models import (
    NodeAnnouncement,
    NodeDeclaration,
    Publication,
    ResourceDeclaration,
    ToolBinding,
    utcnow,
)


def from_node(
    node,
    declaration: NodeDeclaration,
    *,
    version: str,
    revision: int = 1,
    ttl_seconds: int = 3600,
    visibility: str = "restricted",
    publication: Publication | None = None,
):
    cards = {c.id: c for c in node.geocard_registry.list_cards()}
    bindings = {}
    for skill in node.skill_registry.list_skills():
        if skill.geocard is None:
            raise ValueError(f"Skill {skill.name} needs a GeoCard before governed announcement")
        identity = skill.geocard.id
        if identity in bindings:
            raise ValueError("A resource must identify one unambiguous execution tool")
        card = cards.get(identity)
        if card is None:
            raise ValueError("Register the skill GeoCard in the node registry before announcement")
        if card.to_dict() != skill.geocard.to_dict():
            raise ValueError("Skill GeoCard conflicts with registered resource snapshot")
        cards[identity] = skill.geocard
        bindings[identity] = ToolBinding(
            tool=skill.name,
            input_schema=skill.input_schema,
            output_schema=skill.output_schema or None,
        )
    now = utcnow()
    return NodeAnnouncement(
        node=declaration,
        revision=revision,
        declared_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        resources=[
            ResourceDeclaration(
                card=cards[k].to_dict(),
                version=version,
                visibility=visibility,
                publication=publication or Publication(),
                binding=bindings.get(k),
            )
            for k in sorted(cards)
        ],
    )
