"""How places connect. Never inferred, always declared.

# Containment is not connectivity

    Broken Crown.parent = Riverwood

says the tavern is *inside* the town. It does not say there is a usable traversal
from the town to the tavern, and this module will not invent one. That looks
pedantic until the first walled district, sealed vault or cellar whose only entrance
collapsed -- all of which are inside their parent and none of which can be walked
into.

The reverse holds too: two places with no common ancestor can be one step apart
through a portal. A graph edge is a fact somebody wrote down.

# Distance is not duration

`physical_distance` is how far apart the endpoints are. `base_travel_minutes` is how
long the crossing takes at a nominal pace. A portal is four thousand kilometres and
one minute; a doorway is no meaningful distance and no meaningful time. Neither is
derivable from the other, so neither is derived -- the future TravelEngine will
compute an actual duration from the mode, the traveller and the conditions, and both
of these are inputs to that rather than answers.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.world_locations.definitions import (
    Importance,
    clean_tags,
    parse_subtype,
)
from app.domain.world_locations.enums import ConnectionCategory

MAX_DISTANCE_UNIT_LENGTH = 20


class PhysicalDistance(BaseModel):
    """How far apart the endpoints are, in whatever unit the world thinks in.

    Both halves or neither: a number with no unit is not a distance, it is a number.
    The unit is a free string because a world may measure in kilometres, leagues,
    parsecs or days' march, and the engine does no arithmetic on it -- anything that
    eventually does will convert explicitly rather than assume metres.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=MAX_DISTANCE_UNIT_LENGTH)

    @field_validator("unit")
    @classmethod
    def _tidy_unit(cls, value: str) -> str:
        return value.strip().casefold()

    def describe(self) -> str:
        rounded = int(self.value) if self.value.is_integer() else self.value
        return f"{rounded} {self.unit}"


class LocationConnection(BaseModel):
    """A declared traversal between two places.

    Structure only. Whether it is currently passable is `LocationConnectionState`, and
    a collapsed bridge keeps its connection row -- that is what makes repair,
    historical reference and "the ruins of the crossing" expressible at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    world_id: uuid.UUID
    origin_session_id: uuid.UUID | None = None

    from_location_id: uuid.UUID
    to_location_id: uuid.UUID

    bidirectional: bool = True
    """False means one-way, and one-way is honoured. A cliff descent, a drop shaft, a
    waterfall and a one-way portal all go somewhere you cannot come back from, and
    nothing here quietly supplies the return edge."""

    category: ConnectionCategory
    subtype: str | None = None

    physical_distance: PhysicalDistance | None = None
    base_travel_minutes: int | None = Field(default=None, ge=0)
    """Nominal crossing time in simulation minutes, or None when nobody has said. Zero
    is meaningful and different from None: a doorway takes no time, whereas an
    unmeasured road is simply unmeasured."""

    tags: tuple[str, ...] = ()
    importance: Importance = 3

    created_at: dt.datetime
    updated_at: dt.datetime

    @field_validator("subtype")
    @classmethod
    def _canonical_subtype(cls, value: str | None) -> str | None:
        return parse_subtype(value)

    @field_validator("tags")
    @classmethod
    def _sane_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return clean_tags(value)

    @model_validator(mode="after")
    def _connects_two_places(self) -> Self:
        if self.from_location_id == self.to_location_id:
            raise ValueError(
                f"A connection must join two different locations; both ends are "
                f"{self.from_location_id}."
            )
        return self

    def links(self, location_id: uuid.UUID) -> bool:
        return location_id in (self.from_location_id, self.to_location_id)

    def leads_from(self, location_id: uuid.UUID) -> uuid.UUID | None:
        """Where this edge goes when leaving `location_id`, or None if it does not.

        None for a location this edge does not touch, and None for the far end of a
        one-way edge -- standing at the bottom of a drop shaft, the shaft is not an
        exit.
        """
        if location_id == self.from_location_id:
            return self.to_location_id
        if location_id == self.to_location_id and self.bidirectional:
            return self.from_location_id
        return None

    def is_template(self) -> bool:
        return self.origin_session_id is None

    def visible_to(self, session_id: uuid.UUID) -> bool:
        return self.origin_session_id is None or self.origin_session_id == session_id

    def describe(self) -> str:
        arrow = "<->" if self.bidirectional else "->"
        detail = f"{self.category.value}/{self.subtype}" if self.subtype else self.category.value
        return f"{self.from_location_id} {arrow} {self.to_location_id} ({detail})"
