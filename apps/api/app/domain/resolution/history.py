"""Something that objectively happened, and is not going to stop having happened.

A `GameEvent` is world history. It is immutable during normal gameplay: if the bridge
collapsed and is later repaired, that is a second event, not an edit to the first. A
history you can rewrite is not a history -- it is current state with extra steps, and
every question worth asking of it ("why is the valley cut off?") stops being
answerable.

# Ordering

    occurred_at   fictional session time, in elapsed minutes
    sequence      monotonic per session

Ties on `occurred_at` are the normal case, not the exception: a resolution usually
produces everything it produces in the same fictional minute. So ordering needs a
second key, and it is a counter rather than invented seconds -- a clock that only has
minutes should not grow a seconds field to satisfy a sort.

`created_at` is wall-clock and never participates in fictional ordering. Two saves of
the same story restored on different machines have different `created_at` values and
the same history.

# Promotion, and why there is none

A stranger hands the player a strange coin on day two. On day forty it turns out to
belong to the missing heir. The coin event is not rewritten to importance 5 -- what
happened on day two is still exactly what happened on day two. What is recorded is a
new event referring to the old one. Retrieval may then treat both as relevant, which
is the actual goal, and history stays append-only.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.domain.resolution.enums import EventCategory
from app.domain.resolution.events import MAX_IMPORTANCE, MIN_IMPORTANCE
from app.domain.vocabulary import MetadataValue


class GameEvent(BaseModel):
    """One persisted historical fact, as the rest of the application reads it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    session_id: uuid.UUID

    resolution_id: uuid.UUID | None = None
    """The resolution that produced this. Groups the events of one outcome: a siege
    pass that breached a gate and started a famine wrote both under one id.

    Nullable because history predates this boundary and because a session's seeding is
    not a resolution."""

    turn_index: int = Field(ge=0)

    category: EventCategory
    subtype: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    """For people and models to read. Never parsed for mechanics -- `payload` is where
    anything a machine acts on lives, and keeping that boundary is what stops a
    rephrased sentence from changing the game."""

    occurred_at: int = Field(ge=0)
    sequence: int = Field(ge=1)

    importance: int = Field(ge=MIN_IMPORTANCE, le=MAX_IMPORTANCE)
    """Already clamped by policy. Whatever the proposer wanted, this is what the
    application decided, and it is the only number retrieval sees."""

    primary_location_id: uuid.UUID | None = None
    caused_by_event_id: uuid.UUID | None = None
    """Causal history across resolutions: FIRE_STARTED -> FIRE_SPREAD -> COLLAPSE.

    One link, not a graph. A full relation graph is a system with its own semantics and
    its own query surface, and it is not needed to answer "what led to this?"."""

    payload: dict[str, MetadataValue] = Field(default_factory=dict)

    created_at: dt.datetime
    """Wall clock. Technical audit only; see the module docstring."""

    def key(self) -> tuple[int, int]:
        """Total fictional order within a session: minute, then sequence."""
        return (self.occurred_at, self.sequence)
