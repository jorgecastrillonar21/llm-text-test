"""The authoritative simulation clock.

One number decides what time it is in a story: `elapsed_minutes`, counted from the
start of a GameSession. Everything else -- the date, the hour, whether it is dusk --
is derived from it by `calendar`, and none of it is stored.

# What zero means

`elapsed_minutes = 0` is the beginning of *this session*, not the beginning of the
fictional universe. A world says what date that instant corresponds to (see
`FictionalDateTime`); the session only counts forward from it.

# What it is not

    simulation time  !=  turn index
    simulation time  !=  calendar date
    simulation time  !=  real-world time

Four turns of conversation may consume no fictional time at all, and one action may
consume six months. Nothing may infer the clock from how many messages exist, and
nothing synchronises it to the wall clock: closing the app for a week does not move
the story a week forward. See docs/world-state-time.md.

# Resolution

One unit is one fictional minute, deliberately. Combat and other future systems may
model rounds internally, but they convert to whole minutes before they touch this.
There are no seconds in the game clock, so there is nothing to round.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import ValidationError

ElapsedMinutes = Annotated[int, Field(ge=0)]
"""A position on the session clock. Never negative: zero is the session's own start.

Deliberately a plain `int`. Python integers do not overflow, and the column behind
this is a 64-bit integer, so a story can run for longer than any story will.
"""

DurationMinutes = Annotated[int, Field(ge=0)]
"""A length of fictional time. Zero is meaningful -- a turn that took no time at all."""


class TimeState(BaseModel):
    """A session's position on its own clock.

    Small on purpose: the future `WorldState` will hold what is *true* in the world,
    and this holds only when. Deriving the hour, the date or the season and storing
    them alongside would create four things that can disagree with each other.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    elapsed_minutes: ElapsedMinutes = 0

    def advance(self, minutes: DurationMinutes) -> TimeState:
        """The state `minutes` later.

        Refuses to move backward. Flashbacks, visions and memories are changes of
        narrative viewpoint, not rewinds -- whatever tells that story does not touch
        this clock. A rewind mechanic, if one is ever built, will need its own
        explicitly-authorised path rather than a negative duration slipped in here.
        """
        if minutes < 0:
            raise ValidationError(
                f"Simulation time cannot move backward: asked to advance {minutes} minutes."
            )
        return TimeState(elapsed_minutes=self.elapsed_minutes + minutes)
