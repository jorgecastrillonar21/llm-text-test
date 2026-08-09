"""Typed changes to spatial state.

These sit alongside `SetFact` and `RemoveFact` in one `StateMutationBatch` -- see
`app.domain.state_mutations` -- so a single resolved event can collapse a bridge, block
the crossing and raise the local danger together, or not at all.

# Why these are not facts

A location's condition could be stored as `WorldFact(system.location_condition)`. It is
not, and must not be: a dedicated model gives it a closed vocabulary, a check
constraint, a column the engine can index and query, and one authority. Writing it as a
fact instead would put mechanical state in a table whose values are free-form JSON, and
then two places would claim to know whether the bridge is standing.

The division holds in both directions. WorldFacts still describe locations -- what a
place is *known for*, what the story has established about it -- and those are open,
narrative and exactly what the fact store is good at. Mechanical condition,
accessibility, security, ownership and control are not.

# Partial by design

Every field but the target is optional, and `None` means "leave it". A mutation that
required the whole state would make "block this gate" into a read-modify-write, and the
value the caller read would be the one the language model saw before the turn began.
"""

from __future__ import annotations

import uuid
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.world_locations.enums import LocationAccessibility, LocationCondition
from app.domain.world_locations.states import DangerModifier, SecurityLevel


class _SpatialMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    condition: LocationCondition | None = None
    accessibility: LocationAccessibility | None = None

    reason: str = ""
    """One line for the audit trail. Not required -- the GameEvent carries the real
    explanation -- but useful when a batch changes six things for six reasons."""

    def _require_something_to_do(self, fields: tuple[object, ...]) -> None:
        if all(field is None for field in fields):
            raise ValueError(
                "A spatial mutation must change at least one field. An empty mutation "
                "would move the state revision without moving the world."
            )


class UpdateLocationState(_SpatialMutation):
    """Change what is currently true about a place, in this session."""

    op: Literal["update_location_state"] = "update_location_state"

    location_id: uuid.UUID

    security_level: SecurityLevel | None = None
    local_danger_modifier: DangerModifier | None = None

    owner_entity_id: uuid.UUID | None = None
    controller_entity_id: uuid.UUID | None = None
    """Ownership and control move independently and often in opposite directions --
    a garrison changes who holds a fort without changing whose fort it is."""

    clear_owner: bool = False
    clear_controller: bool = False
    """`None` already means "leave this alone", so it cannot also mean "set it to
    nobody". These two say the second thing. Ugly, and the alternative -- a sentinel
    value, or a partial-update wrapper on every field -- is uglier for one case that
    arises when a faction is driven out and nobody replaces it."""

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        self._require_something_to_do(
            (
                self.condition,
                self.accessibility,
                self.security_level,
                self.local_danger_modifier,
                self.owner_entity_id,
                self.controller_entity_id,
                True if self.clear_owner or self.clear_controller else None,
            )
        )
        if self.clear_owner and self.owner_entity_id is not None:
            raise ValueError("clear_owner and owner_entity_id contradict each other.")
        if self.clear_controller and self.controller_entity_id is not None:
            raise ValueError("clear_controller and controller_entity_id contradict each other.")
        return self

    def target(self) -> tuple[str, str]:
        """What this mutation competes for, for the one-mutation-per-target rule."""
        return ("location_state", str(self.location_id))


class UpdateConnectionState(_SpatialMutation):
    """Change whether and how a traversal can currently be made."""

    op: Literal["update_connection_state"] = "update_connection_state"

    connection_id: uuid.UUID
    traversal_modifier: DangerModifier | None = None

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        self._require_something_to_do((self.condition, self.accessibility, self.traversal_modifier))
        return self

    def target(self) -> tuple[str, str]:
        return ("connection_state", str(self.connection_id))


SpatialMutation = UpdateLocationState | UpdateConnectionState
