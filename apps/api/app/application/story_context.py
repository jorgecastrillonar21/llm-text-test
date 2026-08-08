"""Everything a story provider is allowed to see about a turn.

Providers receive this object and nothing else -- no database session, no ORM
models. Retrieval policy therefore lives in one place (context_builder) and stays
testable and deterministic.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Language, MemoryKind, MessageRole


class WorldContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    description: str
    genre: str
    setting: str
    language: Language = Language.EN


class PlayerContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str


class SessionContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    title: str
    current_location: str
    summary: str
    turn_index: int


class CharacterContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    description: str
    appearance: str
    personality: str
    backstory: str
    speech_style: str
    goals: list[str] = Field(default_factory=list)
    # Secrets are given to the director so NPCs can act on them without the
    # narration revealing them. See docs/ai-contract.md.
    secrets: list[str] = Field(default_factory=list)


class MessageContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn_index: int
    role: MessageRole
    speaker: str
    content: str


class MemoryContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: MemoryKind
    summary: str
    importance: int
    character_id: uuid.UUID | None = None


class RelationshipContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    character_id: uuid.UUID
    character_name: str
    trust: int
    affection: int
    respect: int
    fear: int


class StoryContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    world: WorldContext
    player: PlayerContext
    session: SessionContext
    relevant_characters: list[CharacterContext] = Field(default_factory=list)
    recent_messages: list[MessageContext] = Field(default_factory=list)
    relevant_memories: list[MemoryContext] = Field(default_factory=list)
    relationships: list[RelationshipContext] = Field(default_factory=list)
    player_action: str
