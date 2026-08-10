"""The structured contract every story provider must satisfy.

`TurnGeneration` is the single source of truth for what a model may return. It is
validated on the way in from any provider, and its JSON Schema is what Ollama is
constrained to. The model proposes; the application layer decides what to persist.
"""

from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from app.domain.enums import MemoryKind
from app.domain.relationships import DELTA_MAX, DELTA_MIN
from app.domain.world_facts import FactSubjectType
from app.domain.world_locations import LocationCategory, LocationScale
from app.domain.world_situations import SituationCategory, SituationScope

logger = logging.getLogger(__name__)

MAX_SUGGESTED_ACTIONS = 4
MAX_FACT_PROPOSALS = 5
MAX_LOCATION_PROPOSALS = 3
"""Lower than the fact cap. A turn that invents three new places has stopped
narrating a scene and started drawing a map."""

MAX_SITUATION_PROPOSALS = 2
"""Lower still. A turn that starts three ongoing processes is not narrating a scene,
it is writing a season finale, and the cost of a wrong one is far higher than a
spurious bookshop: a situation persists, reaches future prompts and asks to be
simulated."""


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


class FactProposal(BaseModel):
    """Something the model believes the story just established as objectively true.

    A *proposal*. Nothing here is written; every one is reviewed against the property's
    policy, the model's authority, what is already established and the world's rules,
    and most of what a model proposes is refused. See `app.application.fact_proposals`.

    The model never returns a replacement WorldState and has no way to express one:
    this is a list of individual claims about single properties, which is the shape
    that can be adjudicated one at a time.
    """

    model_config = ConfigDict(extra="ignore")

    subject_type: FactSubjectType
    subject_id: uuid.UUID | None = Field(
        default=None, description="Required for anything but the world itself."
    )
    property: str = Field(
        min_length=1,
        max_length=120,
        description="Canonical snake_case name, e.g. 'narrative.birthplace'.",
    )

    value: bool | int | float | str | list[str] | None
    """Scalars and short string lists only -- narrower than what a fact can hold.

    Structured objects are excluded deliberately: a model proposing one is proposing an
    aggregate, and aggregates are what the fact store exists to keep out. It also keeps
    the JSON schema flat, which matters for grammar-constrained decoding.
    """

    importance: int = Field(default=2, ge=1, le=5)
    reason: str = Field(default="", max_length=300, description="What in this turn established it.")


class LocationProposal(BaseModel):
    """Somewhere the story just established exists.

    A *proposal*, like `FactProposal`. The application decides whether it may exist,
    where it sits, and -- crucially -- what its id is. There is no `id` field here and
    there never will be: a model that could name a uuid could overwrite a place, and
    the uuid it would name is one it read in a prompt.

    `parent_location_id` is the exception, and it is an id the context *gave* the
    model. Anything it invents there fails to resolve and the proposal is refused.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)

    category: LocationCategory
    subtype: str | None = Field(default=None, max_length=60)
    scale: LocationScale
    """How big. This is what the creation policy reads: narration may establish a shop
    or a room, not a country. See `world_locations.policy`."""

    parent_location_id: uuid.UUID | None = Field(
        default=None, description="An existing place this sits inside, from the context."
    )
    reason: str = Field(default="", max_length=300)


class SituationProposal(BaseModel):
    """A process the story just set in motion.

    A *proposal*, and a more tightly constrained one than `FactProposal` or
    `LocationProposal`. Notice what is missing: `intensity`, `threat`, `momentum`,
    `importance`, `status`, and any way to name an existing situation. All of them are
    absent on purpose.

    A location the story mentions is a noun. A situation is a process with three bounded
    numbers, a lifecycle and a claim on future simulation -- and a model that could set
    those could declare a war at intensity 100 by writing an atmospheric sentence, or
    end a siege because the scene felt like it should be over. So the model says *what
    kind of thing began*, and the application decides every number, exactly as it
    decides a proposed location's importance.

    Existing situations are read-only to the director in the strongest available sense:
    there is no field here that could address one, so "the siege is now resolved" is a
    sentence it can write in narration and nothing more. Moving a situation is
    `UpdateSituation`, which requires mechanical authority the model does not have.
    """

    model_config = ConfigDict(extra="ignore")

    category: SituationCategory
    subtype: str | None = Field(default=None, max_length=60)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)

    scope: SituationScope = SituationScope.LOCAL
    """How far it reaches. The one shaping value the model does supply, because it is a
    description of what happened rather than a measure of it -- a tavern brawl is local
    and a succession crisis is not, and neither claim moves a number."""

    primary_location_id: uuid.UUID | None = Field(
        default=None, description="Where it is centred, from the context."
    )
    """An id the context *gave* the model, like `LocationProposal.parent_location_id`.
    Anything it invents here fails to resolve and the proposal is refused."""

    reason: str = Field(default="", max_length=300)


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

    # Optional, unlike `suggested_actions`, and for the opposite reason. Suggestions
    # are wanted every turn, so the schema demands them. The right number of new facts
    # for most turns is zero, and a required field is one a constrained model will fill
    # -- which would turn "record what the story established" into "invent something".
    fact_proposals: list[FactProposal] = Field(default_factory=list)

    # Optional for the same reason, and rarer still. Most turns happen somewhere that
    # already exists; a model required to name a new place every turn would produce a
    # world of bookshops nobody entered.
    location_proposals: list[LocationProposal] = Field(default_factory=list)

    # Rarest of the three. A turn that starts a war is a turn where a war started; most
    # turns start nothing, and a required field here would produce a world permanently
    # at war with itself.
    situation_proposals: list[SituationProposal] = Field(default_factory=list)

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

    @model_validator(mode="before")
    @classmethod
    def _drop_unusable_fact_proposals(cls, data: object) -> object:
        """A malformed proposal is discarded, never fatal.

        The same judgement as the validator above, applied to a field where the stakes
        are higher. One proposal with a bad subject type would otherwise fail the whole
        `TurnGeneration`, roll back a turn of real prose, and show the player a 502 --
        because the model got a detail wrong in an optional extra it was not obliged to
        send at all.

        Malformed proposals are logged rather than silently dropped: a model that keeps
        emitting them is a prompt problem, and the log is where that shows up.
        """
        if not isinstance(data, dict) or "fact_proposals" not in data:
            return data

        raw = data["fact_proposals"]
        if not isinstance(raw, list):
            logger.warning("Story provider sent fact_proposals as %s; ignored.", type(raw).__name__)
            return {**data, "fact_proposals": []}

        kept: list[FactProposal] = []
        for item in raw:
            try:
                kept.append(FactProposal.model_validate(item))
            except PydanticValidationError as exc:
                logger.warning("Story provider sent an unusable fact proposal; dropped. %s", exc)
        return {**data, "fact_proposals": kept}

    @field_validator("suggested_actions")
    @classmethod
    def _trim_suggestions(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        return cleaned[:MAX_SUGGESTED_ACTIONS]

    @field_validator("fact_proposals")
    @classmethod
    def _cap_proposals(cls, value: list[FactProposal]) -> list[FactProposal]:
        """A turn establishes a few things at most. The cap is on the contract rather
        than the reviewer so a runaway model costs one truncation, not fifty reads."""
        return value[:MAX_FACT_PROPOSALS]

    @model_validator(mode="before")
    @classmethod
    def _drop_unusable_location_proposals(cls, data: object) -> object:
        """Same judgement as `_drop_unusable_fact_proposals`, same reasoning.

        A model that sends `scale: "huge"` has got one enum wrong in an optional field.
        Failing the turn over it would roll back the narration and show the player a
        502 for a bookshop nobody asked for.
        """
        if not isinstance(data, dict) or "location_proposals" not in data:
            return data

        raw = data["location_proposals"]
        if not isinstance(raw, list):
            logger.warning(
                "Story provider sent location_proposals as %s; ignored.", type(raw).__name__
            )
            return {**data, "location_proposals": []}

        kept: list[LocationProposal] = []
        for item in raw:
            try:
                kept.append(LocationProposal.model_validate(item))
            except PydanticValidationError as exc:
                logger.warning(
                    "Story provider sent an unusable location proposal; dropped. %s", exc
                )
        return {**data, "location_proposals": kept}

    @field_validator("location_proposals")
    @classmethod
    def _cap_locations(cls, value: list[LocationProposal]) -> list[LocationProposal]:
        return value[:MAX_LOCATION_PROPOSALS]

    @model_validator(mode="before")
    @classmethod
    def _drop_unusable_situation_proposals(cls, data: object) -> object:
        """Same judgement as the two validators above, same reasoning.

        A model that sends `category: "riot"` has got one enum wrong in an optional
        field. Failing the turn over it would roll back real narration and show the
        player a 502 for a process nobody asked to start.
        """
        if not isinstance(data, dict) or "situation_proposals" not in data:
            return data

        raw = data["situation_proposals"]
        if not isinstance(raw, list):
            logger.warning(
                "Story provider sent situation_proposals as %s; ignored.", type(raw).__name__
            )
            return {**data, "situation_proposals": []}

        kept: list[SituationProposal] = []
        for item in raw:
            try:
                kept.append(SituationProposal.model_validate(item))
            except PydanticValidationError as exc:
                logger.warning(
                    "Story provider sent an unusable situation proposal; dropped. %s", exc
                )
        return {**data, "situation_proposals": kept}

    @field_validator("situation_proposals")
    @classmethod
    def _cap_situations(cls, value: list[SituationProposal]) -> list[SituationProposal]:
        return value[:MAX_SITUATION_PROPOSALS]
