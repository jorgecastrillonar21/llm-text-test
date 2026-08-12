"""Where actors are: reading it, writing it, and refusing the impossible.

The canonical answer to "where is this character?" lives in
`app.domain.character_position`; this is the only thing allowed to write one. Every
position that reaches the database has been through `_validate`, which is where a
location id becomes a location *this session can see* rather than a uuid somebody
typed.

    resolve_position               one actor's position, `Unlocated` when unwritten
    player_position                the same, for the actor a session is played as
    actors_at                      who is at a place -- the reverse lookup
    set_position                   validate and store, replacing whole
    materialize_initial_position   what a new session starts at

# Why validation is here and not in the domain

The four shapes know their own internal rules -- a journey has two different ends, an
arrival is not before a departure -- and enforce them in `positions.py`. What they
cannot know is whether location `X` exists, whether this session can see it, or whether
that connection actually runs the way somebody claims. Those are questions about a
session's geography, which only a port can answer, so they are answered here.

# The player's actor id is the session id

There is no `player_character_id` in this build; the player is the session. Rather than
invent a nullable character reference and inherit SQLite's nullable-uniqueness hole,
`ActorKind` discriminates and the player's `actor_id` is the session's own id. When
Character Foundation arrives it re-points `actor_id` at a real character row and
nothing else about this seam changes -- which is the point of drawing it here rather
than inside a character model that does not exist yet.
"""

from __future__ import annotations

import logging
import uuid

from app.application.persistence import (
    ActorPosition,
    CharacterPositionPort,
    CharacterPositionReaderPort,
    SpatialPort,
    SpatialReaderPort,
)
from app.application.spatial_service import MAX_GRAPH_SIZE
from app.domain.character_position import (
    ActorKind,
    AtLocation,
    CharacterPosition,
    InTransit,
    Unlocated,
)
from app.domain.errors import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

MAX_POSITIONS = 200
"""How many positions one read returns.

A ceiling on a scene's cast, not a page size -- the same reasoning as
`spatial_service.MAX_GRAPH_SIZE`. A session with more actors than this has outgrown
loading them all at once, and the fix is a narrower query rather than a bigger number.
"""


async def resolve_position(
    reader: CharacterPositionReaderPort,
    *,
    session_id: uuid.UUID,
    actor_kind: ActorKind,
    actor_id: uuid.UUID,
) -> CharacterPosition:
    """One actor's position. `Unlocated` when nobody has written one.

    The single place where "no row" becomes a position, which is why the port returns
    `None` instead of doing it: an adapter that invented `Unlocated` itself would make
    "unwritten" and "deliberately unknown" indistinguishable at the one layer that can
    still tell them apart.
    """
    stored = await reader.get_character_position(
        session_id, actor_kind=actor_kind, actor_id=actor_id
    )
    return Unlocated() if stored is None else stored.position


async def player_position(
    reader: CharacterPositionReaderPort, *, session_id: uuid.UUID
) -> CharacterPosition:
    """Where the player is. See the module docstring for why the ids are the same."""
    return await resolve_position(
        reader, session_id=session_id, actor_kind=ActorKind.PLAYER, actor_id=session_id
    )


async def actors_at(
    reader: CharacterPositionReaderPort, *, session_id: uuid.UUID, location_id: uuid.UUID
) -> list[ActorPosition]:
    """Who is at a place, asked of the actors rather than of the place.

    This is what `LocationState.occupants` would have been, and the reason there is no
    such field: two authorities for one fact, guaranteed to disagree the first time a
    position is written without the room being told. One position row per actor, and
    "who is here" is a query.
    """
    return await reader.load_character_positions(
        session_id, location_id=location_id, limit=MAX_POSITIONS
    )


async def set_position(store: SpatialPort, position: ActorPosition) -> uuid.UUID:
    """Validate an actor's new position against this session's world, then store it.

    Staged, not committed: a position written during a turn belongs to that turn's
    transaction, and an actor who moved in a turn that failed did not move.

    Raises `NotFoundError` when a referenced session, location, zone, connection or
    character does not exist or is not visible here, and `ValidationError` when the
    position is well-formed but impossible -- a transit along a connection that does not
    run between its two ends, or a player whose actor id is not their session.
    """
    session = await store.get_session(position.session_id)
    if session is None:
        raise NotFoundError("GameSession", position.session_id)

    await _validate_actor(store, position, world_id=session.world_id)
    await _validate_place(store, position)
    return await store.set_character_position(position)


async def _validate_actor(
    store: CharacterPositionPort, position: ActorPosition, *, world_id: uuid.UUID
) -> None:
    """Whose position this claims to be has to be somebody.

    `actor_id` carries no foreign key -- it points at a session or at a character
    depending on `actor_kind`, and no single column can reference both -- so the check
    the database cannot make is made here instead. See
    `infrastructure.db.models.CharacterPositionRow`.
    """
    if position.actor_kind is ActorKind.PLAYER:
        if position.actor_id != position.session_id:
            raise ValidationError(
                f"Player position for session {position.session_id} names actor "
                f"{position.actor_id}. The player of a session is that session; a "
                f"different id here is a second player nobody created."
            )
        return

    if position.actor_id not in await store.known_character_ids(world_id):
        raise NotFoundError("Character", position.actor_id)


async def _validate_place(store: SpatialReaderPort, position: ActorPosition) -> None:
    """Every id in the position has to name geography this session can actually see.

    The whole reason `CharacterPosition` exists. A name match could only fail to find
    something; an unchecked uuid can succeed at finding *another save's* room, and hand
    a director its exits as canon.
    """
    session_id = position.session_id
    match position.position:
        case AtLocation(location_id=location_id, zone_id=zone_id):
            if await store.get_location(session_id, location_id) is None:
                raise NotFoundError("Location", location_id)
            if zone_id is not None:
                zones = await store.load_zones(location_id)
                if all(zone.id != zone_id for zone in zones):
                    # Not a missing zone but a contradictory one: a zone of somewhere
                    # else would place the actor in two locations at once.
                    raise NotFoundError("LocationZone", zone_id)
        case InTransit() as transit:
            for endpoint in (transit.origin_location_id, transit.destination_location_id):
                if await store.get_location(session_id, endpoint) is None:
                    raise NotFoundError("Location", endpoint)
            connection = await store.get_connection(session_id, transit.connection_id)
            if connection is None:
                raise NotFoundError("LocationConnection", transit.connection_id)
            # Direction is honoured, so a one-way connection cannot be walked backwards
            # by writing a position: `leads_from` returns None from the far end of a
            # one-way edge, which is the same answer geography gives a traveller.
            if connection.leads_from(transit.origin_location_id) != transit.destination_location_id:
                raise ValidationError(
                    f"Connection {transit.connection_id} does not run from "
                    f"{transit.origin_location_id} to {transit.destination_location_id}. "
                    f"A transit has to use an edge that goes where it claims to go."
                )
        case _:
            pass  # Offstage and Unlocated reference nothing, so there is nothing to check.


async def materialize_initial_position(store: SpatialPort, *, session_id: uuid.UUID) -> uuid.UUID:
    """Give a new session an explicit position for its player.

    Always a row, never an absence. A session with no position at all would leave every
    later read guessing between "nowhere yet" and "we never wrote one", which is the
    ambiguity this whole correction exists to remove.

    The legacy `current_location` string gets read here, and this is the *only* place in
    the running system that still reads it. If it names exactly one place the session
    can see, the player starts `AtLocation` there; otherwise `Unlocated`. That is the
    last name-to-id resolution in the system's life -- see
    `migrations/versions/7c41a9f2b6d3` for the same rule applied once to existing saves.

    Staged, not committed: session creation is one transaction and this is part of it.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    return await store.set_character_position(
        ActorPosition(
            session_id=session_id,
            actor_kind=ActorKind.PLAYER,
            actor_id=session_id,
            position=await _seed_position(
                store,
                session_id=session_id,
                world_id=session.world_id,
                current_location=session.current_location,
            ),
        )
    )


async def _seed_position(
    store: SpatialReaderPort,
    *,
    session_id: uuid.UUID,
    world_id: uuid.UUID,
    current_location: str,
) -> CharacterPosition:
    """The legacy string, resolved to an id exactly once, or an honest gap.

    Matching is exact, case-insensitive, whitespace-trimmed, and **refuses ambiguity**:
    two places named "Market Street" resolve to nothing rather than to whichever row the
    query reached first. No fuzzy matching, no substring search, no "closest name" --
    those turn a missing match into a *wrong* one, and being wrong here is worse than
    being empty, because what comes out is written down as canon.

    Nothing calls this afterwards. From the second turn onward the position is the
    authority and the string is presentation; validation of the id it produces is the
    caller's, via `set_position` -- which is why this seeds through `store` and the
    caller commits.
    """
    wanted = current_location.strip().casefold()
    if not wanted:
        return Unlocated()

    # The session's own visible geography, template plus this save's -- the adapter
    # applies that filter, and it is what stops another save's "Market Street" counting
    # as a match here.
    definitions = await store.load_locations(session_id, world_id=world_id, limit=MAX_GRAPH_SIZE)
    matches = [
        definition for definition in definitions if definition.name.strip().casefold() == wanted
    ]
    if len(matches) != 1:
        if matches:
            logger.info(
                "Session %s named %r at creation and this world has %d places by that "
                "name. Starting unlocated rather than guessing.",
                session_id,
                current_location,
                len(matches),
            )
        return Unlocated()
    return AtLocation(location_id=matches[0].id)
