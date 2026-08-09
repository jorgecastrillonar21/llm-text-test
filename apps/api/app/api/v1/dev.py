"""Development-only endpoints for the simulation clock.

Registered by `create_app` only when `Settings.dev_endpoints_enabled` says so, which
is a short allowlist of environments rather than "anything that is not production" --
an unrecognised APP_ENV should switch these off, not on.

They exist because nothing in the game moves time yet. `ActionResolutionService`,
`TravelService` and `RestService` are the callers this was built for, and until one of
them exists these two endpoints and the tests are the only way to exercise the clock.

Nothing here is a shortcut. Both go through the same application service any future
caller will use: a paused world still refuses, the never-backward rule still holds,
scheduled events still resolve, and the advance is still recorded in the audit trail.
The only privilege a developer gets is the `debug` reason, which every world accepts.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import SessionClock
from app.api.schemas import ScheduledEventCreate
from app.application.persistence import ScheduledEventRecord
from app.application.time_service import advance_time, cancel_scheduled_event, schedule_event
from app.domain.errors import NotFoundError
from app.domain.world_time import TimeAdvanceRequest, TimeAdvanceResult

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/sessions/{session_id}/advance-time", response_model=TimeAdvanceResult)
async def advance_session_time(
    session_id: uuid.UUID, payload: TimeAdvanceRequest, clock: SessionClock
) -> TimeAdvanceResult:
    """Move a session's clock forward.

    The result reports what actually happened, which may be less than was asked for:
    a scheduled event that interrupts player action stops the advance where it is.
    """
    return await advance_time(clock, session_id=session_id, request=payload)


@router.post(
    "/sessions/{session_id}/scheduled-events",
    response_model=ScheduledEventRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_scheduled_event(
    session_id: uuid.UUID, payload: ScheduledEventCreate, clock: SessionClock
) -> ScheduledEventRecord:
    """Put something on the session's schedule, `delay_minutes` from now."""
    event_id = await schedule_event(
        clock,
        session_id=session_id,
        event_type=payload.type,
        delay_minutes=payload.delay_minutes,
        payload=payload.payload,
        interrupt_player_action=payload.interrupt_player_action,
    )
    # Read back so the response carries the resolved absolute `due_at` rather than
    # the delay that was asked for -- the difference is the whole point of the model.
    stored = await clock.get_scheduled_event(event_id)
    if stored is None:  # pragma: no cover - written and committed a moment ago
        raise NotFoundError("ScheduledEvent", event_id)
    return stored


@router.delete(
    "/scheduled-events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_event(event_id: uuid.UUID, clock: SessionClock) -> None:
    """Call off a pending event. Cancelling one that already fired is a 422."""
    await cancel_scheduled_event(clock, event_id=event_id)
