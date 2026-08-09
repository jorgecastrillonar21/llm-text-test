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

from app.domain.errors import ValidationError
from app.domain.world_locations.enums import LocationCategory, LocationScale

Importance = Annotated[int, Field(ge=1, le=5)]
"""How much a place matters when the prompt has room for only some of them. As with
facts, never a permission."""

MAX_NAME_LENGTH = 200
MAX_SUBTYPE_LENGTH = 60
MAX_DESCRIPTION_LENGTH = 4000
MAX_TAGS = 8
MAX_TAG_LENGTH = 40
MAX_METADATA_KEYS = 12
MAX_METADATA_KEY_LENGTH = 60
MAX_METADATA_VALUE_LENGTH = 200

_SUBTYPE_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789_")


def parse_subtype(raw: str | None) -> str | None:
    """Normalise a free-form subtype, or raise.

    Deliberately permissive about *which* words are allowed -- `orbital_station` and
    `enchanted_forest` are equally valid and no enum could hold both genres -- and
    deliberately strict about their *shape*. A subtype is an identifier the engine
    groups and filters by, so `Tavern`, `tavern ` and `tavern` must not be three
    things.
    """
    if raw is None:
        return None
    text = raw.strip().casefold().replace(" ", "_").replace("-", "_")
    if not text:
        return None
    if len(text) > MAX_SUBTYPE_LENGTH:
        raise ValidationError(
            f"A subtype may be at most {MAX_SUBTYPE_LENGTH} characters; got {len(text)}."
        )
    if not set(text) <= _SUBTYPE_ALLOWED:
        raise ValidationError(
            f"{raw!r} is not a usable subtype. Use lowercase words joined by underscores, "
            "for example 'tavern', 'orbital_station' or 'enchanted_forest'."
        )
    return text


def check_spatial_metadata(value: object) -> dict[str, str | int | float | bool]:
    """A small flat bag for whatever a world genuinely measures.

    Optional in the strongest sense: nothing in the engine reads it, and nothing may
    come to require it. It exists so a world that really does care that a corridor is
    forty metres long has somewhere to say so, without every other world inventing
    dimensions to fill a column.

    Flat and bounded for the same reason fact values are: a nested document here is
    the beginning of a second, unvalidated spatial model living inside a JSON blob.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(
            f"spatial_metadata must be an object of simple values; got {type(value).__name__}."
        )
    if len(value) > MAX_METADATA_KEYS:
        raise ValidationError(
            f"spatial_metadata may hold at most {MAX_METADATA_KEYS} keys; got {len(value)}."
        )
    cleaned: dict[str, str | int | float | bool] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValidationError("spatial_metadata keys must be non-empty strings.")
        if len(key) > MAX_METADATA_KEY_LENGTH:
            raise ValidationError(
                f"spatial_metadata key {key!r} is longer than {MAX_METADATA_KEY_LENGTH} characters."
            )
        if not isinstance(item, str | int | float | bool):
            raise ValidationError(
                f"spatial_metadata[{key!r}] must be a string, number or boolean; "
                f"got {type(item).__name__}. Nested structures belong in their own model."
            )
        if isinstance(item, str) and len(item) > MAX_METADATA_VALUE_LENGTH:
            raise ValidationError(
                f"spatial_metadata[{key!r}] is longer than {MAX_METADATA_VALUE_LENGTH} characters."
            )
        cleaned[key.strip()] = item
    return cleaned


def clean_tags(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(value) > MAX_TAGS:
        raise ValueError(f"At most {MAX_TAGS} tags are allowed; got {len(value)}.")
    cleaned: list[str] = []
    for tag in value:
        text = tag.strip().casefold()
        if not text:
            continue
        if len(text) > MAX_TAG_LENGTH:
            raise ValueError(f"Tag {tag!r} is longer than {MAX_TAG_LENGTH} characters.")
        if text not in cleaned:
            cleaned.append(text)
    return tuple(cleaned)


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
