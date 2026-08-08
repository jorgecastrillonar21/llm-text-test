"""The structured contract every story provider must satisfy.

`TurnGeneration` is the single source of truth for what a model may return. It is
validated on the way in from any provider, and its JSON Schema is what Ollama is
constrained to. The model proposes; the application layer decides what to persist.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    @model_validator(mode="after")
    def _keep_the_cue_coherent(self) -> VisualCue:
        """A cue is either a request carrying a prompt, or nothing at all.

        Models routinely write a scene_prompt while leaving `generate` false, and
        occasionally ask to generate with nothing to draw. Both shapes are stored
        as-is otherwise, leaving `visual_cue_generated` disagreeing with the payload
        and Phase 4 inheriting rows it cannot act on.
        """
        if self.generate and not (self.scene_prompt or "").strip():
            self.generate = False
        if not self.generate:
            self.scene_prompt = None
            self.character_ids = []
        return self


class TurnGeneration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    narration: str = Field(min_length=1)
    dialogue: list[DialogueLine] = Field(default_factory=list)

    # No default, which is what puts this in the schema's `required` list: a field
    # with a default is optional there, and Ollama's grammar-constrained decoding
    # lets the model skip anything optional. Measured against mistral:7b it omitted
    # the key outright in every world, and the UI lost its suggestion chips with no
    # error anywhere. Requiring it in the schema is what actually fixes that.
    #
    # The `before` validator below deliberately keeps *validation* forgiving; see it
    # for why the two must not be the same decision.
    suggested_actions: list[str]
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    relationship_changes: list[RelationshipChange] = Field(default_factory=list)
    world_events: list[WorldEvent] = Field(default_factory=list)
    visual_cue: VisualCue = Field(default_factory=VisualCue)

    @model_validator(mode="before")
    @classmethod
    def _tolerate_a_missing_suggestions_key(cls, data: object) -> object:
        """Required of the model, forgiving of the response.

        What we want is for a grammar-constrained model to be unable to *choose* to
        omit suggestions, and the schema's `required` list does that. Rejecting a
        response that lacks them is a different decision, and the wrong one: this
        contract also accepts providers that are not schema-constrained, and
        suggestions are an affordance rather than the interface -- the player can
        always type freely. Failing an otherwise good turn over missing chips would
        roll back real prose to protect a convenience.
        """
        if isinstance(data, dict) and "suggested_actions" not in data:
            return {**data, "suggested_actions": []}
        return data

    @field_validator("suggested_actions")
    @classmethod
    def _trim_suggestions(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        return cleaned[:MAX_SUGGESTED_ACTIONS]
