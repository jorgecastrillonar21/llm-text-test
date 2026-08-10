"""A validated structured request to attempt something.

    natural language  ->  Intent  ->  Command  ->  Resolution  ->  Outcome

A Command is the third of those, and the first one this build has. It is what a
resolver consumes: already parsed, already typed, already known to name real ids.
Resolvers never see the player's sentence, which is the point -- "I kick the guard and
run through the door" is two commands or none, and deciding which is the Intent
Interpreter's job, not a resolver's.

# Two commands, deliberately

`AdvanceTimeCommand` and `ProgressSituationCommand`, because those are the two
mechanical operations this game actually has. There is no `AttackCommand`, no
`CastAbilityCommand`, no `TraverseConnectionCommand`: writing them now would mean
writing resolvers with no mechanics behind them, and a resolver that returns a
plausible-looking outcome it invented is worse than no resolver at all.

# What a Command is not

Not an event. `CastSpellCommand` is a request; `SpellCastEvent` is a thing that
happened. The tenses are load-bearing -- a codebase that calls its requests events
ends up with a history table full of things nobody did.

# `context_request`

Each command states which entities its resolver will need, and the application loads
exactly those. The alternative is either a resolver that reaches into the database
mid-calculation, or a context object that eagerly loads a world so that one resolver
can read one number. Both get worse as commands are added; this gets longer.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.world_situations import ProgressionTrigger
from app.domain.world_time.advancement import TimeAdvanceReason


class ContextRequest(BaseModel):
    """The entities a command's resolver needs loaded before it can decide anything.

    Ids only. A resolver asks for "situation 3f2a" and gets the situation; it cannot
    ask for "every active situation", because a resolver that can sweep the database is
    a resolver whose cost depends on how long the save has been played.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    situation_ids: tuple[uuid.UUID, ...] = ()
    location_ids: tuple[uuid.UUID, ...] = ()


class _BaseCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_character_id: uuid.UUID | None = None
    """Who is attempting this, when it is anybody. None for the world acting on its
    own -- a fire does not have an actor, and inventing one so the field is never null
    would put the player's id on a collapsing bridge."""

    def context_request(self) -> ContextRequest:
        return ContextRequest()


class AdvanceTimeCommand(_BaseCommand):
    """Move the session clock, resolving whatever comes due on the way.

    A command rather than a direct call to the time service, so that advancing time is
    idempotent, audited and refusable like everything else. The actual advancement is
    still the time service's -- see `ResolutionOutcome.time_advance`, which is a
    *request* the application hands on, never a number the resolver writes.
    """

    kind: Literal["advance_time"] = "advance_time"

    minutes: int = Field(ge=0, le=60 * 24 * 365 * 100)
    """Zero is legal and means "resolve anything already due without moving". The
    ceiling is a century of fictional time: high enough for any authored timeskip, low
    enough that a misplaced decimal is refused rather than fast-forwarding the world
    past the heat death of its own calendar."""

    reason: TimeAdvanceReason
    detail: str = Field(default="", max_length=300)
    interruptible: bool = True


class ProgressSituationCommand(_BaseCommand):
    """Evaluate one ongoing process from where it was last looked at to now.

    The interval is not a parameter. A caller that could choose it could evaluate a
    fire over six hours twice and burn the city down in an afternoon that never
    happened; the application derives it from the situation's own `last_progressed_at`
    and the session clock. See `app.application.situation_service`.
    """

    kind: Literal["progress_situation"] = "progress_situation"

    situation_id: uuid.UUID

    trigger: ProgressionTrigger = ProgressionTrigger.SCHEDULED
    """Why this pass is running. Recorded, and available to a resolver that wants to
    treat a player poking at a fire differently from the fire being checked on a timer.
    Nothing branches on it yet, and nothing should have to in order for it to be
    worth carrying -- it is the difference between "the world moved" and "the world
    moved because somebody made it"."""

    def context_request(self) -> ContextRequest:
        return ContextRequest(situation_ids=(self.situation_id,))

    @model_validator(mode="after")
    def _actor_is_not_the_situation(self) -> Self:
        if self.actor_character_id == self.situation_id:
            raise ValueError("A situation cannot be its own actor.")
        return self


Command = Annotated[
    AdvanceTimeCommand | ProgressSituationCommand,
    Field(discriminator="kind"),
]
"""Discriminated on `kind`, so a malformed command names its own problem.

`kind` is also the resolver registry's key: one command type, one resolver, looked up
by a string rather than by isinstance branching that grows a limb per system.
"""
