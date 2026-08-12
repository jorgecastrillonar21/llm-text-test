"""Reading and authoring geography.

# Two scopes, and why the paths say which

    /worlds/{id}/locations              the reusable template
    /sessions/{id}/locations/...        what this save can see, with its state

Every session-scoped read applies the visibility rule -- template geography plus this
session's own generated canon -- and the path is what makes that unambiguous. A bare
`/locations/{id}` could not: it has no session, so it either leaks another save's
geography or silently hides half of this one. See docs/world-state-locations.md.

# No state mutation here

Changing a location's condition or a connection's accessibility is a `StateMutation`,
applied by `state_service` inside an event and a transaction. There is no PATCH on this
router and there should not be one: a bridge that collapses through a REST call would
have no event explaining it and would not move the state revision.

Creation *is* here, for template authoring, because writing geography a world starts
with is a different act from changing what has happened to it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, SpatialStore
from app.api.schemas import (
    ConnectionCreate,
    ConnectionRead,
    ConnectionStateRead,
    LocationCreate,
    LocationDetailRead,
    LocationRead,
    LocationStateRead,
    ZoneCreate,
)
from app.application.persistence import NewConnection, NewLocation, NewZone
from app.application.spatial_context import get_spatial_context
from app.application.spatial_service import (
    MAX_GRAPH_SIZE,
    create_connection,
    create_location,
    create_zone,
    load_spatial_graph,
)
from app.application.story_context import SpatialContext
from app.domain.errors import NotFoundError
from app.domain.world_locations import (
    LocationConnection,
    LocationConnectionState,
    LocationDefinition,
    LocationState,
    LocationZone,
    get_children,
)
from app.infrastructure.db import models

router = APIRouter(tags=["locations"])


def _read(definition: LocationDefinition, state: LocationState | None) -> LocationRead:
    return LocationRead(
        definition=definition,
        state=None if state is None else LocationStateRead(**state.model_dump()),
    )


def _connection_read(
    connection: LocationConnection, state: LocationConnectionState | None
) -> ConnectionRead:
    return ConnectionRead(
        connection=connection,
        state=None if state is None else ConnectionStateRead(**state.model_dump()),
    )


# -- template authoring -------------------------------------------------------


@router.get("/worlds/{world_id}/locations", response_model=list[LocationDefinition])
async def list_template_locations(
    world_id: uuid.UUID, db: DbSession, store: SpatialStore
) -> list[LocationDefinition]:
    """The world's reusable geography, without any session's state.

    Template rows only. What a *session* currently sees, including anything its own
    play generated, is `/sessions/{id}/locations` -- a different question with a
    different answer.
    """
    if await db.get(models.World, world_id) is None:
        raise NotFoundError("World", world_id)
    # `None` asks the same filtered query for template rows alone, rather than adding a
    # second query that could drift from the one every session read uses.
    return await store.load_locations(None, world_id=world_id, limit=MAX_GRAPH_SIZE)


@router.post(
    "/worlds/{world_id}/locations",
    response_model=LocationDefinition,
    status_code=status.HTTP_201_CREATED,
)
async def create_template_location(
    world_id: uuid.UUID, payload: LocationCreate, db: DbSession, store: SpatialStore
) -> LocationDefinition:
    """Author a place into a world's template.

    Not narrated, so the creation policy does not apply -- an author writing a continent
    is the intended use of the model. The containment rules still do: no cycles, no
    cross-world parents, no parent that exists only inside somebody's save.
    """
    if await db.get(models.World, world_id) is None:
        raise NotFoundError("World", world_id)

    location_id = await create_location(
        store,
        session_id=None,
        location=NewLocation(
            world_id=world_id,
            origin_session_id=None,
            **payload.model_dump(),
        ),
        narrated=False,
    )
    await store.commit()
    created = await store.get_location(None, location_id)
    if created is None:  # pragma: no cover - written and committed a moment ago
        raise NotFoundError("Location", location_id)
    return created


@router.post(
    "/worlds/{world_id}/connections",
    response_model=LocationConnection,
    status_code=status.HTTP_201_CREATED,
)
async def create_template_connection(
    world_id: uuid.UUID, payload: ConnectionCreate, db: DbSession, store: SpatialStore
) -> LocationConnection:
    """Declare a traversal between two template locations.

    Never inferred from containment: a cellar inside a tavern is not reachable from it
    until somebody writes the stairs down, and this is where that happens.
    """
    if await db.get(models.World, world_id) is None:
        raise NotFoundError("World", world_id)

    connection_id = await create_connection(
        store,
        session_id=None,
        connection=NewConnection(world_id=world_id, origin_session_id=None, **payload.model_dump()),
    )
    await store.commit()
    created = await store.get_connection(None, connection_id)
    if created is None:  # pragma: no cover - written and committed a moment ago
        raise NotFoundError("LocationConnection", connection_id)
    return created


@router.post(
    "/worlds/{world_id}/locations/{location_id}/zones",
    response_model=LocationZone,
    status_code=status.HTTP_201_CREATED,
)
async def create_location_zone(
    world_id: uuid.UUID, location_id: uuid.UUID, payload: ZoneCreate, store: SpatialStore
) -> LocationZone:
    """Add a named area inside a place -- the bar, the fireplace.

    Zones carry no state and are not travel destinations. When something needs its own
    condition, its own exits, or to be somewhere a character can go, it is a location.
    """
    zone_id = await create_zone(
        store,
        session_id=None,
        zone=NewZone(location_id=location_id, **payload.model_dump()),
    )
    await store.commit()
    zones = await store.load_zones(location_id)
    created = next((zone for zone in zones if zone.id == zone_id), None)
    if created is None:  # pragma: no cover - written and committed a moment ago
        raise NotFoundError("LocationZone", zone_id)
    return created


# -- session-scoped reads -----------------------------------------------------


@router.get("/sessions/{session_id}/locations", response_model=list[LocationRead])
async def list_session_locations(
    session_id: uuid.UUID,
    store: SpatialStore,
    parent_location_id: uuid.UUID | None = Query(default=None),
) -> list[LocationRead]:
    """Everything this session can see, each with its current state.

    Template geography plus whatever this save's own play created, and nothing from any
    other save. `parent_location_id` narrows to direct children.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    graph = await load_spatial_graph(store, session_id=session_id, world_id=session.world_id)
    if parent_location_id is not None:
        if graph.index.get(parent_location_id) is None:
            raise NotFoundError("Location", parent_location_id)
        found = get_children(graph.index, parent_location_id)
    else:
        found = list(graph.index)
    return [_read(definition, graph.location_states.get(definition.id)) for definition in found]


@router.get("/sessions/{session_id}/locations/{location_id}", response_model=LocationDetailRead)
async def get_session_location(
    session_id: uuid.UUID, location_id: uuid.UUID, store: SpatialStore
) -> LocationDetailRead:
    """One place: its definition, this session's state, its zones, children and exits.

    One step outwards and no further. Anything that walks the graph is the spatial
    context endpoint, which has a tier budget written into it precisely so that a
    read cannot turn into a crawl.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    graph = await load_spatial_graph(store, session_id=session_id, world_id=session.world_id)
    definition = graph.index.get(location_id)
    if definition is None:
        raise NotFoundError("Location", location_id)

    return LocationDetailRead(
        definition=definition,
        state=(
            None
            if (state := graph.location_states.get(location_id)) is None
            else LocationStateRead(**state.model_dump())
        ),
        zones=await store.load_zones(location_id),
        children=[
            _read(child, graph.location_states.get(child.id))
            for child in get_children(graph.index, location_id)
        ],
        connections=[
            _connection_read(connection, graph.connection_states.get(connection.id))
            for connection, _destination in graph.exits_from(location_id)
        ],
    )


@router.get("/sessions/{session_id}/spatial-context/{location_id}", response_model=SpatialContext)
async def read_spatial_context(
    session_id: uuid.UUID, location_id: uuid.UUID, store: SpatialStore
) -> SpatialContext:
    """The scene-sized view of a place: here, inside, out, and what contains it.

    The same deterministic selection the Story Director's prompt is built from, exposed
    so that what the model was told is inspectable rather than inferred from its output.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)
    return await get_spatial_context(
        store, session_id=session_id, world_id=session.world_id, location_id=location_id
    )
