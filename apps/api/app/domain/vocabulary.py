"""The small shared rules for open-ended words the engine has to group by.

Three domains now let a world name things the enums could not anticipate -- a
location's `subtype`, a situation's `subtype`, tags on both, and a flat metadata bag
on each. The *vocabulary* is deliberately open. Its *shape* is not, and the shape
rules are identical everywhere: a subtype is a lowercase identifier, a tag list is
short and deduplicated, a metadata bag is flat and bounded.

They live here because a third consumer arrived. Two copies of a normaliser drift
slowly and silently -- one gains a length check, the other does not, and then
`Tavern` and `tavern` are two subtypes in one half of the system and one in the
other. One copy cannot.

# Why not a base model

Because these are used by fields with different names, on models with different
validators, in packages that must not import each other. `world_situations` cannot
import `world_locations` and should not have to; both import this.
"""

from __future__ import annotations

from app.domain.errors import ValidationError

MAX_SUBTYPE_LENGTH = 60
MAX_TAGS = 8
MAX_TAG_LENGTH = 40
MAX_METADATA_KEYS = 12
MAX_METADATA_KEY_LENGTH = 60
MAX_METADATA_VALUE_LENGTH = 200

MetadataValue = str | int | float | bool
"""What a metadata bag may hold. Scalars only -- see `check_flat_metadata`."""

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


def clean_tags(value: tuple[str, ...]) -> tuple[str, ...]:
    """Lowercase, trim, drop blanks and duplicates, keep the order they arrived in."""
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


def check_flat_metadata(value: object, *, field: str) -> dict[str, MetadataValue]:
    """A small flat bag for whatever a world genuinely measures.

    Optional in the strongest sense: nothing in the engine reads it, and nothing may
    come to require it. It exists so a world that really does care that a corridor is
    forty metres long, or that an investigation is at stage three, has somewhere to say
    so -- without every other world inventing dimensions to fill a column.

    Flat and bounded for the same reason fact values are: a nested document here is the
    beginning of a second, unvalidated domain model living inside a JSON blob. When a
    subtype's data outgrows this, the answer is a typed model, not a deeper dict.

    `field` names the caller's field so the error says `spatial_metadata` or
    `situation_metadata` rather than something generic the reader has to trace.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(
            f"{field} must be an object of simple values; got {type(value).__name__}."
        )
    if len(value) > MAX_METADATA_KEYS:
        raise ValidationError(
            f"{field} may hold at most {MAX_METADATA_KEYS} keys; got {len(value)}."
        )
    cleaned: dict[str, MetadataValue] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValidationError(f"{field} keys must be non-empty strings.")
        if len(key) > MAX_METADATA_KEY_LENGTH:
            raise ValidationError(
                f"{field} key {key!r} is longer than {MAX_METADATA_KEY_LENGTH} characters."
            )
        if not isinstance(item, str | int | float | bool):
            raise ValidationError(
                f"{field}[{key!r}] must be a string, number or boolean; "
                f"got {type(item).__name__}. Nested structures belong in their own model."
            )
        if isinstance(item, str) and len(item) > MAX_METADATA_VALUE_LENGTH:
            raise ValidationError(
                f"{field}[{key!r}] is longer than {MAX_METADATA_VALUE_LENGTH} characters."
            )
        cleaned[key.strip()] = item
    return cleaned
