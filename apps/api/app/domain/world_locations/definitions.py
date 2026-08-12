"""What places exist, and what contains what.

A `LocationDefinition` answers *what and where is this place structurally?* It does
not answer *what is currently true about it?* -- that is `LocationState`, which is
per-session and mutable, and the two are kept apart because a definition is shared
across every save of a world while its condition is not.

# Template content and session canon

    origin_session_id = None          reusable world template
    origin_session_id = <session id>  canon local to that one save

A definition is never copied per session. Ten sessions of one world read the same
template rows; only their states differ. When gameplay invents a bookshop, that
definition is written once with an `origin_session_id`, and no other save can see it.

# Containment is a forest, not a graph

`parent_location_id` is the only containment link, and it must stay acyclic. The
checks live in `hierarchy.py` because they need to see more than one node at a time.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.vocabulary import (
    MAX_METADATA_KEY_LENGTH,
    MAX_METADATA_KEYS,
    MAX_METADATA_VALUE_LENGTH,
    MAX_SUBTYPE_LENGTH,
    MAX_TAG_LENGTH,
    MAX_TAGS,
    MetadataValue,
    check_flat_metadata,
    clean_tags,
    parse_subtype,
)
from app.domain.world_locations.enums import LocationCategory, LocationScale

__all__ = [
    "MAX_DESCRIPTION_LENGTH",
    "MAX_METADATA_KEYS",
    "MAX_METADATA_KEY_LENGTH",
    "MAX_METADATA_VALUE_LENGTH",
    "MAX_NAME_LENGTH",
    "MAX_SUBTYPE_LENGTH",
    "MAX_TAGS",
    "MAX_TAG_LENGTH",
    "Importance",
    "LocationDefinition",
    "LocationZone",
    "check_spatial_metadata",
    "clean_tags",
    "parse_subtype",
]

Importance = Annotated[int, Field(ge=1, le=5)]
"""How much a place matters when the prompt has room for only some of them. As with
facts, never a permission."""

MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 4000


def check_spatial_metadata(value: object) -> dict[str, MetadataValue]:
    """The shared flat-metadata rule, named for the field it guards.

    A thin call rather than a copy: locations and situations both carry an optional
    scalar bag, and the day one of them grows a length check the other does not is the
    day the two stop agreeing about what `40` means. See `app.domain.vocabulary`.
    """
    return check_flat_metadata(value, field="spatial_metadata")


class LocationDefinition(BaseModel):
    """A place that persistently exists. Structure only, never current condition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    world_id: uuid.UUID

    origin_session_id: uuid.UUID | None = None
    """None for reusable template geography; a session id for canon that gameplay
    invented inside one save and that no other save may see."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)

    category: LocationCategory
    subtype: str | None = None
    """The genre-specific noun. `structure` + `tavern`, `structure` + `orbital_station`."""

    scale: LocationScale
    """Spatial granularity, independent of category. See `enums.LocationScale`."""

    parent_location_id: uuid.UUID | None = None
    importance: Importance = 3
    tags: tuple[str, ...] = ()
    spatial_metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    created_at: dt.datetime
    updated_at: dt.datetime

    @field_validator("subtype")
    @classmethod
    def _canonical_subtype(cls, value: str | None) -> str | None:
        return parse_subtype(value)

    @field_validator("spatial_metadata", mode="before")
    @classmethod
    def _small_flat_metadata(cls, value: object) -> dict[str, str | int | float | bool]:
        return check_spatial_metadata(value)

    @field_validator("tags")
    @classmethod
    def _sane_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return clean_tags(value)

    @model_validator(mode="after")
    def _cannot_contain_itself(self) -> Self:
        """The one containment rule visible from a single node. The rest -- cycles,
        missing parents, scope mismatches -- needs the graph and lives in
        `hierarchy.py`."""
        if self.parent_location_id == self.id:
            raise ValueError(f"Location {self.id} cannot be its own parent.")
        return self

    @property
    def is_template(self) -> bool:
        return self.origin_session_id is None

    def visible_to(self, session_id: uuid.UUID) -> bool:
        """Whether this definition is part of `session_id`'s spatial reality.

        Template geography is visible to every session of its world; session-local
        canon only to the one that created it. This is the whole of the leakage rule,
        and every query that loads definitions has to honour it.
        """
        return self.origin_session_id is None or self.origin_session_id == session_id

    def describe(self) -> str:
        detail = f"{self.category.value}/{self.subtype}" if self.subtype else self.category.value
        return f"{self.name} ({detail}, {self.scale.value})"


class LocationZone(BaseModel):
    """A named area *inside* a location, deliberately lighter than a location.

    The fireplace, the bar, the tables by the window. A zone has no state, no
    connections, no children and cannot be travelled to -- it exists so a scene can say
    where someone is standing without minting a `LocationDefinition` (and a
    `LocationState` in every session) for a table.

    The rule for choosing, stated once here and again in the docs: make it a location
    when it needs persistent state, can contain other places, has its own exits, can be
    a travel destination, or matters outside the immediate scene. Otherwise a zone.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    location_id: uuid.UUID

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    category: str | None = None
    """Free-form and unvalidated beyond shape -- `seating`, `counter`, `threshold`.
    Zones are scene furniture; an enum here would be a vocabulary nobody consumes."""

    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    importance: Importance = 2
    tags: tuple[str, ...] = ()

    created_at: dt.datetime
    updated_at: dt.datetime

    @field_validator("category")
    @classmethod
    def _canonical_category(cls, value: str | None) -> str | None:
        return parse_subtype(value)

    @field_validator("tags")
    @classmethod
    def _sane_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return clean_tags(value)
