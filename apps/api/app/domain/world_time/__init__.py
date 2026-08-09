"""Simulation time: the first piece of the future `WorldState`.

A GameSession owns one authoritative number, `elapsed_minutes`, counted from the
moment that session began. Dates, hours and parts of the day are projections of it.
Turn count is a separate thing entirely -- four turns of dialogue can share a minute,
and one action can cost six months.

    clock        the authoritative counter, and the rule that it never runs backward
    calendar     the readable projection: date, hour, part of day, elapsed duration
    advancement  who may move the clock, by how much, and what came of it
    scheduling   notes about things due at a future minute

Only application code advances time. The Story Director is told what time it is and
has no way to change it. See docs/world-state-time.md.
"""

from __future__ import annotations

from app.domain.world_time.advancement import (
    Interruption,
    TimeAdvanceReason,
    TimeAdvanceRequest,
    TimeAdvanceResult,
    is_permitted,
    require_permitted,
)
from app.domain.world_time.calendar import (
    DEFAULT_INITIAL_DATETIME,
    STANDARD_CALENDAR,
    Calendar,
    CalendarMonth,
    FictionalDateTime,
    TimeOfDay,
    TimeProjection,
    describe_duration,
    project_time,
)
from app.domain.world_time.clock import DurationMinutes, ElapsedMinutes, TimeState
from app.domain.world_time.scheduling import (
    TERMINAL_STATUSES,
    ScheduledEventStatus,
    require_transition,
)

__all__ = [
    "DEFAULT_INITIAL_DATETIME",
    "STANDARD_CALENDAR",
    "TERMINAL_STATUSES",
    "Calendar",
    "CalendarMonth",
    "DurationMinutes",
    "ElapsedMinutes",
    "FictionalDateTime",
    "Interruption",
    "ScheduledEventStatus",
    "TimeAdvanceReason",
    "TimeAdvanceRequest",
    "TimeAdvanceResult",
    "TimeOfDay",
    "TimeProjection",
    "TimeState",
    "describe_duration",
    "is_permitted",
    "project_time",
    "require_permitted",
    "require_transition",
]
