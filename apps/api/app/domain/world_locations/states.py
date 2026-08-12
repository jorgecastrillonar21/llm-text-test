"""What is currently true about a place, in one session.

Definitions are shared across every save of a world; state is not. Ten sessions read
the same `LocationDefinition` for the Broken Crown and each keeps its own row saying
whether it is still standing.

# What deliberately is not here

    characters_inside      position belongs to CharacterState, not to the room
    discovered_by_player   knowing a place exists is KnowledgeState's business
    weather                environment will be spatially scoped, later, elsewhere
    population, economy    their own systems, when they exist
    tactical positions     ephemeral; SceneState and EncounterSpace own those

Each omission is load-bearing rather than an oversight. A character list on a location
duplicates a fact that lives on the character and immediately disagrees with it. A
discovery flag makes a secret passage that objectively exists indistinguishable from
one that does not. Both are documented in docs/world-state-locations.md.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domain.world_locations.enums import (
    LocationAccessibility,
    LocationCondition,
    is_traversable,
)

SecurityLevel = Annotated[int, Field(ge=0, le=100)]
"""How guarded a place is, as intensity rather than probability. A future system will
turn it into guards, surveillance, access checks and law response; nothing here does,
and nothing here should read it as "70% chance of being caught"."""

DangerModifier = Annotated[int, Field(ge=-100, le=100)]
"""How this place shifts the world's baseline danger, not a replacement for it.

The world's rules already say how dangerous the universe is; a location says only
whether it is worse or better than that. Storing a full danger configuration per
location would mean every place carried a copy of the world's rules, free to drift.
"""


class LocationState(BaseModel):
    """One session's current view of one place."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    session_id: uuid.UUID
    location_id: uuid.UUID

    condition: LocationCondition = LocationCondition.INTACT
    accessibility: LocationAccessibility = LocationAccessibility.OPEN
    """Two independent axes. `pristine` + `restricted`, `damaged` + `open`, `intact` +
    `sealed` and `destroyed` + `open` are all things a world routinely contains, so
    neither field constrains the other."""

    security_level: SecurityLevel = 0
    local_danger_modifier: DangerModifier = 0

    owner_entity_id: uuid.UUID | None = None
    """Who it belongs to. Deliberately untyped beyond an id: an owner may be a
    character today and a faction when factions exist."""

    controller_entity_id: uuid.UUID | None = None
    """Who currently holds it -- which is frequently not the owner. A palace owned by
    the Kingdom of Aster and controlled by the Northern Rebellion is the interesting
    case, and one field could not say it."""

    created_at: dt.datetime
    updated_at: dt.datetime

    @property
    def is_enterable(self) -> bool:
        """Whether the place itself permits entry, before any connection is consulted.

        Not sufficient on its own: reaching a place also needs a traversable edge. A
        castle whose every gate is blocked is `open` and unreachable, which is why
        `spatial_service` checks both.
        """
        return is_traversable(self.accessibility)


class LocationConnectionState(BaseModel):
    """One session's current view of one traversal.

    The state that most often decides whether somewhere can be reached. A castle is
    usually still `open` after its main gate is barred -- what changed is the edge, not
    the place, and a model that only looked at `LocationState` would get it wrong.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    session_id: uuid.UUID
    connection_id: uuid.UUID

    condition: LocationCondition = LocationCondition.INTACT
    accessibility: LocationAccessibility = LocationAccessibility.OPEN

    traversal_modifier: DangerModifier = 0
    """How much harder or easier this crossing currently is than its nominal cost.
    Reuses the -100..100 shape rather than inventing a second scale; the future
    TravelEngine decides what it means in minutes."""

    created_at: dt.datetime
    updated_at: dt.datetime

    @property
    def is_passable(self) -> bool:
        """Whether this edge may currently be crossed at all.

        Topology only. Whether *this* character may cross it -- keys, permissions,
        faction standing -- is the future access-requirement system's question, and
        `restricted` deliberately returns True so that system has something to gate.
        """
        return is_traversable(self.accessibility)
