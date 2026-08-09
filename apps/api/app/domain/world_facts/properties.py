"""Property names: the canonical identity of a fact.

A property is `namespace.segment[.segment...]`, every part snake_case:

    system.alive
    system.location
    world.political_status
    narrative.birthplace
    gameplay.palace_secret_discovered

The grammar is enforced, not suggested. `birthPlace`, `whereBorn` and
`Narrative.Birthplace` are rejected outright rather than normalised, because a
tolerant parser is how one logical property quietly becomes three: the uniqueness
constraint in storage can only prevent a contradiction between two facts it can see
are about the same thing.

# What normalisation can and cannot do

`PROPERTY_ALIASES` handles the spellings we know are the same field -- British and
American spellings, an obvious synonym, a rename we have already made. Everything
else that follows the grammar is accepted as a new property in its namespace.

There is deliberately no attempt to decide that `narrative.where_she_was_born` means
`narrative.birthplace`. That is semantic equivalence, it needs a language model to
guess at, and a wrong guess silently overwrites an unrelated fact. Structured
identity is the whole point of this table; see docs/world-state-facts.md.
"""

from __future__ import annotations

import re
from enum import StrEnum

from app.domain.errors import ValidationError

MAX_PROPERTY_LENGTH = 120


class PropertyNamespace(StrEnum):
    """The first segment of every property. Closed: a new one is a design decision."""

    SYSTEM = "system"
    """Mechanical state owned by deterministic game systems. Alive, location, hp."""

    WORLD = "world"
    """Diegetic truth about a place, object or the world itself. Condition, status."""

    NARRATIVE = "narrative"
    """Colour established through storytelling. Birthplace, tastes, nicknames."""

    GAMEPLAY = "gameplay"
    """Internal progression flags. Not necessarily a statement about the fiction."""

    DERIVED = "derived"
    """Computed from other state and never stored. Reserved so the name is taken."""


_SEGMENT = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*"
_PROPERTY_PATTERN = re.compile(rf"^{_SEGMENT}(?:\.{_SEGMENT})+$")

PROPERTY_ALIASES: dict[str, str] = {
    # Spellings of the same field, and renames we have already made. Small and
    # hand-maintained on purpose: every entry is a claim that two names mean exactly
    # one thing, and that claim has to be made by a person.
    "narrative.favourite_food": "narrative.favorite_food",
    "narrative.birth_place": "narrative.birthplace",
    "narrative.place_of_birth": "narrative.birthplace",
    "system.is_alive": "system.alive",
    "system.hit_points": "system.hp",
    "system.position": "system.location",
}


def parse_property(raw: str) -> str:
    """Validate and canonicalise a property name, or raise `ValidationError`."""
    text = raw.strip()
    if not text:
        raise ValidationError("A fact property must not be empty.")
    if len(text) > MAX_PROPERTY_LENGTH:
        raise ValidationError(
            f"A fact property may be at most {MAX_PROPERTY_LENGTH} characters; got {len(text)}."
        )
    if not _PROPERTY_PATTERN.match(text):
        raise ValidationError(
            f"{raw!r} is not a valid fact property. Use snake_case segments separated by "
            "dots, starting with a namespace -- for example 'system.alive' or "
            "'narrative.birthplace'. Display prose is not an identifier."
        )

    canonical = PROPERTY_ALIASES.get(text, text)
    namespace = canonical.split(".", 1)[0]
    if namespace not in set(PropertyNamespace):
        known = ", ".join(sorted(PropertyNamespace))
        raise ValidationError(
            f"Unknown property namespace {namespace!r} in {raw!r}. Known namespaces: {known}."
        )
    return canonical


def namespace_of(canonical_property: str) -> PropertyNamespace:
    """The namespace of an already-parsed property.

    Takes canonical input: anything reaching here has been through `parse_property`,
    so an unknown namespace is a programming error rather than bad input.
    """
    return PropertyNamespace(canonical_property.split(".", 1)[0])
