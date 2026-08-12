"""WorldState as one thing: the root, the decomposition, and the promises they make.

The domains each have their own suite -- facts, geography, situations, time, events.
This one is about the claims that only make sense across all of them at once, and that
nothing else would catch if they broke:

    the root stays small              a session's state is four columns, not a document
    the parts stay separate           six tables, six lifecycles, one conceptual root
    a snapshot composes, never stores nothing here is written back
    scopes bound the answer           minimal is the default and the cheapest
    three counters stay independent   fictional time, turns and revision
    a session is its own world        two saves of one world share nothing mutable
    the template is read-only         playing never writes back to what a world starts as
    the prompt does not grow          a bigger world is not a bigger context
    one authority per question        one clock, one revision mechanism
    the trail is not the state        deleting history changes nothing that is true

See docs/world-state.md.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.context_builder import FACT_LIMIT, build_story_context
from app.application.position_service import materialize_initial_position
from app.application.situation_service import materialize_initial_situations
from app.application.spatial_service import materialize_initial_spatial_state
from app.application.state_consistency import (
    ConsistencyCheck,
    check_state_consistency,
)
from app.application.state_service import apply_state_change, materialize_initial_facts
from app.application.story_context import StoryContext
from app.application.time_service import advance_time, schedule_event
from app.application.world_state_service import (
    IMPORTANT_FACT_LIMIT,
    SnapshotScope,
    build_snapshot,
    get_current_time,
    get_revision,
    get_state_root,
)
from app.domain.errors import (
    FactPolicyError,
    NotFoundError,
    UnsupportedWorldStateVersionError,
)
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import (
    WORLD_SUBJECT,
    FactAuthority,
    FactKind,
    FactSubject,
    FactSubjectType,
    SetFact,
)
from app.domain.world_locations import (
    ConnectionCategory,
    LocationCategory,
    LocationCondition,
    LocationScale,
    UpdateLocationState,
)
from app.domain.world_situations import SituationCategory, StartSituation
from app.domain.world_state import WORLD_STATE_VERSION, WorldStateV1
from app.domain.world_time import TimeAdvanceReason, TimeAdvanceRequest
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway
from tests.support import cause_from_resolution

# ---------------------------------------------------------------------------
# Fixtures in the small: a world with geography, and a session initialised the
# way `create_session` initialises one.
# ---------------------------------------------------------------------------


def _place(world_id: uuid.UUID, name: str, **overrides: object) -> models.LocationDefinition:
    data: dict[str, object] = {
        "world_id": world_id,
        "name": name,
        "category": LocationCategory.STRUCTURE,
        "scale": LocationScale.BUILDING,
    }
    data.update(overrides)
    return models.LocationDefinition(**data)  # type: ignore[arg-type]


async def _world_with_geography(
    db: AsyncSession, make_world, make_character, **overrides: object
) -> tuple[models.World, models.Character, models.LocationDefinition]:
    """Riverwood, the Broken Crown inside it, a door between them, and Elena."""
    world = make_world(**overrides)
    db.add(world)
    await db.flush()
    character = make_character(world.id)
    db.add(character)
    town = _place(
        world.id, "Riverwood", category=LocationCategory.SETTLEMENT, scale=LocationScale.SETTLEMENT
    )
    db.add(town)
    await db.flush()
    tavern = _place(world.id, "Broken Crown", subtype="tavern", parent_location_id=town.id)
    db.add(tavern)
    await db.flush()
    db.add(
        models.LocationConnection(
            world_id=world.id,
            from_location_id=town.id,
            to_location_id=tavern.id,
            category=ConnectionCategory.PASSAGE,
            subtype="door",
        )
    )
    await db.flush()
    return world, character, tavern


async def _initialise(
    db: AsyncSession, world: models.World, **overrides: object
) -> models.GameSession:
    """A session, seeded exactly the way `POST /sessions` seeds one.

    Facts, then geography, then situations, then the player's position, in that order
    and in one transaction -- the same four calls `api.v1.sessions.create_session`
    makes. Duplicated here on purpose: a test that seeded a session some other way would
    prove things about a world no player can ever start.
    """
    data: dict[str, object] = {"world_id": world.id, "title": "Run", "player_name": "Rin"}
    data.update(overrides)
    session = models.GameSession(**data)  # type: ignore[arg-type]
    db.add(session)
    await db.flush()
    store = SqlAlchemyTurnGateway(db)
    await materialize_initial_facts(store, session_id=session.id)
    await materialize_initial_spatial_state(store, session_id=session.id)
    await materialize_initial_situations(store, session_id=session.id)
    await materialize_initial_position(store, session_id=session.id)
    return session


def _template_facts(character_id: uuid.UUID) -> list[dict]:
    return [
        SetFact(
            subject=WORLD_SUBJECT,
            property="world.political_status",
            value="contested",
            importance=5,
        ).model_dump(mode="json"),
        SetFact(
            subject=FactSubject(type=FactSubjectType.CHARACTER, id=character_id),
            property="narrative.birthplace",
            value="Arven",
            importance=1,
        ).model_dump(mode="json"),
    ]


def _template_situations() -> list[dict]:
    return [
        StartSituation(
            category=SituationCategory.CONFLICT,
            title="The road tolls",
            importance=4,
        ).model_dump(mode="json")
    ]


# ---------------------------------------------------------------------------
# 54. Initialization
# ---------------------------------------------------------------------------


async def test_a_new_session_is_one_coherent_world_at_revision_zero(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """Everything a session needs, present at once, and nothing claiming to have
    changed. A world arriving is not a world changing."""
    world, character, _ = await _world_with_geography(db_session, make_world, make_character)
    world.initial_facts = _template_facts(character.id)
    world.initial_situations = _template_situations()
    await db_session.flush()

    session = await _initialise(db_session, world)
    reader = SqlAlchemyTurnGateway(db_session)

    root = await get_state_root(reader, session_id=session.id)
    assert root.version == WORLD_STATE_VERSION
    assert root.session_id == session.id
    assert root.revision == 0
    assert root.time.elapsed_minutes == 0

    snapshot = await build_snapshot(reader, session_id=session.id, scope=SnapshotScope.FULL_DEBUG)
    assert snapshot.counts.facts == 2
    assert snapshot.counts.locations == 2
    assert snapshot.counts.connections == 1
    assert snapshot.counts.situations == 1
    # The state rows exist too, not only the definitions: a session that could see a
    # place but had no answer for whether it was standing would be half-initialised.
    assert [view.state for view in snapshot.locations] != [None, None]


async def test_the_revision_convention_does_not_depend_on_how_much_a_world_declared(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """Zero after initialisation, whether the template held everything or nothing.

    The alternative -- counting the seed as the first change -- would make the starting
    revision a function of how much its author wrote, and "has this session changed
    since it began?" would stop having an answer. See "The initial revision" in
    docs/world-state.md.
    """
    full, character, _ = await _world_with_geography(db_session, make_world, make_character)
    full.initial_facts = _template_facts(character.id)
    full.initial_situations = _template_situations()
    empty = make_world(name="Bare")
    db_session.add(empty)
    await db_session.flush()

    seeded = await _initialise(db_session, full)
    bare = await _initialise(db_session, empty)
    reader = SqlAlchemyTurnGateway(db_session)

    assert await get_revision(reader, session_id=seeded.id) == 0
    assert await get_revision(reader, session_id=bare.id) == 0


async def test_initialisation_that_fails_leaves_no_half_built_world(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """A template naming a character of another world is refused, and the session it
    was seeding goes with it. Half a world is worse than no world: there is no retry
    that repairs a save the player has already started."""
    _, character, _ = await _world_with_geography(db_session, make_world, make_character)
    await db_session.commit()

    other = make_world(name="Elsewhere")
    other.initial_facts = _template_facts(character.id)
    db_session.add(other)
    await db_session.flush()

    with pytest.raises(NotFoundError):
        await _initialise(db_session, other)
    await db_session.rollback()

    remaining = (
        await db_session.execute(select(func.count()).select_from(models.GameSession))
    ).scalar()
    assert remaining == 0


# ---------------------------------------------------------------------------
# 55. Composition: the root is small, the parts are separate
# ---------------------------------------------------------------------------


def test_the_root_carries_nothing_with_a_cardinality() -> None:
    """Four fields, and none of them a collection.

    This is the constraint the whole consolidation exists to hold. A root that grew a
    `facts` list would be one JSON document holding a world -- unqueryable,
    unindexable, and rewritten in full every time a lamp went out.
    """
    assert set(WorldStateV1.model_fields) == {"version", "session_id", "revision", "time"}
    for name, field in WorldStateV1.model_fields.items():
        annotation = str(field.annotation)
        assert "list" not in annotation and "dict" not in annotation, (
            f"{name} carries a collection; the root must not grow with a session"
        )


def test_each_kind_of_state_lives_in_its_own_table_keyed_by_session() -> None:
    """Separate tables, separate indexes, separate lifecycles -- and every one of them
    reachable from the session that owns it. That is what makes the decomposition a
    decomposition rather than six ways of spelling the same document."""
    owned = [
        models.WorldFact,
        models.LocationState,
        models.LocationConnectionState,
        models.Situation,
        models.ScheduledEvent,
        models.GameEvent,
    ]
    assert len({model.__tablename__ for model in owned}) == len(owned)
    for model in owned:
        column = model.__table__.columns["session_id"]
        assert not column.nullable
        assert [key.column.table.name for key in column.foreign_keys] == ["game_sessions"]


def test_no_column_anywhere_stores_a_session_world_as_one_blob() -> None:
    """The session row holds the root and nothing else that is state.

    `worlds` may carry JSON -- rules, the starting date, the template -- because a
    template is configuration read as a unit. A session's *mutable* reality may not.
    """
    json_columns = [
        column.name
        for column in models.GameSession.__table__.columns
        if column.type.__class__.__name__ == "JSON"
    ]
    assert json_columns == []


async def test_a_snapshot_composes_the_parts_it_never_owns(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world, character, tavern = await _world_with_geography(db_session, make_world, make_character)
    world.initial_facts = _template_facts(character.id)
    await db_session.flush()
    session = await _initialise(db_session, world)
    store = SqlAlchemyTurnGateway(db_session)
    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.DAMAGED),
                StartSituation(
                    category=SituationCategory.CONFLICT,
                    title="Fire at the Broken Crown",
                    primary_location_id=tavern.id,
                    importance=5,
                ),
            ],
        ),
        cause=cause_from_resolution(),
    )
    await schedule_event(
        store, session_id=session.id, event_type="roof_collapses", delay_minutes=90
    )

    snapshot = await build_snapshot(store, session_id=session.id, scope=SnapshotScope.FULL_DEBUG)

    assert {fact.property for fact in snapshot.facts} == {
        "world.political_status",
        "narrative.birthplace",
    }
    assert [situation.title for situation in snapshot.situations] == ["Fire at the Broken Crown"]
    assert [event.type for event in snapshot.scheduled_events] == ["roof_collapses"]
    damaged = next(view for view in snapshot.locations if view.definition.id == tavern.id)
    assert damaged.state is not None
    assert damaged.state.condition is LocationCondition.DAMAGED
    # Composed, never stored: the snapshot's revision is the root's, and the root is
    # four columns that know nothing about any of the above.
    assert snapshot.revision == await get_revision(store, session_id=session.id)


# ---------------------------------------------------------------------------
# 56. Snapshot scoping
# ---------------------------------------------------------------------------


async def _played_session(
    db: AsyncSession, make_world, make_character
) -> tuple[models.GameSession, SqlAlchemyTurnGateway]:
    world, character, _ = await _world_with_geography(db, make_world, make_character)
    world.initial_facts = _template_facts(character.id)
    world.initial_situations = _template_situations()
    await db.flush()
    session = await _initialise(db, world, current_location="Broken Crown")
    return session, SqlAlchemyTurnGateway(db)


async def test_a_minimal_snapshot_counts_the_world_without_carrying_it(
    db_session: AsyncSession, make_world, make_character
) -> None:
    session, reader = await _played_session(db_session, make_world, make_character)

    snapshot = await build_snapshot(reader, session_id=session.id)

    assert snapshot.scope is SnapshotScope.MINIMAL, "the cheap answer is the default one"
    assert snapshot.counts.facts == 2
    assert snapshot.counts.locations == 2
    # Counted, not carried. The lists that grow with a session are empty here, and the
    # two that are not are hard-bounded.
    assert snapshot.facts == []
    assert snapshot.locations == []
    assert snapshot.connections == []
    assert snapshot.situations == []
    assert snapshot.scheduled_events == []
    assert snapshot.recent_events == []
    assert len(snapshot.important_facts) <= IMPORTANT_FACT_LIMIT
    # Only the load-bearing ones: "the world is contested", not where Elena was born.
    assert [fact.property for fact in snapshot.important_facts] == ["world.political_status"]


async def test_each_scope_adds_to_the_one_below_and_never_takes_away(
    db_session: AsyncSession, make_world, make_character
) -> None:
    session, reader = await _played_session(db_session, make_world, make_character)

    snapshots = {
        scope: await build_snapshot(reader, session_id=session.id, scope=scope)
        for scope in SnapshotScope
    }

    assert snapshots[SnapshotScope.MINIMAL].current_location is None
    assert snapshots[SnapshotScope.RELEVANT].current_location is not None
    assert snapshots[SnapshotScope.RELEVANT].nearby_locations == []
    assert snapshots[SnapshotScope.REGIONAL].nearby_locations != []
    assert snapshots[SnapshotScope.REGIONAL].locations == []
    assert snapshots[SnapshotScope.FULL_DEBUG].locations != []

    # The counts are the same at every scope, because they are the part a caller has to
    # be able to trust regardless of how much it asked for.
    counts = {snapshot.counts for snapshot in snapshots.values()}
    assert len(counts) == 1


async def test_the_gameplay_endpoint_refuses_to_dump_a_whole_world(app_client) -> None:
    """`full_debug` is an operator's view, not a query string a game screen can pass."""
    world = (await app_client.post("/api/v1/worlds", json={"name": "W"})).json()
    session = (
        await app_client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()

    refused = await app_client.get(
        f"/api/v1/sessions/{session['id']}/world-state", params={"scope": "full_debug"}
    )
    assert refused.status_code == 422, refused.text

    allowed = await app_client.get(f"/api/v1/sessions/{session['id']}/world-state")
    assert allowed.status_code == 200
    assert allowed.json()["scope"] == "minimal"

    # And it is still reachable for a person with a problem, on the development router.
    debug = await app_client.get(f"/api/v1/dev/sessions/{session['id']}/world-state")
    assert debug.status_code == 200
    assert debug.json()["scope"] == "full_debug"


# ---------------------------------------------------------------------------
# 57 / 11. One revision per resolution, and three independent counters
# ---------------------------------------------------------------------------


async def test_one_batch_across_three_domains_moves_the_revision_once(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """The revision counts logical state changes, not fields touched. A resolution that
    burns a tavern, kills a rumour and starts a panic is one change to the world."""
    world, character, tavern = await _world_with_geography(db_session, make_world, make_character)
    session = await _initialise(db_session, world)
    store = SqlAlchemyTurnGateway(db_session)

    result = await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                SetFact(
                    subject=FactSubject(type=FactSubjectType.CHARACTER, id=character.id),
                    property="system.alive",
                    value=False,
                    importance=5,
                ),
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.DESTROYED),
                StartSituation(
                    category=SituationCategory.CONFLICT, title="Panic in Riverwood", importance=4
                ),
            ],
        ),
        cause=cause_from_resolution(),
    )

    assert len(result.applied) == 3
    assert result.revision == 1


async def test_fictional_time_turns_and_the_revision_never_derive_from_each_other(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """Three counters, three reasons to move, and no arithmetic between them.

    Time moving is not a state change. A state change is not time passing. Either can
    happen without the other, and a build that derived one from another would make one
    of them a lie the first time they disagreed.
    """
    world, character, _ = await _world_with_geography(db_session, make_world, make_character)
    session = await _initialise(db_session, world)
    session_id = session.id
    await db_session.commit()
    store = SqlAlchemyTurnGateway(db_session)

    # Time forward, state still.
    await advance_time(
        store,
        session_id=session_id,
        request=TimeAdvanceRequest(requested_minutes=180, reason=TimeAdvanceReason.NARRATIVE),
    )
    root = await get_state_root(store, session_id=session_id)
    assert root.time.elapsed_minutes == 180
    assert root.revision == 0

    # State forward, clock still.
    await apply_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                SetFact(
                    subject=FactSubject(type=FactSubjectType.CHARACTER, id=character.id),
                    property="system.alive",
                    value=False,
                    importance=5,
                )
            ],
        ),
        cause=cause_from_resolution(),
    )
    after = await get_state_root(store, session_id=session_id)
    assert after.revision == 1
    assert after.time.elapsed_minutes == 180

    # And the turn index moved for neither of them.
    row = await db_session.get(models.GameSession, session_id)
    assert row is not None
    assert row.turn_index == 0


# ---------------------------------------------------------------------------
# 58. Cross-session isolation
# ---------------------------------------------------------------------------


async def test_two_saves_of_one_world_never_see_each_others_reality(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world, character, tavern = await _world_with_geography(db_session, make_world, make_character)
    world.initial_facts = _template_facts(character.id)
    await db_session.flush()
    first = await _initialise(db_session, world, title="A")
    second = await _initialise(db_session, world, title="B", player_name="Kai")
    store = SqlAlchemyTurnGateway(db_session)

    await apply_state_change(
        store,
        session_id=first.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.DESTROYED),
                SetFact(subject=WORLD_SUBJECT, property="world.political_status", value="fallen"),
                StartSituation(
                    category=SituationCategory.CONFLICT, title="The looting", importance=4
                ),
            ],
        ),
        cause=cause_from_resolution(),
    )

    ruined = await build_snapshot(store, session_id=first.id, scope=SnapshotScope.FULL_DEBUG)
    intact = await build_snapshot(store, session_id=second.id, scope=SnapshotScope.FULL_DEBUG)

    assert ruined.revision == 1
    assert intact.revision == 0
    assert [situation.title for situation in intact.situations] == []
    assert (
        next(view for view in intact.locations if view.definition.id == tavern.id).state.condition
        is LocationCondition.INTACT
    )
    assert {fact.value for fact in intact.facts if fact.property == "world.political_status"} == {
        "contested"
    }
    # Same geography, because definitions are shared. Different answers about it,
    # because state is not.
    assert {view.definition.id for view in ruined.locations} == {
        view.definition.id for view in intact.locations
    }


# ---------------------------------------------------------------------------
# 59. Template immutability
# ---------------------------------------------------------------------------


async def test_playing_a_session_never_writes_back_to_the_world_it_started_from(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """The template is a starting configuration, not a save file. Burning the tavern in
    one run must leave every other run, and every future one, exactly as it was."""
    world, character, tavern = await _world_with_geography(db_session, make_world, make_character)
    world.initial_facts = _template_facts(character.id)
    world.initial_situations = _template_situations()
    await db_session.flush()
    before = {
        "facts": list(world.initial_facts),
        "situations": list(world.initial_situations),
        "rules": dict(world.rules_json),
        "start": dict(world.initial_datetime),
    }
    session = await _initialise(db_session, world)
    store = SqlAlchemyTurnGateway(db_session)

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.DESTROYED),
                SetFact(subject=WORLD_SUBJECT, property="world.political_status", value="fallen"),
            ],
        ),
        cause=cause_from_resolution(),
    )
    await advance_time(
        store,
        session_id=session.id,
        request=TimeAdvanceRequest(requested_minutes=600, reason=TimeAdvanceReason.NARRATIVE),
    )

    await db_session.refresh(world)
    assert world.initial_facts == before["facts"]
    assert world.initial_situations == before["situations"]
    assert world.rules_json == before["rules"]
    assert world.initial_datetime == before["start"]

    # And the shared geography itself: a destroyed place keeps its definition, because
    # the ruin is still somewhere the next session starts with.
    await db_session.refresh(tavern)
    assert tavern.name == "Broken Crown"
    definitions = (
        await db_session.execute(
            select(func.count())
            .select_from(models.LocationDefinition)
            .where(models.LocationDefinition.world_id == world.id)
        )
    ).scalar()
    assert definitions == 2


# ---------------------------------------------------------------------------
# 60 / 19 / 26. StoryContext stays bounded however large the world gets
# ---------------------------------------------------------------------------


async def test_a_bigger_world_does_not_make_a_bigger_prompt(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """Having more state has never meant sending more of it.

    A hundred and twenty established truths reach the context as at most `FACT_LIMIT`,
    chosen by importance. The alternative is a prompt whose size is a function of how
    long the player has been playing.
    """
    world, _, _ = await _world_with_geography(db_session, make_world, make_character)
    session = await _initialise(db_session, world)
    store = SqlAlchemyTurnGateway(db_session)

    for batch_index in range(3):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ADMIN,
                mutations=[
                    SetFact(
                        subject=WORLD_SUBJECT,
                        property=f"narrative.detail_{batch_index}_{index}",
                        value="something true",
                        importance=(index % 5) + 1,
                    )
                    for index in range(40)
                ],
            ),
            cause=cause_from_resolution(),
        )

    snapshot = await build_snapshot(store, session_id=session.id)
    assert snapshot.counts.facts == 120

    session_snapshot = await store.get_session(session.id)
    world_snapshot = await store.get_world(world.id)
    assert session_snapshot is not None and world_snapshot is not None
    context = await build_story_context(
        store,
        session=session_snapshot,
        world=world_snapshot,
        player_action="I look around.",
    )

    reaching_the_prompt = len(context.world_facts.critical) + len(context.world_facts.relevant)
    assert reaching_the_prompt <= FACT_LIMIT
    assert reaching_the_prompt < snapshot.counts.facts


def test_the_story_director_is_never_handed_the_mechanical_audit_trail() -> None:
    """Resolutions are an audit record, not something a narrator reasons over. There is
    no field to put them in, which is the only reliable way to keep them out."""
    assert "resolutions" not in StoryContext.model_fields
    assert not any(
        "Resolution" in str(field.annotation) for field in StoryContext.model_fields.values()
    )


# ---------------------------------------------------------------------------
# 61 / 30 / 52. One authority per question
# ---------------------------------------------------------------------------


async def test_the_root_reads_the_clock_and_does_not_keep_one(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world, _, _ = await _world_with_geography(db_session, make_world, make_character)
    session = await _initialise(db_session, world)
    session_id = session.id
    await db_session.commit()
    store = SqlAlchemyTurnGateway(db_session)

    result = await advance_time(
        store,
        session_id=session_id,
        request=TimeAdvanceRequest(requested_minutes=45, reason=TimeAdvanceReason.NARRATIVE),
    )

    # One number, read through two doors. A second `elapsed_minutes` on the root would
    # be a second answer to what time it is, and the first disagreement would be a bug
    # nobody could adjudicate.
    assert (await get_current_time(store, session_id=session_id)).elapsed_minutes == 45
    assert result.ended_at == 45
    row = await db_session.get(models.GameSession, session_id)
    assert row is not None
    assert row.elapsed_minutes == 45


def test_only_the_session_row_carries_a_revision() -> None:
    """One mechanism, not two. `resolutions` records the revision before and after as
    an audit fact about a change that already happened; nothing else keeps a counter of
    its own, because a second counter is a second truth waiting to diverge."""
    counters = {
        (model.__tablename__, column.name)
        for model in (
            models.GameSession,
            models.WorldFact,
            models.LocationState,
            models.LocationConnectionState,
            models.Situation,
            models.ScheduledEvent,
            models.GameEvent,
        )
        for column in model.__table__.columns
        if "revision" in column.name
    }
    assert counters == {("game_sessions", "state_revision")}


# ---------------------------------------------------------------------------
# 62 / 53. One transaction
# ---------------------------------------------------------------------------


async def test_a_refused_change_leaves_the_world_exactly_as_it_was(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world, character, tavern = await _world_with_geography(db_session, make_world, make_character)
    world.initial_facts = _template_facts(character.id)
    await db_session.flush()
    session = await _initialise(db_session, world)
    store = SqlAlchemyTurnGateway(db_session)
    before = await build_snapshot(store, session_id=session.id, scope=SnapshotScope.FULL_DEBUG)

    # A batch the engine is entitled to send, holding one mutation nobody may make:
    # `derived.*` is computed and stored nowhere. The tavern burns in the same batch, so
    # a refusal that let the first mutation through would show up below.
    with pytest.raises(FactPolicyError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ENGINE,
                mutations=[
                    UpdateLocationState(
                        location_id=tavern.id, condition=LocationCondition.DESTROYED
                    ),
                    SetFact(
                        subject=FactSubject(type=FactSubjectType.CHARACTER, id=character.id),
                        property="derived.is_injured",
                        value=True,
                    ),
                ],
            ),
            cause=cause_from_resolution(),
        )

    after = await build_snapshot(store, session_id=session.id, scope=SnapshotScope.FULL_DEBUG)
    assert after.model_dump() == before.model_dump()


async def test_a_snapshot_never_shows_a_revision_its_contents_disagree_with(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """Built from one open transaction, so the counter and the things it counts come
    from the same view of the database rather than from eight independent reads."""
    world, _, tavern = await _world_with_geography(db_session, make_world, make_character)
    session = await _initialise(db_session, world)
    store = SqlAlchemyTurnGateway(db_session)

    for index, condition in enumerate(
        (LocationCondition.DAMAGED, LocationCondition.DESTROYED), start=1
    ):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ENGINE,
                mutations=[UpdateLocationState(location_id=tavern.id, condition=condition)],
            ),
            cause=cause_from_resolution(),
        )
        snapshot = await build_snapshot(
            store, session_id=session.id, scope=SnapshotScope.FULL_DEBUG
        )
        assert snapshot.revision == index
        state = next(view for view in snapshot.locations if view.definition.id == tavern.id).state
        assert state is not None
        assert state.condition is condition


# ---------------------------------------------------------------------------
# 63 / 45. The trail is not the state
# ---------------------------------------------------------------------------


class _NoHistory:
    """A reader that refuses to be asked about history.

    Wraps the real adapter and fails the test if anything reads the event trail while
    building a gameplay-scope snapshot. Current state is read from the tables that hold
    it; a build that had to replay events to know what is true would be an event-sourced
    system, which this deliberately is not.
    """

    def __init__(self, inner: SqlAlchemyTurnGateway) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def load_events(self, *args, **kwargs):
        raise AssertionError("current state must not be derived from the event trail")


async def test_reading_the_world_does_not_touch_the_event_trail(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world, character, _ = await _world_with_geography(db_session, make_world, make_character)
    world.initial_facts = _template_facts(character.id)
    await db_session.flush()
    session = await _initialise(db_session, world, current_location="Broken Crown")
    store = SqlAlchemyTurnGateway(db_session)

    for scope in (SnapshotScope.MINIMAL, SnapshotScope.RELEVANT, SnapshotScope.REGIONAL):
        snapshot = await build_snapshot(_NoHistory(store), session_id=session.id, scope=scope)
        assert snapshot.counts.facts == 2


async def test_deleting_every_event_changes_nothing_that_is_currently_true(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """History explains the world. It does not constitute it.

    A session whose entire trail is gone still knows the tavern burned, because the
    tavern's state is a row about the tavern -- not a conclusion drawn from a log.
    """
    world, character, tavern = await _world_with_geography(db_session, make_world, make_character)
    world.initial_facts = _template_facts(character.id)
    await db_session.flush()
    session = await _initialise(db_session, world)
    session_id = session.id
    store = SqlAlchemyTurnGateway(db_session)
    await apply_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.DESTROYED),
                SetFact(subject=WORLD_SUBJECT, property="world.political_status", value="fallen"),
            ],
        ),
        cause=cause_from_resolution(),
    )
    before = await build_snapshot(store, session_id=session_id, scope=SnapshotScope.REGIONAL)
    # Stated up front so the provenance assertion at the end cannot go vacuous: the
    # seeded facts do point at the event that established them, before it is deleted.
    seeded = await store.load_facts(session_id, limit=50)
    assert any(fact.source_event_id is not None for fact in seeded)

    await db_session.execute(
        delete(models.GameEvent).where(models.GameEvent.session_id == session_id)
    )
    await db_session.commit()
    db_session.expire_all()

    after = await build_snapshot(store, session_id=session_id, scope=SnapshotScope.REGIONAL)
    assert after.model_dump() == before.model_dump()
    assert after.revision == 1, "the revision is a column, not a count of events"

    facts = await store.load_facts(session_id, limit=50)
    assert {fact.property: fact.value for fact in facts} == {
        "world.political_status": "fallen",
        "narrative.birthplace": "Arven",
    }
    # What the deletion did take is provenance: the seeded facts pointed at the event
    # that established them and now point at nothing. That is the correct casualty --
    # "why is this true" is a question about history, and history is what was removed.
    assert all(fact.source_event_id is None for fact in facts)


# ---------------------------------------------------------------------------
# 34. The version boundary
# ---------------------------------------------------------------------------


async def test_a_session_written_by_a_build_we_cannot_read_is_refused_loudly(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """Not best-effort. A root stamped `2` means something this code does not know, and
    playing a story against a state model nobody wrote fails later as nonsense instead
    of here as an error."""
    world, _, _ = await _world_with_geography(db_session, make_world, make_character)
    session = await _initialise(db_session, world)
    session.world_state_version = 2
    await db_session.flush()
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(UnsupportedWorldStateVersionError):
        await get_state_root(store, session_id=session.id)
    with pytest.raises(UnsupportedWorldStateVersionError):
        await get_revision(store, session_id=session.id)
    with pytest.raises(UnsupportedWorldStateVersionError):
        await build_snapshot(store, session_id=session.id)


async def test_the_consistency_check_describes_an_unreadable_version_rather_than_refusing(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """The one place the refusal is inverted. Declining to describe the thing somebody
    is trying to diagnose would defeat the diagnostic."""
    world, _, _ = await _world_with_geography(db_session, make_world, make_character)
    session = await _initialise(db_session, world)
    session.world_state_version = 2
    await db_session.flush()
    store = SqlAlchemyTurnGateway(db_session)

    report = await check_state_consistency(store, session_id=session.id)

    assert report.consistent is False
    assert [issue.check for issue in report.issues] == [ConsistencyCheck.ROOT]
    assert "world_state_version 2" in report.issues[0].detail


# ---------------------------------------------------------------------------
# 40. The consistency validator
# ---------------------------------------------------------------------------


async def test_a_healthy_session_passes_every_check(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world, character, tavern = await _world_with_geography(db_session, make_world, make_character)
    world.initial_facts = _template_facts(character.id)
    world.initial_situations = _template_situations()
    await db_session.flush()
    session = await _initialise(db_session, world)
    store = SqlAlchemyTurnGateway(db_session)
    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateLocationState(location_id=tavern.id, condition=LocationCondition.DAMAGED),
                StartSituation(
                    category=SituationCategory.CONFLICT,
                    title="Fire at the Broken Crown",
                    primary_location_id=tavern.id,
                    importance=5,
                ),
            ],
        ),
        cause=cause_from_resolution(),
    )
    await schedule_event(
        store, session_id=session.id, event_type="roof_collapses", delay_minutes=90
    )

    report = await check_state_consistency(store, session_id=session.id)

    assert report.issues == []
    assert report.consistent is True
    assert report.truncated is False
    assert report.revision == 1
    # What ran is as load-bearing as what it found: a clean report from a run that
    # skipped half the world would mean nothing.
    assert report.checks == list(ConsistencyCheck)


async def test_a_state_row_for_a_place_this_session_cannot_see_is_reported(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """The gap a foreign key does not close. `location_states.location_id` points at a
    real definition -- of somewhere in another world."""
    world, _, _ = await _world_with_geography(db_session, make_world, make_character)
    session = await _initialise(db_session, world)
    elsewhere = make_world(name="Elsewhere")
    db_session.add(elsewhere)
    await db_session.flush()
    stranger = _place(elsewhere.id, "A tower nobody here has heard of")
    db_session.add(stranger)
    await db_session.flush()
    db_session.add(models.LocationState(session_id=session.id, location_id=stranger.id))
    await db_session.flush()

    report = await check_state_consistency(SqlAlchemyTurnGateway(db_session), session_id=session.id)

    assert [issue.check for issue in report.issues] == [ConsistencyCheck.LOCATION_STATES]
    assert report.issues[0].entity == "LocationState"


async def test_a_fact_about_a_character_nobody_ever_wrote_is_reported(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """`world_facts.subject_id` has no foreign key -- a subject may be a character
    today and a faction when factions exist, and a column pointing at two tables
    constrains neither. This check is what stands in for the missing constraint."""
    world, _, _ = await _world_with_geography(db_session, make_world, make_character)
    session = await _initialise(db_session, world)
    ghost = uuid.uuid4()
    db_session.add(
        models.WorldFact(
            session_id=session.id,
            kind=FactKind.WORLD_TRUTH,
            subject_type=FactSubjectType.CHARACTER,
            subject_id=ghost,
            property="narrative.birthplace",
            value="Arven",
            importance=1,
            current_value_since=0,
            authority=FactAuthority.ADMIN,
        )
    )
    await db_session.flush()

    report = await check_state_consistency(SqlAlchemyTurnGateway(db_session), session_id=session.id)

    assert [issue.check for issue in report.issues] == [ConsistencyCheck.FACT_SUBJECTS]
    assert str(ghost) in report.issues[0].detail


async def test_checking_a_session_that_does_not_exist_is_a_refusal_not_a_clean_report(
    db_session: AsyncSession,
) -> None:
    """ "Nothing wrong" is the one answer this must never give by accident."""
    with pytest.raises(NotFoundError):
        await check_state_consistency(SqlAlchemyTurnGateway(db_session), session_id=uuid.uuid4())
