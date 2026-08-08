"""Request/response DTOs. ORM models never cross this boundary."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.application.ports import ProviderState
from app.application.turn_service import AppliedRelationship, TurnMessage
from app.domain.enums import Language, MemoryKind, MessageRole


class WorldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    genre: str = Field(default="", max_length=120)
    setting: str = Field(default="", max_length=4000)
    # Fixed for the lifetime of the world; there is deliberately no update path.
    language: Language = Language.EN


class WorldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    genre: str
    setting: str
    language: Language
    created_at: datetime
    updated_at: datetime


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    appearance: str = Field(default="", max_length=4000)
    personality: str = Field(default="", max_length=4000)
    backstory: str = Field(default="", max_length=8000)
    speech_style: str = Field(default="", max_length=2000)
    goals: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)


class CharacterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    world_id: uuid.UUID
    name: str
    description: str
    appearance: str
    personality: str
    backstory: str
    speech_style: str
    goals: list[str]
    secrets: list[str]
    created_at: datetime
    updated_at: datetime


class SessionCreate(BaseModel):
    world_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    player_name: str = Field(min_length=1, max_length=200)
    player_description: str = Field(default="", max_length=4000)
    current_location: str = Field(default="", max_length=300)


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    world_id: uuid.UUID
    title: str
    player_name: str
    player_description: str
    current_location: str
    summary: str
    turn_index: int
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionRead):
    world: WorldRead


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    turn_index: int
    role: MessageRole
    speaker_character_id: uuid.UUID | None
    content: str
    created_at: datetime


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    character_id: uuid.UUID | None
    kind: MemoryKind
    summary: str
    importance: int
    created_at: datetime


class RelationshipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    character_id: uuid.UUID
    trust: int
    affection: int
    respect: int
    fear: int
    updated_at: datetime


class TurnRequest(BaseModel):
    action: str = Field(min_length=1, max_length=2000)


class TurnResponse(BaseModel):
    session_id: uuid.UUID
    turn_index: int
    messages: list[TurnMessage]
    suggested_actions: list[str]
    relationships: list[AppliedRelationship]
    memories_created: int
    events_created: int
    visual_cue_generated: bool


class ProviderStatusRead(BaseModel):
    provider: str
    state: ProviderState
    detail: str
    model: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class AiStatusResponse(BaseModel):
    story: ProviderStatusRead
    image: ProviderStatusRead


class HealthResponse(BaseModel):
    status: str
    app_env: str
    database_ready: bool


class ErrorResponse(BaseModel):
    """Consistent error envelope for every non-2xx response."""

    error: str
    detail: str
    provider: str | None = None
    retryable: bool = False
