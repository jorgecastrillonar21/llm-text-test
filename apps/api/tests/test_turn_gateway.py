"""The SQLAlchemy adapter, against a real database.

`test_turn_ports.py` proves the use case needs no database. This proves the
adapter honours the contracts those ports declare -- ordering, limits, and the
staged-but-uncommitted visibility the atomic turn depends on.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.persistence import NewEvent, NewMemory, NewMessage
from app.domain.enums import MemoryKind, MessageRole
from app.domain.relationships import RelationshipVector
from app.domain.resolution import EventCategory
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway


async def _world_with_session(
    db: AsyncSession, make_world, make_character
) -> tuple[models.World, models.Character, models.GameSession]:
    world = make_world()
    db.add(world)
    await db.flush()
    character = make_character(world.id)
    db.add(character)
    session = models.GameSession(world_id=world.id, title="Run", player_name="Rin")
    db.add(session)
    await db.flush()
    return world, character, session


async def test_a_staged_message_is_readable_before_commit(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """The whole atomic turn rests on this: staged, visible, still rollback-able."""
    _, _, session = await _world_with_session(db_session, make_world, make_character)
    gateway = SqlAlchemyTurnGateway(db_session)

    await gateway.add_message(
        NewMessage(session_id=session.id, turn_index=1, role=MessageRole.PLAYER, content="I knock.")
    )

    staged = await gateway.load_recent_messages(session.id, limit=20)
    assert [m.content for m in staged] == ["I knock."]

    # Never committed, so rolling back must take it away again.
    await db_session.rollback()
    assert await gateway.load_recent_messages(session.id, limit=20) == []


async def test_recent_messages_come_back_oldest_first_and_capped(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, _, session = await _world_with_session(db_session, make_world, make_character)
    gateway = SqlAlchemyTurnGateway(db_session)
    for turn in range(1, 6):
        await gateway.add_message(
            NewMessage(
                session_id=session.id,
                turn_index=turn,
                role=MessageRole.PLAYER,
                content=f"turn {turn}",
            )
        )

    recent = await gateway.load_recent_messages(session.id, limit=3)

    # Newest three selected, then presented in reading order.
    assert [m.content for m in recent] == ["turn 3", "turn 4", "turn 5"]


async def test_memories_come_back_most_important_first(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, character, session = await _world_with_session(db_session, make_world, make_character)
    gateway = SqlAlchemyTurnGateway(db_session)
    for importance in (2, 5, 1, 4):
        await gateway.add_memory(
            NewMemory(
                session_id=session.id,
                character_id=character.id,
                kind=MemoryKind.FACT,
                summary=f"importance {importance}",
                importance=importance,
            )
        )
    await db_session.flush()

    memories = await gateway.load_memories(session.id, limit=10)

    assert [m.importance for m in memories] == [5, 4, 2, 1]


async def test_saving_a_relationship_inserts_then_updates(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, character, session = await _world_with_session(db_session, make_world, make_character)
    gateway = SqlAlchemyTurnGateway(db_session)

    assert await gateway.get_relationship(session.id, character.id) is None

    await gateway.save_relationship(
        session.id, character.id, RelationshipVector(trust=3, affection=1)
    )
    first = await gateway.get_relationship(session.id, character.id)
    assert first is not None and first.trust == 3

    await gateway.save_relationship(
        session.id, character.id, RelationshipVector(trust=8, affection=1)
    )
    second = await gateway.get_relationship(session.id, character.id)

    assert second is not None and second.trust == 8
    # Updated in place rather than duplicated -- there is a uniqueness constraint.
    assert len(await gateway.load_relationships(session.id)) == 1


async def test_snapshots_are_none_when_the_row_is_missing(db_session: AsyncSession) -> None:
    gateway = SqlAlchemyTurnGateway(db_session)

    assert await gateway.get_session(uuid.uuid4()) is None
    assert await gateway.get_world(uuid.uuid4()) is None


async def test_known_character_ids_are_scoped_to_the_world(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world_a, character_a, _ = await _world_with_session(db_session, make_world, make_character)
    world_b = make_world()
    db_session.add(world_b)
    await db_session.flush()
    db_session.add(make_character(world_b.id, name="Stranger"))
    await db_session.flush()
    gateway = SqlAlchemyTurnGateway(db_session)

    assert await gateway.known_character_ids(world_a.id) == {character_a.id}


async def test_turn_index_and_events_are_persisted(
    db_session: AsyncSession, make_world, make_character
) -> None:
    _, _, session = await _world_with_session(db_session, make_world, make_character)
    gateway = SqlAlchemyTurnGateway(db_session)

    event_id = await gateway.add_event(
        NewEvent(
            session_id=session.id,
            turn_index=1,
            occurred_at=0,
            category=EventCategory.ACTION,
            subtype="arrival",
            summary="Rin arrived.",
            importance=2,
        )
    )
    await gateway.set_turn_index(session.id, 7)
    await gateway.commit()

    reloaded = await gateway.get_session(session.id)
    assert reloaded is not None and reloaded.turn_index == 7

    # The adapter assigns the sequence, because only it can see what the session
    # already has -- the caller passes a fictional minute and nothing else.
    stored = await gateway.get_event(session.id, event_id)
    assert stored is not None
    assert stored.subtype == "arrival"
    assert stored.sequence == 1
