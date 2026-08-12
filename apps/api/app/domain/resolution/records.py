"""The mechanical trail: what was resolved, by what, against which state.

One row per resolution attempt that reached a verdict. This is the audit log the
project has -- deliberately, so that `game_events` does not have to be one. The two
answer different questions and a reader can tell them apart:

    resolutions   why is the world in this state, mechanically
    game_events   what has happened in this story
    messages      what was said

A player drinking water leaves a `ResolutionRecord`, possibly a mutation, and no
GameEvent. A player killing a king leaves all three.

# What is not here

The narration. `resolution_id` on a message points the other way, so prose can be
traced back to the outcome it describes without the outcome carrying a copy of the
prose. Duplicating it would give the same sentence two homes and, eventually, two
different versions of itself.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.resolution.enums import ResolutionDisposition, ResolutionSourceType

MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_RESOLVER_NAME_LENGTH = 80
MAX_RESOLVER_VERSION_LENGTH = 20


class Resolution(BaseModel):
    """A committed resolution, as the rest of the application reads it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    session_id: uuid.UUID

    source_type: ResolutionSourceType
    source_id: uuid.UUID | None = None
    """What specifically asked: the scheduled event, the situation, the character.
    Untyped on purpose -- it points into whichever table `source_type` names, and a
    column constrained to one of them would be wrong for all the others."""

    parent_resolution_id: uuid.UUID | None = None
    """The resolution this one is part of.

    Nothing produces children yet. It is here because the shape of a compound action --
    "I kick the guard, take the keys, and run" -- is three sub-resolutions under one
    parent, and the alternative is one giant atomic resolution in which failing to open
    the door un-kicks the guard. Retrofitting parentage means rewriting whatever was
    built assuming it did not exist.
    """

    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)
    """Unique per session. Retrying with the same key returns this record instead of
    resolving again -- see `app.application.resolution_service`."""

    disposition: ResolutionDisposition
    reason_code: str | None = None

    resolver_name: str = Field(min_length=1, max_length=MAX_RESOLVER_NAME_LENGTH)
    resolver_version: str = Field(min_length=1, max_length=MAX_RESOLVER_VERSION_LENGTH)
    """Which formula produced this, and which revision of it.

    Audit, not dispatch: nothing looks a resolver up by the recorded version, and there
    is no historical resolver runtime. It exists so that when the siege formula changes
    in a year, an outcome recorded today can still be explained rather than looking
    like a bug in the current one.
    """

    state_revision_before: int = Field(ge=0)
    state_revision_after: int = Field(ge=0)

    occurred_at: int = Field(ge=0)
    """Fictional session time, in elapsed minutes. Not `created_at`."""

    turn_index: int | None = Field(default=None, ge=0)
    """The exchange this belongs to, when it belongs to one. None for a resolution
    nobody was present for -- an offscreen simulation pass has a fictional minute but
    no turn. Carried for the same reason `game_events` carries it: "which exchange" and
    "when in the story" are different questions, and a long action makes them diverge."""

    event_count: int = Field(ge=0)
    mutation_count: int = Field(ge=0)

    created_at: dt.datetime
    """Wall clock. Technical audit only -- never fictional ordering. A save restored on
    a different machine has honest `created_at` values and the same story."""

    @model_validator(mode="after")
    def _the_record_agrees_with_itself(self) -> Self:
        """Invariants that hold on every path that can produce one of these.

        Stated here rather than trusted to the service, because this record is the
        evidence. A row claiming a rejection that bumped the revision would make the
        whole trail unreadable, and it is exactly the shape a bug would take.
        """
        if self.state_revision_after < self.state_revision_before:
            raise ValueError(
                f"state_revision_after ({self.state_revision_after}) cannot be lower than "
                f"state_revision_before ({self.state_revision_before}). The revision is "
                "monotonic."
            )
        if self.disposition is ResolutionDisposition.APPLIED:
            return self

        if self.state_revision_after != self.state_revision_before:
            raise ValueError(
                f"A {self.disposition.value!r} resolution changed the state revision "
                f"({self.state_revision_before} -> {self.state_revision_after}). "
                "Nothing was applied, so nothing may have moved."
            )
        if self.event_count or self.mutation_count:
            raise ValueError(
                f"A {self.disposition.value!r} resolution recorded "
                f"{self.event_count} event(s) and {self.mutation_count} mutation(s). "
                "Neither is possible without something having been applied."
            )
        if self.disposition is ResolutionDisposition.REJECTED and not self.reason_code:
            raise ValueError("A rejected resolution must carry a reason code.")
        return self

    @property
    def changed_state(self) -> bool:
        return self.state_revision_after != self.state_revision_before
