"""State changes end to end: the service, the adapter and a real database.

`test_world_facts.py` proves the domain refuses what it should. This proves the rest
of the promise -- that one logical fact has exactly one current value, that a batch is
all or nothing, that the revision only moves when something committed, and that two
sessions of the same world do not share a truth between them.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.persistence import NewFact
from app.application.state_service import (
    INITIAL_FACTS_EVENT,
    StateChangeEvent,
    apply_state_change,
    materialize_initial_facts,
    stage_state_change,
)
from app.domain.errors import (
    FactPolicyError,
    IncompatibleFactError,
    NotFoundError,
    StaleStateError,
    ValidationError,
)
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import (
    WORLD_SUBJECT,
    FactAuthority,
    FactKind,
    FactSubject,
    FactSubjectType,
    RemoveFact,
    SetFact,
)
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway


async def _world_session(
    db: AsyncSession, make_world, make_character, **world_overrides: object
) -> tuple[models.World, models.Character, models.GameSession]:
    world = make_world(**world_overrides)
    db.add(world)
    await db.flush()
    character = make_character(world.id)
    db.add(character)
    session = models.GameSession(world_id=world.id, title="Run", player_name="Rin")
    db.add(session)
    await db.flush()
    return world, character, session


def _debug_event() -> StateChangeEvent:
    return StateChangeEvent(type="test.change", description="A test made this happen.")


async def _fact_rows(db: AsyncSession, session_id: uuid.UUID) -> list[models.WorldFact]:
    return list(
        (
            await db.execute(
                select(models.WorldFact).where(models.WorldFact.session_id == session_id)
            )
        ).scalars()
    )


# -- uniqueness ---------------------------------------------------------------


async def test_one_subject_and_property_can_only_have_one_current_value(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """The invariant the whole design rests on: Aldren cannot be alive and dead."""
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)

    await store.set_fact(
        NewFact(
            session_id=session.id,
            kind=FactKind.WORLD_TRUTH,
            subject=subject,
            property="system.alive",
            value=True,
            importance=5,
            current_value_since=0,
            authority=FactAuthority.SEED,
        )
    )
    # Straight at the table, bypassing the adapter's upsert: what stops a second
    # current value has to be the database, not a method that remembers to look.
    db_session.add(
        models.WorldFact(
            session_id=session.id,
            kind=FactKind.WORLD_TRUTH,
            subject_type=FactSubjectType.CHARACTER,
            subject_id=character.id,
            property="system.alive",
            value=False,
            importance=5,
            current_value_since=0,
            authority=FactAuthority.ENGINE,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_the_world_subject_is_unique_too_despite_its_null_id(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """SQL treats two NULLs as distinct, so a single unique index over `subject_id`
    would not constrain world-scoped facts at all. Two partial indexes do."""
    _, _, session = await _world_session(db_session, make_world, make_character)
    store = SqlAlchemyTurnGateway(db_session)

    await store.set_fact(
        NewFact(
            session_id=session.id,
            kind=FactKind.WORLD_TRUTH,
            subject=WORLD_SUBJECT,
            property="world.political_status",
            value="stable",
            importance=4,
            current_value_since=0,
            authority=FactAuthority.SEED,
        )
    )
    db_session.add(
        models.WorldFact(
            session_id=session.id,
            kind=FactKind.WORLD_TRUTH,
            subject_type=FactSubjectType.WORLD,
            subject_id=None,
            property="world.political_status",
            value="collapsed",
            importance=4,
            current_value_since=0,
            authority=FactAuthority.ENGINE,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# -- null vs absence ----------------------------------------------------------


async def test_an_explicit_null_is_not_the_same_as_an_absent_fact(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)

    assert await store.get_fact(session.id, subject, "narrative.birthplace") is None

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ADMIN,
            mutations=[SetFact(subject=subject, property="narrative.birthplace", value=None)],
        ),
        event=_debug_event(),
    )

    stored = await store.get_fact(session.id, subject, "narrative.birthplace")
    assert stored is not None, "an established null must be a fact, not an absence"
    assert stored.value is None
    # And it is not false either. Three states, none collapsed into another.
    assert stored.value is not False


# -- SetFact ------------------------------------------------------------------


async def test_setting_a_fact_creates_it_with_its_authority_and_provenance(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)

    result = await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[SetFact(subject=subject, property="system.alive", value=True, importance=5)],
        ),
        event=StateChangeEvent(type="combat.survived", description="Elena walked away."),
    )

    stored = await store.get_fact(session.id, subject, "system.alive")
    assert stored is not None
    assert stored.value is True
    assert stored.authority is FactAuthority.ENGINE
    assert stored.source_event_id == result.event_id
    assert stored.source_event_id is not None


async def test_setting_an_existing_fact_replaces_it_in_place_and_restamps_when(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """A new value is the same fact with a different value, so the row survives and
    `current_value_since` moves to when the new value became true."""
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)
    batch = StateMutationBatch(
        authority=FactAuthority.ENGINE,
        mutations=[SetFact(subject=subject, property="system.location", value="the tavern")],
    )
    await apply_state_change(store, session_id=session.id, batch=batch, event=_debug_event())
    first = await store.get_fact(session.id, subject, "system.location")
    assert first is not None
    assert first.current_value_since == 0

    # The story moves on before the second change, so the two timestamps differ.
    row = await db_session.get(models.GameSession, session.id)
    assert row is not None
    row.elapsed_minutes = 480
    await db_session.flush()

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[SetFact(subject=subject, property="system.location", value="the docks")],
        ),
        event=_debug_event(),
    )

    second = await store.get_fact(session.id, subject, "system.location")
    assert second is not None
    assert second.id == first.id, "the fact's identity is subject+property, not its value"
    assert second.value == "the docks"
    assert second.current_value_since == 480
    assert second.created_at == first.created_at
    assert len(await _fact_rows(db_session, session.id)) == 1


# -- RemoveFact ---------------------------------------------------------------


async def test_removing_a_fact_withdraws_it_rather_than_making_it_false(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)
    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ADMIN,
            mutations=[SetFact(subject=subject, property="narrative.birthplace", value="Arven")],
        ),
        event=_debug_event(),
    )

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ADMIN,
            mutations=[RemoveFact(subject=subject, property="narrative.birthplace")],
        ),
        event=_debug_event(),
    )

    # Absent, not false, and not null: the row is gone entirely.
    assert await store.get_fact(session.id, subject, "narrative.birthplace") is None
    assert await _fact_rows(db_session, session.id) == []


async def test_removing_a_fact_that_was_never_established_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """A caller that believes a property is there and is wrong should find out."""
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(ValidationError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ADMIN,
                mutations=[RemoveFact(subject=subject, property="narrative.birthplace")],
            ),
            event=_debug_event(),
        )


# -- revision -----------------------------------------------------------------


async def test_a_new_session_starts_at_revision_zero(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, _, session = await _world_session(db_session, make_world, make_character)
    assert session.state_revision == 0


async def test_the_revision_moves_once_per_batch_not_once_per_mutation(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)

    result = await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ADMIN,
            mutations=[
                SetFact(subject=subject, property="narrative.birthplace", value="Arven"),
                SetFact(subject=subject, property="narrative.childhood_nickname", value="Ren"),
                SetFact(subject=WORLD_SUBJECT, property="world.condition", value="storm season"),
            ],
        ),
        event=_debug_event(),
    )

    assert result.revision == 1
    assert len(result.applied) == 3


async def test_the_revision_does_not_move_when_the_transaction_rolls_back(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """`stage_state_change` deliberately does not commit; a caller that fails after it
    takes the whole change with it, revision included."""
    _, character, session = await _world_session(db_session, make_world, make_character)
    # Ids read before the commit: committing expires the instances, and reloading one
    # outside the greenlet SQLAlchemy runs async IO in is an error, not a query.
    session_id, subject = session.id, FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    # Committed first so the rollback below has a baseline to return to rather than
    # taking the world and the session with it.
    await db_session.commit()
    store = SqlAlchemyTurnGateway(db_session)

    staged = await stage_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(
            authority=FactAuthority.ADMIN,
            mutations=[SetFact(subject=subject, property="narrative.birthplace", value="Arven")],
        ),
        event=_debug_event(),
    )
    assert staged.revision == 1

    await db_session.rollback()

    reread = await store.get_session(session_id)
    assert reread is not None
    assert reread.state_revision == 0
    assert await _fact_rows(db_session, session_id) == []


async def test_a_batch_decided_against_an_older_revision_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)
    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ADMIN,
            mutations=[SetFact(subject=subject, property="narrative.birthplace", value="Arven")],
        ),
        event=_debug_event(),
    )

    with pytest.raises(StaleStateError) as caught:
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ADMIN,
                mutations=[
                    SetFact(subject=subject, property="narrative.childhood_nickname", value="Ren")
                ],
                expected_revision=0,
            ),
            event=_debug_event(),
        )
    assert caught.value.expected == 0
    assert caught.value.actual == 1


# -- atomicity ----------------------------------------------------------------


async def test_one_bad_mutation_takes_the_whole_batch_with_it(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """Event not written, no fact half-changed, revision where it was.

    The refusal happens during validation, before the first write -- which is why the
    "rollback" here is that there was nothing to roll back.
    """
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(FactPolicyError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.STORY_DIRECTOR,
                mutations=[
                    # Allowed on its own.
                    SetFact(subject=subject, property="narrative.birthplace", value="Arven"),
                    # Not allowed for anyone: derived state is computed, never stored.
                    SetFact(subject=subject, property="derived.is_injured", value=True),
                ],
            ),
            event=_debug_event(),
        )

    assert await _fact_rows(db_session, session.id) == []
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


async def test_a_fact_about_a_character_who_does_not_exist_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, _, session = await _world_session(db_session, make_world, make_character)
    store = SqlAlchemyTurnGateway(db_session)
    stranger = FactSubject(type=FactSubjectType.CHARACTER, id=uuid.uuid4())

    with pytest.raises(NotFoundError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ADMIN,
                mutations=[SetFact(subject=stranger, property="narrative.birthplace", value="X")],
            ),
            event=_debug_event(),
        )
    assert await _fact_rows(db_session, session.id) == []


async def test_a_mechanical_change_with_no_event_is_refused(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """A world that cannot say why something is true has provenance in name only."""
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(ValidationError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ENGINE,
                mutations=[SetFact(subject=subject, property="system.alive", value=False)],
            ),
        )


# -- session isolation --------------------------------------------------------


async def test_changing_one_session_leaves_the_other_alone(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world, character, first = await _world_session(db_session, make_world, make_character)
    second = models.GameSession(world_id=world.id, title="Another run", player_name="Kai")
    db_session.add(second)
    await db_session.flush()

    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)
    await apply_state_change(
        store,
        session_id=first.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[SetFact(subject=subject, property="system.alive", value=False)],
        ),
        event=StateChangeEvent(type="death", description="Elena died in this run only."),
    )

    assert await store.get_fact(second.id, subject, "system.alive") is None
    assert await store.load_facts(second.id, limit=50) == []
    other = await store.get_session(second.id)
    assert other is not None
    assert other.state_revision == 0


# -- initial facts ------------------------------------------------------------


async def test_a_worlds_starting_facts_are_copied_into_a_new_session(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    world.initial_facts = [
        SetFact(
            subject=WORLD_SUBJECT,
            property="world.political_status",
            value="contested",
            importance=4,
        ).model_dump(mode="json"),
        SetFact(subject=subject, property="narrative.birthplace", value="Arven").model_dump(
            mode="json"
        ),
    ]
    await db_session.flush()
    store = SqlAlchemyTurnGateway(db_session)

    result = await materialize_initial_facts(store, session_id=session.id)

    assert result is not None
    assert result.revision == 1
    facts = await store.load_facts(session.id, limit=50)
    assert {fact.property for fact in facts} == {"world.political_status", "narrative.birthplace"}
    assert all(fact.authority is FactAuthority.SEED for fact in facts)

    # One event for the whole seed, not one per fact.
    events = list(
        (
            await db_session.execute(
                select(models.GameEvent).where(models.GameEvent.session_id == session.id)
            )
        ).scalars()
    )
    assert [event.type for event in events] == [INITIAL_FACTS_EVENT]


async def test_materialising_a_world_with_no_starting_facts_does_nothing(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, _, session = await _world_session(db_session, make_world, make_character)
    store = SqlAlchemyTurnGateway(db_session)

    assert await materialize_initial_facts(store, session_id=session.id) is None
    reread = await store.get_session(session.id)
    assert reread is not None
    assert reread.state_revision == 0, "an empty seed is not a state change"


async def test_playing_a_session_never_writes_back_to_the_world_template(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """The template is a starting configuration. Killing the king in one save must
    leave every other save, and every future one, untouched."""
    world, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    world.initial_facts = [
        SetFact(subject=subject, property="system.alive", value=True, importance=5).model_dump(
            mode="json"
        )
    ]
    await db_session.flush()
    store = SqlAlchemyTurnGateway(db_session)
    await materialize_initial_facts(store, session_id=session.id)

    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[SetFact(subject=subject, property="system.alive", value=False)],
        ),
        event=StateChangeEvent(type="death", description="Elena died."),
    )

    template = await store.load_initial_facts(world.id)
    assert [fact.value for fact in template] == [True]


# -- authority, through the service -------------------------------------------


async def test_the_story_director_cannot_reach_mechanical_state_through_the_service(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(FactPolicyError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.STORY_DIRECTOR,
                mutations=[SetFact(subject=subject, property="system.alive", value=False)],
            ),
        )
    assert await _fact_rows(db_session, session.id) == []


async def test_the_story_director_cannot_reach_guarded_world_state_either(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, _, session = await _world_session(db_session, make_world, make_character)
    store = SqlAlchemyTurnGateway(db_session)

    with pytest.raises(FactPolicyError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.STORY_DIRECTOR,
                mutations=[
                    SetFact(
                        subject=WORLD_SUBJECT,
                        property="world.political_status",
                        value="fallen",
                    )
                ],
            ),
        )


async def test_the_worlds_rules_refuse_a_change_no_authority_can_make(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """Admin is not exempt. The universe does not work that way for anyone."""
    _, character, session = await _world_session(db_session, make_world, make_character)
    subject = FactSubject(type=FactSubjectType.CHARACTER, id=character.id)
    store = SqlAlchemyTurnGateway(db_session)
    await apply_state_change(
        store,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.ENGINE,
            mutations=[SetFact(subject=subject, property="system.alive", value=False)],
        ),
        event=StateChangeEvent(type="death", description="Elena died."),
    )

    with pytest.raises(IncompatibleFactError):
        await apply_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.ADMIN,
                mutations=[SetFact(subject=subject, property="system.alive", value=True)],
            ),
            event=StateChangeEvent(type="miracle", description="Not in this world."),
        )
