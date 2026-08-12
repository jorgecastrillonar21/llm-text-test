"""Where an actor is, canonically, by id.

This is the smallest complete answer to "where is this character?" that the rest of
the system can be held to. It is one authority, not a second one: every other place
that had an opinion about position -- a free-text string on the session, a name match
against the spatial graph -- either reads this or is documented as legacy.

    AtLocation   at a place, optionally in a zone of it
    InTransit    between two places, on a declared connection
    Offstage     not in the scene, on purpose
    Unlocated    nobody has said

# Ids, never names

Every reference here is a uuid, and the application validates each one against the
geography the session can actually see before a position is stored. That is the whole
reason this exists: a string like "Market Street" is ambiguous the moment a world has
two of them, and the failure mode of a name match is not "no answer" but "the wrong
room, with the wrong exits, handed to a language model as fact".

# What deliberately is not here

    occupants / characters_inside   the room does not keep a list; see LocationState
    discovered, known, remembered   Knowledge and Perception own those, when they exist
    x, y, facing, range band        tactical space is ephemeral; SceneState owns it
    speed, path, progress, arrival  TravelEngine's, when it exists

`InTransit` records the *commitment* -- who left where for where, along which
connection, when, and when they are expected -- and computes nothing. It has no notion
of speed, no path, no partial progress and no arrival logic, because working out
whether a journey is going to plan is a system that does not exist yet and would be
wrong to half-write here. Until it does, arriving is a caller writing `AtLocation`.

# Why there is no version integer on a position

A session already stores `world_state_version`, and that is what says which shape its
state is stored in. A second counter on each position row would be a second answer to
the same question, free to disagree with the first. `read_position` is the boundary
instead: stored columns come back through it or they do not come back at all.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.character_position.enums import PositionKind
from app.domain.errors import ValidationError

Minute = Annotated[int, Field(ge=0)]
"""Session elapsed minutes. Fictional time, never a wall clock -- the same scale
`world_time` counts in and `ScheduledEvent.due_at` is written on."""


class AtLocation(BaseModel):
    """At a specific place, and optionally somewhere within it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[PositionKind.AT_LOCATION] = PositionKind.AT_LOCATION

    location_id: uuid.UUID
    """The canonical location. Always present -- a position that is `at_location`
    without a location is not a position, and the type says so rather than a validator
    having to."""

    zone_id: uuid.UUID | None = None
    """Optionally, which part of it: by the fireplace, in the cellar, on the north
    wall. Optional because most positions do not need one, and a zone that had to be
    supplied would be invented rather than chosen. The application checks that it
    belongs to `location_id` -- a zone of somewhere else is not a finer answer, it is
    a contradictory one."""


class InTransit(BaseModel):
    """On the way from one place to another, along a declared connection.

    A record of a commitment, not a simulation of a journey. See the module docstring:
    nothing here computes progress, and nothing here decides that an actor has arrived.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[PositionKind.IN_TRANSIT] = PositionKind.IN_TRANSIT

    origin_location_id: uuid.UUID
    destination_location_id: uuid.UUID

    connection_id: uuid.UUID
    """Which traversal is being used. Required, and it is what makes this a position in
    the spatial model rather than a note: travel happens along declared edges in this
    system, and "between two places by no particular route" is a claim the geography
    cannot support or contradict."""

    departed_at: Minute
    expected_arrival_at: Minute
    """When arrival is *expected*, which is not when it happens. Nothing in this
    package advances a journey; a clock that reached this minute has reached it and
    that is all -- exactly the distinction `ScheduledEvent` draws between due and
    processed."""

    @model_validator(mode="after")
    def _a_journey_goes_somewhere_else(self) -> Self:
        if self.origin_location_id == self.destination_location_id:
            raise ValueError(
                "A transit's origin and destination are the same place. Standing still "
                "is `at_location`, not a journey of zero length."
            )
        return self

    @model_validator(mode="after")
    def _arrival_is_not_before_departure(self) -> Self:
        if self.expected_arrival_at < self.departed_at:
            raise ValueError(
                f"Transit expects to arrive at minute {self.expected_arrival_at} having "
                f"departed at {self.departed_at}. Time does not run backwards here."
            )
        return self


class Offstage(BaseModel):
    """Not in the scene, deliberately.

    Fieldless, and that is the contract: an actor the story has set aside is not
    secretly somewhere, and a hidden "really at" location would be exactly the second
    authority this package exists to prevent. When they return, somebody writes an
    `AtLocation` saying where.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[PositionKind.OFFSTAGE] = PositionKind.OFFSTAGE


class Unlocated(BaseModel):
    """Nobody has said where this actor is.

    Also fieldless, and distinct from `Offstage` on purpose -- see `PositionKind`. This
    is what a session starts at when its world has no geography, or when the place its
    player typed matches nothing. It is an honest gap, written down as one, rather than
    an absent row that every later read would have to interpret.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal[PositionKind.UNLOCATED] = PositionKind.UNLOCATED


CharacterPosition = Annotated[
    AtLocation | InTransit | Offstage | Unlocated,
    Field(discriminator="kind"),
]
"""Discriminated on `kind`, so a malformed position names its own problem instead of
producing a four-branch union report."""


def scene_location_id(position: CharacterPosition) -> uuid.UUID | None:
    """The place a scene should be built in, or None when there is not one.

    `InTransit` returns None on purpose. Somebody on the road between two cities is in
    neither of them, and answering with the origin would hand the director a room, its
    exits and its ongoing situations for a place the actor has left. A journey needs
    its own scene shape, and that belongs to the future TravelEngine rather than to a
    convenience default here.
    """
    return position.location_id if isinstance(position, AtLocation) else None


def referenced_location_ids(position: CharacterPosition) -> tuple[uuid.UUID, ...]:
    """Every location this position points at, for the caller that has to check them.

    Validation lives in the application -- only it can see the session's geography --
    but *which* ids need checking is a property of the shape, and leaving that to each
    call site is how a new position kind gets added with one end unvalidated.
    """
    match position:
        case AtLocation():
            return (position.location_id,)
        case InTransit():
            return (position.origin_location_id, position.destination_location_id)
        case _:
            return ()


def read_position(
    *,
    kind: str,
    location_id: uuid.UUID | None = None,
    zone_id: uuid.UUID | None = None,
    origin_location_id: uuid.UUID | None = None,
    destination_location_id: uuid.UUID | None = None,
    connection_id: uuid.UUID | None = None,
    departed_at: int | None = None,
    expected_arrival_at: int | None = None,
) -> CharacterPosition:
    """Rebuild a position from stored columns, refusing anything that is not one.

    The boundary this package has instead of a per-row version integer. A row is stored
    as a discriminator plus a set of nullable columns -- which is how a union has to be
    stored in one table -- and this is the single place where that flat shape becomes a
    typed position again. A row whose kind says `at_location` and whose `location_id` is
    null is not a position with a missing field; it is a row nobody should have written,
    and it fails here rather than three layers up as an attribute error.

    Raises `ValidationError` for an unknown kind or a combination that cannot be one of
    the four shapes.
    """
    match kind:
        case PositionKind.AT_LOCATION:
            if location_id is None:
                raise ValidationError(
                    "Stored position says 'at_location' but names no location. A place "
                    "an actor is at is the one thing that shape cannot be missing."
                )
            return AtLocation(location_id=location_id, zone_id=zone_id)
        case PositionKind.IN_TRANSIT:
            if (
                origin_location_id is None
                or destination_location_id is None
                or connection_id is None
                or departed_at is None
                or expected_arrival_at is None
            ):
                raise ValidationError(
                    "Stored position says 'in_transit' but is missing part of the "
                    "journey. A transit needs both ends, the connection it uses, when "
                    "it began and when it is expected to end."
                )
            return InTransit(
                origin_location_id=origin_location_id,
                destination_location_id=destination_location_id,
                connection_id=connection_id,
                departed_at=departed_at,
                expected_arrival_at=expected_arrival_at,
            )
        case PositionKind.OFFSTAGE:
            return Offstage()
        case PositionKind.UNLOCATED:
            return Unlocated()
        case _:
            raise ValidationError(
                f"Stored position has kind {kind!r}, which is not one of "
                f"{', '.join(sorted(k.value for k in PositionKind))}."
            )
