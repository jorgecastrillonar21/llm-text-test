"""Situations through the application layer, against a real database.

Covers the things the domain tests cannot: session isolation, participant queries,
progression against real WorldRules, the atomicity of a batch that touches three
domains, and which situations reach a prompt.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.persistence import NewLocation, ResolutionStorePort
from app.application.resolution_service import (
    ResolutionRequest,
    ResolutionResult,
    resolve,
)
from app.application.resolvers import (
    SITUATION_PROGRESS_DUE_EVENT,
    SITUATION_PROGRESSED_EVENT,
)
from app.application.situation_context import (
    MAX_SITUATIONS_IN_CONTEXT,
    SECRET_TAG,
    build_situations_context,
    select_relevant,
)
from app.application.situation_service import (
    GenericProgressionResolver,
    ProgressionContext,
    materialize_initial_situations,
    progress_situation,
    situations_involving,
    start_situation,
)
from app.application.spatial_service import create_location
from app.application.state_service import apply_state_change
from app.domain.errors import NotFoundError, StaleStateError, ValidationError
from app.domain.resolution import (
    ProgressSituationCommand,
    ResolutionDisposition,
    ResolutionSourceType,
)
from app.domain.situation_progression import GeneratedEvent, SituationProgressionResult
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import FactAuthority
from app.domain.world_locations import (
    LocationAccessibility,
    LocationCategory,
    LocationCondition,
    LocationScale,
    UpdateLocationState,
)
from app.domain.world_rules import default_world_rules
from app.domain.world_situations import (
    LIVE_STATUSES,
    ParticipantEntityType,
    ParticipantSpec,
    ProgressionTrigger,
    ResolveSituation,
    SituationCategory,
    SituationDeltas,
    SituationProgressionRequest,
    SituationScope,
    SituationStatus,
    StartSituation,
    UpdateSituation,
)
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway
from tests.support import cause_from_event, cause_from_resolution

from .test_world_situations import make_situation


async def _world_and_session(
    db_session: AsyncSession, make_world, **world_overrides: object
) -> tuple[uuid.UUID, uuid.UUID]:
    # `rules_json` spelled out because the column default only applies at INSERT, and
    # these tests read `world.rules` through the gateway before that happens.
    defaults: dict[str, object] = {"rules_json": default_world_rules().model_dump(mode="json")}
    world = make_world(**{**defaults, **world_overrides})
    db_session.add(world)
    await db_session.flush()
    session = models.GameSession(
        world_id=world.id, title="S", player_name="Rin", current_location="somewhere"
    )
    db_session.add(session)
    await db_session.commit()
    # Captured before the commit expires the instances.
    return world.id, session.id


async def _progress(
    store: ResolutionStorePort,
    session_id: uuid.UUID,
    situation_id: uuid.UUID,
    *,
    trigger: ProgressionTrigger = ProgressionTrigger.SCHEDULED,
    key: str | None = None,
) -> ResolutionResult:
    """Run one situation forward through the real pipeline.

    The interval is not passed: the resolver derives it from the situation's own
    `last_progressed_at` and the session clock, which is the property that stops a
    caller evaluating the same six hours twice. Tests set the clock and then ask.

    A fresh idempotency key by default, because most of these tests want a resolution
    rather than a replay. The ones testing replay pass their own.
    """
    return await resolve(
        store,
        request=ResolutionRequest(
            session_id=session_id,
            command=ProgressSituationCommand(situation_id=situation_id, trigger=trigger),
            idempotency_key=key or f"test:{uuid.uuid4()}",
            source_type=ResolutionSourceType.SITUATION_PROGRESSION,
            source_id=situation_id,
        ),
    )


def _siege(**overrides: object) -> StartSituation:
    data: dict[str, object] = {
        "category": SituationCategory.CONFLICT,
        "subtype": "siege",
        "title": "Siege of Asterfall",
        "intensity": 50,
        "threat": 70,
        "momentum": 30,
        "importance": 4,
        "scope": SituationScope.REGIONAL,
    }
    data.update(overrides)
    return StartSituation(**data)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Creation and session isolation
# ---------------------------------------------------------------------------


async def test_a_situation_is_written_with_the_application_minting_its_id(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(), started_at=120
    )
    await db_session.commit()

    stored = await store.get_situation(session_id, situation_id)
    assert stored is not None
    assert stored.title == "Siege of Asterfall"
    assert stored.started_at == 120
    # A brand-new process has just been looked at; starting this at zero would make its
    # first interval run from the beginning of the session.
    assert stored.last_progressed_at == 120
    assert stored.resolved_at is None


async def test_a_situation_in_one_session_is_invisible_to_another(
    db_session: AsyncSession, make_world
) -> None:
    world_id, session_id = await _world_and_session(db_session, make_world)
    other = models.GameSession(world_id=world_id, title="B", player_name="Other")
    db_session.add(other)
    await db_session.flush()
    other_id = other.id

    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(), started_at=0
    )
    await db_session.commit()

    assert await store.get_situation(session_id, situation_id) is not None
    assert await store.get_situation(other_id, situation_id) is None
    assert await store.load_situations(other_id, limit=50) == []


async def test_a_situation_cannot_be_mutated_from_another_session(
    db_session: AsyncSession, make_world
) -> None:
    world_id, session_id = await _world_and_session(db_session, make_world)
    other = models.GameSession(world_id=world_id, title="B", player_name="Other")
    db_session.add(other)
    await db_session.flush()
    other_id = other.id

    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(), started_at=0
    )
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await apply_state_change(
            store,
            session_id=other_id,
            batch=StateMutationBatch(
                authority=FactAuthority.ENGINE,
                mutations=[UpdateSituation(situation_id=situation_id, intensity_delta=50)],
            ),
            cause=cause_from_resolution(),
        )


async def test_a_primary_location_this_session_cannot_see_is_refused(
    db_session: AsyncSession, make_world
) -> None:
    world_id, session_id = await _world_and_session(db_session, make_world)
    other = models.GameSession(world_id=world_id, title="B", player_name="Other")
    db_session.add(other)
    await db_session.flush()
    other_id = other.id

    store = SqlAlchemyTurnGateway(db_session)
    # Generated inside the *other* save, so this one cannot see it.
    hidden = await create_location(
        store,
        session_id=other_id,
        location=NewLocation(
            world_id=world_id,
            origin_session_id=other_id,
            name="Their castle",
            category=LocationCategory.STRUCTURE,
            scale=LocationScale.SITE,
        ),
        narrated=False,
    )
    await db_session.commit()

    with pytest.raises(NotFoundError):
        await start_situation(
            store,
            session_id=session_id,
            mutation=_siege(primary_location_id=hidden),
            started_at=0,
        )


async def test_a_character_participant_must_exist(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world_id, session_id = await _world_and_session(db_session, make_world)
    character = make_character(world_id)
    db_session.add(character)
    await db_session.commit()
    character_id = character.id

    store = SqlAlchemyTurnGateway(db_session)
    with pytest.raises(NotFoundError):
        await start_situation(
            store,
            session_id=session_id,
            mutation=_siege(
                participants=(
                    ParticipantSpec(
                        entity_type=ParticipantEntityType.CHARACTER,
                        entity_id=uuid.uuid4(),
                        role="defender",
                    ),
                )
            ),
            started_at=0,
        )

    # The real one goes in.
    situation_id = await start_situation(
        store,
        session_id=session_id,
        mutation=_siege(
            participants=(
                ParticipantSpec(
                    entity_type=ParticipantEntityType.CHARACTER,
                    entity_id=character_id,
                    role="Defender",
                ),
            )
        ),
        started_at=0,
    )
    await db_session.commit()

    participants = await store.load_participants([situation_id])
    assert [p.role for p in participants] == ["defender"]


async def test_a_faction_participant_is_accepted_on_trust(
    db_session: AsyncSession, make_world
) -> None:
    """There is no faction table yet. Recorded as a test rather than tolerated
    silently, so the day one exists this is the line that changes."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    situation_id = await start_situation(
        store,
        session_id=session_id,
        mutation=_siege(
            participants=(
                ParticipantSpec(
                    entity_type=ParticipantEntityType.FACTION,
                    entity_id=uuid.uuid4(),
                    role="attacker",
                ),
            )
        ),
        started_at=0,
    )
    await db_session.commit()
    assert len(await store.load_participants([situation_id])) == 1


async def test_situations_can_be_found_by_participant(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world_id, session_id = await _world_and_session(db_session, make_world)
    character = make_character(world_id)
    db_session.add(character)
    await db_session.commit()
    character_id = character.id

    store = SqlAlchemyTurnGateway(db_session)
    involved = await start_situation(
        store,
        session_id=session_id,
        mutation=_siege(
            title="The siege",
            participants=(
                ParticipantSpec(
                    entity_type=ParticipantEntityType.CHARACTER,
                    entity_id=character_id,
                    role="defender",
                ),
            ),
        ),
        started_at=0,
    )
    await start_situation(
        store, session_id=session_id, mutation=_siege(title="Something else"), started_at=0
    )
    await db_session.commit()

    found = await situations_involving(store, session_id=session_id, entity_id=character_id)
    assert [s.id for s in found] == [involved]


async def test_attaching_a_known_participant_twice_is_idempotent(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world_id, session_id = await _world_and_session(db_session, make_world)
    character = make_character(world_id)
    db_session.add(character)
    await db_session.commit()
    character_id = character.id

    store = SqlAlchemyTurnGateway(db_session)
    spec = ParticipantSpec(
        entity_type=ParticipantEntityType.CHARACTER, entity_id=character_id, role="defender"
    )
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(participants=(spec,)), started_at=0
    )
    from app.application.persistence import NewParticipant

    first = await store.add_participant(
        NewParticipant(
            situation_id=situation_id,
            entity_type=spec.entity_type,
            entity_id=spec.entity_id,
            role=spec.role,
        )
    )
    await db_session.commit()

    assert len(await store.load_participants([situation_id])) == 1
    assert first is not None


# ---------------------------------------------------------------------------
# Hierarchy through the service
# ---------------------------------------------------------------------------


async def test_a_child_situation_records_its_cause(db_session: AsyncSession, make_world) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    war = await start_situation(
        store, session_id=session_id, mutation=_siege(title="The war"), started_at=0
    )
    siege = await start_situation(
        store,
        session_id=session_id,
        mutation=_siege(title="Siege of Asterfall", parent_situation_id=war),
        started_at=10,
    )
    await db_session.commit()

    child = await store.get_situation(session_id, siege)
    assert child is not None and child.parent_situation_id == war


async def test_a_parent_from_another_session_is_refused(
    db_session: AsyncSession, make_world
) -> None:
    world_id, session_id = await _world_and_session(db_session, make_world)
    other = models.GameSession(world_id=world_id, title="B", player_name="Other")
    db_session.add(other)
    await db_session.flush()
    other_id = other.id

    store = SqlAlchemyTurnGateway(db_session)
    theirs = await start_situation(
        store, session_id=other_id, mutation=_siege(title="Their war"), started_at=0
    )
    await db_session.commit()

    # From this session the parent is not merely out of scope -- it is not in the index
    # at all, so the failure is "no such situation".
    with pytest.raises(NotFoundError):
        await start_situation(
            store,
            session_id=session_id,
            mutation=_siege(title="My siege", parent_situation_id=theirs),
            started_at=0,
        )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def test_a_world_template_starts_each_session_with_its_own_copies(
    db_session: AsyncSession, make_world
) -> None:
    world = make_world(
        rules_json=default_world_rules().model_dump(mode="json"),
        initial_situations=[
            _siege(title="The long war").model_dump(mode="json"),
            StartSituation(
                category=SituationCategory.ECONOMIC, title="The grain shortage"
            ).model_dump(mode="json"),
        ],
    )
    db_session.add(world)
    await db_session.flush()
    first = models.GameSession(world_id=world.id, title="A", player_name="Rin")
    second = models.GameSession(world_id=world.id, title="B", player_name="Kai")
    db_session.add_all([first, second])
    await db_session.commit()
    first_id, second_id = first.id, second.id

    store = SqlAlchemyTurnGateway(db_session)
    assert await materialize_initial_situations(store, session_id=first_id) == 2
    assert await materialize_initial_situations(store, session_id=second_id) == 2
    await db_session.commit()

    mine = await store.load_situations(first_id, limit=50)
    theirs = await store.load_situations(second_id, limit=50)
    assert {s.title for s in mine} == {s.title for s in theirs}
    # Same content, different rows. Resolving one leaves the other alone.
    assert {s.id for s in mine}.isdisjoint({s.id for s in theirs})
    assert all(s.started_at == 0 for s in mine)
    # Seeded situations have no originating event -- the documented exception.
    assert all(s.source_event_id is None for s in mine)


async def test_a_world_with_no_starting_processes_seeds_nothing(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    assert await materialize_initial_situations(store, session_id=session_id) == 0


# ---------------------------------------------------------------------------
# Mutations through state_service
# ---------------------------------------------------------------------------


async def test_deltas_are_applied_to_the_stored_value_and_clamped(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(intensity=95), started_at=0
    )
    await db_session.commit()

    await apply_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                UpdateSituation(situation_id=situation_id, intensity_delta=40, threat_delta=-100)
            ],
        ),
        cause=cause_from_resolution(),
    )

    stored = await store.get_situation(session_id, situation_id)
    assert stored is not None
    assert stored.intensity == 100
    assert stored.threat == 0


async def test_the_story_director_cannot_move_a_situation() -> None:
    """Refused at construction, so a director batch carrying one cannot exist."""
    with pytest.raises(Exception, match="may not change a situation"):
        StateMutationBatch(
            authority=FactAuthority.STORY_DIRECTOR,
            mutations=[UpdateSituation(situation_id=uuid.uuid4(), intensity_delta=50)],
        )


def test_the_story_director_cannot_start_one_either() -> None:
    with pytest.raises(Exception, match="may not change a situation"):
        StateMutationBatch(
            authority=FactAuthority.STORY_DIRECTOR,
            mutations=[StartSituation(category=SituationCategory.CONFLICT, title="A war")],
        )


async def test_resolving_records_when_it_ended_and_keeps_the_row(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(), started_at=0
    )
    session = await store.get_session(session_id)
    assert session is not None
    await store.set_elapsed_minutes(session_id, 4320)
    await db_session.commit()

    await apply_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[
                ResolveSituation(
                    situation_id=situation_id,
                    resolution_status=SituationStatus.RESOLVED,
                    reason="Relief arrived from the north.",
                )
            ],
        ),
        cause=cause_from_resolution(),
    )

    stored = await store.get_situation(session_id, situation_id)
    assert stored is not None
    assert stored.status is SituationStatus.RESOLVED
    assert stored.resolved_at == 4320
    # Still queryable -- history is the reason terminal rows stay.
    assert stored.id in {s.id for s in await store.load_situations(session_id, limit=50)}
    # But not among the live ones.
    live = await store.load_situations(session_id, statuses=LIVE_STATUSES, limit=50)
    assert stored.id not in {s.id for s in live}


async def test_a_concluded_situation_cannot_be_updated(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(), started_at=0
    )
    await apply_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[ResolveSituation(situation_id=situation_id, reason="It ended.")],
        ),
        cause=cause_from_resolution(),
    )

    with pytest.raises(ValidationError, match="cannot be updated"):
        await apply_state_change(
            store,
            session_id=session_id,
            batch=StateMutationBatch(
                authority=FactAuthority.ENGINE,
                mutations=[UpdateSituation(situation_id=situation_id, intensity_delta=10)],
            ),
            cause=cause_from_resolution(),
        )


async def test_an_invalid_transition_is_refused_before_anything_is_written(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(), started_at=0
    )
    await db_session.commit()
    before = await store.get_session(session_id)
    assert before is not None

    with pytest.raises(ValidationError):
        await apply_state_change(
            store,
            session_id=session_id,
            batch=StateMutationBatch(
                authority=FactAuthority.ENGINE,
                mutations=[
                    UpdateSituation(
                        situation_id=situation_id, resulting_status=SituationStatus.PLANNED
                    )
                ],
            ),
            cause=cause_from_resolution(),
        )

    after = await store.get_session(session_id)
    assert after is not None and after.state_revision == before.state_revision


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


async def test_one_batch_changes_a_situation_a_place_and_starts_a_child(
    db_session: AsyncSession, make_world
) -> None:
    """The whole point of putting situation mutations in the shared batch."""
    world_id, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    gate = await create_location(
        store,
        session_id=session_id,
        location=NewLocation(
            world_id=world_id,
            name="The eastern gate",
            category=LocationCategory.STRUCTURE,
            scale=LocationScale.SITE,
        ),
        narrated=False,
    )
    siege = await start_situation(store, session_id=session_id, mutation=_siege(), started_at=0)
    await db_session.commit()
    before = await store.get_session(session_id)
    assert before is not None

    cause = await cause_from_event(store, session_id, subtype="east_gate_fell")
    result = await apply_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(
            authority=FactAuthority.SIMULATION,
            mutations=[
                UpdateSituation(situation_id=siege, intensity_delta=12),
                UpdateLocationState(
                    location_id=gate,
                    condition=LocationCondition.DESTROYED,
                    accessibility=LocationAccessibility.BLOCKED,
                ),
                StartSituation(
                    category=SituationCategory.ECONOMIC,
                    subtype="famine",
                    title="Food crisis in Asterfall",
                    parent_situation_id=siege,
                ),
            ],
        ),
        cause=cause,
    )

    # One revision bump for three things happening, all attributed to the one event
    # that caused them.
    assert result.revision == before.state_revision + 1
    assert result.event_id == cause.event_id
    assert len(result.applied) == 3

    started = [entry for entry in result.applied if entry.op == "start_situation"]
    assert len(started) == 1 and started[0].entity_id is not None

    child = await store.get_situation(session_id, started[0].entity_id)
    assert child is not None and child.parent_situation_id == siege
    state = await store.get_location_state(session_id, gate)
    assert state is not None and state.condition is LocationCondition.DESTROYED


async def test_a_batch_that_fails_partway_leaves_nothing_behind(
    db_session: AsyncSession, make_world
) -> None:
    """One bad mutation in five refuses the whole batch. Validation runs to completion
    before the first write, so this is a refusal rather than a rollback -- and the
    observable outcome is the same either way, which is what matters."""
    world_id, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    gate = await create_location(
        store,
        session_id=session_id,
        location=NewLocation(
            world_id=world_id,
            name="The eastern gate",
            category=LocationCategory.STRUCTURE,
            scale=LocationScale.SITE,
        ),
        narrated=False,
    )
    siege = await start_situation(
        store, session_id=session_id, mutation=_siege(intensity=50), started_at=0
    )
    await db_session.commit()
    before = await store.get_session(session_id)
    assert before is not None

    with pytest.raises(NotFoundError):
        await apply_state_change(
            store,
            session_id=session_id,
            batch=StateMutationBatch(
                authority=FactAuthority.SIMULATION,
                mutations=[
                    UpdateSituation(situation_id=siege, intensity_delta=40),
                    UpdateLocationState(location_id=gate, condition=LocationCondition.DESTROYED),
                    # The one that cannot work: nothing by this id exists.
                    UpdateSituation(situation_id=uuid.uuid4(), intensity_delta=10),
                ],
            ),
            cause=cause_from_resolution(),
        )
    await db_session.rollback()

    unchanged = await store.get_situation(session_id, siege)
    assert unchanged is not None and unchanged.intensity == 50
    state = await store.get_location_state(session_id, gate)
    assert state is not None and state.condition is LocationCondition.INTACT
    after = await store.get_session(session_id)
    assert after is not None and after.state_revision == before.state_revision
    events = (await db_session.execute(models.GameEvent.__table__.select())).all()
    assert not [row for row in events if row.type == "EAST_GATE_BREACHED"]


async def test_a_stale_revision_refuses_the_batch(db_session: AsyncSession, make_world) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(), started_at=0
    )
    await db_session.commit()

    with pytest.raises(StaleStateError):
        await apply_state_change(
            store,
            session_id=session_id,
            batch=StateMutationBatch(
                authority=FactAuthority.ENGINE,
                expected_revision=99,
                mutations=[UpdateSituation(situation_id=situation_id, intensity_delta=1)],
            ),
            cause=cause_from_resolution(),
        )


# ---------------------------------------------------------------------------
# Progression
# ---------------------------------------------------------------------------


async def test_progression_moves_intensity_by_momentum_and_decays_momentum(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    siege = await start_situation(
        store,
        session_id=session_id,
        mutation=_siege(intensity=40, momentum=60),
        started_at=0,
    )
    await store.set_elapsed_minutes(session_id, 360)
    await db_session.commit()

    result = await _progress(store, session_id, siege)

    assert result.disposition is ResolutionDisposition.APPLIED
    assert result.outcome is not None
    assert result.outcome.narrative_context["intensity_delta"] > 0
    # Towards zero, never past it.
    assert -60 < result.outcome.narrative_context["momentum_delta"] < 0

    stored = await store.get_situation(session_id, siege)
    assert stored is not None
    assert stored.intensity > 40
    assert 0 < stored.momentum < 60
    assert stored.last_progressed_at == 360


async def test_a_negative_momentum_process_shrinks(db_session: AsyncSession, make_world) -> None:
    """The world is allowed to solve its own problems."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    fire = await start_situation(
        store,
        session_id=session_id,
        mutation=StartSituation(
            category=SituationCategory.HAZARD,
            subtype="fire",
            title="Fire at the Broken Crown",
            intensity=60,
            threat=70,
            momentum=-60,
        ),
        started_at=0,
    )
    await store.set_elapsed_minutes(session_id, 360)
    await db_session.commit()

    result = await _progress(store, session_id, fire)
    assert result.outcome is not None
    assert result.outcome.narrative_context["intensity_delta"] < 0
    stored = await store.get_situation(session_id, fire)
    assert stored is not None and stored.intensity < 60


async def test_progression_is_deterministic(db_session: AsyncSession, make_world) -> None:
    """No game RNG exists, so the same inputs must give the same answer every time."""
    resolver = GenericProgressionResolver()
    situation = make_situation(intensity=40, momentum=45, status=SituationStatus.ACTIVE)
    context = ProgressionContext(
        situation=situation,
        request=SituationProgressionRequest(
            situation_id=situation.id,
            from_time=0,
            to_time=600,
            trigger=ProgressionTrigger.SIMULATION,
        ),
        rules=default_world_rules(),
    )
    answers = {resolver.resolve(context).deltas.intensity_delta for _ in range(20)}
    assert len(answers) == 1


async def test_a_world_that_does_not_move_without_the_player_does_not_move(
    db_session: AsyncSession, make_world
) -> None:
    rules = default_world_rules().model_dump(mode="json")
    rules["simulation"]["world_continues_without_player"] = False
    _, session_id = await _world_and_session(db_session, make_world, rules_json=rules)

    store = SqlAlchemyTurnGateway(db_session)
    siege = await start_situation(
        store, session_id=session_id, mutation=_siege(momentum=80), started_at=0
    )
    await store.set_elapsed_minutes(session_id, 4320)
    await db_session.commit()
    before = await store.get_session(session_id)
    assert before is not None

    result = await _progress(store, session_id, siege, trigger=ProgressionTrigger.SIMULATION)

    # `no_effect`, not `rejected`. The pass ran and the world's rules had nothing to
    # refuse -- a world that waits for the player simply produced no change, which is a
    # different thing from an action the rules forbade.
    assert result.disposition is ResolutionDisposition.NO_EFFECT
    assert result.resolution.reason_code == "no_change"
    assert result.events == []
    after = await store.get_session(session_id)
    assert after is not None and after.state_revision == before.state_revision


async def test_a_higher_escalation_rate_moves_a_process_faster() -> None:
    """WorldRules is consumed by value, never by preset name."""
    resolver = GenericProgressionResolver()
    situation = make_situation(intensity=40, momentum=60, status=SituationStatus.ACTIVE)
    request = SituationProgressionRequest(
        situation_id=situation.id, from_time=0, to_time=600, trigger=ProgressionTrigger.SIMULATION
    )

    def drift(escalation: int) -> int:
        document = default_world_rules().model_dump(mode="json")
        document["danger"]["escalation_rate"] = escalation
        rules = default_world_rules().model_validate(document)
        return resolver.resolve(
            ProgressionContext(situation=situation, request=request, rules=rules)
        ).deltas.intensity_delta

    assert drift(90) > drift(40) > drift(5)


async def test_a_dormant_process_does_not_drift(db_session: AsyncSession, make_world) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    conspiracy = await start_situation(
        store,
        session_id=session_id,
        mutation=StartSituation(
            category=SituationCategory.POLITICAL,
            title="The quiet plot",
            status=SituationStatus.DORMANT,
            momentum=50,
        ),
        started_at=0,
    )
    await store.set_elapsed_minutes(session_id, 10_000)
    await db_session.commit()

    result = await _progress(store, session_id, conspiracy, trigger=ProgressionTrigger.SIMULATION)
    assert result.disposition is ResolutionDisposition.NO_EFFECT
    assert result.changed_state is False
    stored = await store.get_situation(session_id, conspiracy)
    assert stored is not None and stored.momentum == 50


async def test_a_concluded_situation_cannot_progress(db_session: AsyncSession, make_world) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    siege = await start_situation(store, session_id=session_id, mutation=_siege(), started_at=0)
    await apply_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[ResolveSituation(situation_id=siege, reason="It ended.")],
        ),
        cause=cause_from_resolution(),
    )

    with pytest.raises(ValidationError, match="cannot progress"):
        await progress_situation(
            store,
            session_id=session_id,
            request=SituationProgressionRequest(
                situation_id=siege, from_time=0, to_time=60, trigger=ProgressionTrigger.EXPLICIT
            ),
        )


async def test_progressing_from_before_a_situation_existed_is_refused(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    siege = await start_situation(store, session_id=session_id, mutation=_siege(), started_at=500)
    await db_session.commit()

    with pytest.raises(ValidationError, match="did not exist until"):
        await progress_situation(
            store,
            session_id=session_id,
            request=SituationProgressionRequest(
                situation_id=siege, from_time=0, to_time=600, trigger=ProgressionTrigger.EXPLICIT
            ),
        )


async def test_a_progression_schedules_its_next_evaluation_in_absolute_time(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    fire = await start_situation(
        store,
        session_id=session_id,
        mutation=StartSituation(
            category=SituationCategory.HAZARD,
            subtype="fire",
            title="Fire at the Crown",
            intensity=30,
            momentum=60,
        ),
        started_at=0,
    )
    await store.set_elapsed_minutes(session_id, 60)
    await db_session.commit()

    result = await _progress(store, session_id, fire)
    assert len(result.scheduled_event_ids) == 1
    stored = await store.get_scheduled_event(result.scheduled_event_ids[0])
    assert stored is not None
    assert stored.type == SITUATION_PROGRESS_DUE_EVENT
    # Absolute, not a delay, and a hazard is looked at again soon.
    assert stored.due_at == 75
    assert stored.payload["situation_id"] == str(fire)
    assert stored.interrupt_player_action is False


async def test_a_resolver_event_becomes_the_events_history_keeps(
    db_session: AsyncSession, make_world
) -> None:
    """`east_gate_breached` is a better history entry than "the siege progressed".

    And the generic one is not written alongside it -- not because it was suppressed
    here, but because a resolver that named what happened is not also asked for a
    fallback. The fallback's own policy is `NONE`, so even when it is the only thing
    on offer history keeps nothing.
    """
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    siege = await start_situation(store, session_id=session_id, mutation=_siege(), started_at=0)
    await store.set_elapsed_minutes(session_id, 60)
    await db_session.commit()

    class BreachResolver:
        def resolve(self, context: ProgressionContext) -> SituationProgressionResult:
            return SituationProgressionResult(
                situation_id=context.situation.id,
                deltas=SituationDeltas(intensity_delta=12),
                generated_events=(
                    GeneratedEvent(type="east_gate_breached", description="The gate fell."),
                ),
            )

    from app.application import situation_service

    situation_service._RESOLVERS[(SituationCategory.CONFLICT, "siege")] = BreachResolver()
    try:
        result = await _progress(store, session_id, siege)
    finally:
        del situation_service._RESOLVERS[(SituationCategory.CONFLICT, "siege")]

    assert result.disposition is ResolutionDisposition.APPLIED
    subtypes = {event.subtype for event in result.events}
    assert "east_gate_breached" in subtypes
    assert SITUATION_PROGRESSED_EVENT not in subtypes
    # And the event points back at the verdict that produced it.
    assert all(event.resolution_id == result.resolution.id for event in result.events)


async def test_a_progression_with_nothing_to_say_writes_no_history(
    db_session: AsyncSession, make_world
) -> None:
    """The generic subtype is registered `NONE`, so the world moved and history did not.

    This is the case the event policy exists for. The intensity really did change and
    the revision really did move -- the `ResolutionRecord` says so -- but "the siege
    progressed" is the engine narrating its own bookkeeping, and a year of it would
    bury everything worth reading.
    """
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    siege = await start_situation(
        store, session_id=session_id, mutation=_siege(momentum=60), started_at=0
    )
    await store.set_elapsed_minutes(session_id, 360)
    await db_session.commit()

    result = await _progress(store, session_id, siege)

    assert result.disposition is ResolutionDisposition.APPLIED
    assert result.events == []
    assert result.resolution.event_count == 0
    assert result.resolution.mutation_count >= 1
    assert result.changed_state is True


# ---------------------------------------------------------------------------
# StoryContext relevance
# ---------------------------------------------------------------------------


def test_a_situation_here_outranks_a_distant_one() -> None:
    place = uuid.uuid4()
    here = make_situation(title="Fire in this room", primary_location_id=place, importance=1)
    far = make_situation(title="A distant war", scope=SituationScope.GLOBAL, importance=5)

    chosen = select_relevant([far, here], here=frozenset({place}), involving=frozenset(), limit=5)
    assert [s.title for s in chosen] == ["Fire in this room", "A distant war"]


def test_an_unrelated_minor_local_situation_is_left_out_entirely() -> None:
    """Not ranked low -- omitted. Giving it a rank would let it displace something that
    matters on a quiet turn."""
    elsewhere = make_situation(
        title="A scuffle three regions away", scope=SituationScope.LOCAL, importance=1
    )
    assert select_relevant([elsewhere], here=frozenset(), involving=frozenset(), limit=5) == []


def test_a_minor_regional_situation_is_left_out_and_a_major_one_is_not() -> None:
    minor = make_situation(title="A small strike", scope=SituationScope.REGIONAL, importance=2)
    major = make_situation(
        title="The provincial famine", scope=SituationScope.REGIONAL, importance=4
    )
    chosen = select_relevant([minor, major], here=frozenset(), involving=frozenset(), limit=5)
    assert [s.title for s in chosen] == ["The provincial famine"]


def test_a_situation_involving_someone_present_is_included() -> None:
    manhunt = make_situation(
        title="The manhunt", scope=SituationScope.ENTITY_SPECIFIC, importance=2
    )
    chosen = select_relevant(
        [manhunt], here=frozenset(), involving=frozenset({manhunt.id}), limit=5
    )
    assert [s.title for s in chosen] == ["The manhunt"]


def test_an_entity_specific_situation_nobody_here_is_in_does_not_reach_the_scene() -> None:
    manhunt = make_situation(
        title="A manhunt for a stranger", scope=SituationScope.ENTITY_SPECIFIC, importance=5
    )
    assert select_relevant([manhunt], here=frozenset(), involving=frozenset(), limit=5) == []


def test_concluded_situations_are_omitted_by_default() -> None:
    over = make_situation(
        title="The siege that ended",
        scope=SituationScope.GLOBAL,
        importance=5,
        status=SituationStatus.RESOLVED,
        resolved_at=100,
    )
    assert select_relevant([over], here=frozenset(), involving=frozenset(), limit=5) == []
    assert (
        len(
            select_relevant(
                [over], here=frozenset(), involving=frozenset(), limit=5, include_resolved=True
            )
        )
        == 1
    )


def test_a_secret_situation_never_reaches_player_facing_context() -> None:
    plot = make_situation(
        title="The assassination plot",
        scope=SituationScope.GLOBAL,
        importance=5,
        tags=(SECRET_TAG,),
    )
    assert select_relevant([plot], here=frozenset(), involving=frozenset(), limit=5) == []


def test_selection_is_stable_between_two_reads() -> None:
    place = uuid.uuid4()
    tied = [
        make_situation(title=title, primary_location_id=place, importance=3, threat=50)
        for title in ("Beta", "Alpha", "Gamma")
    ]
    first = select_relevant(tied, here=frozenset({place}), involving=frozenset(), limit=5)
    second = select_relevant(
        list(reversed(tied)), here=frozenset({place}), involving=frozenset(), limit=5
    )
    assert [s.title for s in first] == [s.title for s in second] == ["Alpha", "Beta", "Gamma"]


def test_the_context_is_capped() -> None:
    place = uuid.uuid4()
    many = [
        make_situation(title=f"Thing {n}", primary_location_id=place, importance=3)
        for n in range(20)
    ]
    chosen = select_relevant(
        many, here=frozenset({place}), involving=frozenset(), limit=MAX_SITUATIONS_IN_CONTEXT
    )
    assert len(chosen) == MAX_SITUATIONS_IN_CONTEXT


async def test_a_session_with_nothing_under_way_gets_no_block(
    db_session: AsyncSession, make_world
) -> None:
    """None rather than an empty section: an empty heading tells a model the game
    tracks processes and has none."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    assert await build_situations_context(store, session_id=session_id, elapsed_minutes=0) is None


async def test_the_context_carries_a_readable_duration(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    await start_situation(
        store,
        session_id=session_id,
        mutation=_siege(scope=SituationScope.REGIONAL, importance=4),
        started_at=0,
    )
    await db_session.commit()

    context = await build_situations_context(store, session_id=session_id, elapsed_minutes=4320)
    assert context is not None
    assert context.ongoing[0].duration == "3 days"
    assert context.ongoing[0].kind == "siege"
