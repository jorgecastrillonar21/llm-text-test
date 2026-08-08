"""Builds the StoryContext handed to a story provider.

All retrieval policy lives here so it is deterministic and testable in one place:
how much history is worth sending, how many characters, and what a stored row
turns into once the provider sees it. Reads go through StoryContextReaderPort --
this module does not know what a database is.

Retrieval is currently recency and importance ordering, no embeddings. Semantic
retrieval replaces the memory read in Phase 3; see docs/ai-contract.md.
"""

from __future__ import annotations

import uuid

from app.application.persistence import (
    CharacterRecord,
    SessionSnapshot,
    StoryContextReaderPort,
    TranscriptMessage,
    WorldSnapshot,
)
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

RECENT_MESSAGE_LIMIT = 20
MEMORY_LIMIT = 30
CHARACTER_LIMIT = 12


async def build_story_context(
    reader: StoryContextReaderPort,
    *,
    session: SessionSnapshot,
    world: WorldSnapshot,
    player_action: str,
) -> StoryContext:
    characters = await reader.load_characters(world.id, limit=CHARACTER_LIMIT)
    names = {character.id: character.name for character in characters}

    messages = await reader.load_recent_messages(session.id, limit=RECENT_MESSAGE_LIMIT)
    memories = await reader.load_memories(session.id, limit=MEMORY_LIMIT)
    relationships = await reader.load_relationships(session.id)

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
        relevant_characters=[_to_character_context(record) for record in characters],
        recent_messages=[_to_message_context(message, names) for message in messages],
        relevant_memories=[
            MemoryContext(
                kind=memory.kind,
                summary=memory.summary,
                importance=memory.importance,
                character_id=memory.character_id,
            )
            for memory in memories
        ],
        relationships=[
            RelationshipContext(
                character_id=relationship.character_id,
                character_name=names.get(relationship.character_id, "Unknown"),
                trust=relationship.trust,
                affection=relationship.affection,
                respect=relationship.respect,
                fear=relationship.fear,
            )
            for relationship in relationships
        ],
        player_action=player_action,
    )


def _to_character_context(record: CharacterRecord) -> CharacterContext:
    return CharacterContext(
        id=record.id,
        name=record.name,
        description=record.description,
        appearance=record.appearance,
        personality=record.personality,
        backstory=record.backstory,
        speech_style=record.speech_style,
        goals=list(record.goals),
        secrets=list(record.secrets),
    )


def _to_message_context(message: TranscriptMessage, names: dict[uuid.UUID, str]) -> MessageContext:
    return MessageContext(
        turn_index=message.turn_index,
        role=message.role,
        speaker=_speaker_label(message, names),
        content=message.content,
    )


def _speaker_label(message: TranscriptMessage, names: dict[uuid.UUID, str]) -> str:
    if message.speaker_character_id is not None:
        return names.get(message.speaker_character_id, "Unknown")
    return {
        MessageRole.PLAYER: "Player",
        MessageRole.NARRATOR: "Narrator",
        MessageRole.SYSTEM: "System",
    }.get(message.role, "Narrator")
