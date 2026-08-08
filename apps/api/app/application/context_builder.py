"""Builds the StoryContext handed to a story provider.

All retrieval policy lives here so it is deterministic and testable in one place.
Retrieval is currently pure SQL ordering -- no embeddings. Semantic retrieval
replaces `_load_memories` in Phase 3; see docs/ai-contract.md.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.story_context import (
    CharacterContext,
    MemoryContext,
    MessageContext,
    PlayerContext,
    RelationshipContext,
    SessionContext,
    StoryContext,
    WorldContext,
)
from app.domain.enums import MessageRole
from app.infrastructure.db import models

RECENT_MESSAGE_LIMIT = 20
MEMORY_LIMIT = 30
CHARACTER_LIMIT = 12


async def build_story_context(
    db: AsyncSession,
    *,
    session: models.GameSession,
    world: models.World,
    player_action: str,
) -> StoryContext:
    characters = await _load_characters(db, world.id)
    return StoryContext(
        world=WorldContext(
            id=world.id,
            name=world.name,
            description=world.description,
            genre=world.genre,
            setting=world.setting,
            language=world.language,
        ),
        player=PlayerContext(name=session.player_name, description=session.player_description),
        session=SessionContext(
            id=session.id,
            title=session.title,
            current_location=session.current_location,
            summary=session.summary,
            turn_index=session.turn_index,
        ),
        relevant_characters=characters,
        recent_messages=await _load_recent_messages(db, session.id, characters),
        relevant_memories=await _load_memories(db, session.id),
        relationships=await _load_relationships(db, session.id, characters),
        player_action=player_action,
    )


async def _load_characters(db: AsyncSession, world_id: uuid.UUID) -> list[CharacterContext]:
    rows = (
        await db.execute(
            select(models.Character)
            .where(models.Character.world_id == world_id)
            .order_by(models.Character.created_at)
            .limit(CHARACTER_LIMIT)
        )
    ).scalars()
    return [
        CharacterContext(
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


async def _load_recent_messages(
    db: AsyncSession, session_id: uuid.UUID, characters: list[CharacterContext]
) -> list[MessageContext]:
    """Newest N by insertion order, returned oldest-first for natural reading."""
    rows = (
        (
            await db.execute(
                select(models.Message)
                .where(models.Message.session_id == session_id)
                .order_by(models.Message.turn_index.desc(), models.Message.created_at.desc())
                .limit(RECENT_MESSAGE_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    names = {character.id: character.name for character in characters}
    return [
        MessageContext(
            turn_index=row.turn_index,
            role=MessageRole(row.role),
            speaker=_speaker_label(row, names),
            content=row.content,
        )
        for row in reversed(rows)
    ]


def _speaker_label(message: models.Message, names: dict[uuid.UUID, str]) -> str:
    if message.speaker_character_id is not None:
        return names.get(message.speaker_character_id, "Unknown")
    return {
        MessageRole.PLAYER: "Player",
        MessageRole.NARRATOR: "Narrator",
        MessageRole.SYSTEM: "System",
    }.get(MessageRole(message.role), "Narrator")


async def _load_memories(db: AsyncSession, session_id: uuid.UUID) -> list[MemoryContext]:
    """Most important first, then most recent. Deterministic; no embeddings yet."""
    rows = (
        await db.execute(
            select(models.Memory)
            .where(models.Memory.session_id == session_id)
            .order_by(models.Memory.importance.desc(), models.Memory.created_at.desc())
            .limit(MEMORY_LIMIT)
        )
    ).scalars()
    return [
        MemoryContext(
            kind=row.kind,
            summary=row.summary,
            importance=row.importance,
            character_id=row.character_id,
        )
        for row in rows
    ]


async def _load_relationships(
    db: AsyncSession, session_id: uuid.UUID, characters: list[CharacterContext]
) -> list[RelationshipContext]:
    rows = (
        await db.execute(
            select(models.Relationship).where(models.Relationship.session_id == session_id)
        )
    ).scalars()
    names = {character.id: character.name for character in characters}
    return [
        RelationshipContext(
            character_id=row.character_id,
            character_name=names.get(row.character_id, "Unknown"),
            trust=row.trust,
            affection=row.affection,
            respect=row.respect,
            fear=row.fear,
        )
        for row in rows
    ]
