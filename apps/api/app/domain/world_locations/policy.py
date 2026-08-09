"""Who may bring a new place into existence, and at what size.

Gameplay inventing a bookshop on a street the player just walked down is the feature.
Gameplay inventing a continent is a different thing wearing the same shape, and the
only difference the engine can see is scale.

# Deliberately a boundary, not an engine

Two tiers and a table. No conditions, no scripting, no per-world configuration. The
purpose is that a decision *exists* and has one obvious place to grow, not that it is
already sophisticated -- a policy engine written before anything consults it would be
guessing at requirements that the travel, faction and quest systems have not stated.

`importance` is carried alongside scale into every decision, unused today, because a
"minor historical landmark" and a "major historical landmark" are the same scale and a
future policy will need to tell them apart.
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.errors import ValidationError
from app.domain.world_locations.definitions import Importance
from app.domain.world_locations.enums import LocationScale, scale_rank


class CreationPolicy(StrEnum):
    PERMISSIVE = "permissive"
    """Small, local, replaceable. A shop, a room, an alley, a house. Narration may
    establish these because getting one wrong costs a detail."""

    GUARDED = "guarded"
    """Geography the whole world is measured against. A city, a country, a planet.
    Getting one wrong reshapes every scene afterwards, so an authored source has to
    ask for it."""


LARGEST_PERMISSIVE_SCALE = LocationScale.SITE
"""The line, drawn once. A site -- a courtyard, a market square, a small landmark -- is
the largest thing narration may bring into being on its own. A district is not: a new
district implies streets, residents and a place in a city nobody authored."""


def creation_policy_for(scale: LocationScale) -> CreationPolicy:
    if scale_rank(scale) <= scale_rank(LARGEST_PERMISSIVE_SCALE):
        return CreationPolicy.PERMISSIVE
    return CreationPolicy.GUARDED


def check_creation_allowed(
    scale: LocationScale, *, importance: Importance, narrated: bool, name: str
) -> None:
    """Whether a new location of this size may be created by this kind of caller.

    `narrated` is the whole distinction: True for anything the Story Director asked
    for, False for authored template content and for game systems that resolved an
    action. Authored geography is not policed here -- an author writing a continent is
    the intended use of the model, and the tier exists to keep *narration* from doing
    it mid-scene.
    """
    if not narrated:
        return

    policy = creation_policy_for(scale)
    if policy is CreationPolicy.GUARDED:
        raise ValidationError(
            f"{name!r} is {scale.value}-scale, which narration may not create. The story "
            f"may establish places up to {LARGEST_PERMISSIVE_SCALE.value} scale -- a shop, "
            "a room, a courtyard. Anything larger reshapes geography every later scene is "
            "measured against, and has to be authored."
        )

    if importance >= 5:
        # Scale is what the model chose; importance is what it claims the place means.
        # A "point"-scale location asserted to reshape the story is the shape a model
        # takes when it has decided something big and picked a small enough box for it.
        raise ValidationError(
            f"{name!r} is asked for at importance {importance}, which is reserved for places "
            "the story is built around. Narration may establish ordinary places; a landmark "
            "that changes what the world is has to be authored."
        )
