"""Development-only endpoints for the simulation clock and the world's state.

Registered by `create_app` only when `Settings.dev_endpoints_enabled` says so, which
is a short allowlist of environments rather than "anything that is not production" --
an unrecognised APP_ENV should switch these off, not on.

They exist because nothing in the game moves time or changes state yet.
`ActionResolutionService`, `TravelService` and `RestService` are the callers this was
built for, and until one of them exists these endpoints and the tests are the only way
to exercise either system.

Nothing here is a shortcut. Every one goes through the same application service a real
caller will use: a paused world still refuses to advance, the never-backward rule still
holds, and a state change is still validated against the property's policy, the
world's rules and the session's revision before anything is written. The only
privilege a developer gets is the `debug` reason and the `admin` authority, and even
admin cannot write a derived property or resurrect someone in a world where death is
permanent.

This is emphatically not gameplay CRUD over facts. It is mounted under `/dev`, it is
off by default, and the day a resolution service exists it should be the first thing
reconsidered.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import ProgressionStore, SessionClock, WorldStateStore
from app.api.schemas import (
    ScheduledEventCreate,
    SituationProgressRequest,
    SituationProgressResponse,
    StateChangeRequest,
)
from app.application.persistence import ScheduledEventRecord
from app.application.progression_service import evaluate_and_apply
from app.application.state_service import StateChangeResult, apply_state_change
from app.application.time_service import advance_time, cancel_scheduled_event, schedule_event
from app.domain.errors import NotFoundError
from app.domain.world_facts import FactAuthority
from app.domain.world_situations import SituationProgressionRequest
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
    return await apply_state_change(
        store, session_id=session_id, batch=payload.batch, event=payload.event
    )


@router.post(
    "/sessions/{session_id}/situations/{situation_id}/progress",
    response_model=SituationProgressResponse,
)
async def progress_session_situation(
    session_id: uuid.UUID,
    situation_id: uuid.UUID,
    payload: SituationProgressRequest,
    store: ProgressionStore,
) -> SituationProgressResponse:
    """Bring one situation up to date with the session clock.

    This is the caller the progression boundary was built for and does not have yet.
    A future SimulationEngine will do exactly this, in bulk, when a scheduled evaluation
    comes due; until it exists, this endpoint and the tests are the only things that
    exercise a resolver end to end.

    The interval is not the caller's to choose. It runs from where the situation was
    last evaluated to where the session clock now is, so a progression can only ever
    account for fictional time the session has actually lived through. Advance the clock
    first to make that interval large; see `SituationProgressRequest`.

    Nothing else here is a shortcut either. It goes through `evaluate_and_apply`, which
    runs the registered resolver, assembles one `StateMutationBatch` and commits it
    atomically -- so a world whose rules say it does not move without the player still
    does not move, an invalid transition is still refused, and every value is clamped.
    """
    situation = await store.get_situation(session_id, situation_id)
    if situation is None:
        raise NotFoundError("Situation", situation_id)

    session = await store.get_session(session_id)
    if session is None:  # pragma: no cover - the situation above proves it exists
        raise NotFoundError("GameSession", session_id)

    outcome = await evaluate_and_apply(
        store,
        session_id=session_id,
        request=SituationProgressionRequest(
            situation_id=situation_id,
            from_time=situation.last_progressed_at,
            # `max` rather than a validation error: a situation started after the clock
            # last moved is already up to date, and "evaluate a zero-length interval" is
            # a legitimate no-op rather than a bad request.
            to_time=max(situation.last_progressed_at, session.elapsed_minutes),
            trigger=payload.trigger,
        ),
        # `admin`, not `simulation`: a person asked for this. The authority model is not
        # lifted by the endpoint being development-only -- it is what the endpoint is
        # for testing.
        authority=FactAuthority.ADMIN,
    )

    return SituationProgressResponse(
        situation_id=outcome.situation_id,
        changed=outcome.applied is not None,
        intensity_delta=outcome.result.deltas.intensity_delta,
        threat_delta=outcome.result.deltas.threat_delta,
        momentum_delta=outcome.result.deltas.momentum_delta,
        status_change=outcome.result.status_change,
        state_revision=None if outcome.applied is None else outcome.applied.revision,
        event_id=None if outcome.applied is None else outcome.applied.event_id,
        created_situation_ids=outcome.created_situation_ids,
        scheduled_event_id=outcome.scheduled_event_id,
        next_progression_at=outcome.result.next_progression_at,
        notes=outcome.result.notes,
    )
