"""Moving the simulation clock, and only ever from here.

This is the single door to `game_sessions.elapsed_minutes`. Nothing else writes it:
not the turn service, not a router, and certainly not the story provider. A caller
states how long something took and why, and this decides what the world does with
that -- including refusing, when the world's rules do not let that kind of caller
move time at all.

# Who will call this

    ActionResolutionService   a resolved action consumed time
    TravelService             getting somewhere took a while
    RestService               sleeping through the night
    SimulationService         the world moving on its own
    developer tooling         the /dev endpoints

None of those exist yet, which is the point: the authority boundary is in place
before the systems that will need it, so none of them can grow its own private path
to the clock. Today the only callers are the dev router and the tests.

# Turns do not advance time

Submitting a turn deliberately leaves the clock where it is. Deciding that "I search
the room carefully" costs twelve minutes needs a duration model that does not exist,
and the wrong way to get one is to let the language model pick a number -- that would
make token sampling the arbiter of how long a journey took. See
docs/world-state-time.md.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.application.persistence import (
    NewEvent,
    NewScheduledEvent,
    SessionClockPort,
)
from app.domain.errors import NotFoundError, ValidationError
from app.domain.world_time import (
    Interruption,
    ScheduledEventStatus,
    TimeAdvanceRequest,
    TimeAdvanceResult,
    TimeState,
    require_permitted,
    require_transition,
)

TIME_ADVANCED_EVENT = "time.advanced"
"""GameEvent type for the audit trail. There is no separate audit table: a
GameEvent already carries a session, a turn, a fictional timestamp and a stable
position, which is everything "why did the clock jump?" needs."""


async def advance_time(
    clock: SessionClockPort,
    *,
    session_id: uuid.UUID,
    request: TimeAdvanceRequest,
) -> TimeAdvanceResult:
    """Move a session's clock forward and resolve anything that came due.

    Long skips cost the same as short ones. Advancing six months looks at the events
    scheduled inside that span, not at every minute of it -- there is no per-minute
    loop here and there must never be one.
    """
    session = await clock.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    world = await clock.get_world(session.world_id)
    if world is None:  # FK guarantees this, but the type checker does not.
        raise NotFoundError("World", session.world_id)

    # The world's own rules decide whether this kind of caller may move time.
    require_permitted(world.rules.simulation.time_progression, request.reason)

    started_at = session.elapsed_minutes
    target = TimeState(elapsed_minutes=started_at).advance(request.requested_minutes)

    due = await clock.load_due_scheduled_events(session_id, through=target.elapsed_minutes)

    processed: list[uuid.UUID] = []
    interruption: Interruption | None = None
    ended_at = target.elapsed_minutes

    for event in due:
        # An event whose minute is already behind the clock resolves *now*, not by
        # rewinding to when it was meant to happen. Time never runs backward, so a
        # schedule written into the past is late rather than lost.
        at = max(started_at, event.due_at)

        require_transition(event.status, ScheduledEventStatus.PROCESSED)
        await clock.set_scheduled_event_status(event.id, ScheduledEventStatus.PROCESSED)
        processed.append(event.id)

        if request.interruptible and event.interrupt_player_action:
            # The event itself still happened -- it is what cut the advance short.
            # Everything scheduled after it stays pending for the next advance.
            interruption = Interruption(event_id=event.id, event_type=event.type, at=at)
            ended_at = at
            break

    result = TimeAdvanceResult(
        requested_minutes=request.requested_minutes,
        advanced_minutes=ended_at - started_at,
        started_at=started_at,
        ended_at=ended_at,
        interrupted=interruption is not None,
        interruption=interruption,
        processed_event_ids=processed,
    )

    if result.advanced_minutes:
        await clock.set_elapsed_minutes(session_id, ended_at)

    # A request that neither moved the clock nor resolved anything is not worth a row.
    if result.advanced_minutes or processed:
        await clock.add_event(
            NewEvent(
                session_id=session_id,
                turn_index=session.turn_index,
                occurred_at=ended_at,
                type=TIME_ADVANCED_EVENT,
                description=_describe(request, result),
            )
        )

    await clock.commit()
    return result


async def schedule_event(
    clock: SessionClockPort,
    *,
    session_id: uuid.UUID,
    event_type: str,
    delay_minutes: int,
    payload: dict[str, Any] | None = None,
    interrupt_player_action: bool = False,
) -> uuid.UUID:
    """Note that something is due `delay_minutes` of fictional time from now.

    The delay is converted here and thrown away; what gets stored is an absolute
    position on the session clock. A row saying "in three days" would mean something
    different every time it was read, which is why this function takes the delay and
    the port takes `due_at`.

    Nothing in the game schedules anything yet. This is the infrastructure a shop
    closing, a caravan arriving or a deadline passing will use, deliberately built
    before any of those.
    """
    if delay_minutes < 0:
        raise ValidationError(
            f"A scheduled event cannot be due in the past: delay {delay_minutes} minutes."
        )

    session = await clock.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    event_id = await clock.add_scheduled_event(
        NewScheduledEvent(
            session_id=session_id,
            due_at=session.elapsed_minutes + delay_minutes,
            type=event_type,
            payload=payload or {},
            interrupt_player_action=interrupt_player_action,
        )
    )
    await clock.commit()
    return event_id


async def cancel_scheduled_event(clock: SessionClockPort, *, event_id: uuid.UUID) -> None:
    """Call off something that has not happened yet.

    Cancelling an event that already fired is refused rather than ignored: the
    fictional moment it describes is part of the story now.
    """
    event = await clock.get_scheduled_event(event_id)
    if event is None:
        raise NotFoundError("ScheduledEvent", event_id)

    require_transition(event.status, ScheduledEventStatus.CANCELLED)
    await clock.set_scheduled_event_status(event_id, ScheduledEventStatus.CANCELLED)
    await clock.commit()


def _describe(request: TimeAdvanceRequest, result: TimeAdvanceResult) -> str:
    """The audit line: enough to answer "why did the clock jump?" without a join."""
    parts = [
        f"{request.reason}: requested {request.requested_minutes} min",
        f"advanced {result.advanced_minutes} min",
        f"{result.started_at} -> {result.ended_at}",
    ]
    if result.interruption is not None:
        parts.append(f"interrupted by {result.interruption.event_type}")
    if result.processed_event_ids:
        count = len(result.processed_event_ids)
        parts.append(f"{count} scheduled event{'' if count == 1 else 's'} processed")
    if request.source_event_id is not None:
        parts.append(f"source event {request.source_event_id}")
    if request.detail:
        parts.append(request.detail)
    return " | ".join(parts)
