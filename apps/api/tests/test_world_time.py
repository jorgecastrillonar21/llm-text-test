"""Simulation time: the clock, the calendar projection, and who may move it.

The service half runs entirely through `SessionClockPort` against a dictionary, for
the same reason `test_turn_ports.py` does: if advancing time needs a database to
work, the rule about who owns the clock lives in the wrong layer.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.application.persistence import (
    NewEvent,
    NewScheduledEvent,
    ScheduledEventRecord,
    SessionSnapshot,
    WorldSnapshot,
)
from app.application.time_service import (
    TIME_ADVANCED_EVENT,
    advance_time,
    cancel_scheduled_event,
    schedule_event,
)
from app.domain.enums import Language
from app.domain.errors import NotFoundError, TimeProgressionError, ValidationError
from app.domain.world_rules import WorldRules, default_world_rules
from app.domain.world_rules.enums import TimeProgression
from app.domain.world_time import (
    DEFAULT_INITIAL_DATETIME,
    STANDARD_CALENDAR,
    Calendar,
    CalendarMonth,
    FictionalDateTime,
    Interruption,
    ScheduledEventStatus,
    TimeAdvanceReason,
    TimeAdvanceRequest,
    TimeAdvanceResult,
    TimeOfDay,
    TimeState,
    describe_duration,
    is_permitted,
    project_time,
    require_permitted,
    require_transition,
)

SESSION_ID = uuid.uuid4()
WORLD_ID = uuid.uuid4()

DAY = 24 * 60


# ---------------------------------------------------------------------------
# TimeState
# ---------------------------------------------------------------------------


def test_a_session_starts_at_zero() -> None:
    assert TimeState().elapsed_minutes == 0


def test_a_session_can_be_anywhere_on_its_own_clock() -> None:
    assert TimeState(elapsed_minutes=28980).elapsed_minutes == 28980


def test_a_negative_clock_is_not_a_state_that_exists() -> None:
    with pytest.raises(PydanticValidationError):
        TimeState(elapsed_minutes=-1)


def test_advancing_returns_a_later_state_and_leaves_the_original_alone() -> None:
    start = TimeState(elapsed_minutes=100)

    later = start.advance(380)

    assert later.elapsed_minutes == 480
    assert start.elapsed_minutes == 100


def test_zero_is_a_legal_advance() -> None:
    assert TimeState(elapsed_minutes=7).advance(0).elapsed_minutes == 7


def test_the_clock_refuses_to_run_backward() -> None:
    with pytest.raises(ValidationError, match="backward"):
        TimeState(elapsed_minutes=100).advance(-1)


# ---------------------------------------------------------------------------
# Calendar projection
# ---------------------------------------------------------------------------


def test_a_date_survives_a_round_trip_through_absolute_minutes() -> None:
    moment = FictionalDateTime(year=842, month=5, day=13, hour=16, minute=42)

    assert STANDARD_CALENDAR.from_minutes(STANDARD_CALENDAR.to_minutes(moment)) == moment


def test_elapsed_minutes_are_added_to_the_worlds_start_date() -> None:
    start = FictionalDateTime(year=842, month=5, day=13, hour=13, minute=0)

    now = project_time(20 * DAY + 3 * 60 + 42, initial=start)

    assert now.calendar_date == "2 June, 842"
    assert now.clock == "16:42"
    assert now.period is TimeOfDay.AFTERNOON
    assert now.elapsed_since_start == "20 days, 3 hours"


def test_a_session_at_minute_zero_reads_as_its_worlds_start_date() -> None:
    now = project_time(0, initial=DEFAULT_INITIAL_DATETIME)

    assert now.calendar_date == "1 January, 1"
    assert now.clock == "08:00"
    assert now.elapsed_since_start == "0 minutes"


def test_crossing_a_year_boundary_rolls_the_year_over() -> None:
    end_of_year = FictionalDateTime(year=842, month=12, day=31, hour=23, minute=0)

    now = project_time(120, initial=end_of_year)

    assert now.calendar_date == "1 January, 843"
    assert now.clock == "01:00"


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, TimeOfDay.LATE_NIGHT),
        (4, TimeOfDay.LATE_NIGHT),
        (5, TimeOfDay.DAWN),
        (6, TimeOfDay.DAWN),
        (7, TimeOfDay.MORNING),
        (11, TimeOfDay.MORNING),
        (12, TimeOfDay.AFTERNOON),
        (16, TimeOfDay.AFTERNOON),
        (17, TimeOfDay.EVENING),
        (20, TimeOfDay.EVENING),
        (21, TimeOfDay.NIGHT),
        (23, TimeOfDay.NIGHT),
    ],
)
def test_every_hour_lands_in_the_part_of_day_it_should(hour: int, expected: TimeOfDay) -> None:
    moment = FictionalDateTime(year=1, month=1, day=1, hour=hour, minute=0)

    assert STANDARD_CALENDAR.period_of_day(moment) is expected


def test_the_part_of_day_follows_the_calendar_rather_than_a_24_hour_assumption() -> None:
    """A ten-hour day still has a midday. Nothing here hard-codes 24."""
    short = Calendar(months=(CalendarMonth(name="Only", days=10),), hours_per_day=10)

    assert short.period_of_day(FictionalDateTime(year=1, month=1, day=1, hour=5, minute=0)) is (
        TimeOfDay.AFTERNOON
    )
    assert short.period_of_day(FictionalDateTime(year=1, month=1, day=1, hour=0, minute=0)) is (
        TimeOfDay.LATE_NIGHT
    )


def test_a_day_the_calendar_does_not_have_is_refused() -> None:
    with pytest.raises(ValidationError, match="February"):
        STANDARD_CALENDAR.check(FictionalDateTime(year=1, month=2, day=30, hour=0, minute=0))


def test_a_month_the_calendar_does_not_have_is_refused() -> None:
    with pytest.raises(ValidationError, match="Month 13"):
        STANDARD_CALENDAR.check(FictionalDateTime(year=1, month=13, day=1, hour=0, minute=0))


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0, "0 minutes"),
        (1, "1 minute"),
        (59, "59 minutes"),
        (60, "1 hour"),
        (90, "1 hour, 30 minutes"),
        (DAY, "1 day"),
        (20 * DAY + 3 * 60 + 45, "20 days, 3 hours"),
    ],
)
def test_durations_read_the_way_a_person_would_say_them(minutes: int, expected: str) -> None:
    assert describe_duration(minutes) == expected


# ---------------------------------------------------------------------------
# Who may move the clock
# ---------------------------------------------------------------------------


def test_every_time_progression_setting_has_an_authority_entry() -> None:
    """A new TimeProgression member must be a decision, not a KeyError in production."""
    for progression in TimeProgression:
        assert is_permitted(progression, TimeAdvanceReason.DEBUG)


def test_a_paused_world_refuses_gameplay_but_allows_an_authored_skip() -> None:
    assert not is_permitted(TimeProgression.PAUSED, TimeAdvanceReason.ACTION)
    assert not is_permitted(TimeProgression.PAUSED, TimeAdvanceReason.SIMULATION)
    assert is_permitted(TimeProgression.PAUSED, TimeAdvanceReason.NARRATIVE)


def test_only_an_active_world_lets_a_simulation_system_ask_for_time() -> None:
    assert not is_permitted(TimeProgression.ACTION_BASED, TimeAdvanceReason.SIMULATION)
    assert is_permitted(TimeProgression.ACTIVE, TimeAdvanceReason.SIMULATION)
    # Everything action-based allows, active allows too.
    for reason in (TimeAdvanceReason.ACTION, TimeAdvanceReason.REST, TimeAdvanceReason.TRAVEL):
        assert is_permitted(TimeProgression.ACTION_BASED, reason)
        assert is_permitted(TimeProgression.ACTIVE, reason)


def test_refusal_names_the_setting_and_what_it_would_have_allowed() -> None:
    with pytest.raises(TimeProgressionError, match="paused"):
        require_permitted(TimeProgression.PAUSED, TimeAdvanceReason.ACTION)


# ---------------------------------------------------------------------------
# Request and result models
# ---------------------------------------------------------------------------


def test_a_request_for_negative_time_cannot_be_constructed() -> None:
    with pytest.raises(PydanticValidationError):
        TimeAdvanceRequest(requested_minutes=-1, reason=TimeAdvanceReason.ACTION)


def test_a_result_whose_arithmetic_disagrees_with_itself_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="ended_at"):
        TimeAdvanceResult(requested_minutes=60, advanced_minutes=60, started_at=0, ended_at=59)


def test_a_result_cannot_claim_more_time_than_was_asked_for() -> None:
    with pytest.raises(PydanticValidationError, match="cannot exceed"):
        TimeAdvanceResult(requested_minutes=60, advanced_minutes=90, started_at=0, ended_at=90)


def test_being_interrupted_and_having_no_interruption_is_a_contradiction() -> None:
    with pytest.raises(PydanticValidationError, match="must agree"):
        TimeAdvanceResult(
            requested_minutes=60,
            advanced_minutes=60,
            started_at=0,
            ended_at=60,
            interrupted=True,
        )


# ---------------------------------------------------------------------------
# Scheduled event statuses
# ---------------------------------------------------------------------------


def test_a_pending_event_can_be_processed_or_cancelled() -> None:
    require_transition(ScheduledEventStatus.PENDING, ScheduledEventStatus.PROCESSED)
    require_transition(ScheduledEventStatus.PENDING, ScheduledEventStatus.CANCELLED)


@pytest.mark.parametrize(
    "resolved", [ScheduledEventStatus.PROCESSED, ScheduledEventStatus.CANCELLED]
)
def test_a_resolved_event_never_moves_again(resolved: ScheduledEventStatus) -> None:
    with pytest.raises(ValidationError, match="already resolved"):
        require_transition(resolved, ScheduledEventStatus.PROCESSED)


def test_nothing_can_be_put_back_to_pending() -> None:
    with pytest.raises(ValidationError, match="not a resolution"):
        require_transition(ScheduledEventStatus.PENDING, ScheduledEventStatus.PENDING)


# ---------------------------------------------------------------------------
# The time service, driven through its port
# ---------------------------------------------------------------------------


def _rules_with(progression: TimeProgression) -> WorldRules:
    rules = default_world_rules()
    return rules.model_copy(
        update={"simulation": rules.simulation.model_copy(update={"time_progression": progression})}
    )


class FakeSessionClock:
    """In-memory SessionClockPort. Records what the service asked it to do."""

    def __init__(
        self,
        *,
        elapsed_minutes: int = 0,
        progression: TimeProgression = TimeProgression.ACTIVE,
        turn_index: int = 0,
    ) -> None:
        self.session = SessionSnapshot(
            id=SESSION_ID,
            world_id=WORLD_ID,
            title="Run",
            player_name="Rin",
            player_description="",
            current_location="a town",
            summary="",
            turn_index=turn_index,
            elapsed_minutes=elapsed_minutes,
        )
        self.world = WorldSnapshot(
            id=WORLD_ID,
            name="W",
            description="",
            genre="fantasy",
            setting="a town",
            language=Language.EN,
            rules=_rules_with(progression),
            initial_datetime=DEFAULT_INITIAL_DATETIME,
        )
        self.scheduled: list[ScheduledEventRecord] = []
        self.events: list[NewEvent] = []
        self.commits = 0

    async def get_session(self, session_id: uuid.UUID) -> SessionSnapshot | None:
        return self.session if session_id == self.session.id else None

    async def get_world(self, world_id: uuid.UUID) -> WorldSnapshot | None:
        return self.world if world_id == self.world.id else None

    async def set_elapsed_minutes(self, session_id: uuid.UUID, elapsed_minutes: int) -> None:
        self.session = self.session.model_copy(update={"elapsed_minutes": elapsed_minutes})

    async def add_event(self, event: NewEvent) -> None:
        self.events.append(event)

    async def add_scheduled_event(self, event: NewScheduledEvent) -> uuid.UUID:
        record = ScheduledEventRecord(
            id=uuid.uuid4(),
            session_id=event.session_id,
            due_at=event.due_at,
            type=event.type,
            payload=dict(event.payload),
            status=ScheduledEventStatus.PENDING,
            interrupt_player_action=event.interrupt_player_action,
        )
        self.scheduled.append(record)
        return record.id

    async def get_scheduled_event(self, event_id: uuid.UUID) -> ScheduledEventRecord | None:
        return next((e for e in self.scheduled if e.id == event_id), None)

    async def load_due_scheduled_events(
        self, session_id: uuid.UUID, *, through: int
    ) -> list[ScheduledEventRecord]:
        due = [
            event
            for event in self.scheduled
            if event.session_id == session_id
            and event.status is ScheduledEventStatus.PENDING
            and event.due_at <= through
        ]
        # Stable sort: ties keep insertion order, as the port's contract requires.
        return sorted(due, key=lambda event: event.due_at)

    async def set_scheduled_event_status(
        self, event_id: uuid.UUID, status: ScheduledEventStatus
    ) -> None:
        self.scheduled = [
            event.model_copy(update={"status": status}) if event.id == event_id else event
            for event in self.scheduled
        ]

    async def commit(self) -> None:
        self.commits += 1

    # -- test helpers -----------------------------------------------------------

    def given_scheduled(
        self, *, due_at: int, event_type: str, interrupts: bool = False
    ) -> uuid.UUID:
        record = ScheduledEventRecord(
            id=uuid.uuid4(),
            session_id=SESSION_ID,
            due_at=due_at,
            type=event_type,
            payload={},
            status=ScheduledEventStatus.PENDING,
            interrupt_player_action=interrupts,
        )
        self.scheduled.append(record)
        return record.id

    def status_of(self, event_id: uuid.UUID) -> ScheduledEventStatus:
        return next(event.status for event in self.scheduled if event.id == event_id)


def _request(minutes: int, **overrides: object) -> TimeAdvanceRequest:
    data: dict[str, object] = {
        "requested_minutes": minutes,
        "reason": TimeAdvanceReason.ACTION,
    }
    data.update(overrides)
    return TimeAdvanceRequest.model_validate(data)


async def test_a_normal_advance_moves_the_clock_and_reports_the_span() -> None:
    clock = FakeSessionClock(elapsed_minutes=28980)

    result = await advance_time(clock, session_id=SESSION_ID, request=_request(480))

    assert result.started_at == 28980
    assert result.ended_at == 29460
    assert result.advanced_minutes == 480
    assert result.interrupted is False
    assert result.interruption is None
    assert clock.session.elapsed_minutes == 29460
    assert clock.commits == 1


async def test_zero_advancement_is_valid_and_leaves_no_trace() -> None:
    clock = FakeSessionClock(elapsed_minutes=100)

    result = await advance_time(clock, session_id=SESSION_ID, request=_request(0))

    assert result.advanced_minutes == 0
    assert result.started_at == result.ended_at == 100
    assert clock.session.elapsed_minutes == 100
    # Nothing happened, so nothing is worth an audit row.
    assert clock.events == []


async def test_a_paused_world_refuses_an_action_and_nothing_moves() -> None:
    clock = FakeSessionClock(elapsed_minutes=100, progression=TimeProgression.PAUSED)

    with pytest.raises(TimeProgressionError):
        await advance_time(clock, session_id=SESSION_ID, request=_request(60))

    assert clock.session.elapsed_minutes == 100
    assert clock.commits == 0


async def test_a_paused_world_still_allows_an_authored_timeskip() -> None:
    clock = FakeSessionClock(progression=TimeProgression.PAUSED)

    result = await advance_time(
        clock,
        session_id=SESSION_ID,
        request=_request(3 * 30 * DAY, reason=TimeAdvanceReason.NARRATIVE, interruptible=False),
    )

    assert result.advanced_minutes == 3 * 30 * DAY


async def test_an_unknown_session_is_reported_rather_than_created() -> None:
    clock = FakeSessionClock()

    with pytest.raises(NotFoundError):
        await advance_time(clock, session_id=uuid.uuid4(), request=_request(10))

    assert clock.commits == 0


async def test_the_advance_is_recorded_for_audit() -> None:
    clock = FakeSessionClock(elapsed_minutes=840, turn_index=412)

    await advance_time(
        clock,
        session_id=SESSION_ID,
        request=_request(265, reason=TimeAdvanceReason.TRAVEL, detail="Riverwood to the Capital"),
    )

    (recorded,) = clock.events
    assert recorded.type == TIME_ADVANCED_EVENT
    assert recorded.occurred_at == 1105
    assert recorded.turn_index == 412
    assert "travel" in recorded.description
    assert "840 -> 1105" in recorded.description
    assert "Riverwood to the Capital" in recorded.description


async def test_events_due_inside_the_span_are_processed_in_chronological_order() -> None:
    clock = FakeSessionClock(elapsed_minutes=0)
    late = clock.given_scheduled(due_at=300, event_type="caravan_arrives")
    early = clock.given_scheduled(due_at=100, event_type="shop_closes")
    outside = clock.given_scheduled(due_at=900, event_type="festival")

    result = await advance_time(clock, session_id=SESSION_ID, request=_request(480))

    assert result.processed_event_ids == [early, late]
    assert clock.status_of(outside) is ScheduledEventStatus.PENDING
    assert result.ended_at == 480


async def test_an_interrupting_event_stops_the_advance_where_it_happens() -> None:
    """Sleeping eight hours, woken after three hours and twelve minutes."""
    clock = FakeSessionClock(elapsed_minutes=0)
    alarm = clock.given_scheduled(due_at=192, event_type="fire_in_the_stables", interrupts=True)

    result = await advance_time(
        clock, session_id=SESSION_ID, request=_request(480, reason=TimeAdvanceReason.REST)
    )

    assert result.advanced_minutes == 192
    assert result.ended_at == 192
    assert result.interrupted is True
    assert result.interruption == Interruption(
        event_id=alarm, event_type="fire_in_the_stables", at=192
    )
    assert clock.session.elapsed_minutes == 192
    # It still happened -- being the interruption is not the same as being skipped.
    assert clock.status_of(alarm) is ScheduledEventStatus.PROCESSED


async def test_events_scheduled_after_an_interruption_stay_pending() -> None:
    clock = FakeSessionClock(elapsed_minutes=0)
    clock.given_scheduled(due_at=100, event_type="knock", interrupts=True)
    later = clock.given_scheduled(due_at=200, event_type="dawn_bell")

    await advance_time(clock, session_id=SESSION_ID, request=_request(480))

    assert clock.status_of(later) is ScheduledEventStatus.PENDING


async def test_a_non_interruptible_skip_runs_straight_past_an_interrupting_event() -> None:
    clock = FakeSessionClock(elapsed_minutes=0)
    alarm = clock.given_scheduled(due_at=100, event_type="knock", interrupts=True)

    result = await advance_time(
        clock,
        session_id=SESSION_ID,
        request=_request(480, reason=TimeAdvanceReason.NARRATIVE, interruptible=False),
    )

    assert result.interrupted is False
    assert result.advanced_minutes == 480
    assert clock.status_of(alarm) is ScheduledEventStatus.PROCESSED


async def test_an_event_already_behind_the_clock_resolves_now_rather_than_rewinding() -> None:
    clock = FakeSessionClock(elapsed_minutes=1000)
    clock.given_scheduled(due_at=10, event_type="overdue_rent", interrupts=True)

    result = await advance_time(clock, session_id=SESSION_ID, request=_request(60))

    assert result.interruption is not None
    assert result.interruption.at == 1000
    assert result.ended_at == 1000
    assert result.advanced_minutes == 0
    # An interruption that costs no time still gets recorded: something happened.
    assert clock.events[0].type == TIME_ADVANCED_EVENT


async def test_scheduling_converts_a_delay_into_an_absolute_due_time() -> None:
    clock = FakeSessionClock(elapsed_minutes=28980)

    event_id = await schedule_event(
        clock, session_id=SESSION_ID, event_type="rent_due", delay_minutes=3 * DAY
    )

    stored = await clock.get_scheduled_event(event_id)
    assert stored is not None
    assert stored.due_at == 28980 + 4320
    assert stored.status is ScheduledEventStatus.PENDING
    assert clock.commits == 1


async def test_scheduling_something_in_the_past_is_refused() -> None:
    clock = FakeSessionClock()

    with pytest.raises(ValidationError, match="past"):
        await schedule_event(clock, session_id=SESSION_ID, event_type="oops", delay_minutes=-1)

    assert clock.scheduled == []


async def test_a_pending_event_can_be_called_off() -> None:
    clock = FakeSessionClock()
    event_id = await schedule_event(
        clock, session_id=SESSION_ID, event_type="duel_at_dawn", delay_minutes=600
    )

    await cancel_scheduled_event(clock, event_id=event_id)

    assert clock.status_of(event_id) is ScheduledEventStatus.CANCELLED


async def test_an_event_that_already_fired_cannot_be_cancelled() -> None:
    clock = FakeSessionClock(elapsed_minutes=0)
    event_id = clock.given_scheduled(due_at=10, event_type="shop_closes")
    await advance_time(clock, session_id=SESSION_ID, request=_request(60))

    with pytest.raises(ValidationError, match="already resolved"):
        await cancel_scheduled_event(clock, event_id=event_id)

    assert clock.status_of(event_id) is ScheduledEventStatus.PROCESSED
