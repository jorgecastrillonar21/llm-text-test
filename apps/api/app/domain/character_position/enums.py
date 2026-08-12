"""The closed vocabularies of spatial presence.

Two enums, both small on purpose. `PositionKind` is the discriminator of the position
union and grows only when a genuinely new *shape* of presence exists -- not when a new
reason for being somewhere does. `ActorKind` says whose position a row is, and exists
because the player is not a `Character` row in this system yet.
"""

from __future__ import annotations

from enum import StrEnum


class PositionKind(StrEnum):
    """The four shapes a spatial presence can have.

    AT_LOCATION  somewhere specific, and the scene can be built there
    IN_TRANSIT   between two places, on a declared connection
    OFFSTAGE     deliberately not in the scene -- exists, is not anywhere for now
    UNLOCATED    nobody has said where this actor is

    OFFSTAGE and UNLOCATED are both "not at a location", and collapsing them would
    lose the distinction that matters most: one is a decision and the other is a gap.
    A character the story has parked between appearances is not the same as a session
    whose starting place was never resolved, and a system that could not tell them
    apart would either invent a position for the first or report the second as
    intentional.
    """

    AT_LOCATION = "at_location"
    IN_TRANSIT = "in_transit"
    OFFSTAGE = "offstage"
    UNLOCATED = "unlocated"


class ActorKind(StrEnum):
    """Whose position this is.

    PLAYER     the session's own protagonist, identified by the session id
    CHARACTER  a row in `characters`, identified by its own id

    The player is a separate kind rather than a `Character` because this application
    has no player character record: a session carries `player_name` and
    `player_description` and nothing that could be positioned. Rather than mint a
    shadow character -- which is Character Foundation's decision to make, not this
    correction's -- the player's actor id *is* the session id, and this enum is what
    keeps that from being a collision with a real character that happens to share it.

    When Character Foundation gives the player a real character row, that row's id
    becomes the actor id and the kind becomes CHARACTER. Nothing else about the
    contract changes, which is the point of having the discriminator now.
    """

    PLAYER = "player"
    CHARACTER = "character"
