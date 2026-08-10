"""What a resolver is allowed to see, and nothing else.

    Command + ResolutionContext  ->  ResolutionOutcome

The context is the left half of that arrow, and it is assembled by the application
from the command's own `ContextRequest`. Handed in rather than fetched, for the same
reason `ProgressionContext` is: a resolver that could query would reach past the
transaction boundary, make its own N+1s, and become untestable without a database.

# Bounded by the command, not by the session

A resolver asks for "situation 3f2a" and gets that situation and its participants. It
cannot ask for "every active situation", so the cost of resolving one command does not
grow with how long the save has been played. Whatever a future resolver needs gets
added here, by someone who has to justify it.

# Read-only, and only current state

There is no history in here. A resolver decides what happens *now* from what is true
*now*; a resolver that read the event stream would be one step from reconstructing
state by replaying it, which this project deliberately does not do.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.domain.errors import NotFoundError
from app.domain.world_locations import LocationDefinition
from app.domain.world_rules import WorldRules
from app.domain.world_situations import Situation, SituationParticipant


class ResolutionContext(BaseModel):
    """Everything one resolution may read, as a value.

    Frozen, so a resolver cannot smuggle a change back to the application by mutating
    what it was handed. The only thing it may return is a `ResolutionOutcome`.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    session_id: uuid.UUID
    world_id: uuid.UUID

    turn_index: int
    """Which exchange this resolution belongs to. Not every resolution has a player
    behind it -- a scheduled event resolves during whichever turn was last taken."""

    now: int
    """The session clock, in elapsed fictional minutes, as it stood when this context
    was loaded. A resolver converts its own cadences against this and never against a
    wall clock."""

    state_revision: int
    """What the world's authoritative state was at, when this was read. Carried so an
    outcome can be checked against the same number at commit time -- see
    `app.application.resolution_service`."""

    rules: WorldRules
    """The world's own settings. Read for real values -- `danger.escalation_rate`,
    `simulation.world_continues_without_player` -- never branched on a preset name."""

    situations: tuple[Situation, ...] = ()
    participants: tuple[SituationParticipant, ...] = ()
    locations: tuple[LocationDefinition, ...] = ()
    """Definitions only. No `LocationState` yet: no resolver reads one, and loading a
    row on the chance that one might is how a bounded context stops being bounded."""

    def situation(self, situation_id: uuid.UUID) -> Situation | None:
        return next((s for s in self.situations if s.id == situation_id), None)

    def require_situation(self, situation_id: uuid.UUID) -> Situation:
        """The situation, or a failure naming it.

        A resolver reaching for something the application did not load is a bug in the
        command's `context_request`, not a gameplay outcome, so this raises rather than
        returning a rejection.
        """
        found = self.situation(situation_id)
        if found is None:
            raise NotFoundError("Situation", situation_id)
        return found

    def participants_of(self, situation_id: uuid.UUID) -> tuple[SituationParticipant, ...]:
        return tuple(p for p in self.participants if p.situation_id == situation_id)

    def location(self, location_id: uuid.UUID) -> LocationDefinition | None:
        return next((loc for loc in self.locations if loc.id == location_id), None)
