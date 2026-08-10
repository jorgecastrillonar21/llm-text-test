"""Development-only endpoints for the simulation clock and the world's state.

Registered by `create_app` only when `Settings.dev_endpoints_enabled` says so, which
is a short allowlist of environments rather than "anything that is not production" --
an unrecognised APP_ENV should switch these off, not on.

They exist because the game itself drives almost none of this yet: no travel, no rest,
no simulation engine. Until those callers exist, these endpoints and the tests are the
only things that move a clock or run a resolver.

Nothing here is a shortcut. Every one goes through the same application service a real
caller will use -- the progression endpoint through the resolution pipeline itself, so
it leaves a `ResolutionRecord` like any other. A paused world still refuses to advance,
the never-backward rule still holds, and a state change is still validated against the
property's policy, the world's rules and the session's revision before anything is
written. The only privilege a developer gets is the `debug` reason and the `admin`
authority, and even admin cannot write a derived property or resurrect someone in a
world where death is permanent.

This is emphatically not gameplay CRUD over facts. It is mounted under `/dev` and it is
off by default. The direct state-change endpoint is the one left to reconsider: it
bypasses the pipeline by design, because a person editing the world by hand is not a
resolution and pretending otherwise would put a fiction in the audit trail.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import ResolutionStore, SessionClock, WorldStateStore
from app.api.schemas import (
    ResolutionResponse,
    ScheduledEventCreate,
    SituationProgressRequest,
    StateChangeRequest,
)
from app.application.persistence import ScheduledEventRecord
from app.application.resolution_service import ResolutionRequest, resolve
from app.application.state_service import StateChangeResult, apply_state_change
from app.application.time_service import advance_time, cancel_scheduled_event, schedule_event
from app.domain.errors import NotFoundError
from app.domain.resolution import ProgressSituationCommand, ResolutionSourceType
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


@router.post("/sessions/{session_id}/world-state/changes", response_model=StateChangeResult)
async def apply_world_state_change(
    session_id: uuid.UUID, payload: StateChangeRequest, store: WorldStateStore
) -> StateChangeResult:
    """Apply one batch of state mutations, atomically.

    Straight through `apply_state_change`, which is the only door to the fact store
    and stays the only one: this handler decides nothing. Refusals come back as they
    are -- 422 for a policy or rules violation, 404 for a subject that does not exist,
    409 when `expected_revision` no longer matches.

    Sending `authority: "story_director"` here is allowed and behaves exactly as it
    does in a turn: open properties only. The endpoint being development-only does not
    lift the authority model, because the authority model is the feature.
    """
    return await apply_state_change(store, session_id=session_id, batch=payload.batch)


@router.post(
    "/sessions/{session_id}/situations/{situation_id}/progress",
    response_model=ResolutionResponse,
)
async def progress_session_situation(
    session_id: uuid.UUID,
    situation_id: uuid.UUID,
    payload: SituationProgressRequest,
    store: ResolutionStore,
) -> ResolutionResponse:
    """Bring one situation up to date with the session clock.

    A `ProgressSituationCommand` through the resolution pipeline -- the same path a
    future SimulationEngine will take when a scheduled evaluation comes due. Until that
    exists, this endpoint and the tests are the only things that drive a resolver end to
    end.

    The interval is not the caller's to choose. The resolver derives it from the
    situation's own `last_progressed_at` and the session clock, so a progression can only
    ever account for fictional time the session has actually lived through. Advance the
    clock first to make that interval large; see `SituationProgressRequest`.

    Nothing here is a shortcut. A world whose rules say it does not move without the
    player still does not move, an invalid transition is still refused, every value is
    still clamped, and the whole thing lands in one transaction with a `ResolutionRecord`
    saying who asked.

    The idempotency key pins the request to a situation at a fictional minute, so calling
    this twice without moving the clock replays the first resolution rather than
    evaluating the same interval again. That is the honest behaviour: the second call is
    asking about an interval that has already been resolved.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    result = await resolve(
        store,
        request=ResolutionRequest(
            session_id=session_id,
            command=ProgressSituationCommand(situation_id=situation_id, trigger=payload.trigger),
            idempotency_key=f"dev:progress:{situation_id}:{session.elapsed_minutes}",
            # `admin`, not `simulation`: a person asked for this. The authority model is
            # not lifted by the endpoint being development-only -- it is what the
            # endpoint is for testing.
            source_type=ResolutionSourceType.ADMIN,
            source_id=situation_id,
        ),
    )

    return ResolutionResponse(
        resolution=result.resolution,
        events=result.events,
        replayed=result.replayed,
        created_situation_ids=result.created_situation_ids,
        scheduled_event_ids=result.scheduled_event_ids,
        narrative_context=dict(result.outcome.narrative_context) if result.outcome else {},
    )
