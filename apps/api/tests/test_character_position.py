"""Canonical spatial position end to end: the four shapes, and what they replaced.

`app.domain.character_position` proves the shapes refuse what they should on their own.
This proves the rest: that every id is validated against the geography the session can
actually see, that the answer to "where is the player?" comes from one place, and that
the free-text string it replaced can no longer contradict it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.persistence import ActorPosition
from app.application.position_service import (
    actors_at,
    materialize_initial_position,
    player_position,
    resolve_position,
    set_position,
)
from app.application.spatial_context import build_scene_spatial_context
from app.application.state_service import apply_state_change
from app.application.world_state_service import SnapshotScope, build_snapshot
from app.domain.character_position import (
    ActorKind,
    AtLocation,
    InTransit,
    Offstage,
    PositionKind,
    Unlocated,
    read_position,
    scene_location_id,
)
from app.domain.errors import FactPolicyError, NotFoundError, ValidationError
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import FactAuthority, FactSubject, FactSubjectType, SetFact
from app.domain.world_locations import ConnectionCategory, LocationCategory, LocationScale
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway
from tests.support import cause_from_resolution


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


async def _two_places_and_a_road(
    db: AsyncSession, world_id: uuid.UUID, *, bidirectional: bool = True
) -> tuple[models.LocationDefinition, models.LocationDefinition, models.LocationConnection]:
    """A tavern, a keep, and the road between them."""
    tavern = _place(world_id, "Broken Crown", subtype="tavern")
    keep = _place(world_id, "The Keep", subtype="fortress")
    db.add_all([tavern, keep])
    await db.flush()
    road = models.LocationConnection(
        world_id=world_id,
        from_location_id=tavern.id,
        to_location_id=keep.id,
        bidirectional=bidirectional,
        category=ConnectionCategory.PATH,
        subtype="road",
        base_travel_minutes=40,
    )
    db.add(road)
    await db.flush()
    return tavern, keep, road


def _player(session_id: uuid.UUID, position) -> ActorPosition:
    return ActorPosition(
        session_id=session_id,
        actor_kind=ActorKind.PLAYER,
        actor_id=session_id,
        position=position,
    )


# -- the four shapes ----------------------------------------------------------


async def test_a_player_at_a_location_is_stored_and_read_back_by_id(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    tavern, _, _ = await _two_places_and_a_road(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    zone = models.LocationZone(location_id=tavern.id, name="the bar", category="counter")
    db_session.add(zone)
    await db_session.flush()

    await set_position(
        store, _player(session.id, AtLocation(location_id=tavern.id, zone_id=zone.id))
    )

    position = await player_position(store, session_id=session.id)
    assert position == AtLocation(location_id=tavern.id, zone_id=zone.id)
    assert scene_location_id(position) == tavern.id


async def test_an_unwritten_position_reads_as_unlocated_rather_than_as_nothing(
    db_session: AsyncSession, make_world
) -> None:
    """`None` from the port, `Unlocated` from the service, and the difference matters:
    only the application layer can still tell "nobody wrote one" from "we know they are
    nowhere in particular"."""
    world = await _world(db_session, make_world)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    assert (
        await store.get_character_position(
            session.id, actor_kind=ActorKind.PLAYER, actor_id=session.id
        )
        is None
    )
    assert await player_position(store, session_id=session.id) == Unlocated()


async def test_offstage_is_not_a_hidden_location(db_session: AsyncSession, make_world) -> None:
    """An actor the story set aside is not secretly somewhere. The shape is fieldless,
    and the row it writes keeps no location behind it."""
    world = await _world(db_session, make_world)
    tavern, _, _ = await _two_places_and_a_road(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    await set_position(store, _player(session.id, AtLocation(location_id=tavern.id)))
    await set_position(store, _player(session.id, Offstage()))

    assert await player_position(store, session_id=session.id) == Offstage()
    row = (
        await db_session.execute(
            models.CharacterPositionRow.__table__.select().where(
                models.CharacterPositionRow.session_id == session.id
            )
        )
    ).one()
    assert row.kind == PositionKind.OFFSTAGE
    assert row.location_id is None, "the place they were is not kept in a spare column"
    assert scene_location_id(Offstage()) is None


async def test_a_transit_records_the_commitment_and_belongs_to_neither_end(
    db_session: AsyncSession, make_world
) -> None:
    """Origin, destination, connection, departure and expected arrival -- and no scene.

    Somebody on the road between two places is in neither of them, so the scene has no
    location. Nothing here computes progress or decides that they arrived: that is
    TravelEngine's, when it exists.
    """
    world = await _world(db_session, make_world)
    tavern, keep, road = await _two_places_and_a_road(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    transit = InTransit(
        origin_location_id=tavern.id,
        destination_location_id=keep.id,
        connection_id=road.id,
        departed_at=120,
        expected_arrival_at=160,
    )
    await set_position(store, _player(session.id, transit))

    stored = await player_position(store, session_id=session.id)
    assert stored == transit
    assert scene_location_id(stored) is None
    assert (
        await build_scene_spatial_context(store, session_id=session.id, world_id=world.id) is None
    )


async def test_a_journey_of_zero_length_and_a_backwards_arrival_are_both_refused() -> None:
    """The shape's own rules, enforced before any port is involved."""
    place = uuid.uuid4()
    with pytest.raises(Exception, match="same place"):
        InTransit(
            origin_location_id=place,
            destination_location_id=place,
            connection_id=uuid.uuid4(),
            departed_at=0,
            expected_arrival_at=10,
        )
    with pytest.raises(Exception, match="Time does not run backwards"):
        InTransit(
            origin_location_id=uuid.uuid4(),
            destination_location_id=uuid.uuid4(),
            connection_id=uuid.uuid4(),
            departed_at=100,
            expected_arrival_at=99,
        )


async def test_a_stored_row_that_cannot_be_a_position_fails_at_the_boundary() -> None:
    """`read_position` is what this package has instead of a per-row version integer."""
    with pytest.raises(ValidationError, match="names no location"):
        read_position(kind=PositionKind.AT_LOCATION)
    with pytest.raises(ValidationError, match="missing part of the journey"):
        read_position(kind=PositionKind.IN_TRANSIT, origin_location_id=uuid.uuid4())
    with pytest.raises(ValidationError, match="not one of"):
        read_position(kind="somewhere_vague")


# -- validation ---------------------------------------------------------------


async def test_a_position_at_a_place_this_session_cannot_see_is_refused(
    db_session: AsyncSession, make_world
) -> None:
    """The whole reason ids replaced names: an unchecked uuid can find another save's
    room and hand a director its exits as canon."""
    world = await _world(db_session, make_world)
    other_world = await _world(db_session, make_world)
    elsewhere = _place(other_world.id, "Somebody Else's Tavern")
    db_session.add(elsewhere)
    await db_session.flush()
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(NotFoundError):
        await set_position(store, _player(session.id, AtLocation(location_id=uuid.uuid4())))
    with pytest.raises(NotFoundError):
        await set_position(store, _player(session.id, AtLocation(location_id=elsewhere.id)))

    assert await player_position(store, session_id=session.id) == Unlocated()


async def test_a_zone_belonging_to_somewhere_else_is_refused(
    db_session: AsyncSession, make_world
) -> None:
    """Not a finer answer but a contradictory one: it would place the actor twice."""
    world = await _world(db_session, make_world)
    tavern, keep, _ = await _two_places_and_a_road(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)
    zone = models.LocationZone(location_id=keep.id, name="the battlements", category="wall")
    db_session.add(zone)
    await db_session.flush()

    with pytest.raises(NotFoundError):
        await set_position(
            store, _player(session.id, AtLocation(location_id=tavern.id, zone_id=zone.id))
        )


async def test_a_transit_needs_a_connection_that_goes_where_it_claims(
    db_session: AsyncSession, make_world
) -> None:
    """An unknown connection is missing; a one-way edge walked backwards is impossible."""
    world = await _world(db_session, make_world)
    tavern, keep, road = await _two_places_and_a_road(db_session, world.id, bidirectional=False)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(NotFoundError):
        await set_position(
            store,
            _player(
                session.id,
                InTransit(
                    origin_location_id=tavern.id,
                    destination_location_id=keep.id,
                    connection_id=uuid.uuid4(),
                    departed_at=0,
                    expected_arrival_at=40,
                ),
            ),
        )

    # The road runs tavern -> keep only. Going the other way along it is not a position
    # the geography can support.
    with pytest.raises(ValidationError, match="does not run from"):
        await set_position(
            store,
            _player(
                session.id,
                InTransit(
                    origin_location_id=keep.id,
                    destination_location_id=tavern.id,
                    connection_id=road.id,
                    departed_at=0,
                    expected_arrival_at=40,
                ),
            ),
        )


async def test_a_position_for_a_character_nobody_wrote_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """`actor_id` carries no foreign key -- it points at a session or a character
    depending on the kind -- so the check the database cannot make is made in the
    service."""
    world = await _world(db_session, make_world)
    tavern, _, _ = await _two_places_and_a_road(db_session, world.id)
    character = make_character(world.id)
    db_session.add(character)
    await db_session.flush()
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    real = ActorPosition(
        session_id=session.id,
        actor_kind=ActorKind.CHARACTER,
        actor_id=character.id,
        position=AtLocation(location_id=tavern.id),
    )
    await set_position(store, real)
    assert await resolve_position(
        store, session_id=session.id, actor_kind=ActorKind.CHARACTER, actor_id=character.id
    ) == AtLocation(location_id=tavern.id)

    with pytest.raises(NotFoundError):
        await set_position(store, real.model_copy(update={"actor_id": uuid.uuid4()}))

    # And a player whose actor id is not their session is a second player nobody made.
    with pytest.raises(ValidationError, match="second player"):
        await set_position(
            store,
            ActorPosition(
                session_id=session.id,
                actor_kind=ActorKind.PLAYER,
                actor_id=uuid.uuid4(),
                position=AtLocation(location_id=tavern.id),
            ),
        )


# -- one authority ------------------------------------------------------------


async def test_two_places_with_one_name_are_not_ambiguous_because_positions_use_ids(
    db_session: AsyncSession, make_world
) -> None:
    """The failure mode names had and ids do not.

    Two "Market Street"s are indistinguishable to a name match, which is why the old
    bridge refused to answer at all. Positions address them separately, and each one
    resolves to exactly the place it points at.
    """
    world = await _world(db_session, make_world)
    first = _place(world.id, "Market Street", scale=LocationScale.SITE)
    second = _place(world.id, "Market Street", scale=LocationScale.SITE)
    db_session.add_all([first, second])
    await db_session.flush()
    session = await _session(db_session, world.id, current_location="Market Street")
    store = SqlAlchemyTurnGateway(db_session)

    await set_position(store, _player(session.id, AtLocation(location_id=second.id)))

    assert scene_location_id(await player_position(store, session_id=session.id)) == second.id
    assert [
        p.actor_id for p in await actors_at(store, session_id=session.id, location_id=second.id)
    ] == [session.id]
    assert await actors_at(store, session_id=session.id, location_id=first.id) == []


async def test_location_state_keeps_no_occupant_list(db_session: AsyncSession, make_world) -> None:
    """Who is here is a query against positions, not a column on the room.

    Two authorities for one fact would disagree the first time a position was written
    without the room being told, so the room is never told.
    """
    columns = {c.name for c in inspect(models.LocationState).columns}
    assert not {name for name in columns if "occupant" in name or "character" in name}

    world = await _world(db_session, make_world)
    tavern, keep, _ = await _two_places_and_a_road(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    await set_position(store, _player(session.id, AtLocation(location_id=tavern.id)))
    assert len(await actors_at(store, session_id=session.id, location_id=tavern.id)) == 1
    assert await actors_at(store, session_id=session.id, location_id=keep.id) == []

    # Moving writes one row and the reverse lookup follows immediately -- there is no
    # second place that has to be kept in step.
    await set_position(store, _player(session.id, AtLocation(location_id=keep.id)))
    assert await actors_at(store, session_id=session.id, location_id=tavern.id) == []
    assert len(await actors_at(store, session_id=session.id, location_id=keep.id)) == 1


async def test_someone_travelling_towards_a_place_is_not_counted_as_being_in_it(
    db_session: AsyncSession, make_world
) -> None:
    world = await _world(db_session, make_world)
    tavern, keep, road = await _two_places_and_a_road(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    await set_position(
        store,
        _player(
            session.id,
            InTransit(
                origin_location_id=tavern.id,
                destination_location_id=keep.id,
                connection_id=road.id,
                departed_at=0,
                expected_arrival_at=40,
            ),
        ),
    )

    assert await actors_at(store, session_id=session.id, location_id=keep.id) == []
    assert await actors_at(store, session_id=session.id, location_id=tavern.id) == []


async def test_the_legacy_location_string_cannot_override_the_canonical_position(
    db_session: AsyncSession, make_world
) -> None:
    """`current_location` is read once at session creation and is presentation after.

    A session whose string says one place and whose position says another resolves to
    the position, everywhere: the prompt's scene, the session block that used to carry
    the raw string, and the world-state snapshot.
    """
    world = await _world(db_session, make_world)
    tavern, keep, _ = await _two_places_and_a_road(db_session, world.id)
    session = await _session(db_session, world.id, current_location="Broken Crown")
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_position(store, session_id=session.id)
    assert await player_position(store, session_id=session.id) == AtLocation(location_id=tavern.id)

    # The player moves. The string on the session still says the tavern, and stops being
    # true the moment it is no longer the thing anybody reads.
    await set_position(store, _player(session.id, AtLocation(location_id=keep.id)))
    assert session.current_location == "Broken Crown"

    context = await build_scene_spatial_context(store, session_id=session.id, world_id=world.id)
    assert context is not None
    assert context.current.name == keep.name

    snapshot = await build_snapshot(store, session_id=session.id, scope=SnapshotScope.RELEVANT)
    assert snapshot.current_location is not None
    assert snapshot.current_location.definition.name == keep.name


async def test_a_position_is_replaced_whole_rather_than_patched(
    db_session: AsyncSession, make_world
) -> None:
    """The four shapes share no fields, so a stale one left behind would be a journey
    that outlived itself -- and a row violating its own check constraint."""
    world = await _world(db_session, make_world)
    tavern, keep, road = await _two_places_and_a_road(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    await set_position(
        store,
        _player(
            session.id,
            InTransit(
                origin_location_id=tavern.id,
                destination_location_id=keep.id,
                connection_id=road.id,
                departed_at=0,
                expected_arrival_at=40,
            ),
        ),
    )
    await set_position(store, _player(session.id, AtLocation(location_id=keep.id)))

    row = (
        await db_session.execute(
            models.CharacterPositionRow.__table__.select().where(
                models.CharacterPositionRow.session_id == session.id
            )
        )
    ).one()
    assert row.kind == PositionKind.AT_LOCATION
    assert row.location_id == keep.id
    assert row.origin_location_id is None
    assert row.destination_location_id is None
    assert row.connection_id is None
    assert row.departed_at is None
    assert row.expected_arrival_at is None


async def test_one_actor_has_exactly_one_position(db_session: AsyncSession, make_world) -> None:
    """Enforced by a unique constraint rather than by every writer remembering to
    delete first."""
    world = await _world(db_session, make_world)
    tavern, keep, _ = await _two_places_and_a_road(db_session, world.id)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    for place in (tavern, keep, tavern):
        await set_position(store, _player(session.id, AtLocation(location_id=place.id)))

    assert len(await store.load_character_positions(session.id, limit=50)) == 1


async def test_a_new_session_always_gets_an_explicit_position(
    db_session: AsyncSession, make_world
) -> None:
    """Even when it says `unlocated`. A session with no row at all is the ambiguity the
    whole correction exists to remove."""
    world = await _world(db_session, make_world)
    session = await _session(db_session, world.id, current_location="a place with no geography")
    store = SqlAlchemyTurnGateway(db_session)

    await materialize_initial_position(store, session_id=session.id)

    stored = await store.get_character_position(
        session.id, actor_kind=ActorKind.PLAYER, actor_id=session.id
    )
    assert stored is not None
    assert stored.position == Unlocated()


async def test_seeding_a_position_for_a_session_that_does_not_exist_is_not_found(
    db_session: AsyncSession,
) -> None:
    store = SqlAlchemyTurnGateway(db_session)
    with pytest.raises(NotFoundError):
        await materialize_initial_position(store, session_id=uuid.uuid4())


async def test_no_fact_may_say_where_someone_is(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """The other way a second answer could have got in.

    `character_positions` being canonical is only true if nothing else may write the
    same claim, and a fact is writable by any trusted caller. `system.location` is
    registered DEDICATED so both obvious spellings are a refusal that names the owner
    -- and ENGINE, the most privileged authority there is, gets the same answer.
    """
    world = await _world(db_session, make_world)
    tavern, _, _ = await _two_places_and_a_road(db_session, world.id)
    character = make_character(world.id)
    db_session.add(character)
    session = await _session(db_session, world.id)
    store = SqlAlchemyTurnGateway(db_session)

    for spelling in ("system.location", "system.position"):
        with pytest.raises(FactPolicyError, match="CharacterPosition owns this"):
            await apply_state_change(
                store,
                session_id=session.id,
                batch=StateMutationBatch(
                    authority=FactAuthority.ENGINE,
                    mutations=[
                        SetFact(
                            subject=FactSubject(type=FactSubjectType.CHARACTER, id=character.id),
                            property=spelling,
                            value=str(tavern.id),
                        )
                    ],
                ),
                cause=cause_from_resolution(),
            )
