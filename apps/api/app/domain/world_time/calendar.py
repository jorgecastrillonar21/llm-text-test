"""Turning elapsed minutes into a date somebody can read.

A calendar is a *projection* of the clock, never a second source of truth. Nothing
here is persisted: given a world's starting date and a session's `elapsed_minutes`,
every value below is recomputed. That is why "it is the 13th of Harvest, 16:42, late
afternoon" can never drift out of agreement with the clock -- there is nothing to
drift from.

# Why not `datetime`

Because a fictional world is not on the Gregorian calendar, and the standard library
insists that it is: leap years, a year 1 floor, timezones, and month lengths that a
world author cannot change. `Calendar` below is a dozen lines of arithmetic over a
list of months, which is both smaller and honest about what it assumes.

This release ships exactly one calendar, `STANDARD_CALENDAR`, and does not let a
world define its own. The shape is here so that custom month names, month lengths and
eras are a matter of storing a different `Calendar` rather than rewriting the
projection -- but the storage, the editor and the era handling are deliberately not
built yet. See docs/world-state-time.md.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import ValidationError


class TimeOfDay(StrEnum):
    """Which part of the day it is. Always derived, never stored."""

    DAWN = "dawn"
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"
    LATE_NIGHT = "late_night"


class FictionalDateTime(BaseModel):
    """A moment on a world's calendar.

    Only the calendar-independent bounds are checked here -- that a month is at least
    the first and an hour is not negative. Whether month 13 or hour 27 exists depends
    on which calendar is asking, so `Calendar.check` owns that half.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int = Field(ge=0)
    month: int = Field(ge=1)
    day: int = Field(ge=1)
    hour: int = Field(ge=0)
    minute: int = Field(ge=0)


class CalendarMonth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    days: int = Field(ge=1, le=1000)


class Calendar(BaseModel):
    """How a world divides its minutes.

    No leap rule and no week: both are real calendar features and both are additions
    a future version can make without changing what `elapsed_minutes` means.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    months: tuple[CalendarMonth, ...] = Field(min_length=1)
    hours_per_day: int = Field(default=24, ge=1)
    minutes_per_hour: int = Field(default=60, ge=1)

    @property
    def minutes_per_day(self) -> int:
        return self.hours_per_day * self.minutes_per_hour

    @property
    def days_per_year(self) -> int:
        return sum(month.days for month in self.months)

    @property
    def minutes_per_year(self) -> int:
        return self.days_per_year * self.minutes_per_day

    def check(self, moment: FictionalDateTime) -> None:
        """Raise unless `moment` is a date this calendar actually has."""
        if moment.month > len(self.months):
            raise ValidationError(
                f"Month {moment.month} does not exist: this calendar has {len(self.months)} months."
            )
        month = self.months[moment.month - 1]
        if moment.day > month.days:
            raise ValidationError(
                f"Day {moment.day} does not exist in {month.name}, which has {month.days} days."
            )
        if moment.hour >= self.hours_per_day:
            raise ValidationError(
                f"Hour {moment.hour} does not exist: this calendar has "
                f"{self.hours_per_day} hours in a day."
            )
        if moment.minute >= self.minutes_per_hour:
            raise ValidationError(
                f"Minute {moment.minute} does not exist: this calendar has "
                f"{self.minutes_per_hour} minutes in an hour."
            )

    def to_minutes(self, moment: FictionalDateTime) -> int:
        """Absolute minutes from year 0, day 1, 00:00 of this calendar."""
        self.check(moment)
        days_before_month = sum(month.days for month in self.months[: moment.month - 1])
        return (
            moment.year * self.minutes_per_year
            + (days_before_month + moment.day - 1) * self.minutes_per_day
            + moment.hour * self.minutes_per_hour
            + moment.minute
        )

    def from_minutes(self, total: int) -> FictionalDateTime:
        """The inverse of `to_minutes`."""
        if total < 0:
            raise ValidationError(f"A calendar position cannot be negative: {total}.")
        year, within_year = divmod(total, self.minutes_per_year)
        day_of_year, minute_of_day = divmod(within_year, self.minutes_per_day)

        month_index = 0
        while day_of_year >= self.months[month_index].days:
            day_of_year -= self.months[month_index].days
            month_index += 1

        hour, minute = divmod(minute_of_day, self.minutes_per_hour)
        return FictionalDateTime(
            year=year,
            month=month_index + 1,
            day=day_of_year + 1,
            hour=hour,
            minute=minute,
        )

    def month_name(self, month: int) -> str:
        return self.months[month - 1].name

    def period_of_day(self, moment: FictionalDateTime) -> TimeOfDay:
        """Which part of the day `moment` falls in.

        Computed from the fraction of the day elapsed rather than from `hour`, so a
        calendar with twenty hours in a day still has a recognisable afternoon.
        """
        minute_of_day = moment.hour * self.minutes_per_hour + moment.minute
        # The day rescaled to 24 parts, so the bands below can be written as hours.
        twentyfourths = minute_of_day * 24 // self.minutes_per_day
        period = TimeOfDay.LATE_NIGHT
        for starts_at, candidate in _PERIOD_BANDS:
            if twentyfourths >= starts_at:
                period = candidate
        return period


# Ordered, ascending. Anything before the first band is the tail of the previous
# night, which is why LATE_NIGHT is the default rather than an entry here.
_PERIOD_BANDS: tuple[tuple[int, TimeOfDay], ...] = (
    (5, TimeOfDay.DAWN),
    (7, TimeOfDay.MORNING),
    (12, TimeOfDay.AFTERNOON),
    (17, TimeOfDay.EVENING),
    (21, TimeOfDay.NIGHT),
)


STANDARD_CALENDAR = Calendar(
    months=(
        CalendarMonth(name="January", days=31),
        CalendarMonth(name="February", days=28),
        CalendarMonth(name="March", days=31),
        CalendarMonth(name="April", days=30),
        CalendarMonth(name="May", days=31),
        CalendarMonth(name="June", days=30),
        CalendarMonth(name="July", days=31),
        CalendarMonth(name="August", days=31),
        CalendarMonth(name="September", days=30),
        CalendarMonth(name="October", days=31),
        CalendarMonth(name="November", days=30),
        CalendarMonth(name="December", days=31),
    ),
)
"""Twelve familiar months, 365 days, no leap rule.

A placeholder with a deliberate omission: leap years exist to keep a calendar aligned
with an orbit this world does not have, and adding one would be inventing astronomy
for a fantasy setting. Worlds that want their own month names are waiting on
per-world calendars, not on this constant.
"""

DEFAULT_INITIAL_DATETIME = FictionalDateTime(year=1, month=1, day=1, hour=8, minute=0)
"""Where a world starts if its author does not say: the first morning of year one.

Obviously fictional on purpose. A real-looking date would imply a history the world
has not been given.
"""


class TimeProjection(BaseModel):
    """Everything readable that follows from `elapsed_minutes`.

    Produced on demand and thrown away. Persisting any of it would create a second
    place where the current time is recorded, and therefore a second place for it to
    be wrong.
    """

    model_config = ConfigDict(frozen=True)

    elapsed_minutes: int
    moment: FictionalDateTime
    period: TimeOfDay
    calendar_date: str
    clock: str
    elapsed_since_start: str


def project_time(
    elapsed_minutes: int,
    *,
    initial: FictionalDateTime = DEFAULT_INITIAL_DATETIME,
    calendar: Calendar = STANDARD_CALENDAR,
) -> TimeProjection:
    """Read the clock as a date, an hour and a part of the day."""
    if elapsed_minutes < 0:
        raise ValidationError(f"Elapsed time cannot be negative: {elapsed_minutes}.")
    moment = calendar.from_minutes(calendar.to_minutes(initial) + elapsed_minutes)
    return TimeProjection(
        elapsed_minutes=elapsed_minutes,
        moment=moment,
        period=calendar.period_of_day(moment),
        calendar_date=f"{moment.day} {calendar.month_name(moment.month)}, {moment.year}",
        clock=f"{moment.hour:02d}:{moment.minute:02d}",
        elapsed_since_start=describe_duration(elapsed_minutes, calendar=calendar),
    )


def describe_duration(minutes: int, *, calendar: Calendar = STANDARD_CALENDAR) -> str:
    """`29115` -> `20 days, 3 hours`.

    Two units at most: the third is never what the reader wanted to know. English
    only, and only ever shown next to a number -- player-visible prose is written by
    the story provider in the world's own language.
    """
    if minutes < 0:
        raise ValidationError(f"A duration cannot be negative: {minutes}.")
    days, within_day = divmod(minutes, calendar.minutes_per_day)
    hours, remainder = divmod(within_day, calendar.minutes_per_hour)

    parts = [
        f"{value} {unit}" if value == 1 else f"{value} {unit}s"
        for value, unit in ((days, "day"), (hours, "hour"), (remainder, "minute"))
        if value
    ]
    if not parts:
        return "0 minutes"
    return ", ".join(parts[:2])
