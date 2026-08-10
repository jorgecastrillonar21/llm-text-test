"""What a progression pass decided, before any of it is written.

A resolver is handed a situation and an interval and returns one of these. Nothing in
it has happened yet: it is a proposal from a deterministic system, which the
application then turns into exactly one `StateMutationBatch` and commits atomically --
or does not commit at all.

# Why this is here and not in `world_situations`

Same reason `state_mutations` is here. A result composes a situation's own arithmetic
with `StateMutation` (which spans facts, space and situations) and with GameEvent
descriptors, so it belongs to a module that composes those packages rather than to any
one of them. Putting it inside `world_situations` would make that package import
`state_mutations`, which imports `world_situations` -- a cycle whose failure mode
depends on which module happened to be imported first.

# One batch, or nothing

    siege progresses
        -> intensity +12                  UpdateSituation
        -> EAST_GATE_BREACHED             GameEvent
        -> gate condition = destroyed     UpdateLocationState
        -> crossing impassable            UpdateConnectionState
        -> food crisis begins             StartSituation

Five changes across three domains, one decision. There is no version of that outcome
where the gate falls and the crisis does not, so the whole thing goes through the
existing transaction boundary or none of it does. `state_revision` moves once.

# The situation does not reach outside itself

A resolver may not rewrite arbitrary state. What it can do is say what its own process
did, and emit the specific mutations that follow from it -- through the same typed
mutations everything else uses. There is deliberately no escape hatch here for "run
this SQL", "call this service" or "store this script".
"""

from __future__ import annotations

import uuid
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.state_mutations import StateMutation
from app.domain.world_situations.mutations import StartSituation
from app.domain.world_situations.progression import SituationDeltas
from app.domain.world_situations.situations import SituationStatus

MAX_GENERATED_EVENTS = 8
MAX_SIDE_MUTATIONS = 16
MAX_NEW_SITUATIONS = 4
"""Caps, so a misbehaving resolver produces a refusal rather than a thousand-row batch.

Low on purpose. One evaluation of one process that wants to start five new ones and
write sixteen state changes is not progressing -- it is rewriting the session, and the
right response to that is a loud failure rather than a large commit.
"""


class GeneratedEvent(BaseModel):
    """A GameEvent a progression pass produced.

    Type and description only, the same shape `StateChangeEvent` uses: the session, the
    turn and the fictional timestamp come from the session being changed, and a
    resolver that could set them could write an event into a turn that never happened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)


class SituationProgressionResult(BaseModel):
    """Everything one evaluation decided, as one unit of work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    situation_id: uuid.UUID

    deltas: SituationDeltas = Field(default_factory=SituationDeltas)
    """How the three numbers moved. Deltas rather than values, and clamped when they are
    applied to the value read inside the transaction -- see `world_situations.apply_deltas`."""

    status_change: SituationStatus | None = None
    """Where the lifecycle went, when it went anywhere. Validated against the current
    status by `lifecycle.require_transition` at apply time, not here: this object does
    not know what the status currently is, and a resolver that thought it did would be
    reasoning from a read that predates the transaction."""

    resolution_reason: str = Field(default="", max_length=300)
    """Required by `ResolveSituation` when `status_change` is terminal. Carried here so
    a resolver that ends a process has to say why in the same breath."""

    generated_events: tuple[GeneratedEvent, ...] = ()
    """What happened, in the world's own history. `SIEGE -> EAST_GATE_BREACHED`.

    Events are what drive authoritative mutations everywhere else in this system, and
    a progression that changed the world without recording an event would leave a
    session that cannot answer why its geography changed.
    """

    state_mutations: tuple[StateMutation, ...] = ()
    """Changes to *other* domains that follow from this one. The gate becoming
    destroyed, the crossing closing, a fact being established.

    Typed mutations from the existing union -- there is no second mutation concept for
    situations to use. A resolver that wants to change a location says so with
    `UpdateLocationState`, exactly as every other caller does.
    """

    new_situations: tuple[StartSituation, ...] = ()
    """Processes this one caused. `Siege -> Food Crisis -> Disease Outbreak`.

    `StartSituation` mutations rather than a bespoke proposal type, so the same
    validation, the same id minting and the same atomicity apply. The application links
    them to their parent when it writes them.
    """

    next_progression_at: int | None = Field(default=None, ge=0)
    """Absolute session time for the next evaluation, or None for "nothing scheduled".

    Absolute, never a delay, for the reason `ScheduledEvent.due_at` is. None is the
    ordinary answer for a process that moves only when something happens to it -- there
    is no mandatory cadence here and no situation is required to have one.
    """

    notes: str = Field(default="", max_length=500)
    """Human-facing, for developer tooling and tests. Never rendered into a prompt and
    never persisted as state."""

    @model_validator(mode="after")
    def _within_caps(self) -> Self:
        for label, count, cap in (
            ("generated_events", len(self.generated_events), MAX_GENERATED_EVENTS),
            ("state_mutations", len(self.state_mutations), MAX_SIDE_MUTATIONS),
            ("new_situations", len(self.new_situations), MAX_NEW_SITUATIONS),
        ):
            if count > cap:
                raise ValueError(
                    f"A single progression may produce at most {cap} {label}; got {count}. "
                    "An evaluation that large is rewriting the session rather than "
                    "advancing one process."
                )
        return self

    @model_validator(mode="after")
    def _an_ending_is_explained(self) -> Self:
        """A terminal status change must carry its reason.

        `ResolveSituation` requires one and would refuse the batch later; catching it
        here means the resolver that produced the gap is what fails, rather than the
        application assembling its output.
        """
        ends_it = self.status_change in {SituationStatus.RESOLVED, SituationStatus.CANCELLED}
        if ends_it and not self.resolution_reason.strip():
            raise ValueError(
                f"A progression that ends a situation ({self.status_change}) must say why."
            )
        return self

    def is_noop(self) -> bool:
        """Whether this changed nothing at all.

        A frequent and legitimate outcome: a dormant conspiracy evaluated over six hours
        did nothing, and writing an event and bumping the revision to say so would fill
        a session's history with silence. The caller checks this and skips the batch.
        """
        return (
            self.deltas.is_noop()
            and self.status_change is None
            and not self.generated_events
            and not self.state_mutations
            and not self.new_situations
        )
