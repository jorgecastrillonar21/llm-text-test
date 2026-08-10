"""Asking a process what it did over an interval.

    Situation + elapsed interval + WorldRules + participants + places + RNG
                                 ↓
                     SituationProgressionService
                                 ↓
                     SituationProgressionResult

This module owns the *question* and the arithmetic. The answer -- which composes
mutations and events from other domains -- lives in `app.domain.situation_progression`,
one level up, for the same reason `state_mutations` does: a module that composes two
packages must belong to neither.

# There is no tick

Nothing here loops over minutes, and nothing may. A fire is evaluated in minutes, a
siege in hours, a war in days, a political transition in weeks; a per-minute sweep
would do the same work for all four and be wrong about three of them. Progression is
asked for over an interval -- `from_time` to `to_time` -- and how often that happens is
decided by scheduling, by events, or by a person, never by the clock passing.

That is also why there is no `tick_interval_minutes` on `Situation`. A process that
only moves when something happens to it should have nothing to schedule.

# Randomness

Uncertain progression must come from a seeded game RNG. There is no game RNG in this
project yet, so nothing here is stochastic: `elapsed` in, deltas out, same answer every
time. `random`, wall-clock time and model sampling temperature are all disqualified --
the first two make a replay diverge, and the third makes token sampling the arbiter of
whether a city starves.
"""

from __future__ import annotations

import uuid
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.world_situations.enums import ProgressionTrigger, SituationStatus
from app.domain.world_situations.situations import (
    Situation,
    clamp_intensity,
    clamp_momentum,
    clamp_threat,
)


class SituationProgressionRequest(BaseModel):
    """Evaluate one situation over one interval of fictional time.

    `from_time` and `to_time` are absolute positions on the session clock, not a
    duration, for the same reason `ScheduledEvent.due_at` is: a request that said "six
    hours" would mean something different depending on when it was read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    situation_id: uuid.UUID
    from_time: int = Field(ge=0)
    to_time: int = Field(ge=0)
    trigger: ProgressionTrigger

    @model_validator(mode="after")
    def _interval_runs_forward(self) -> Self:
        """A zero-length interval is allowed; a backward one is not.

        Zero is legitimate -- an event-triggered evaluation happens at an instant, and
        `EVENT` is exactly that case. Backward is always a bug, and a resolver handed
        `elapsed = -240` would produce a situation that un-happened for four hours.
        """
        if self.to_time < self.from_time:
            raise ValueError(
                f"A progression interval cannot run backwards: {self.from_time} -> "
                f"{self.to_time}. Fictional time only moves one way."
            )
        return self

    @property
    def elapsed_minutes(self) -> int:
        return self.to_time - self.from_time


class SituationDeltas(BaseModel):
    """What an interval did to the three numbers, before anything is written.

    Deltas rather than values, so a resolver never has to have read the current state
    correctly. The authoritative value is read at apply time, inside the transaction;
    see `apply_deltas`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    intensity_delta: int = 0
    threat_delta: int = 0
    momentum_delta: int = 0

    def is_noop(self) -> bool:
        return not (self.intensity_delta or self.threat_delta or self.momentum_delta)


def apply_deltas(situation: Situation, deltas: SituationDeltas) -> tuple[int, int, int]:
    """Current values plus deltas, clamped. Returns `(intensity, threat, momentum)`.

    Clamping happens here, once, rather than in every caller: a resolver that returns
    `+80` for a situation already at 60 is not wrong, it is saying "as hard as it can",
    and 140 must never reach a column.
    """
    return (
        clamp_intensity(situation.intensity + deltas.intensity_delta),
        clamp_threat(situation.threat + deltas.threat_delta),
        clamp_momentum(situation.momentum + deltas.momentum_delta),
    )


def momentum_drift(momentum: int, elapsed_minutes: int, *, per_hour: int) -> int:
    """How much intensity a process gains or loses by simply continuing.

    The one piece of shared arithmetic every generic resolver wants, and the reason it
    is a function rather than a policy object: it is `momentum * hours * rate`, rounded
    towards zero, and dressing that up would hide how little it claims to model.

    Deliberately symmetric. Positive momentum grows intensity and negative shrinks it,
    which is how a fire brigade containing a blaze and a festival winding down are the
    same arithmetic with a different sign. Nothing in this system makes unattended
    processes drift towards catastrophe -- the world is allowed to solve its own
    problems, and a model that only ever escalated would make that impossible.
    """
    if elapsed_minutes <= 0 or momentum == 0 or per_hour == 0:
        return 0
    hours = elapsed_minutes / 60
    return int(momentum * hours * per_hour / 100)


def next_status_after(current: SituationStatus, intensity: int) -> SituationStatus | None:
    """The status an interval implies, or None to leave it alone.

    The only automatic transition this project makes: an active process whose intensity
    has fallen to nothing has stopped manifesting, and stays real but stops moving.
    Notably it becomes `DORMANT` and not `RESOLVED` -- a fire that burns out has ended,
    but a strike that goes quiet has not, and nothing at this level can tell the two
    apart. Deciding a process actually *concluded* is a judgement about what it was
    for, and belongs to a resolver that knows.
    """
    if current is SituationStatus.ACTIVE and intensity <= 0:
        return SituationStatus.DORMANT
    return None
