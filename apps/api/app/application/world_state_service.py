"""Reading one session's world: the root, and a composed view of it.

# What this is

Four small reads and one projection. `get_state_root`, `get_revision` and
`get_current_time` answer questions about the root -- which shape the state is in, how
many times it has changed, what time it is -- and `build_snapshot` assembles a view
across the domains that own the rest.

# What a snapshot is not

`CurrentWorldSnapshot` is a **read projection**. It is built on demand from the tables
that own each part of a session's reality and thrown away; nothing here is stored,
nothing here is authoritative, and there is no path by which writing to one of these
objects could reach the database. The persisted root is four columns on `game_sessions`
and the state itself lives in `world_facts`, `location_states`,
`location_connection_states`, `situations`, `scheduled_events` and `game_events`. A
snapshot is a photograph of those; it is not where they live. See `docs/world-state.md`.

That distinction is why this module takes `WorldStateReaderPort` -- a port with no write
method on it at all. A service that could read the whole world *and* change it would
invite the one shortcut this decomposition exists to prevent: read everything, fix it
up in memory, hand the result back as the new truth. State changes go through a
resolution and a typed mutation, always.

# Scopes, because size is the whole problem

    MINIMAL      the root, the counts, the few facts and situations that matter
    RELEVANT     that, plus where the scene is and what is one step away
    REGIONAL     that, plus the containers and contents around it
    FULL_DEBUG   everything, for a human looking at a database

Only the first is cheap enough to be free. `FULL_DEBUG` exists for an operator with a
browser open and is not a context source: nothing that builds a prompt should call this
module at all, because `story_context` already decides what a language model sees and
having more state available has never meant sending more of it. See "StoryContext is not
WorldState" in docs/world-state.md.

# One transaction

Every read goes through the caller's port, which is the request's open session. A
snapshot therefore comes from one consistent view of the database rather than from
eight queries that could each see a different revision.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.application.persistence import (
    ScheduledEventRecord,
    SessionSnapshot,
    WorldStateReaderPort,
)
from app.application.situation_service import MAX_SITUATIONS
from app.application.spatial_context import resolve_scene_location
from app.application.spatial_service import MAX_GRAPH_SIZE, SpatialGraph, load_spatial_graph
from app.domain.errors import NotFoundError
from app.domain.resolution import GameEvent
from app.domain.world_facts import WorldFact
from app.domain.world_locations import (
    LocationConnection,
    LocationConnectionState,
    LocationDefinition,
    LocationState,
    get_ancestors,
    get_children,
)
from app.domain.world_situations import LIVE_STATUSES, Situation
from app.domain.world_state import WorldStateV1, read_world_state
from app.domain.world_time import (
    ScheduledEventStatus,
    TimeProjection,
    TimeState,
    project_time,
)

logger = logging.getLogger(__name__)

SNAPSHOT_FACT_LIMIT = 500
"""How many facts a snapshot will read. A ceiling rather than a page size: a session
holding five hundred established truths has outgrown "show me the world" as a single
request, and the snapshot says so through `truncated` rather than lying about the count.
"""

SNAPSHOT_EVENT_LIMIT = 50
"""Recent history in a debug snapshot. Fifty is a screenful; the full trail has its own
endpoint, which pages."""

IMPORTANT_FACT_LIMIT = 10
"""How many facts reach a minimal snapshot. These are the load-bearing ones -- the
reader wants "the king is dead", not the colour of a door."""

MINIMAL_FACT_IMPORTANCE = 4
"""The floor for "critical" in a minimal snapshot. Importance runs 1-5; 4 and 5 are the
truths a reader would want to know before anything else."""

ACTIVE_SITUATION_LIMIT = 8
"""How many live situations reach a minimal snapshot, most important first."""


class SnapshotScope(StrEnum):
    """How much of the world a snapshot carries.

    Ordered from cheapest to most expensive, and each one is a superset of the one
    before. A caller that does not say which it wants gets `MINIMAL`, because the
    expensive answer should never be the accidental one.
    """

    MINIMAL = "minimal"
    RELEVANT = "relevant"
    REGIONAL = "regional"
    FULL_DEBUG = "full_debug"


_ORDER = {
    SnapshotScope.MINIMAL: 0,
    SnapshotScope.RELEVANT: 1,
    SnapshotScope.REGIONAL: 2,
    SnapshotScope.FULL_DEBUG: 3,
}


class PlaceView(BaseModel):
    """A place and how this session has it.

    Two objects rather than one merged shape, because they are two different things:
    `definition` may be shared template geography that every session sees, and `state`
    is this session's own and may be absent. Flattening them would hide which half a
    reader is looking at, and the answer to "did we change this?" is exactly that.
    """

    model_config = ConfigDict(frozen=True)

    definition: LocationDefinition
    state: LocationState | None = None


class ConnectionView(BaseModel):
    """A traversal and this session's state for it. Same split, same reason."""

    model_config = ConfigDict(frozen=True)

    connection: LocationConnection
    state: LocationConnectionState | None = None


class SnapshotCounts(BaseModel):
    """How much of each thing this session has.

    Counted from the same bounded reads the snapshot is built from, so a count and the
    list beside it never disagree. When a read hits its ceiling the count is a floor
    rather than a total, and `CurrentWorldSnapshot.truncated` is what says so.
    """

    model_config = ConfigDict(frozen=True)

    facts: int
    locations: int
    connections: int
    active_situations: int
    situations: int
    pending_scheduled_events: int


class CurrentWorldSnapshot(BaseModel):
    """A composed view of one session's world at one revision.

    Built, read and discarded. See the module docstring for why this is emphatically
    not a persistence aggregate.

    Which fields are populated depends on `scope`, and an empty list means "not in this
    scope" exactly as often as it means "there are none" -- `counts` is what
    distinguishes them, and it is filled at every scope.
    """

    model_config = ConfigDict(frozen=True)

    scope: SnapshotScope
    version: int
    session_id: uuid.UUID
    revision: int
    time: TimeProjection
    counts: SnapshotCounts

    truncated: bool = False
    """True when some read hit its ceiling. A client that sees this and treats the
    snapshot as the whole world is wrong, and should know."""

    # MINIMAL and up.
    important_facts: list[WorldFact] = []
    active_situations: list[Situation] = []

    # RELEVANT and up.
    current_location: PlaceView | None = None
    """Where the scene is, when the session's location string resolves to a known
    place. None for most sessions today: there is no canonical position in this system
    yet, and `GameSession.current_location` is still free text. See
    `spatial_context.resolve_scene_location`."""

    exits: list[ConnectionView] = []

    # REGIONAL and up.
    nearby_locations: list[PlaceView] = []
    """The containers above the scene and the places directly inside or one edge from
    it. Not "nearby" by distance -- there is no distance in this system, deliberately."""

    # FULL_DEBUG only.
    facts: list[WorldFact] = []
    locations: list[PlaceView] = []
    connections: list[ConnectionView] = []
    situations: list[Situation] = []
    scheduled_events: list[ScheduledEventRecord] = []
    recent_events: list[GameEvent] = []


async def get_state_root(reader: WorldStateReaderPort, *, session_id: uuid.UUID) -> WorldStateV1:
    """The session's WorldState root, or `NotFoundError` if there is no such session.

    Raises `UnsupportedWorldStateVersionError` when the stored row declares a version
    this build cannot read. That is a refusal rather than a fallback: see
    `app.domain.world_state.read_world_state`.
    """
    session = await _require_session(reader, session_id)
    return read_world_state(
        version=session.world_state_version,
        session_id=session.id,
        revision=session.state_revision,
        elapsed_minutes=session.elapsed_minutes,
    )


async def get_revision(reader: WorldStateReaderPort, *, session_id: uuid.UUID) -> int:
    """How many times this session's reality has changed.

    Through the root rather than around it, so a session written by a build this one
    cannot read fails here too rather than quietly handing back a number from a shape
    nobody understands.
    """
    return (await get_state_root(reader, session_id=session_id)).revision


async def get_current_time(reader: WorldStateReaderPort, *, session_id: uuid.UUID) -> TimeState:
    """Where this session sits on its own clock.

    The clock's authority is `world_time`, and this reads it rather than keeping one.
    A caller that wants the date and the hour projects it with `project_time` and the
    world's `initial_datetime`; a caller doing arithmetic wants the number here.
    """
    return (await get_state_root(reader, session_id=session_id)).time


async def build_snapshot(
    reader: WorldStateReaderPort,
    *,
    session_id: uuid.UUID,
    scope: SnapshotScope = SnapshotScope.MINIMAL,
) -> CurrentWorldSnapshot:
    """Compose a view of this session's world at the scope asked for.

    Every scope reads the root, the facts and the situations, because the counts are
    part of every answer and a count nobody can trust is worse than no count. Geography
    is loaded from `RELEVANT` upward, history and the schedule only for `FULL_DEBUG`.
    """
    session = await _require_session(reader, session_id)
    root = read_world_state(
        version=session.world_state_version,
        session_id=session.id,
        revision=session.state_revision,
        elapsed_minutes=session.elapsed_minutes,
    )
    world = await reader.get_world(session.world_id)
    if world is None:  # FK guarantees this, but the type checker does not.
        raise NotFoundError("World", session.world_id)

    truncated = False

    facts = await reader.load_facts(session_id, limit=SNAPSHOT_FACT_LIMIT)
    if len(facts) == SNAPSHOT_FACT_LIMIT:
        logger.warning(
            "Session %s reached the %d-fact snapshot ceiling; counts are a floor.",
            session_id,
            SNAPSHOT_FACT_LIMIT,
        )
        truncated = True

    situations = await reader.load_situations(session_id, limit=MAX_SITUATIONS)
    truncated = truncated or len(situations) == MAX_SITUATIONS
    live = [situation for situation in situations if situation.status in LIVE_STATUSES]

    graph: SpatialGraph | None = None
    if _at_least(scope, SnapshotScope.RELEVANT):
        graph = await load_spatial_graph(reader, session_id=session_id, world_id=world.id)
        truncated = truncated or len(graph.index) == MAX_GRAPH_SIZE

    pending = await reader.load_scheduled_events(
        session_id, statuses=_PENDING, limit=MAX_SITUATIONS
    )

    counts = SnapshotCounts(
        facts=len(facts),
        locations=len(graph.index) if graph is not None else 0,
        connections=len(graph.connections) if graph is not None else 0,
        active_situations=len(live),
        situations=len(situations),
        pending_scheduled_events=len(pending),
    )
    if graph is None:
        # Counted without loading the whole graph: a minimal snapshot should not pay for
        # geography it will not show. The two lists are still bounded by the same
        # ceiling, so a world at the ceiling reads as truncated here too.
        definitions = await reader.load_locations(
            session_id, world_id=world.id, limit=MAX_GRAPH_SIZE
        )
        connections = await reader.load_connections(
            session_id, world_id=world.id, limit=MAX_GRAPH_SIZE
        )
        truncated = truncated or len(definitions) == MAX_GRAPH_SIZE
        counts = counts.model_copy(
            update={"locations": len(definitions), "connections": len(connections)}
        )

    snapshot = CurrentWorldSnapshot(
        scope=scope,
        version=root.version,
        session_id=root.session_id,
        revision=root.revision,
        time=project_time(root.time.elapsed_minutes, initial=world.initial_datetime),
        counts=counts,
        truncated=truncated,
        important_facts=[fact for fact in facts if fact.importance >= MINIMAL_FACT_IMPORTANCE][
            :IMPORTANT_FACT_LIMIT
        ],
        active_situations=live[:ACTIVE_SITUATION_LIMIT],
    )
    if graph is None:
        return snapshot

    current = resolve_scene_location(graph, session.current_location)
    snapshot = snapshot.model_copy(
        update={
            "current_location": _place(graph, current) if current is not None else None,
            "exits": _exits(graph, current.id) if current is not None else [],
        }
    )

    if _at_least(scope, SnapshotScope.REGIONAL) and current is not None:
        snapshot = snapshot.model_copy(update={"nearby_locations": _surroundings(graph, current)})

    if not _at_least(scope, SnapshotScope.FULL_DEBUG):
        return snapshot

    return snapshot.model_copy(
        update={
            "facts": facts,
            "locations": [_place(graph, definition) for definition in graph.index],
            "connections": [
                ConnectionView(
                    connection=connection,
                    state=graph.connection_states.get(connection.id),
                )
                for connection in graph.connections
            ],
            "situations": situations,
            # Every status, not just the pending ones: what the world already did with
            # its commitments is the interesting half when something looks wrong.
            "scheduled_events": await reader.load_scheduled_events(
                session_id, limit=MAX_SITUATIONS
            ),
            "recent_events": await reader.load_events(session_id, limit=SNAPSHOT_EVENT_LIMIT),
        }
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_PENDING = frozenset({ScheduledEventStatus.PENDING})


def _at_least(scope: SnapshotScope, floor: SnapshotScope) -> bool:
    return _ORDER[scope] >= _ORDER[floor]


async def _require_session(reader: WorldStateReaderPort, session_id: uuid.UUID) -> SessionSnapshot:
    session = await reader.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)
    return session


def _place(graph: SpatialGraph, definition: LocationDefinition) -> PlaceView:
    return PlaceView(definition=definition, state=graph.location_states.get(definition.id))


def _exits(graph: SpatialGraph, location_id: uuid.UUID) -> list[ConnectionView]:
    """Every edge leaving this place, blocked ones included.

    Blocked edges stay in. A door that will not open is something a reader of this
    snapshot needs to see, and hiding it would make a stuck session look like a session
    with nowhere to go.
    """
    return [
        ConnectionView(connection=connection, state=graph.connection_states.get(connection.id))
        for connection, _destination in graph.exits_from(location_id)
    ]


def _surroundings(graph: SpatialGraph, current: LocationDefinition) -> list[PlaceView]:
    """The containers above the scene, what is directly inside it, and one edge away.

    Deduplicated and stable: containers first, then contents, then neighbours, each in
    the order the graph already sorted them. Not a radius -- there is no distance in
    this system, on purpose, because two people in one city are not near each other.
    """
    seen: set[uuid.UUID] = {current.id}
    around: list[PlaceView] = []
    neighbours = (
        list(get_ancestors(graph.index, current.id))
        + list(get_children(graph.index, current.id))
        + [
            definition
            for _connection, destination in graph.exits_from(current.id)
            if (definition := graph.index.get(destination)) is not None
        ]
    )
    for definition in neighbours:
        if definition.id in seen:
            continue
        seen.add(definition.id)
        around.append(_place(graph, definition))
    return around
