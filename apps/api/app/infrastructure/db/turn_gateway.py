"""SQLAlchemy implementation of the application's persistence ports.

One adapter satisfies every port a session request needs, because they all run in
one transaction: the reads that build the context, the writes that record a turn's
outcome, and the clock the time service moves have to see the same uncommitted
state. Splitting it into four objects over one database session would be four names
for the same thing.

Queries and row-to-DTO mapping live here. Retrieval *policy* -- how many
messages, how many memories, what gets into StoryContext -- stays in the
application layer; this module only honours the ordering each port documents.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.persistence import (
    CharacterRecord,
    MemoryRecord,
    NewEvent,
    NewMemory,
    NewMessage,
    NewScheduledEvent,
    RelationshipRecord,
    ScheduledEventRecord,
    SessionSnapshot,
    TranscriptMessage,
    WorldSnapshot,
)
from app.domain.relationships import RelationshipVector
from app.domain.world_rules import parse_world_rules
from app.domain.world_time import FictionalDateTime, ScheduledEventStatus
from app.infrastructure.db import models


def _to_scheduled_event(row: models.ScheduledEvent) -> ScheduledEventRecord:
    return ScheduledEventRecord(
        id=row.id,
        session_id=row.session_id,
        due_at=row.due_at,
        type=row.type,
        payload=dict(row.payload or {}),
        status=row.status,
        interrupt_player_action=row.interrupt_player_action,
    )


class SqlAlchemyTurnGateway:
    """Implements StoryContextReaderPort, TurnPersistencePort, TurnUnitOfWorkPort
    and SessionClockPort.

    Named for the turn because that is the transaction it was shaped around. The
    session clock joined later and runs inside the same one, which is why it is here
    rather than in a second adapter that would have to re-map the same rows.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # -- StoryContextReaderPort -------------------------------------------------

    async def load_characters(self, world_id: uuid.UUID, *, limit: int) -> list[CharacterRecord]:
        rows = (
            await self._db.execute(
                select(models.Character)
                .where(models.Character.world_id == world_id)
                .order_by(models.Character.created_at)
                .limit(limit)
            )
        ).scalars()
        return [
            CharacterRecord(
                id=row.id,
                name=row.name,
                description=row.description,
                appearance=row.appearance,
                personality=row.personality,
                backstory=row.backstory,
                speech_style=row.speech_style,
                goals=list(row.goals or []),
                secrets=list(row.secrets or []),
            )
            for row in rows
        ]

    async def load_recent_messages(
        self, session_id: uuid.UUID, *, limit: int
    ) -> list[TranscriptMessage]:
        rows = (
            (
                await self._db.execute(
                    select(models.Message)
                    .where(models.Message.session_id == session_id)
                    .order_by(models.Message.turn_index.desc(), models.Message.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        # Newest N selected, then reversed: the caller wants oldest-first.
        return [
            TranscriptMessage(
                turn_index=row.turn_index,
                role=row.role,
                speaker_character_id=row.speaker_character_id,
                content=row.content,
            )
            for row in reversed(rows)
        ]

    async def load_memories(self, session_id: uuid.UUID, *, limit: int) -> list[MemoryRecord]:
        rows = (
            await self._db.execute(
                select(models.Memory)
                .where(models.Memory.session_id == session_id)
                .order_by(models.Memory.importance.desc(), models.Memory.created_at.desc())
                .limit(limit)
            )
        ).scalars()
        return [
            MemoryRecord(
                kind=row.kind,
                summary=row.summary,
                importance=row.importance,
                character_id=row.character_id,
            )
            for row in rows
        ]

    async def load_relationships(self, session_id: uuid.UUID) -> list[RelationshipRecord]:
        rows = (
            await self._db.execute(
                select(models.Relationship).where(models.Relationship.session_id == session_id)
            )
        ).scalars()
        return [
            RelationshipRecord(
                character_id=row.character_id,
                trust=row.trust,
                affection=row.affection,
                respect=row.respect,
                fear=row.fear,
            )
            for row in rows
        ]

    # -- TurnPersistencePort ----------------------------------------------------

    async def get_session(self, session_id: uuid.UUID) -> SessionSnapshot | None:
        row = await self._db.get(models.GameSession, session_id)
        if row is None:
            return None
        return SessionSnapshot(
            id=row.id,
            world_id=row.world_id,
            title=row.title,
            player_name=row.player_name,
            player_description=row.player_description,
            current_location=row.current_location,
            summary=row.summary,
            turn_index=row.turn_index,
            elapsed_minutes=row.elapsed_minutes,
        )

    async def get_world(self, world_id: uuid.UUID) -> WorldSnapshot | None:
        row = await self._db.get(models.World, world_id)
        if row is None:
            return None
        return WorldSnapshot(
            id=row.id,
            name=row.name,
            description=row.description,
            genre=row.genre,
            setting=row.setting,
            language=row.language,
            # Validated on the way out, every read. A hand-edited or half-migrated
            # rules_json raises here rather than reaching the Story Director as a
            # document nobody wrote.
            rules=parse_world_rules(row.rules_json),
            initial_datetime=FictionalDateTime.model_validate(row.initial_datetime),
        )

    async def known_character_ids(self, world_id: uuid.UUID) -> set[uuid.UUID]:
        rows = await self._db.execute(
            select(models.Character.id).where(models.Character.world_id == world_id)
        )
        return set(rows.scalars())

    async def add_message(self, message: NewMessage) -> uuid.UUID:
        row = models.Message(
            session_id=message.session_id,
            turn_index=message.turn_index,
            role=message.role,
            speaker_character_id=message.speaker_character_id,
            content=message.content,
        )
        self._db.add(row)
        # Flush, never commit: the staged player action has to be readable by the
        # context queries below while a failed turn still rolls the whole thing back.
        await self._db.flush()
        return row.id

    async def add_memory(self, memory: NewMemory) -> None:
        self._db.add(
            models.Memory(
                session_id=memory.session_id,
                character_id=memory.character_id,
                kind=memory.kind,
                summary=memory.summary,
                importance=memory.importance,
            )
        )

    async def add_event(self, event: NewEvent) -> None:
        self._db.add(
            models.GameEvent(
                session_id=event.session_id,
                turn_index=event.turn_index,
                occurred_at=event.occurred_at,
                event_sequence=await self._next_event_sequence(event.session_id),
                type=event.type,
                description=event.description,
            )
        )
        # Flushed immediately so the next event in this turn sees this one when it
        # asks for the next number. Without it a turn's events would all be handed
        # the same sequence and the unique constraint would fail the whole turn.
        await self._db.flush()

    async def get_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID
    ) -> RelationshipRecord | None:
        row = await self._find_relationship(session_id, character_id)
        if row is None:
            return None
        return RelationshipRecord(
            character_id=row.character_id,
            trust=row.trust,
            affection=row.affection,
            respect=row.respect,
            fear=row.fear,
        )

    async def save_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID, vector: RelationshipVector
    ) -> None:
        row = await self._find_relationship(session_id, character_id)
        if row is None:
            row = models.Relationship(session_id=session_id, character_id=character_id)
            self._db.add(row)
        row.trust = vector.trust
        row.affection = vector.affection
        row.respect = vector.respect
        row.fear = vector.fear
        await self._db.flush()

    async def set_turn_index(self, session_id: uuid.UUID, turn_index: int) -> None:
        row = await self._db.get(models.GameSession, session_id)
        if row is None:  # pragma: no cover - the caller loaded it moments earlier
            raise LookupError(f"GameSession {session_id} vanished mid-turn")
        row.turn_index = turn_index
        await self._db.flush()

    # -- SessionClockPort -------------------------------------------------------

    async def set_elapsed_minutes(self, session_id: uuid.UUID, elapsed_minutes: int) -> None:
        row = await self._db.get(models.GameSession, session_id)
        if row is None:  # pragma: no cover - the caller loaded it moments earlier
            raise LookupError(f"GameSession {session_id} vanished mid-advance")
        row.elapsed_minutes = elapsed_minutes
        await self._db.flush()

    async def add_scheduled_event(self, event: NewScheduledEvent) -> uuid.UUID:
        row = models.ScheduledEvent(
            session_id=event.session_id,
            due_at=event.due_at,
            type=event.type,
            payload=dict(event.payload),
            status=ScheduledEventStatus.PENDING,
            interrupt_player_action=event.interrupt_player_action,
        )
        self._db.add(row)
        await self._db.flush()
        return row.id

    async def get_scheduled_event(self, event_id: uuid.UUID) -> ScheduledEventRecord | None:
        row = await self._db.get(models.ScheduledEvent, event_id)
        return None if row is None else _to_scheduled_event(row)

    async def load_due_scheduled_events(
        self, session_id: uuid.UUID, *, through: int
    ) -> list[ScheduledEventRecord]:
        rows = (
            await self._db.execute(
                select(models.ScheduledEvent)
                .where(
                    models.ScheduledEvent.session_id == session_id,
                    models.ScheduledEvent.status == ScheduledEventStatus.PENDING,
                    models.ScheduledEvent.due_at <= through,
                )
                # Chronological, then by insertion, so two events due in the same
                # fictional minute always resolve in the order they were scheduled.
                .order_by(models.ScheduledEvent.due_at, models.ScheduledEvent.created_at)
            )
        ).scalars()
        return [_to_scheduled_event(row) for row in rows]

    async def set_scheduled_event_status(
        self, event_id: uuid.UUID, status: ScheduledEventStatus
    ) -> None:
        row = await self._db.get(models.ScheduledEvent, event_id)
        if row is None:  # pragma: no cover - the caller loaded it moments earlier
            raise LookupError(f"ScheduledEvent {event_id} vanished mid-advance")
        row.status = status
        await self._db.flush()

    # -- TurnUnitOfWorkPort -----------------------------------------------------

    async def commit(self) -> None:
        await self._db.commit()

    # -- internals --------------------------------------------------------------

    async def _next_event_sequence(self, session_id: uuid.UUID) -> int:
        """One past the highest sequence this session has used.

        Derived rather than kept in a counter column, so there is no second number
        that can disagree with the rows themselves. The unique constraint on
        (session_id, event_sequence) turns any race into a failed transaction rather
        than two events quietly claiming the same position.
        """
        highest = (
            await self._db.execute(
                select(func.max(models.GameEvent.event_sequence)).where(
                    models.GameEvent.session_id == session_id
                )
            )
        ).scalar()
        return (highest or 0) + 1

    async def _find_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID
    ) -> models.Relationship | None:
        return (
            await self._db.execute(
                select(models.Relationship).where(
                    models.Relationship.session_id == session_id,
                    models.Relationship.character_id == character_id,
                )
            )
        ).scalar_one_or_none()
