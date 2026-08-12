"""Request/response DTOs. ORM models never cross this boundary."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application.llm_metrics import (
    LlmGenerationMetrics,
    LlmPerformanceSummary,
    TurnPerformanceMetrics,
)
from app.application.ports import ProviderState
from app.application.turn_service import (
    MAX_ACTION_LENGTH,
    MAX_CLIENT_ACTION_ID_LENGTH,
    AppliedRelationship,
    TurnMessage,
)
from app.domain.enums import Language, MemoryKind, MessageRole
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.resolution import (
    EventCategory,
    GameEvent,
    Resolution,
    ResolutionDisposition,
)
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import (
    FactAuthority,
    FactKind,
    FactSubjectType,
    FactValue,
    SetFact,
    WorldFact,
)
from app.domain.world_locations import (
    MAX_NAME_LENGTH,
    MAX_SUBTYPE_LENGTH,
    ConnectionCategory,
    LocationAccessibility,
    LocationCategory,
    LocationCondition,
    LocationConnection,
    LocationDefinition,
    LocationScale,
    LocationZone,
    PhysicalDistance,
)
from app.domain.world_rules import (
    WorldRules,
    WorldRulesPreset,
    WorldRulesV1,
    build_preset,
    default_world_rules,
)
from app.domain.world_situations import (
    ProgressionTrigger,
    Situation,
    SituationParticipant,
    StartSituation,
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

    initial_situations: list[StartSituation] = Field(default_factory=list)
    """What this world already has under way before anyone plays it.

    A template in the same sense, and validated the same way: `StartSituation` rather
    than a bespoke seed shape, so a siege that could not be started during play cannot
    be authored into a world either. Every session materialises its own copies with
    their own ids and diverges immediately.

    A situation whose `primary_location_id` names a place is only checked when a session
    materialises it -- the location has to exist by then, and at world creation the
    geography may not have been authored yet.
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
    """DEPRECATED. Where the player says they are starting, in their own words.

    Read exactly once, at session creation, to seed a canonical `CharacterPosition`
    when the string names one visible place unambiguously; after that it is neither read
    nor authoritative. It survives as an input because it is how a player says where to
    begin in a world whose geography they have not seen -- not because a name is a
    position. See `app.application.position_service.materialize_initial_position`."""


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
    action: str = Field(min_length=1, max_length=MAX_ACTION_LENGTH)

    client_action_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_CLIENT_ACTION_ID_LENGTH
    )
    """The client's stable name for *this submission*, not for this HTTP request.

    Send the same id when retrying a request that may already have been received --
    a timeout, a dropped connection, a second tap on the button -- and the server
    returns the turn that was played rather than playing another one. Send a new id
    only when the player has genuinely acted again.

    Optional so existing clients keep working, but a client that omits it has no
    protection: two identical submissions are two turns, and the server has no way to
    tell that apart from a player who meant it.
    """


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
    locations_created: int
    """New places this turn established as canon for this session."""
    locations_rejected: int
    situations_started: int
    """Ongoing processes this turn set in motion. Zero on almost every turn."""
    situations_rejected: int
    visual_cue_generated: bool

    resolution_id: uuid.UUID
    """The mechanical record of what this turn established.

    Always present, and the handle the narration endpoint takes. A turn sent without a
    `client_action_id` is recorded like any other; what it lacks is the key that would
    let a retry find it again."""

    replayed: bool = False
    """True when this body is a turn that had already been played, returned because the
    same `client_action_id` arrived twice. The messages are the original ones; the
    suggestions and most counts come back empty, because they were never stored."""


class NarrationRequest(BaseModel):
    """Body for asking for prose about a resolution that already happened."""

    regenerate: bool = False
    """Ask for a *new* paragraph, replacing the stored one.

    Off by default, which makes the endpoint a safe retry: without it, a resolution
    that already has narration returns what is stored and no model runs. Turning it on
    never re-runs the resolution -- the outcome is settled and this is a description
    of it.
    """


class NarrationResponse(BaseModel):
    resolution_id: uuid.UUID
    message_id: uuid.UUID
    narration: str
    generated: bool
    """False when stored prose came back untouched; true when the provider ran."""
    provider: str


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


class LocationCreate(BaseModel):
    """Author a place into a world's reusable template.

    No `origin_session_id`: this endpoint writes template geography by definition, and
    a field that could make it session-local would be a second way to do what the turn
    pipeline already does properly.

    No `id` either. The application mints it, here as everywhere.
    """

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    description: str = Field(default="", max_length=4000)
    category: LocationCategory
    subtype: str | None = Field(default=None, max_length=MAX_SUBTYPE_LENGTH)
    scale: LocationScale
    parent_location_id: uuid.UUID | None = None
    importance: int = Field(default=3, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    spatial_metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectionCreate(BaseModel):
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    bidirectional: bool = True
    category: ConnectionCategory
    subtype: str | None = Field(default=None, max_length=MAX_SUBTYPE_LENGTH)
    physical_distance: PhysicalDistance | None = None
    base_travel_minutes: int | None = Field(default=None, ge=0)
    """Kept separate from the distance, and neither derived from the other. A portal is
    four thousand kilometres and one minute."""
    importance: int = Field(default=3, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    category: str | None = Field(default=None, max_length=MAX_SUBTYPE_LENGTH)
    description: str = Field(default="", max_length=4000)
    importance: int = Field(default=2, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)


class LocationStateRead(BaseModel):
    """One session's current view of a place, flattened for a client."""

    condition: LocationCondition
    accessibility: LocationAccessibility
    security_level: int
    local_danger_modifier: int
    owner_entity_id: uuid.UUID | None
    controller_entity_id: uuid.UUID | None
    updated_at: datetime


class LocationRead(BaseModel):
    """A place, with this session's state when there is a session in the request.

    Definition and state are nested rather than merged, because they have different
    lifetimes: the definition is shared by every save of the world and the state is
    not. Flattening them into one object is how a client ends up believing a world can
    be "damaged".
    """

    definition: LocationDefinition
    state: LocationStateRead | None = None


class ConnectionStateRead(BaseModel):
    condition: LocationCondition
    accessibility: LocationAccessibility
    traversal_modifier: int
    updated_at: datetime


class ConnectionRead(BaseModel):
    connection: LocationConnection
    state: ConnectionStateRead | None = None


class LocationDetailRead(LocationRead):
    """One place and its immediate neighbourhood, for a diagnostics view.

    Children and exits, but not descendants and not routes. Anything that walks further
    than one step is a question for the spatial context endpoint, which has the tier
    budget written into it.
    """

    zones: list[LocationZone] = Field(default_factory=list)
    children: list[LocationRead] = Field(default_factory=list)
    connections: list[ConnectionRead] = Field(default_factory=list)


class SituationRead(BaseModel):
    """One ongoing process, whole.

    The domain model straight through, unlike `LocationRead`, because a situation has
    no definition/state split to preserve: it is one row with one lifetime, and the
    numbers *are* the thing rather than a per-session overlay on it.
    """

    situation: Situation
    participants: list[SituationParticipant] = Field(default_factory=list)


class SituationListRead(BaseModel):
    situations: list[SituationRead] = Field(default_factory=list)


class SituationProgressRequest(BaseModel):
    """Body for the development-only progression endpoint.

    Deliberately carries no interval. A progression runs from where the situation was
    last evaluated to *where the session clock actually is* -- both read by the handler,
    neither the caller's to choose.

    Letting a caller pass "six hours" would let it evaluate fictional time the session
    has not lived through, which is how `last_progressed_at` ends up ahead of
    `elapsed_minutes` and a siege reports three days of progress on a session that has
    been running for twenty minutes. To exercise a long interval, advance the clock
    first: `POST /dev/sessions/{id}/advance-time`, then progress. The two tools compose,
    and fictional time stays the only authority on how much time has passed.
    """

    trigger: ProgressionTrigger = ProgressionTrigger.EXPLICIT

    completes_scheduled_event_id: uuid.UUID | None = None
    """The due `situation.progress` event this call is executing, when it is answering one.

    Optional because a person may progress a situation for its own sake. Supplied when
    acting as the dispatcher the schedule is waiting for: the event is marked PROCESSED
    inside the same transaction as the progression, so the acknowledgement and the world
    change it acknowledges cannot come apart. An event that is not due is a 422.
    """


class ResolutionResponse(BaseModel):
    """What one resolution did. The shape every command endpoint returns.

    One response type rather than one per command, because the interesting part is
    never command-specific: which disposition, whether the revision moved, what
    history it wrote. A caller that needs the mechanical detail of a particular
    resolver reads `narrative_context`, which is the resolver's own summary of its
    outcome and is never parsed for mechanics.
    """

    resolution: Resolution
    events: list[GameEvent] = Field(default_factory=list)
    """What history kept. Frequently empty, and an empty list is not a failure -- most
    outcomes change the world without being worth remembering."""

    replayed: bool = False
    """True when this idempotency key had already been resolved. The body is the
    original record, re-read; nothing ran a second time."""

    created_situation_ids: list[uuid.UUID] = Field(default_factory=list)
    scheduled_event_ids: list[uuid.UUID] = Field(default_factory=list)

    completed_scheduled_event_id: uuid.UUID | None = None
    """The due scheduled event this resolution executed and marked done, when it was
    answering one. Null for a resolution nobody scheduled."""

    narrative_context: dict[str, Any] = Field(default_factory=dict)
    """Structured, flat, and for the Story Director to narrate. Absent on a replay,
    because the outcome object is not reconstructed from the record."""

    @property
    def disposition(self) -> ResolutionDisposition:
        return self.resolution.disposition


class ResolutionListRead(BaseModel):
    resolutions: list[Resolution] = Field(default_factory=list)


class EventListRead(BaseModel):
    events: list[GameEvent] = Field(default_factory=list)


class EventQuery(BaseModel):
    """Query parameters for the read-only history endpoint."""

    category: EventCategory | None = None
    min_importance: int = Field(default=1, ge=1, le=5)
    limit: int = Field(default=50, ge=1, le=200)


class StateChangeRequest(BaseModel):
    """Body for the debug-only mutation endpoint.

    The batch is the domain model itself rather than a parallel API shape, so the
    endpoint cannot accept something the state service would not: the batch size cap,
    the discriminated union of operations and the "no two mutations touch one fact"
    rule are all already on it.

    No cause field. A change made by hand was caused by a person at a terminal, and
    inventing a resolution id for it would put a lie in the audit trail. `admin` is
    the one authority that does not have to name what caused it, for exactly this
    reason -- see `app.application.state_service.ChangeCause`.
    """

    batch: StateMutationBatch


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


class LlmPerformanceResponse(BaseModel):
    """Recent LLM performance, for a developer looking at a slow turn.

    Counts, durations and identifiers only. No prompt, no generated text, no context --
    a diagnostics endpoint that echoed prompts would be a way to read someone's story
    through a URL, and it would duplicate the Story Director's prompts into a second
    place that has to be kept out of logs and out of git.

    Mounted under `/dev`, so it exists only where `Settings.dev_endpoints_enabled` says
    so and is off by default outside development and test.
    """

    summary: LlmPerformanceSummary
    generations: list[LlmGenerationMetrics]
    turns: list[TurnPerformanceMetrics]
    session_id: uuid.UUID | None = None
    """Set when the records were filtered to one session."""
