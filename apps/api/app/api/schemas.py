"""Request/response DTOs. ORM models never cross this boundary."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application.ports import ProviderState
from app.application.state_service import StateChangeEvent
from app.application.turn_service import AppliedRelationship, TurnMessage
from app.domain.enums import Language, MemoryKind, MessageRole
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.world_facts import (
    FactAuthority,
    FactKind,
    FactSubjectType,
    FactValue,
    SetFact,
    StateMutationBatch,
    WorldFact,
)
from app.domain.world_rules import (
    WorldRules,
    WorldRulesPreset,
    WorldRulesV1,
    build_preset,
    default_world_rules,
)
from app.domain.world_time import (
    DEFAULT_INITIAL_DATETIME,
    STANDARD_CALENDAR,
    FictionalDateTime,
    TimeOfDay,
    project_time,
)


class WorldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    genre: str = Field(default="", max_length=120)
    setting: str = Field(default="", max_length=4000)
    # Fixed for the lifetime of the world; there is deliberately no update path.
    language: Language = Language.EN

    rules_preset: WorldRulesPreset | None = None
    """Pick a named starting point. Resolved at creation; the name is not stored."""

    rules: WorldRulesV1 | None = None
    """Supply the whole document instead. Mutually exclusive with `rules_preset`."""

    initial_datetime: FictionalDateTime | None = None
    """The fictional date and time that a session's minute zero corresponds to.

    Omitted means the first morning of year one. Fixed at creation, like the language
    and the rules: moving a world's origin would silently reinterpret every fictional
    timestamp already recorded against it.
    """

    initial_facts: list[SetFact] = Field(default_factory=list)
    """What is already true in this world before anyone plays it.

    A *template*: every session copies these into its own rows and diverges from there.
    Validated here as the mutations they are, so a malformed property name is a 422 at
    world creation rather than a surprise when the first session starts.
    """

    @model_validator(mode="after")
    def _reject_impossible_start_date(self) -> Self:
        """A start date the calendar does not have is a 422, not a clamped value."""
        if self.initial_datetime is not None:
            try:
                STANDARD_CALENDAR.check(self.initial_datetime)
            except DomainValidationError as exc:
                raise ValueError(str(exc)) from exc
        return self

    @model_validator(mode="after")
    def _reject_ambiguous_rules(self) -> Self:
        """Both fields at once is a 422, not a precedence rule.

        Silently preferring one would make the ignored half look like it took
        effect, and a caller who sent both does not agree with themselves about
        what the world should be. Better to ask.
        """
        if self.rules_preset is not None and self.rules is not None:
            raise ValueError(
                "Provide either 'rules_preset' or 'rules', not both. "
                "To start from a preset and adjust it, read the preset's rules "
                "and send the modified document as 'rules'."
            )
        return self

    def resolved_rules(self) -> WorldRules:
        """The rules this world will actually be created with."""
        if self.rules is not None:
            return self.rules
        if self.rules_preset is not None:
            return build_preset(self.rules_preset)
        return default_world_rules()

    def resolved_initial_datetime(self) -> FictionalDateTime:
        """The fictional instant this world's sessions will start at."""
        return self.initial_datetime or DEFAULT_INITIAL_DATETIME


class WorldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    genre: str
    setting: str
    language: Language
    initial_datetime: FictionalDateTime
    """Small enough to carry in a list, unlike the rules document, and the only way
    to see what a world was actually created with."""
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
    elapsed_minutes: int
    """The raw simulation clock. `SessionDetail` also carries the readable form; this
    stays because a caller doing arithmetic should use the number, not parse a
    sentence."""
    state_revision: int
    """How many batches of state changes this session has applied. Exposed so a client
    can tell "the world changed" from "another turn happened" without diffing facts."""
    created_at: datetime
    updated_at: datetime


class TimeDisplayRead(BaseModel):
    """The clock as text. Derived per request, stored nowhere."""

    date: str
    time: str
    period: TimeOfDay
    elapsed: str


class SessionTimeRead(BaseModel):
    elapsed_minutes: int
    display: TimeDisplayRead

    @classmethod
    def project(cls, elapsed_minutes: int, initial: FictionalDateTime) -> SessionTimeRead:
        now = project_time(elapsed_minutes, initial=initial)
        return cls(
            elapsed_minutes=now.elapsed_minutes,
            display=TimeDisplayRead(
                date=now.calendar_date,
                time=now.clock,
                period=now.period,
                elapsed=now.elapsed_since_start,
            ),
        )


class SessionDetail(SessionRead):
    world: WorldRead
    time: SessionTimeRead
    """Served with the session rather than from its own endpoint: the screen that
    shows the clock already loads this, and a second round trip for four derived
    strings would be one more thing to keep in sync."""


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
    facts_established: int
    """Fact proposals that survived review and are now established truth."""
    facts_rejected: int
    """Proposals the review refused. Normal, and usually the larger number."""
    visual_cue_generated: bool


class WorldFactRead(BaseModel):
    """One established truth, as an API client sees it.

    Flattened: the domain nests `subject`, but a client filtering a table wants two
    columns rather than a nested object it has to reach into.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: FactKind
    subject_type: FactSubjectType
    subject_id: uuid.UUID | None
    property: str
    value: FactValue
    importance: int
    current_value_since: int
    """The fictional minute this value became true -- not when the row was written."""
    authority: FactAuthority
    source_event_id: uuid.UUID | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, fact: WorldFact) -> WorldFactRead:
        return cls(
            id=fact.id,
            kind=fact.kind,
            subject_type=fact.subject.type,
            subject_id=fact.subject.id,
            property=fact.property,
            value=fact.value,
            importance=fact.importance,
            current_value_since=fact.current_value_since,
            authority=fact.authority,
            source_event_id=fact.source_event_id,
            tags=list(fact.tags),
            created_at=fact.created_at,
            updated_at=fact.updated_at,
        )


class WorldStateRead(BaseModel):
    """The session's current truth, with the revision it was read at.

    The revision travels with the facts on purpose: it is what a caller sends back as
    `expected_revision`, and a number read from a different request than the facts it
    describes would defeat the point of having it.
    """

    session_id: uuid.UUID
    state_revision: int
    facts: list[WorldFactRead]
    truncated: bool
    """True when the session has more facts than `limit` returned. A client that sees
    this and treats the page as the whole world state is wrong, and should know."""


class StateChangeRequest(BaseModel):
    """Body for the debug-only mutation endpoint.

    The batch is the domain model itself rather than a parallel API shape, so the
    endpoint cannot accept something the state service would not: the batch size cap,
    the discriminated union of operations and the "no two mutations touch one fact"
    rule are all already on it.
    """

    batch: StateMutationBatch
    event: StateChangeEvent | None = None


class ScheduledEventCreate(BaseModel):
    """Body for the development-only scheduling endpoint.

    Takes a delay rather than an absolute time on purpose: "in three days" is what a
    caller means, and converting it once at the boundary is what keeps `due_at`
    unambiguous in storage.
    """

    type: str = Field(min_length=1, max_length=80)
    delay_minutes: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    interrupt_player_action: bool = False


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
