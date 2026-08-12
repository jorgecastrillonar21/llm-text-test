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

from fastapi import APIRouter, Query, status

from app.api.deps import (
    LlmMetricsReader,
    ResolutionStore,
    SessionClock,
    WorldStateReader,
    WorldStateStore,
)
from app.api.schemas import (
    LlmPerformanceResponse,
    ResolutionResponse,
    ScheduledEventCreate,
    SituationProgressRequest,
    StateChangeRequest,
)
from app.application.llm_metrics import summarize_generations
from app.application.persistence import ScheduledEventRecord
from app.application.resolution_service import ResolutionRequest, resolve
from app.application.state_consistency import ConsistencyReport, check_state_consistency
from app.application.state_service import StateChangeResult, apply_state_change
from app.application.time_service import (
    advance_time,
    cancel_scheduled_event,
    load_due_work,
    schedule_event,
)
from app.application.world_state_service import (
    CurrentWorldSnapshot,
    SnapshotScope,
    build_snapshot,
)
from app.domain.errors import NotFoundError
from app.domain.resolution import ProgressSituationCommand, ResolutionSourceType
from app.domain.world_time import TimeAdvanceRequest, TimeAdvanceResult

router = APIRouter(prefix="/dev", tags=["dev"])

LLM_PERFORMANCE_PAGE_LIMIT = 50
"""Bound on the diagnostics endpoints. Smaller than the buffer, because a response is
something a human reads and fifty records already covers a long play session."""


@router.post("/sessions/{session_id}/advance-time", response_model=TimeAdvanceResult)
async def advance_session_time(
    session_id: uuid.UUID, payload: TimeAdvanceRequest, clock: SessionClock
) -> TimeAdvanceResult:
    """Move a session's clock forward.

    The result reports what actually happened, which may be less than was asked for:
    a scheduled event that interrupts player action stops the advance where it is.

    `due_event_ids` names the scheduled work the clock reached. It is *reached*, not run
    -- nothing here executes a scheduled event, and the ids stay answerable through
    `GET .../scheduled-events/due` until something does.
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


@router.get(
    "/sessions/{session_id}/scheduled-events/due",
    response_model=list[ScheduledEventRecord],
)
async def read_due_work(session_id: uuid.UUID, clock: SessionClock) -> list[ScheduledEventRecord]:
    """Scheduled work the clock has reached and nobody has executed.

    The dispatcher's read, exposed because no dispatcher exists yet. Advancing time no
    longer consumes these -- it marks them due and leaves them here, which is the only
    honest thing Time can do with a `caravan.arrives` it has no way to bring about.

    A list that keeps growing is the symptom to look for: it means something is being
    scheduled that nothing owns. Answering an entry means executing its work through
    whatever service owns that event type and acknowledging it in the same transaction;
    `situation.progress` does that through the progression endpoint below.
    """
    return await load_due_work(clock, session_id=session_id)


@router.delete(
    "/scheduled-events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_event(event_id: uuid.UUID, clock: SessionClock) -> None:
    """Call off an event that will not happen after all.

    Works on pending and due events alike: deciding that work the clock reached is moot
    is a legitimate verdict, and a different one from having carried it out. Cancelling
    something already processed or already cancelled is a 422.
    """
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


@router.get("/sessions/{session_id}/world-state", response_model=CurrentWorldSnapshot)
async def read_full_world_state(
    session_id: uuid.UUID, reader: WorldStateReader
) -> CurrentWorldSnapshot:
    """Everything this session's world currently is, for a person looking at it.

    The `full_debug` scope the public endpoint refuses: every fact, every place and its
    state, every connection and its state, every situation, the whole schedule and the
    recent event trail. Bounded, but bounded high -- this is the view you want when
    something is wrong and you do not yet know where.

    Emphatically not a context source. Nothing that builds a prompt calls this, and
    nothing should: `story_context` decides what a language model sees, and the amount
    of state available here has never been an argument for sending more of it.

    Read-only, like every snapshot. There is no development endpoint that writes a
    world back, because a world that could be uploaded is a world with no invariants.
    """
    return await build_snapshot(reader, session_id=session_id, scope=SnapshotScope.FULL_DEBUG)


@router.get("/sessions/{session_id}/world-state/check", response_model=ConsistencyReport)
async def check_world_state(session_id: uuid.UUID, reader: WorldStateReader) -> ConsistencyReport:
    """Does this session's world still hang together?

    Runs every referential check in `app.application.state_consistency` and reports what
    it found. A diagnostic, not a gate: nothing in the turn loop waits on this, and a
    report of issues is a thing for a person to read rather than something the
    application acts on.

    Always 200 when the session exists, even when the world is inconsistent -- the
    report *is* the answer, and turning a finding into an HTTP error would make the
    interesting case the one a client cannot read.
    """
    return await check_state_consistency(reader, session_id=session_id)


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

    # Answering due scheduled work

    This is the owner seam for `situation.progress`. A time advance that walks past one
    marks it DUE and stops there -- Time cannot progress a siege. Pass
    `completes_scheduled_event_id` to act as the dispatcher: the progression runs, its
    mutations are staged, and only then is the event marked PROCESSED, all inside one
    transaction. Without it the progression still happens and the scheduled event stays
    due, which is the right answer for a person poking at a situation by hand.
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
            completes_scheduled_event_id=payload.completes_scheduled_event_id,
        ),
    )

    return ResolutionResponse(
        resolution=result.resolution,
        events=result.events,
        replayed=result.replayed,
        created_situation_ids=result.created_situation_ids,
        scheduled_event_ids=result.scheduled_event_ids,
        completed_scheduled_event_id=result.completed_scheduled_event_id,
        narrative_context=dict(result.outcome.narrative_context) if result.outcome else {},
    )


@router.get("/llm/performance", response_model=LlmPerformanceResponse)
async def llm_performance(
    metrics: LlmMetricsReader,
    limit: int = Query(default=LLM_PERFORMANCE_PAGE_LIMIT, ge=1, le=LLM_PERFORMANCE_PAGE_LIMIT),
) -> LlmPerformanceResponse:
    """Recent LLM generations across the process, most recent first.

    The answer to "why was that turn slow", without a debugger and without a metrics
    platform. Read `total_ms` against `load_ms`: a large `load_ms` means the model was
    not resident and the next call will be far faster. Read `prompt_tokens` against
    `configured_context_window` -- if `prompt_context_utilization` is climbing toward 1.0
    across a session, the prompt is growing faster than it should be and the retrieval
    caps in `context_builder` are the place to look. Read `output_budget_reached`: for a
    schema-constrained turn that means truncated JSON, not a shorter story.

    In-process and bounded, so it resets on restart and holds only the last
    `LLM_METRICS_BUFFER_SIZE` records. That is the intended lifetime -- this is a
    developer's recent history, not a metrics store.

    Everything here is a technical record. Nothing in it is part of any world's state,
    nothing here moved a `state_revision`, and none of it is ever fed back to a model.
    """
    records = metrics.recent_generations(limit=limit)
    return LlmPerformanceResponse(
        summary=summarize_generations(records),
        generations=records,
        turns=metrics.recent_turns(limit=limit),
    )


@router.get("/sessions/{session_id}/llm-performance", response_model=LlmPerformanceResponse)
async def session_llm_performance(
    session_id: uuid.UUID,
    metrics: LlmMetricsReader,
    limit: int = Query(default=LLM_PERFORMANCE_PAGE_LIMIT, ge=1, le=LLM_PERFORMANCE_PAGE_LIMIT),
) -> LlmPerformanceResponse:
    """The same records, narrowed to one save.

    The more useful of the two in practice: prompt growth is a per-session property --
    it tracks that session's history, geography and facts -- and a process-wide view
    mixes three campaigns together and hides it.

    Not validated against the session table on purpose. This reads a buffer, not the
    database; an unknown or deleted session simply has no records, and a 404 here would
    mean "nothing generated yet" as often as it meant "no such session".
    """
    records = metrics.recent_generations(limit=limit, session_id=session_id)
    return LlmPerformanceResponse(
        summary=summarize_generations(records),
        generations=records,
        turns=metrics.recent_turns(limit=limit, session_id=session_id),
        session_id=session_id,
    )
