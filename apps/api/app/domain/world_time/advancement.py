"""Asking the clock to move, and being told what actually happened.

Time never advances by assignment. Something states how long it wants, and why, and
gets back a record of what the world did with that request. The reason matters
because a world's rules decide who is even allowed to ask.

# The Story Director is not on this list

A language model may narrate that an afternoon slipped by. It may not move the clock.
Every value here is produced by application code and committed by application code;
the model's output has no field that reaches this module, and adding one would make
token sampling the arbiter of how long a journey took. See docs/ai-contract.md.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.errors import TimeProgressionError
from app.domain.world_rules.enums import TimeProgression
from app.domain.world_time.clock import DurationMinutes, ElapsedMinutes


class TimeAdvanceReason(StrEnum):
    """Why the clock is being asked to move.

    A closed vocabulary rather than free text, because `WorldRules` grants permission
    per reason and a string nobody enumerated could not be checked against anything.

    ACTION      a resolved player action consumed fictional time
    REST        sleeping, recovering, waiting on purpose
    TRAVEL      going somewhere that is not instantaneous
    NARRATIVE   an authored timeskip between scenes
    SIMULATION  an autonomous world system asked for time to pass
    DEBUG       developer tooling
    """

    ACTION = "action"
    REST = "rest"
    TRAVEL = "travel"
    NARRATIVE = "narrative"
    SIMULATION = "simulation"
    DEBUG = "debug"


# Which reasons each `simulation.time_progression` setting admits.
#
# PAUSED still allows NARRATIVE and DEBUG: "paused" means the world does not move on
# its own, not that a scenario may never state that a week went by. ACTIVE is the only
# setting under which a simulation system may push the clock forward -- and even there
# it means "while you are playing", never "while the application was closed".
_PERMITTED_REASONS: dict[TimeProgression, frozenset[TimeAdvanceReason]] = {
    TimeProgression.PAUSED: frozenset(
        {TimeAdvanceReason.NARRATIVE, TimeAdvanceReason.DEBUG},
    ),
    TimeProgression.ACTION_BASED: frozenset(
        {
            TimeAdvanceReason.NARRATIVE,
            TimeAdvanceReason.DEBUG,
            TimeAdvanceReason.ACTION,
            TimeAdvanceReason.REST,
            TimeAdvanceReason.TRAVEL,
        },
    ),
    TimeProgression.ACTIVE: frozenset(TimeAdvanceReason),
}


def is_permitted(progression: TimeProgression, reason: TimeAdvanceReason) -> bool:
    return reason in _PERMITTED_REASONS[progression]


def require_permitted(progression: TimeProgression, reason: TimeAdvanceReason) -> None:
    """Raise unless this world lets `reason` move its clock."""
    if not is_permitted(progression, reason):
        allowed = ", ".join(sorted(_PERMITTED_REASONS[progression]))
        raise TimeProgressionError(
            f"This world's time progression is '{progression}', which does not let "
            f"'{reason}' advance the clock. Permitted here: {allowed}."
        )


class TimeAdvanceRequest(BaseModel):
    """A caller's intent, before the world has had its say."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_minutes: DurationMinutes
    """How much fictional time the caller believes this takes.

    Zero is legal and means "nothing took any time" -- still worth asking for, because
    it is also the moment anything already due gets processed. Negative is not legal
    anywhere, at any layer.
    """

    reason: TimeAdvanceReason
    detail: str = Field(default="", max_length=300)
    """Free text for the audit trail: "Riverwood to the Capital". Never parsed."""

    interruptible: bool = True
    """Whether a scheduled event may cut this short.

    Sleeping eight hours is interruptible; an authored "three months later" is not.
    """

    source_event_id: uuid.UUID | None = None
    """The scheduled event that caused this, when one did."""


class Interruption(BaseModel):
    """Why an advance stopped early."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: uuid.UUID
    event_type: str
    at: ElapsedMinutes


class TimeAdvanceResult(BaseModel):
    """What the world did with the request.

    `advanced_minutes` may be less than `requested_minutes` and never more. A future
    duration pipeline -- base duration, terrain, weather, condition -- resolves all of
    that *before* it calls, so the request is always the authoritative ask and an
    interruption is the only thing that can shorten it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_minutes: DurationMinutes
    advanced_minutes: DurationMinutes

    started_at: ElapsedMinutes
    ended_at: ElapsedMinutes

    interrupted: bool = False
    interruption: Interruption | None = None

    processed_event_ids: list[uuid.UUID] = Field(default_factory=list)
    """Scheduled events resolved during this advance, in the order they came due."""

    @model_validator(mode="after")
    def _internally_consistent(self) -> Self:
        """A result that contradicts itself is worse than an error.

        These are cheap and they hold for every code path that can produce one, so
        they are stated here rather than trusted to whoever assembles the object.
        """
        if self.ended_at != self.started_at + self.advanced_minutes:
            raise ValueError(
                f"ended_at ({self.ended_at}) must equal started_at ({self.started_at}) "
                f"plus advanced_minutes ({self.advanced_minutes})."
            )
        if self.advanced_minutes > self.requested_minutes:
            raise ValueError(
                f"advanced_minutes ({self.advanced_minutes}) cannot exceed "
                f"requested_minutes ({self.requested_minutes})."
            )
        if self.interrupted != (self.interruption is not None):
            raise ValueError("'interrupted' and the presence of 'interruption' must agree.")
        return self
