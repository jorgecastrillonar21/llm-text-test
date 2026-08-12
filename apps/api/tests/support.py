"""Helpers shared by tests that have to say *why* a state change happened.

`stage_state_change` refuses a batch from an accountable authority that cannot name
its cause, which is the point of `requires_source_event`. Tests therefore need a real
cause, and a real one means a row that exists: `world_facts.source_event_id` is a
foreign key, so a plausible-looking UUID would fail at flush rather than at the
assertion, and the failure would be about the fixture rather than about the test.
"""

from __future__ import annotations

import uuid

from app.application.event_service import record_events
from app.application.persistence import EventWriterPort
from app.application.state_service import ChangeCause
from app.domain.resolution import EventCandidate, EventCategory

TEST_EVENT_SUBTYPE = "test_change"
"""Unregistered on purpose, so it gets `DEFAULT_POLICY` -- persisted, importance
capped below landmark. A test that needs a specific policy should name a subtype that
has one."""


async def cause_from_event(
    store: EventWriterPort,
    session_id: uuid.UUID,
    *,
    subtype: str = TEST_EVENT_SUBTYPE,
    summary: str = "A test made this happen.",
    occurred_at: int = 0,
    turn_index: int = 0,
) -> ChangeCause:
    """Record one event and return a cause pointing at it.

    Returns a cause with no `event_id` when policy declined to persist the subtype --
    which is a legitimate outcome, not a failure, and a caller that passes a `NONE`
    subtype is asking for a cause that is a resolution and not a happening.
    """
    written = await record_events(
        store,
        session_id=session_id,
        turn_index=turn_index,
        occurred_at=occurred_at,
        candidates=[
            EventCandidate(
                category=EventCategory.SYSTEM,
                subtype=subtype,
                summary=summary,
            )
        ],
    )
    return ChangeCause(event_id=written[0] if written else None)


def cause_from_resolution() -> ChangeCause:
    """A cause that is a mechanical verdict and nothing history records.

    For tests about batches that change the world without anything happening worth
    remembering -- an intensity nudged, a value clamped. The id is not a real row and
    does not need to be: `stage_state_change` stores only `event_id`, and a resolution
    id is checked for presence rather than followed.
    """
    return ChangeCause(resolution_id=uuid.uuid4())
