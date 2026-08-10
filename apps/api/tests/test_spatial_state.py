"""Spatial state end to end: the services, the adapter and a real database.

`test_world_locations.py` proves the domain refuses what it should. This proves the
rest: that a session's state is its own, that generated geography does not leak between
saves, that a batch touching space is still all-or-nothing, and that the context a
prompt is built from is deterministic.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.persistence import NewConnection, NewLocation, NewZone
from app.application.spatial_context import (
    build_scene_spatial_context,
    get_spatial_context,
    resolve_scene_location,
)
from app.application.spatial_service import (
    create_connection,
    create_location,
    create_zone,
    load_spatial_graph,
    materialize_initial_spatial_state,
)
from app.application.state_service import apply_state_change
from app.domain.errors import (
    FactPolicyError,
    NotFoundError,
    StaleStateError,
    ValidationError,
)
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import FactAuthority, FactSubject, FactSubjectType, SetFact
from app.domain.world_locations import (
    ConnectionCategory,
    LocationAccessibility,
    LocationCategory,
    LocationCondition,
    LocationScale,
    UpdateConnectionState,
    UpdateLocationState,
)
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway
from tests.support import cause_from_event, cause_from_resolution


async def _world(db: AsyncSession, make_world) -> models.World:
    world = make_world()
    db.add(world)
    await db.flush()
    return world


async def _session(
    db: AsyncSession, world_id: uuid.UUID, **overrides: object
) -> models.GameSession:
    data: dict[str, object] = {"world_id": world_id, "title": "Run", "player_name": "Rin"}
    data.update(overrides)
    session = models.GameSession(**data)  # type: ignore[arg-type]
    db.add(session)
    await db.flush()
    return session


def _place(world_id: uuid.UUID, name: str, **overrides: object) -> models.LocationDefinition:
    data: dict[str, object] = {
        "world_id": world_id,
        "name": name,
        "category": LocationCategory.STRUCTURE,
        "scale": LocationScale.BUILDING,
    }
    data.update(overrides)
    return models.LocationDefinition(**data)  # type: ignore[arg-type]


async def _town_and_tavern(
    db: AsyncSession, world_id: uuid.UUID
) -> tuple[models.LocationDefinition, models.LocationDefinition, models.LocationConnection]:
    """Riverwood, the Broken Crown inside it, and a door between them.

    The door is written explicitly, because containment does not create one.
    """
    town = _place(
        world_id, "Riverwood", category=LocationCategory.SETTLEMENT, scale=LocationScale.SETTLEMENT
    )
    db.add(town)
    await db.flush()
    tavern = _place(world_id, "Broken Crown", subtype="tavern", parent_location_id=town.id)
    db.add(tavern)
    await db.flush()
    door = models.LocationConnection(
        world_id=world_id,
        from_location_id=town.id,
        to_location_id=tavern.id,
        category=ConnectionCategory.PASSAGE,
        subtype="door",
    )
    db.add(door)
    await db.flush()
    return town, tavern, door


# -- materialisation ----------------------------------------------------------


async def test_a_new_session_gets_a_state_row_for_every_place_and_edge(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    written = await materialize_initial_spatial_state(store, session_id=session.id)

    assert written == 3  # two locations, one connection
    states = await store.load_location_states(session.id)
    assert len(states) == 2
    assert all(state.condition is LocationCondition.INTACT for state in states)
    assert all(state.accessibility is LocationAccessibility.OPEN for state in states)
    assert all(state.security_level == 0 for state in states)


async def test_a_world_with_no_geography_materialises_nothing(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    assert await materialize_initial_spatial_state(store, session_id=session.id) == 0


async def test_definitions_are_shared_and_states_are_not(
    db_session: AsyncSession, make_world
) -> None:
    """The point of two tables. Ten sessions read one Broken Crown and each keeps its
    own answer to whether it is still standing."""
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    first = await _session(db_session, world.id, title="A")
    second = await _session(db_session, world.id, title="B", player_name="Kai")
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=first.id)
    await materialize_initial_spatial_state(store, session_id=second.id)

    await apply_state_change(
        store,
        session_id=first.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.DESTROYED)
            ],
        ),
        cause=cause_from_resolution(),
    )

    ruined = await store.get_location_state(first.id, tavern.id)
    intact = await store.get_location_state(second.id, tavern.id)
    assert ruined is not None and ruined.condition is LocationCondition.DESTROYED
    assert intact is not None and intact.condition is LocationCondition.INTACT

    # And the definition itself is untouched -- a destroyed place stays addressable.
    definition = await store.get_location(second.id, tavern.id)
    assert definition is not None
    assert definition.name == "Broken Crown"


# -- session scoping ----------------------------------------------------------


async def test_template_geography_is_visible_to_every_session(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    await _town_and_tavern(db_session, world.id)
    first = await _session(db_session, world.id, title="A")
    second = await _session(db_session, world.id, title="B", player_name="Kai")
    store = SqlAlchemyTurnGateway(db_session)

    for session in (first, second):
        found = await store.load_locations(session.id, world_id=world.id, limit=50)
        assert {place.name for place in found} == {"Riverwood", "Broken Crown"}


async def test_one_session_s_generated_geography_is_invisible_to_another(
    db_session: AsyncSession, make_world
) -> None:
    """The leakage rule, and the reason every spatial query carries a session."""
    world = await _world(db_session, make_world)
    town, _, _ = await _town_and_tavern(db_session, world.id)
    first = await _session(db_session, world.id, title="A")
    second = await _session(db_session, world.id, title="B", player_name="Kai")
    store = SqlAlchemyTurnGateway(db_session)

    shop_id = await create_location(
        store,
        session_id=first.id,
        location=NewLocation(
            world_id=world.id,
            origin_session_id=first.id,
            name="Starfall Books",
            category=LocationCategory.STRUCTURE,
            subtype="bookstore",
            scale=LocationScale.BUILDING,
            parent_location_id=town.id,
        ),
        narrated=True,
    )

    mine = await store.load_locations(first.id, world_id=world.id, limit=50)
    theirs = await store.load_locations(second.id, world_id=world.id, limit=50)
    assert "Starfall Books" in {place.name for place in mine}
    assert "Starfall Books" not in {place.name for place in theirs}
    assert await store.get_location(second.id, shop_id) is None


async def test_a_generated_place_gets_its_own_state_immediately(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    town, _, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    shop_id = await create_location(
        store,
        session_id=session.id,
        location=NewLocation(
            world_id=world.id,
            origin_session_id=session.id,
            name="Starfall Books",
            category=LocationCategory.STRUCTURE,
            scale=LocationScale.BUILDING,
            parent_location_id=town.id,
        ),
        narrated=True,
    )

    state = await store.get_location_state(session.id, shop_id)
    assert state is not None
    assert state.condition is LocationCondition.INTACT


async def test_a_place_cannot_be_created_inside_another_session_s_canon(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    town, _, _ = await _town_and_tavern(db_session, world.id)
    first = await _session(db_session, world.id, title="A")
    second = await _session(db_session, world.id, title="B", player_name="Kai")
    store = SqlAlchemyTurnGateway(db_session)

    theirs = await create_location(
        store,
        session_id=first.id,
        location=NewLocation(
            world_id=world.id,
            origin_session_id=first.id,
            name="Their Alley",
            category=LocationCategory.TRANSIT,
            scale=LocationScale.SITE,
            parent_location_id=town.id,
        ),
        narrated=True,
    )

    with pytest.raises(NotFoundError):
        # Invisible from here, so it reads as missing rather than forbidden.
        await create_location(
            store,
            session_id=second.id,
            location=NewLocation(
                world_id=world.id,
                origin_session_id=second.id,
                name="My Shop",
                category=LocationCategory.STRUCTURE,
                scale=LocationScale.BUILDING,
                parent_location_id=theirs,
            ),
            narrated=True,
        )


async def test_a_place_cannot_be_created_in_a_world_the_session_is_not_playing(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    elsewhere = await _world(db_session, make_world)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(ValidationError):
        await create_location(
            store,
            session_id=session.id,
            location=NewLocation(
                world_id=elsewhere.id,
                name="Somewhere Else",
                category=LocationCategory.STRUCTURE,
                scale=LocationScale.BUILDING,
            ),
            narrated=False,
        )


# -- connections --------------------------------------------------------------


async def test_a_bidirectional_connection_is_an_exit_from_both_ends(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    town, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    graph = await load_spatial_graph(store, session_id=session.id, world_id=world.id)

    assert [dest for _, dest in graph.exits_from(town.id)] == [tavern.id]
    assert [dest for _, dest in graph.exits_from(tavern.id)] == [town.id]


async def test_a_one_way_connection_is_not_an_exit_back(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    town, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    cellar_id = await create_location(
        store,
        session_id=session.id,
        location=NewLocation(
            world_id=world.id,
            name="Cellar",
            category=LocationCategory.INTERIOR,
            scale=LocationScale.ROOM,
            parent_location_id=tavern.id,
        ),
        narrated=False,
    )
    await create_connection(
        store,
        session_id=session.id,
        connection=NewConnection(
            world_id=world.id,
            from_location_id=town.id,
            to_location_id=cellar_id,
            bidirectional=False,
            category=ConnectionCategory.VERTICAL,
            subtype="coal_chute",
        ),
    )

    graph = await load_spatial_graph(store, session_id=session.id, world_id=world.id)
    assert cellar_id in [dest for _, dest in graph.exits_from(town.id)]
    assert town.id not in [dest for _, dest in graph.exits_from(cellar_id)]


async def test_a_connection_to_a_place_that_does_not_exist_is_refused(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    town, _, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(NotFoundError):
        await create_connection(
            store,
            session_id=session.id,
            connection=NewConnection(
                world_id=world.id,
                from_location_id=town.id,
                to_location_id=uuid.uuid4(),
                category=ConnectionCategory.ROAD,
            ),
        )


async def test_a_blocked_connection_is_still_an_edge_but_not_passable(
    db_session: AsyncSession, make_world
) -> None:
    """The destroyed bridge stays in the graph -- that is what makes repair, ruins and
    historical reference expressible at all."""
    world = await _world(db_session, make_world)
    town, tavern, door = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=session.id)

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateConnectionState(
                    connection_id=door.id,
                    condition=LocationCondition.DESTROYED,
                    accessibility=LocationAccessibility.BLOCKED,
                )
            ],
        ),
        cause=cause_from_resolution(),
    )

    graph = await load_spatial_graph(store, session_id=session.id, world_id=world.id)
    assert [dest for _, dest in graph.exits_from(town.id)] == [tavern.id]
    assert graph.is_passable(door.id) is False
    assert await store.get_connection(session.id, door.id) is not None


# -- zones --------------------------------------------------------------------


async def test_zones_belong_to_a_location_and_are_not_travel_nodes(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    town, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    for name in ("the bar", "the fireplace"):
        await create_zone(
            store, session_id=session.id, zone=NewZone(location_id=tavern.id, name=name)
        )

    zones = await store.load_zones(tavern.id)
    assert {zone.name for zone in zones} == {"the bar", "the fireplace"}

    # A zone is not somewhere you travel to: no connection can name one, because
    # connections reference location ids and a zone has none of its own.
    graph = await load_spatial_graph(store, session_id=session.id, world_id=world.id)
    destinations = {dest for _, dest in graph.exits_from(tavern.id)}
    assert destinations == {town.id}
    assert not any(zone.id in destinations for zone in zones)


async def test_a_zone_on_a_location_that_does_not_exist_is_refused(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(NotFoundError):
        await create_zone(
            store,
            session_id=session.id,
            zone=NewZone(location_id=uuid.uuid4(), name="nowhere"),
        )


# -- spatial context ----------------------------------------------------------


async def test_spatial_context_carries_here_inside_out_and_within(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=session.id)
    cellar_id = await create_location(
        store,
        session_id=session.id,
        location=NewLocation(
            world_id=world.id,
            name="Cellar",
            category=LocationCategory.INTERIOR,
            scale=LocationScale.ROOM,
            parent_location_id=tavern.id,
        ),
        narrated=False,
    )
    await create_zone(
        store, session_id=session.id, zone=NewZone(location_id=tavern.id, name="the bar")
    )

    context = await get_spatial_context(
        store, session_id=session.id, world_id=world.id, location_id=tavern.id
    )

    assert context.current.name == "Broken Crown"
    assert context.current.kind == "tavern"
    assert [place.name for place in context.within] == ["Riverwood"]
    assert [place.name for place in context.contains] == ["Cellar"]
    assert [exit_.name for exit_ in context.exits] == ["Riverwood"]
    assert context.zones == ["the bar"]
    assert cellar_id is not None


async def test_spatial_context_omits_unrelated_geography(
    db_session: AsyncSession, make_world
) -> None:
    """A tier budget, not a graph dump. Somewhere with no relationship to here does not
    appear at any tier."""
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    far = _place(
        world.id,
        "The Capital",
        category=LocationCategory.SETTLEMENT,
        scale=LocationScale.SETTLEMENT,
    )
    db_session.add(far)
    await db_session.flush()
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=session.id)

    context = await get_spatial_context(
        store, session_id=session.id, world_id=world.id, location_id=tavern.id
    )

    everything = (
        [context.current.name]
        + [p.name for p in context.within]
        + [p.name for p in context.contains]
        + [e.name for e in context.exits]
    )
    assert "The Capital" not in everything


async def test_a_default_state_is_left_out_of_the_context(
    db_session: AsyncSession, make_world
) -> None:
    """ "intact, open" on every place is tokens spent teaching a model to ignore the
    field. Only a departure from normal is worth saying."""
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=session.id)

    context = await get_spatial_context(
        store, session_id=session.id, world_id=world.id, location_id=tavern.id
    )
    assert context.current.condition is None
    assert context.current.accessibility is None

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.RUINED)
            ],
        ),
        cause=cause_from_resolution(),
    )
    changed = await get_spatial_context(
        store, session_id=session.id, world_id=world.id, location_id=tavern.id
    )
    assert changed.current.condition is LocationCondition.RUINED


async def test_the_scene_finds_its_place_by_an_exact_unambiguous_name(
    db_session: AsyncSession, make_world
) -> None:
    """The temporary bridge until CharacterPosition exists; see
    `spatial_context.resolve_scene_location` for why it refuses to guess."""
    world = await _world(db_session, make_world)
    await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    graph = await load_spatial_graph(store, session_id=session.id, world_id=world.id)

    found = resolve_scene_location(graph, "  broken crown ")
    assert found is not None
    assert found.name == "Broken Crown"

    assert resolve_scene_location(graph, "the broken crown") is None, "no fuzzy matching"
    assert resolve_scene_location(graph, "") is None


async def test_an_ambiguous_name_resolves_to_nothing_rather_than_to_the_first_row(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    for _ in range(2):
        db_session.add(_place(world.id, "Market Street", scale=LocationScale.SITE))
    await db_session.flush()
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    graph = await load_spatial_graph(store, session_id=session.id, world_id=world.id)

    assert resolve_scene_location(graph, "Market Street") is None


async def test_a_session_with_no_matching_place_gets_no_spatial_block(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id, current_location="somewhere else entirely")
    store = SqlAlchemyTurnGateway(db_session)

    assert (
        await build_scene_spatial_context(
            store,
            session_id=session.id,
            world_id=world.id,
            current_location=session.current_location,
        )
        is None
    )


# -- atomicity ----------------------------------------------------------------


async def test_one_bad_spatial_mutation_takes_the_whole_batch_with_it(
    db_session: AsyncSession, make_world
) -> None:
    """Event not written, no state half-changed, revision where it was."""
    world = await _world(db_session, make_world)
    _, tavern, door = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=session.id)

    with pytest.raises(NotFoundError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ENGINE,
                mutations=[
                    # Fine on its own.
                    UpdateLocationState(
                        location_id=tavern.id, condition=LocationCondition.DESTROYED
                    ),
                    UpdateConnectionState(
                        connection_id=door.id, accessibility=LocationAccessibility.BLOCKED
                    ),
                    # Nowhere. The batch never starts.
                    UpdateLocationState(
                        location_id=uuid.uuid4(), condition=LocationCondition.RUINED
                    ),
                ],
            ),
            cause=cause_from_resolution(),
        )

    state = await store.get_location_state(session.id, tavern.id)
    assert state is not None
    assert state.condition is LocationCondition.INTACT, "no partial location update"
    connection_state = await store.get_connection_state(session.id, door.id)
    assert connection_state is not None
    assert connection_state.accessibility is LocationAccessibility.OPEN, "no partial edge update"

    events = (
        await db_session.execute(
            select(func.count())
            .select_from(models.GameEvent)
            .where(models.GameEvent.session_id == session.id)
        )
    ).scalar()
    assert events == 0
    reread = await store.get_session(session.id)
    assert reread is not None
    assert reread.state_revision == 0


async def test_one_event_can_change_a_place_an_edge_and_a_fact_together(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """BRIDGE_COLLAPSED is one thing that happened, and it moves several."""
    world = await _world(db_session, make_world)
    _, tavern, door = await _town_and_tavern(db_session, world.id)
    character = make_character(world.id)
    db_session.add(character)
    session = await _session(db_session, world.id)
    await db_session.flush()
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=session.id)

    cause = await cause_from_event(store, session.id, subtype="bridge_collapsed")
    result = await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(
                    location_id=tavern.id,
                    condition=LocationCondition.HEAVILY_DAMAGED,
                    local_danger_modifier=30,
                ),
                UpdateConnectionState(
                    connection_id=door.id,
                    condition=LocationCondition.DESTROYED,
                    accessibility=LocationAccessibility.BLOCKED,
                ),
                SetFact(
                    subject=FactSubject(type=FactSubjectType.LOCATION, id=tavern.id),
                    property="narrative.childhood_nickname",
                    value="the wreck",
                ),
            ],
        ),
        cause=cause,
    )

    assert result.revision == 1
    assert len(result.applied) == 3
    assert {entry.scope for entry in result.applied} == {
        "location_state",
        "connection_state",
        f"location:{tavern.id}",
    }
    # The one event is the collapse itself. Applying the batch adds none of its own --
    # three mutations are three consequences of one thing happening, not three things.
    assert result.event_id == cause.event_id
    events = (
        await db_session.execute(
            select(func.count())
            .select_from(models.GameEvent)
            .where(models.GameEvent.session_id == session.id)
        )
    ).scalar()
    assert events == 1


async def test_a_spatial_mutation_leaves_the_definition_alone(
    db_session: AsyncSession, make_world
) -> None:
    """Destroying a place changes its state, never its row. The ruins are still a
    destination."""
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=session.id)
    before = await store.get_location(session.id, tavern.id)

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.DESTROYED)
            ],
        ),
        cause=cause_from_resolution(),
    )

    after = await store.get_location(session.id, tavern.id)
    assert before is not None and after is not None
    assert after.name == before.name
    assert after.parent_location_id == before.parent_location_id


async def test_destroying_a_place_does_not_cascade_to_what_is_inside_it(
    db_session: AsyncSession, make_world
) -> None:
    """A castle can be a ruin with an intact dungeon under it. Consequences are decided
    by whatever resolved the event, one mutation at a time."""
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    cellar_id = await create_location(
        store,
        session_id=session.id,
        location=NewLocation(
            world_id=world.id,
            name="Cellar",
            category=LocationCategory.INTERIOR,
            scale=LocationScale.ROOM,
            parent_location_id=tavern.id,
        ),
        narrated=False,
    )
    await materialize_initial_spatial_state(store, session_id=session.id)

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.DESTROYED)
            ],
        ),
        cause=cause_from_resolution(),
    )

    cellar_state = await store.get_location_state(session.id, cellar_id)
    assert cellar_state is not None
    assert cellar_state.condition is LocationCondition.INTACT


# -- authority ----------------------------------------------------------------


async def test_the_story_director_cannot_change_spatial_state(
    db_session: AsyncSession, make_world
) -> None:
    """No open tier exists here: every spatial field changes what a character can
    physically do next."""
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=session.id)

    with pytest.raises(Exception) as caught:
        StateMutationBatch(
            authority=FactAuthority.STORY_DIRECTOR,
            mutations=[
                UpdateLocationState(location_id=tavern.id, accessibility=LocationAccessibility.OPEN)
            ],
        )
    assert "may not change spatial state" in str(caught.value)

    state = await store.get_location_state(session.id, tavern.id)
    assert state is not None
    assert state.condition is LocationCondition.INTACT


async def test_a_location_s_condition_may_not_be_written_as_a_fact(
    db_session: AsyncSession, make_world
) -> None:
    """Two tables claiming whether the bridge is standing is what dedicated state
    exists to prevent."""
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(FactPolicyError, match="LocationState"):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ADMIN,
                mutations=[
                    SetFact(
                        subject=FactSubject(type=FactSubjectType.LOCATION, id=tavern.id),
                        property="world.condition",
                        value="ruined",
                    )
                ],
            ),
            cause=cause_from_resolution(),
        )


@pytest.mark.parametrize(
    "canonical",
    [
        "system.location_condition",
        "system.location_accessibility",
        "system.location_owner",
        "system.connection_accessibility",
    ],
)
async def test_no_authority_may_write_a_property_a_dedicated_model_owns(
    db_session: AsyncSession, make_world, canonical: str
) -> None:
    world = await _world(db_session, make_world)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(FactPolicyError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ADMIN,
                mutations=[
                    SetFact(
                        subject=FactSubject(type=FactSubjectType.WORLD),
                        property=canonical,
                        value="x",
                    )
                ],
            ),
            cause=cause_from_resolution(),
        )


async def test_a_narrative_fact_about_a_location_is_still_a_fact(
    db_session: AsyncSession, make_world
) -> None:
    """The division cuts both ways: what a place is known for belongs in the store."""
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    subject = FactSubject(type=FactSubjectType.LOCATION, id=tavern.id)

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.SEED,
            mutations=[
                SetFact(
                    subject=subject,
                    property="narrative.childhood_nickname",
                    value="a meeting place for smugglers",
                )
            ],
        ),
    )

    stored = await store.get_fact(session.id, subject, "narrative.childhood_nickname")
    assert stored is not None
    assert stored.value == "a meeting place for smugglers"


async def test_a_stale_revision_still_refuses_a_spatial_batch(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    _, tavern, _ = await _town_and_tavern(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_spatial_state(store, session_id=session.id)

    batch = StateMutationBatch(
        authority=FactAuthority.ENGINE,
        expected_revision=0,
        mutations=[UpdateLocationState(location_id=tavern.id, condition=LocationCondition.WORN)],
    )
    await apply_state_change(
        store,
        session_id=session.id,
        batch=batch,
        cause=cause_from_resolution(),
    )
    with pytest.raises(StaleStateError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=batch,
            cause=cause_from_resolution(),
        )
