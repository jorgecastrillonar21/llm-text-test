"""Reading and growing a session's spatial reality.

Three jobs, and deliberately not a fourth:

    materialize_initial_spatial_state   give a new session a state row per place
    create_session_location             let gameplay add canon to one save
    load_spatial_graph                  the index every hierarchy question needs

What is not here is *changing* state. `UpdateLocationState` and
`UpdateConnectionState` go through `state_service` with every other mutation, because
a collapsing bridge changes a location, a connection and a danger modifier together
and all of them belong to one event. Splitting spatial writes into their own service
would have created a second transaction boundary and a second revision counter.

# Definitions are loaded whole

Containment questions -- ancestors, descendants, is-this-inside-that -- need more than
one node at a time, and answering them one query at a time is how an N+1 gets written.
So the visible definitions are loaded once into a `LocationIndex` and walked in memory.

That is affordable because a narrative world holds tens to low hundreds of places, not
a street network. `MAX_GRAPH_SIZE` is the guard on that assumption rather than a page
size: a world that exceeds it has outgrown this approach, and the fix is a recursive
CTE in the adapter rather than a larger number here.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from pydantic import BaseModel, ConfigDict

from app.application.persistence import (
    ConnectionStateWrite,
    LocationStateWrite,
    NewConnection,
    NewLocation,
    NewZone,
    SpatialPort,
    SpatialReaderPort,
)
from app.domain.errors import NotFoundError, ValidationError
from app.domain.world_locations import (
    LocationAccessibility,
    LocationCondition,
    LocationConnection,
    LocationConnectionState,
    LocationDefinition,
    LocationIndex,
    LocationState,
    check_creation_allowed,
    check_parent,
)

logger = logging.getLogger(__name__)

MAX_GRAPH_SIZE = 500
"""How many definitions or connections one session's spatial graph may hold.

A ceiling, not a page size. See the module docstring for why exceeding it is a signal
rather than something to paginate around.
"""


class SpatialGraph(BaseModel):
    """One session's visible geography, loaded once and queried many times.

    Carries the definitions, the connections and both kinds of state together because
    every interesting question needs at least two of them: "can I get into the castle?"
    is a location, an edge and two states.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    index: LocationIndex
    connections: list[LocationConnection]
    location_states: dict[uuid.UUID, LocationState]
    connection_states: dict[uuid.UUID, LocationConnectionState]

    def exits_from(self, location_id: uuid.UUID) -> list[tuple[LocationConnection, uuid.UUID]]:
        """Edges leaving this place, with where each one leads.

        Direction is honoured: the far end of a one-way connection is not an exit back.
        Every edge is returned regardless of whether it is currently passable --
        deciding that is `is_passable`, and a blocked door is something a scene should
        know about rather than something to hide.
        """
        found: list[tuple[LocationConnection, uuid.UUID]] = []
        for connection in self.connections:
            destination = connection.leads_from(location_id)
            if destination is not None:
                found.append((connection, destination))
        return found

    def is_passable(self, connection_id: uuid.UUID) -> bool:
        """Whether this edge may currently be crossed.

        A connection with no state row is passable: state is materialised for template
        geography when a session starts, and a missing row means nothing has happened
        to it. Absence is not a blockage.
        """
        state = self.connection_states.get(connection_id)
        return True if state is None else state.is_passable


async def load_spatial_graph(
    reader: SpatialReaderPort, *, session_id: uuid.UUID | None, world_id: uuid.UUID
) -> SpatialGraph:
    """Everything this session can see, in one object.

    Four queries, always the same four, regardless of how deep the questions go
    afterwards. Template geography plus this session's own -- the adapter applies that
    filter, and it is the only rule standing between two saves of one world.

    `session_id=None` asks for the template alone, which is what world authoring works
    against. There is no session, so there is no state: both state maps come back
    empty, and every place reads as its unmodified self.
    """
    definitions = await reader.load_locations(session_id, world_id=world_id, limit=MAX_GRAPH_SIZE)
    connections = await reader.load_connections(session_id, world_id=world_id, limit=MAX_GRAPH_SIZE)
    if len(definitions) == MAX_GRAPH_SIZE or len(connections) == MAX_GRAPH_SIZE:
        # Loud, because a truncated graph answers containment questions wrongly rather
        # than partially: a missing parent turns a nested location into a root.
        logger.warning(
            "Session %s reached the %d-row spatial graph ceiling; hierarchy answers may "
            "be incomplete. This world has outgrown in-memory traversal.",
            session_id,
            MAX_GRAPH_SIZE,
        )

    location_states = [] if session_id is None else await reader.load_location_states(session_id)
    connection_states = (
        [] if session_id is None else await reader.load_connection_states(session_id)
    )
    return SpatialGraph(
        index=LocationIndex(definitions),
        connections=connections,
        location_states={state.location_id: state for state in location_states},
        connection_states={state.connection_id: state for state in connection_states},
    )


async def materialize_initial_spatial_state(store: SpatialPort, *, session_id: uuid.UUID) -> int:
    """Give a new session a state row for every place and edge it can see.

    Definitions are *not* copied -- ten sessions of a world read the same geography.
    What is per-session is the mutable part, and this creates it at defaults so that
    "nothing has happened here yet" is a row rather than an absence the rest of the
    code has to keep second-guessing.

    Staged, not committed: session creation is one transaction and this is part of it.
    Returns how many rows were written, which is zero for a world with no geography and
    is the common case today.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    definitions = await store.load_locations(
        session_id, world_id=session.world_id, limit=MAX_GRAPH_SIZE
    )
    connections = await store.load_connections(
        session_id, world_id=session.world_id, limit=MAX_GRAPH_SIZE
    )

    written = 0
    for definition in definitions:
        await store.set_location_state(_default_location_state(session_id, definition.id))
        written += 1
    for connection in connections:
        await store.set_connection_state(_default_connection_state(session_id, connection.id))
        written += 1
    return written


def _default_location_state(session_id: uuid.UUID, location_id: uuid.UUID) -> LocationStateWrite:
    """Intact and open, unguarded, no danger modifier, unowned.

    Defaults live here rather than only on the column so that "what a new session
    starts at" is one readable line in the application, not something reconstructed by
    reading DDL. `LocationState`'s own field defaults agree with these.
    """
    return LocationStateWrite(
        session_id=session_id,
        location_id=location_id,
        condition=LocationCondition.INTACT,
        accessibility=LocationAccessibility.OPEN,
        security_level=0,
        local_danger_modifier=0,
    )


def _default_connection_state(
    session_id: uuid.UUID, connection_id: uuid.UUID
) -> ConnectionStateWrite:
    return ConnectionStateWrite(
        session_id=session_id,
        connection_id=connection_id,
        condition=LocationCondition.INTACT,
        accessibility=LocationAccessibility.OPEN,
        traversal_modifier=0,
    )


async def create_location(
    store: SpatialPort,
    *,
    session_id: uuid.UUID | None,
    location: NewLocation,
    narrated: bool,
) -> uuid.UUID:
    """Add a place, checking everything that cannot be a column constraint.

    `session_id` is the save this is being created *in*: it decides which geography the
    parent must be visible within, and gets a starting `LocationState`. `None` is
    template authoring -- no session, no state row, and containment validated against
    the template alone.

    `narrated` says whether this came from the Story Director. It gates the creation
    policy and nothing else: an authored continent is the intended use of the model,
    and the same continent proposed mid-scene is not.

    The id is minted here, by the application. A caller never supplies one -- least of
    all the language model, which would be reproducing a uuid it read in a prompt.

    Staged, not committed. The caller owns the transaction, which for a turn means a
    location the story invented dies with a failed turn instead of outliving the scene
    that mentioned it.
    """
    if session_id is not None:
        session = await store.get_session(session_id)
        if session is None:
            raise NotFoundError("GameSession", session_id)
        if location.world_id != session.world_id:
            raise ValidationError(
                f"Location {location.name!r} names world {location.world_id}, but this "
                f"session is playing {session.world_id}."
            )

    check_creation_allowed(
        location.scale,
        importance=location.importance,
        narrated=narrated,
        name=location.name,
    )

    if location.parent_location_id is not None:
        graph = await load_spatial_graph(store, session_id=session_id, world_id=location.world_id)
        # Validated as the definition it is about to become, so `check_parent` sees the
        # same world and origin the row will have. The id is a placeholder: nothing is
        # in the index yet, so no cycle can run through it, and the real id is minted
        # below.
        check_parent(
            graph.index,
            child=_prospective(location),
            parent_id=location.parent_location_id,
        )

    location_id = await store.add_location(location)
    if session_id is not None:
        # Created with the state a new place starts at, in the same transaction. A
        # definition with no state would be a place every later read has to
        # special-case. Template authoring writes none: sessions that already exist
        # pick up the default lazily, which `load_spatial_graph` and
        # `_apply_location_update` both handle.
        await store.set_location_state(_default_location_state(session_id, location_id))
    return location_id


def _prospective(location: NewLocation) -> LocationDefinition:
    """The definition `location` will become, for validation that needs a whole one.

    A placeholder id and timestamps, because `check_parent` reads world, origin and
    identity, and building a throwaway is cheaper than teaching it a second shape.
    """
    now = dt.datetime.now(dt.UTC)
    return LocationDefinition(
        id=uuid.uuid4(),
        world_id=location.world_id,
        origin_session_id=location.origin_session_id,
        name=location.name,
        description=location.description,
        category=location.category,
        subtype=location.subtype,
        scale=location.scale,
        parent_location_id=location.parent_location_id,
        importance=location.importance,
        tags=location.tags,
        spatial_metadata=location.spatial_metadata,
        created_at=now,
        updated_at=now,
    )


async def create_connection(
    store: SpatialPort, *, session_id: uuid.UUID | None, connection: NewConnection
) -> uuid.UUID:
    """Add a traversal, after checking both ends exist and are visible here.

    Endpoint validation is the whole point: a connection to a location this session
    cannot see is an edge into another save's geography, and a template connection to a
    session-local place would be an edge that exists in exactly one save.
    """
    if session_id is not None and await store.get_session(session_id) is None:
        raise NotFoundError("GameSession", session_id)

    for endpoint in (connection.from_location_id, connection.to_location_id):
        if await store.get_location(session_id, endpoint) is None:
            raise NotFoundError("Location", endpoint)

    connection_id = await store.add_connection(connection)
    if session_id is not None:
        await store.set_connection_state(_default_connection_state(session_id, connection_id))
    return connection_id


async def create_zone(
    store: SpatialPort, *, session_id: uuid.UUID | None, zone: NewZone
) -> uuid.UUID:
    """Add a zone to a location visible from `session_id` (or to a template place).

    No state row: a zone has none. It is scene furniture, and giving it per-session
    state would make it a location with extra steps.
    """
    if await store.get_location(session_id, zone.location_id) is None:
        raise NotFoundError("Location", zone.location_id)
    return await store.add_zone(zone)
