"""SQLAlchemy implementation of the application's persistence ports.

One adapter satisfies all three ports because a turn is one transaction: the
reads that build the context and the writes that record the outcome have to see
the same uncommitted state. Splitting it into three objects over one session
would be three names for the same thing.

Queries and row-to-DTO mapping live here. Retrieval *policy* -- how many
messages, how many memories, what gets into StoryContext -- stays in the
application layer; this module only honours the ordering each port documents.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.persistence import (
    CharacterRecord,
    MemoryRecord,
    NewEvent,
    NewMemory,
    NewMessage,
    RelationshipRecord,
    SessionSnapshot,
    TranscriptMessage,
    WorldSnapshot,
)
from app.domain.relationships import RelationshipVector
from app.infrastructure.db import models


class SqlAlchemyTurnGateway:
    """Implements StoryContextReaderPort, TurnPersistencePort and TurnUnitOfWorkPort."""

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
                type=event.type,
                description=event.description,
            )
        )

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

    # -- TurnUnitOfWorkPort -----------------------------------------------------

    async def commit(self) -> None:
        await self._db.commit()

    # -- internals --------------------------------------------------------------

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
