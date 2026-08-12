"""Where an actor is: the canonical spatial-presence authority.

    enums       PositionKind and ActorKind -- the closed vocabularies
    positions   the four position shapes, and the boundary that parses them back

# Why this is its own package and not part of a character model

There is no Character Foundation in this build: `characters` holds prose about who
someone is and nothing about where they are. Position was the gap that let a free-text
`GameSession.current_location` act as a de-facto answer, resolved by *name* against the
spatial graph -- which is ambiguous by construction and wrong rather than absent when
it fails.

This package closes that gap at its own size. It is a spatial-presence contract, not a
character model: no sheet, no stats, no inventory, no knowledge, no travel mechanics.
When Character Foundation arrives it composes or adopts this rather than growing a
second position field, and the seam is deliberately drawn so that adoption is a
re-pointing of `actor_id` and not a migration of meaning. See docs/world-state.md.
"""

from __future__ import annotations

from app.domain.character_position.enums import ActorKind, PositionKind
from app.domain.character_position.positions import (
    AtLocation,
    CharacterPosition,
    InTransit,
    Minute,
    Offstage,
    Unlocated,
    read_position,
    referenced_location_ids,
    scene_location_id,
)

__all__ = [
    "ActorKind",
    "AtLocation",
    "CharacterPosition",
    "InTransit",
    "Minute",
    "Offstage",
    "PositionKind",
    "Unlocated",
    "read_position",
    "referenced_location_ids",
    "scene_location_id",
]
