"""Writing history, and only ever from here.

This is the single door to `game_events`. Everything that wants to record something
goes through `record_events`, which means every write is checked against the same
policy: is this worth remembering, and how much?

    candidate  ->  policy lookup  ->  importance clamp  ->  duplicate check  ->  row
                        |
                   or: dropped

The alternative -- each caller writing its own rows -- is how a history table becomes a
log. It never happens in one commit; it happens because one subsystem had a good reason
to record something small, and then every subsystem did.

# Who proposes events

    resolvers        mechanical outcomes: a gate breached, a character dead
    the seed         one row saying a session began with established truth
    Story Director   what the narration claims happened

The third is why clamping exists. A language model asked to rate importance will rate
a spilled drink 5 and a coronation 5, because both were dramatic in the paragraph it
just wrote. The policy decides; the proposer only ever suggests.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from app.application.persistence import EventWriterPort, NewEvent
from app.domain.resolution import (
    EventCandidate,
    EventPersistence,
    EventPolicy,
    policy_for,
)

logger = logging.getLogger(__name__)


class ReviewedEvent(BaseModel):
    """A candidate with the policy's verdict attached, before anything is written."""

    model_config = ConfigDict(frozen=True)

    candidate: EventCandidate
    policy: EventPolicy
    importance: int
    """After clamping. What will actually be stored, whatever the proposer asked for."""

    @property
    def persists(self) -> bool:
        return self.policy.persists

    @property
    def is_landmark(self) -> bool:
        return self.policy.persistence is EventPersistence.LANDMARK


def review(candidate: EventCandidate) -> ReviewedEvent:
    """Apply policy to one candidate. Pure, so the decision is testable on its own."""
    policy = policy_for(candidate.subtype)
    return ReviewedEvent(
        candidate=candidate,
        policy=policy,
        importance=policy.importance_for(candidate.importance),
    )


async def select_events(
    store: EventWriterPort,
    *,
    session_id: uuid.UUID,
    occurred_at: int,
    candidates: Sequence[EventCandidate],
) -> list[ReviewedEvent]:
    """Decide which candidates history keeps, without writing any of them.

    Reads only. Split out from `write_events` for one caller: a resolution has to record
    how many events it produced *in the row the events point at*, and that row has to
    exist before them because they carry its foreign key. Counting first and writing
    second is what lets `resolutions.event_count` be the truth rather than an estimate
    that a deduplicated candidate quietly falsifies.
    """
    selected: list[ReviewedEvent] = []
    for candidate in candidates:
        reviewed = review(candidate)
        if not reviewed.persists:
            # Not a failure. `door_opened` is supposed to land here.
            logger.debug(
                "Event %r is not persisted as history (policy: %s).",
                candidate.subtype,
                reviewed.policy.persistence.value,
            )
            continue

        if await _is_a_repeat(store, session_id=session_id, at=occurred_at, reviewed=reviewed):
            logger.debug(
                "Event %r collapsed into a recent one (%s-minute window).",
                candidate.subtype,
                reviewed.policy.dedupe_window_minutes,
            )
            continue

        selected.append(reviewed)
    return selected


async def write_events(
    store: EventWriterPort,
    *,
    session_id: uuid.UUID,
    turn_index: int,
    occurred_at: int,
    reviewed: Sequence[ReviewedEvent],
    resolution_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Write already-reviewed events, in the order they were proposed.

    Staged, never committed. Events belong to the transaction of whatever caused them,
    so a failed resolution takes its history with it.
    """
    return [
        await store.add_event(
            NewEvent(
                session_id=session_id,
                resolution_id=resolution_id,
                turn_index=turn_index,
                occurred_at=occurred_at,
                category=entry.candidate.category,
                subtype=entry.candidate.subtype,
                summary=entry.candidate.summary,
                importance=entry.importance,
                primary_location_id=entry.candidate.primary_location_id,
                caused_by_event_id=entry.candidate.caused_by_event_id,
                payload=entry.candidate.payload,
            )
        )
        for entry in reviewed
    ]


async def record_events(
    store: EventWriterPort,
    *,
    session_id: uuid.UUID,
    turn_index: int,
    occurred_at: int,
    candidates: Sequence[EventCandidate],
    resolution_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Select and write in one call, for callers that do not need the count first.

    Returns the ids of what was written -- which may be fewer than were offered, and
    may be none. A caller that needs to know *which* candidate became which id should
    not: the only caller that needs an id at all wants the first one, as the cause of
    the state change it is about to make.
    """
    selected = await select_events(
        store, session_id=session_id, occurred_at=occurred_at, candidates=candidates
    )
    return await write_events(
        store,
        session_id=session_id,
        turn_index=turn_index,
        occurred_at=occurred_at,
        reviewed=selected,
        resolution_id=resolution_id,
    )


async def _is_a_repeat(
    store: EventWriterPort, *, session_id: uuid.UUID, at: int, reviewed: ReviewedEvent
) -> bool:
    """Whether an equivalent event was already recorded inside the policy's window.

    Only ever asked when a policy opted in. Deduplication is per subtype and per place:
    two guards raising the alarm in the same room within the hour is noise, and two
    fires starting in two districts is not, and nothing here can tell the difference
    except by refusing to guess.
    """
    window = reviewed.policy.dedupe_window_minutes
    if window is None:
        return False
    existing = await store.count_events_since(
        session_id,
        subtype=reviewed.candidate.subtype,
        since=max(0, at - window),
        primary_location_id=reviewed.candidate.primary_location_id,
    )
    return existing > 0
