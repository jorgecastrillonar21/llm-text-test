"""The structured contract every story provider must satisfy.

`TurnGeneration` is the single source of truth for what a model may return. It is
validated on the way in from any provider, and its JSON Schema is what Ollama is
constrained to. The model proposes; the application layer decides what to persist.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import MemoryKind
from app.domain.relationships import DELTA_MAX, DELTA_MIN

MAX_SUGGESTED_ACTIONS = 4


class DialogueLine(BaseModel):
    model_config = ConfigDict(extra="ignore")

    character_id: uuid.UUID | None = Field(
        default=None, description="Existing character this line belongs to, when known."
    )
    speaker: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1)


class MemoryCandidate(BaseModel):
    """Something worth remembering after the immediate conversation moves on."""

    model_config = ConfigDict(extra="ignore")

    character_id: uuid.UUID | None = None
    kind: MemoryKind
    summary: str = Field(min_length=1, max_length=500)
    importance: int = Field(ge=1, le=5)


class RelationshipChange(BaseModel):
    """A proposed nudge to one character's view of the player.

    Deltas are bounded at the contract level so an out-of-range model output is a
    validation error rather than a silent 200-point swing.
    """

    model_config = ConfigDict(extra="ignore")

    character_id: uuid.UUID
    trust_delta: int = Field(default=0, ge=DELTA_MIN, le=DELTA_MAX)
    affection_delta: int = Field(default=0, ge=DELTA_MIN, le=DELTA_MAX)
    respect_delta: int = Field(default=0, ge=DELTA_MIN, le=DELTA_MAX)
    fear_delta: int = Field(default=0, ge=DELTA_MIN, le=DELTA_MAX)
    reason: str = Field(default="", max_length=300)


class WorldEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)


class VisualCue(BaseModel):
    """Signals a visually significant moment, not every turn."""

    model_config = ConfigDict(extra="ignore")

    generate: bool = False
    scene_prompt: str | None = Field(default=None, max_length=800)
    character_ids: list[uuid.UUID] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=300)


class TurnGeneration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    narration: str = Field(min_length=1)
    dialogue: list[DialogueLine] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    relationship_changes: list[RelationshipChange] = Field(default_factory=list)
    world_events: list[WorldEvent] = Field(default_factory=list)
    visual_cue: VisualCue = Field(default_factory=VisualCue)

    @field_validator("suggested_actions")
    @classmethod
    def _trim_suggestions(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        return cleaned[:MAX_SUGGESTED_ACTIONS]
