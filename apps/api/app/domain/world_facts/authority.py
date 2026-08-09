"""Who is asking, and what that entitles them to.

Authority is not a rank. There is no "level 4 outranks level 2" comparison anywhere,
because the interesting distinctions are not ordered: the simulation and a resolved
player action have the same reach for different reasons, and `seed` may establish
starting truth it would have no business changing mid-story.

What exists instead is a table of which policies each authority may write. It is the
same shape as the time-advance permission table in `world_time.advancement`, for the
same reason: a table can be read, tested and printed, and an `if` chain cannot.

# The Story Director has the least authority over mechanical state

    STORY_DIRECTOR -> {OPEN}

That is the architectural rule of this module. The language model may establish
narrative colour and nothing else. It may not decide that a character is alive, where
they are, what they carry, or that a gameplay flag has flipped -- not by proposing it,
and not by narrating it as though it were already true. Prose is not a write path.

GUARDED is deliberately absent from that set rather than routed somewhere. A guarded
proposal needs review logic that this release does not have, so it is refused with a
reason that says so; see `docs/world-state-facts.md`.

Nobody may write DERIVED. Not the engine, not admin. A derived property has no
storage by definition -- persisting one creates a second copy of a truth that already
exists somewhere else, and the two will disagree.
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.errors import FactPolicyError
from app.domain.world_facts.policy import FactPolicy


class FactAuthority(StrEnum):
    ENGINE = "engine"
    """Deterministic game systems: resolution, travel, rest, combat when it exists."""

    SIMULATION = "simulation"
    """The world moving on its own, offscreen."""

    PLAYER_RESOLUTION = "player_resolution"
    """The resolved outcome of something the player did."""

    STORY_DIRECTOR = "story_director"
    """The language model. Narrative colour only -- see the module docstring."""

    SEED = "seed"
    """A world's starting truth, materialised when a session begins."""

    ADMIN = "admin"
    """Developer and debug tooling. Broad, but not exempt from the world's own rules."""


_PERMITTED_POLICIES: dict[FactAuthority, frozenset[FactPolicy]] = {
    FactAuthority.ENGINE: frozenset({FactPolicy.OPEN, FactPolicy.GUARDED, FactPolicy.SYSTEM}),
    FactAuthority.SIMULATION: frozenset({FactPolicy.OPEN, FactPolicy.GUARDED, FactPolicy.SYSTEM}),
    FactAuthority.PLAYER_RESOLUTION: frozenset(
        {FactPolicy.OPEN, FactPolicy.GUARDED, FactPolicy.SYSTEM}
    ),
    FactAuthority.STORY_DIRECTOR: frozenset({FactPolicy.OPEN}),
    FactAuthority.SEED: frozenset({FactPolicy.OPEN, FactPolicy.GUARDED, FactPolicy.SYSTEM}),
    FactAuthority.ADMIN: frozenset({FactPolicy.OPEN, FactPolicy.GUARDED, FactPolicy.SYSTEM}),
}

_SOURCE_EVENT_EXEMPT = frozenset(
    {FactAuthority.SEED, FactAuthority.ADMIN, FactAuthority.STORY_DIRECTOR}
)
"""Authorities allowed to establish a fact with no originating GameEvent.

`seed` writes before anything has happened. `admin` is out-of-band by nature. The
Story Director establishes small narrative details during a turn, and minting a
`FACT_CREATED` event for "Elena dislikes olives" would fill the history with rows
nobody will ever read -- the turn that produced it is already recorded.

Everything else is a mechanical change, and a mechanical change with no event is a
world that cannot answer "why is this true?".
"""


def is_permitted(authority: FactAuthority, policy: FactPolicy) -> bool:
    return policy in _PERMITTED_POLICIES[authority]


def require_permitted(
    authority: FactAuthority, policy: FactPolicy, *, canonical_property: str
) -> None:
    """Raise unless `authority` may write a property governed by `policy`."""
    if is_permitted(authority, policy):
        return

    if policy is FactPolicy.DERIVED:
        raise FactPolicyError(
            f"{canonical_property!r} is a derived property and is never stored. "
            "It is computed from authoritative state; write that state instead."
        )
    if authority is FactAuthority.STORY_DIRECTOR and policy is FactPolicy.GUARDED:
        raise FactPolicyError(
            f"{canonical_property!r} is guarded: the Story Director may propose it, but "
            "accepting a guarded proposal needs review logic this build does not have. "
            "Establish it through a game system, or use a 'narrative.*' property."
        )
    raise FactPolicyError(
        f"Authority {authority.value!r} may not write {canonical_property!r}, "
        f"which is {policy.value}."
    )


def requires_source_event(authority: FactAuthority) -> bool:
    """Whether a fact from this authority must name the GameEvent that caused it."""
    return authority not in _SOURCE_EVENT_EXEMPT
