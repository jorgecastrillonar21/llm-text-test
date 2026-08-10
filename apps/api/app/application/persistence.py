"""Persistence seen from the application layer: read DTOs and narrow ports.

Nothing here knows that SQLAlchemy, SQLite or an ORM exist. The turn use case
talks to these Protocols; `app.infrastructure.db.turn_gateway` implements them.

The ports are deliberately shaped around the two use cases that exist today
rather than around tables. There is no generic repository, no per-table
abstraction and no unit-of-work framework -- only the operations the bootstrap
turn loop actually performs. Adding a table does not imply adding a port.

Read DTOs are separate from the `story_context` models on purpose. An adapter
maps rows into these; the application then decides what becomes StoryContext,
which is why speaker labels and relationship names are resolved in
`context_builder` and not in SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Language, MemoryKind, MessageRole
from app.domain.relationships import RelationshipVector
from app.domain.world_facts import (
    FactAuthority,
    FactKind,
    FactSubject,
    FactValue,
    Importance,
    SetFact,
    WorldFact,
)
from app.domain.world_locations import (
    ConnectionCategory,
    LocationAccessibility,
    LocationCategory,
    LocationCondition,
    LocationConnection,
    LocationConnectionState,
    LocationDefinition,
    LocationScale,
    LocationState,
    LocationZone,
    PhysicalDistance,
)
from app.domain.world_rules import WorldRules
from app.domain.world_situations import (
    Importance as SituationImportance,
)
from app.domain.world_situations import (
    Intensity,
    Momentum,
    ParticipantEntityType,
    Situation,
    SituationCategory,
    SituationParticipant,
    SituationScope,
    SituationStatus,
    StartSituation,
    Threat,
)
from app.domain.world_time import FictionalDateTime, ScheduledEventStatus

# ---------------------------------------------------------------------------
# Read DTOs
# ---------------------------------------------------------------------------


class WorldSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    description: str
    genre: str
    setting: str
    language: Language

    rules: WorldRules
    """Already validated. Adapters parse the stored document; nothing downstream
    re-checks it, and there is deliberately no default -- an adapter that forgets to
    map this fails loudly instead of quietly running a world on someone else's rules."""

    initial_datetime: FictionalDateTime
    """The fictional instant a session's `elapsed_minutes = 0` corresponds to.

    Also no default, for the same reason: a world silently starting on the first
    morning of year one is a date nobody chose."""


class SessionSnapshot(BaseModel):
    """A game session as the turn use case needs to see it.

    Carries `world_id` and the player's identity, which `SessionContext` does
    not: that one is the narrower view handed to the story provider.
    """

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    world_id: uuid.UUID
    title: str
    player_name: str
    player_description: str
    current_location: str
    summary: str
    turn_index: int

    elapsed_minutes: int
    """Where this session sits on its own simulation clock. Independent of
    `turn_index`: neither one can be computed from the other."""

    state_revision: int
    """How many batches of state mutations this session has committed. A third
    independent counter: turns, minutes and changes each move for their own reasons,
    and a turn of pure conversation moves only the first."""


class CharacterRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    description: str
    appearance: str
    personality: str
    backstory: str
    speech_style: str
    goals: list[str]
    secrets: list[str]


class TranscriptMessage(BaseModel):
    """A stored message. Unlike `MessageContext` it keeps the raw character id:
    turning that into a display name is application policy."""

    model_config = ConfigDict(frozen=True)

    turn_index: int
    role: MessageRole
    speaker_character_id: uuid.UUID | None
    content: str


class MemoryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: MemoryKind
    summary: str
    importance: int
    character_id: uuid.UUID | None


class RelationshipRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    character_id: uuid.UUID
    trust: int
    affection: int
    respect: int
    fear: int


# ---------------------------------------------------------------------------
# Write DTOs
# ---------------------------------------------------------------------------


class NewMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    turn_index: int
    role: MessageRole
    content: str
    speaker_character_id: uuid.UUID | None = None


class NewMemory(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    kind: MemoryKind
    summary: str
    importance: int
    character_id: uuid.UUID | None = None


class NewEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    turn_index: int
    occurred_at: int
    """Fictional time, in session elapsed minutes. Carried alongside `turn_index`
    because they answer different questions: which exchange, and when in the story."""
    type: str
    description: str


class NewScheduledEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    due_at: int
    """Absolute session time. Callers convert delays before they get here."""
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    interrupt_player_action: bool = False


class ScheduledEventRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    session_id: uuid.UUID
    due_at: int
    type: str
    payload: dict[str, Any]
    status: ScheduledEventStatus
    interrupt_player_action: bool


class NewLocation(BaseModel):
    """A place to be written, with the application already having decided everything.

    The id is deliberately absent: the application mints it. A caller that supplied one
    could collide with an existing row, and the one caller most likely to try is a
    language model reproducing a uuid it saw in a prompt.
    """

    model_config = ConfigDict(frozen=True)

    world_id: uuid.UUID
    origin_session_id: uuid.UUID | None = None
    """None writes reusable template geography. A session id writes canon local to one
    save, which is what generative creation during a turn produces."""

    name: str
    description: str = ""
    category: LocationCategory
    subtype: str | None = None
    scale: LocationScale
    parent_location_id: uuid.UUID | None = None
    importance: int = 3
    tags: tuple[str, ...] = ()
    spatial_metadata: dict[str, Any] = Field(default_factory=dict)


class NewConnection(BaseModel):
    model_config = ConfigDict(frozen=True)

    world_id: uuid.UUID
    origin_session_id: uuid.UUID | None = None
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    bidirectional: bool = True
    category: ConnectionCategory
    subtype: str | None = None
    physical_distance: PhysicalDistance | None = None
    base_travel_minutes: int | None = None
    importance: int = 3
    tags: tuple[str, ...] = ()


class NewZone(BaseModel):
    model_config = ConfigDict(frozen=True)

    location_id: uuid.UUID
    name: str
    category: str | None = None
    description: str = ""
    importance: int = 2
    tags: tuple[str, ...] = ()


class LocationStateWrite(BaseModel):
    """A resolved location state, ready to insert or replace.

    Whole-row rather than partial: the application has already read the current state,
    applied the mutation's changes to it and produced the result. Partial updates stop
    at the port so the adapter never has to know what "leave this field alone" means.
    """

    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    location_id: uuid.UUID
    condition: LocationCondition
    accessibility: LocationAccessibility
    security_level: int
    local_danger_modifier: int
    owner_entity_id: uuid.UUID | None = None
    controller_entity_id: uuid.UUID | None = None


class ConnectionStateWrite(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    connection_id: uuid.UUID
    condition: LocationCondition
    accessibility: LocationAccessibility
    traversal_modifier: int


class NewSituation(BaseModel):
    """A process to be written, with the application having decided everything.

    No id, for the reason `NewLocation` has none: the application mints it. There is
    also no `resolved_at` -- a situation cannot be created already over, and leaving the
    field out is a stronger statement of that than validating it away would be.
    """

    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    """Always a session. Unlike geography there is no template tier: a world may seed a
    starting process, but what gets written belongs to one save."""

    category: SituationCategory
    subtype: str | None = None
    title: str
    description: str | None = None

    status: SituationStatus = SituationStatus.ACTIVE
    intensity: Intensity = 30
    threat: Threat = 0
    momentum: Momentum = 0
    importance: SituationImportance = 3

    scope: SituationScope = SituationScope.LOCAL
    primary_location_id: uuid.UUID | None = None
    parent_situation_id: uuid.UUID | None = None

    started_at: int = Field(ge=0)
    """Session elapsed minutes. Fictional time, never a wall clock."""

    source_event_id: uuid.UUID | None = None
    situation_metadata: dict[str, Any] = Field(default_factory=dict)
    tags: tuple[str, ...] = ()


class SituationUpdate(BaseModel):
    """A resolved situation update, ready to write.

    Every value is already decided: the application read the current row inside the
    transaction, applied the mutation's deltas to it, clamped the result and validated
    the transition. Partial updates stop here, the same way they do for
    `LocationStateWrite` -- the adapter never has to know what "leave this alone" means.
    """

    model_config = ConfigDict(frozen=True)

    situation_id: uuid.UUID

    intensity: Intensity
    threat: Threat
    momentum: Momentum
    importance: SituationImportance

    status: SituationStatus
    last_progressed_at: int = Field(ge=0)
    resolved_at: int | None = Field(default=None, ge=0)

    situation_metadata: dict[str, Any]


class NewParticipant(BaseModel):
    model_config = ConfigDict(frozen=True)

    situation_id: uuid.UUID
    entity_type: ParticipantEntityType
    entity_id: uuid.UUID
    role: str


class NewFact(BaseModel):
    """A fact as the application has decided it, ready to be written.

    Every field is already settled: the property is canonical, the value has been
    narrowed, the authority was checked against the property's policy and the world's
    rules were consulted. The adapter's only remaining decision is insert or update.
    """

    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    kind: FactKind
    subject: FactSubject
    property: str
    value: FactValue
    importance: Importance
    current_value_since: int
    authority: FactAuthority
    source_event_id: uuid.UUID | None = None
    tags: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


class FactReaderPort(Protocol):
    """Reading current world state. Shared by context assembly and the state service,
    which want the same query for different reasons."""

    async def load_facts(
        self,
        session_id: uuid.UUID,
        *,
        subject: FactSubject | None = None,
        kind: FactKind | None = None,
        min_importance: int | None = None,
        limit: int,
    ) -> list[WorldFact]:
        """Current facts, most important first, then most recently changed.

        The final tiebreak must be the property name so the order is total: this feeds
        a language model prompt, and a set of equally important facts that shuffles
        between turns is a prompt that will not cache and a diff nobody can read.
        """
        ...


class SpatialReaderPort(Protocol):
    """Reading a session's spatial reality.

    Every method takes `session_id` and every implementation must apply the same
    visibility rule: template geography (`origin_session_id IS NULL`) plus this
    session's own, and nothing from any other session. Stated on the port because it is
    the one rule that, if an adapter forgets it, leaks another save's canon into this
    one with no error anywhere.
    """

    async def load_locations(
        self, session_id: uuid.UUID | None, *, world_id: uuid.UUID, limit: int
    ) -> list[LocationDefinition]:
        """Every location this session can see, most important first, then by name.

        Loaded whole rather than walked, because containment questions need more than
        one node at a time and a narrative world holds tens to low hundreds of places.
        `limit` is the guard on that assumption rather than a page size; when a world
        outgrows it, this is the method that becomes a recursive CTE.
        """
        ...

    async def get_location(
        self, session_id: uuid.UUID | None, location_id: uuid.UUID
    ) -> LocationDefinition | None:
        """One location, or None -- including when it exists but belongs to another
        session, which from here is indistinguishable from not existing, and should be."""
        ...

    async def load_connections(
        self, session_id: uuid.UUID | None, *, world_id: uuid.UUID, limit: int
    ) -> list[LocationConnection]:
        """Every traversal this session can see. Same visibility rule."""
        ...

    async def load_zones(self, location_id: uuid.UUID) -> list[LocationZone]:
        """Zones of one location, most important first, then by name.

        No session parameter: zones hang off a definition and have no state of their
        own, so a zone is visible exactly when its location is.
        """
        ...

    async def load_location_states(self, session_id: uuid.UUID) -> list[LocationState]:
        """Every location state in this session. One row per place that has one."""
        ...

    async def load_connection_states(
        self, session_id: uuid.UUID
    ) -> list[LocationConnectionState]: ...

    async def get_location_state(
        self, session_id: uuid.UUID, location_id: uuid.UUID
    ) -> LocationState | None: ...

    async def get_connection_state(
        self, session_id: uuid.UUID, connection_id: uuid.UUID
    ) -> LocationConnectionState | None: ...

    async def get_connection(
        self, session_id: uuid.UUID | None, connection_id: uuid.UUID
    ) -> LocationConnection | None: ...


class SituationReaderPort(Protocol):
    """Reading what is currently under way in a session.

    Every method takes `session_id` and filters on it. There is no template tier and no
    cross-session read: a situation belongs to one save, and the port has no signature
    that could return another save's.
    """

    async def load_situations(
        self,
        session_id: uuid.UUID,
        *,
        statuses: frozenset[SituationStatus] | None = None,
        category: SituationCategory | None = None,
        scope: SituationScope | None = None,
        primary_location_id: uuid.UUID | None = None,
        limit: int,
    ) -> list[Situation]:
        """Situations in this session, most important first, then most recently
        progressed, then by title.

        The final tiebreak must be the title so the order is total: this feeds a prompt,
        and a set of equally important situations that reshuffles between turns is a
        prompt that will not cache and a diff nobody can read.

        `statuses=None` means every status including the concluded ones -- history is
        the reason they are still here. Callers wanting only live processes pass the set
        they mean.
        """
        ...

    async def get_situation(
        self, session_id: uuid.UUID, situation_id: uuid.UUID
    ) -> Situation | None:
        """One situation, or None -- including when it exists in another session, which
        from here is indistinguishable from not existing, and should be."""
        ...

    async def load_participants(
        self, situation_ids: Sequence[uuid.UUID]
    ) -> list[SituationParticipant]:
        """Participants of several situations at once, ordered by situation then role.

        Batched deliberately. The caller that needs this needs it for a list of
        situations, and one query per situation is how an N+1 gets written into the
        turn loop. An empty sequence returns an empty list without querying.
        """
        ...

    async def load_situations_for_entity(
        self,
        session_id: uuid.UUID,
        *,
        entity_id: uuid.UUID,
        entity_type: ParticipantEntityType | None = None,
        statuses: frozenset[SituationStatus] | None = None,
        limit: int,
    ) -> list[Situation]:
        """Everything this entity is taking part in. The reverse lookup
        `situation_participants` exists for; same ordering as `load_situations`.

        `entity_type=None` matches any, because ids are unique across types in practice
        and a caller holding a character id should not have to say so twice.
        """
        ...


class StoryContextReaderPort(FactReaderPort, SpatialReaderPort, SituationReaderPort, Protocol):
    """Reads that feed context assembly.

    Limits are passed in by the caller because how much history is worth
    retrieving is application policy, not a storage concern. Ordering is part of
    that policy too, but it has to run in the query to be worth anything, so each
    method's contract states the order the adapter must return.
    """

    async def load_characters(self, world_id: uuid.UUID, *, limit: int) -> list[CharacterRecord]:
        """Oldest first, capped at `limit`."""
        ...

    async def load_recent_messages(
        self, session_id: uuid.UUID, *, limit: int
    ) -> list[TranscriptMessage]:
        """The newest `limit` messages, returned oldest-first for natural reading."""
        ...

    async def load_memories(self, session_id: uuid.UUID, *, limit: int) -> list[MemoryRecord]:
        """Most important first, then most recent. No embeddings yet."""
        ...

    async def load_relationships(self, session_id: uuid.UUID) -> list[RelationshipRecord]: ...


class TurnPersistencePort(Protocol):
    """Everything the turn use case reads or writes outside of context assembly."""

    async def get_session(self, session_id: uuid.UUID) -> SessionSnapshot | None: ...

    async def get_world(self, world_id: uuid.UUID) -> WorldSnapshot | None: ...

    async def known_character_ids(self, world_id: uuid.UUID) -> set[uuid.UUID]:
        """Ids the provider is allowed to attribute output to."""
        ...

    async def add_message(self, message: NewMessage) -> uuid.UUID:
        """Stage a message and return its id.

        Implementations must make the message visible to subsequent reads on the
        same port *without committing*: the player's action is staged before the
        provider runs so it appears in the transcript the provider sees, and a
        failed turn must still roll all of it back.
        """
        ...

    async def add_memory(self, memory: NewMemory) -> None: ...

    async def add_event(self, event: NewEvent) -> uuid.UUID:
        """Record something that happened, and return its id.

        Implementations assign the per-session ordering key. Two events in the same
        fictional minute must come back in the order they were added, and choosing
        the number that guarantees that is a storage concern -- the same class of
        thing as a primary key, and not something a caller should have to pass.

        The id comes back because facts point at the event that caused them. A caller
        with nothing to attach may ignore it.
        """
        ...

    async def get_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID
    ) -> RelationshipRecord | None: ...

    async def save_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID, vector: RelationshipVector
    ) -> None:
        """Store already-clamped values. Deciding them is the application's job."""
        ...

    async def set_turn_index(self, session_id: uuid.UUID, turn_index: int) -> None: ...


class TurnUnitOfWorkPort(Protocol):
    async def commit(self) -> None:
        """Make the staged turn durable.

        Called explicitly by the use case before it returns, never in a framework
        teardown hook: a client that re-reads immediately after a successful turn
        must see it.
        """
        ...


class SessionClockPort(TurnUnitOfWorkPort, Protocol):
    """What advancing simulation time needs, and nothing else.

    Separate from `TurnGatewayPort` because they are separate use cases. A turn reads
    the clock -- `SessionSnapshot.elapsed_minutes` -- and never moves it; moving it is
    something only the application's own time systems do, through here. The same
    adapter implements both, since both run inside one request's transaction.
    """

    async def get_session(self, session_id: uuid.UUID) -> SessionSnapshot | None: ...

    async def get_world(self, world_id: uuid.UUID) -> WorldSnapshot | None:
        """The world, for its rules: `simulation.time_progression` decides who may
        advance the clock at all."""
        ...

    async def set_elapsed_minutes(self, session_id: uuid.UUID, elapsed_minutes: int) -> None:
        """Store an already-decided position. Deciding it belongs to the application,
        which is also where the never-backward rule is enforced."""
        ...

    async def add_event(self, event: NewEvent) -> uuid.UUID:
        """Used for the audit trail: why the clock moved, and by how much."""
        ...

    async def add_scheduled_event(self, event: NewScheduledEvent) -> uuid.UUID: ...

    async def get_scheduled_event(self, event_id: uuid.UUID) -> ScheduledEventRecord | None: ...

    async def load_due_scheduled_events(
        self, session_id: uuid.UUID, *, through: int
    ) -> list[ScheduledEventRecord]:
        """Pending events due at or before `through`, earliest first.

        Earliest first is the processing order, so it has to come out of the query.
        The lower bound is deliberately open: anything still pending is due, even if
        its minute is already behind the clock. An event written into the past would
        otherwise sit there forever, which is a worse failure than firing it late.
        """
        ...

    async def set_scheduled_event_status(
        self, event_id: uuid.UUID, status: ScheduledEventStatus
    ) -> None:
        """Store an already-validated transition; the rules live in the domain."""
        ...


class WorldStatePort(FactReaderPort, TurnUnitOfWorkPort, Protocol):
    """What changing the world's current truth needs, and nothing else.

    Deliberately narrow in the same way `SessionClockPort` is. The state service can
    read facts, write facts, record an event and move the revision counter. It cannot
    touch the transcript, the relationships or the clock, and the signature is what
    says so.
    """

    async def get_session(self, session_id: uuid.UUID) -> SessionSnapshot | None: ...

    async def get_world(self, world_id: uuid.UUID) -> WorldSnapshot | None:
        """The world, for its rules: what may become true here is a property of the
        universe, not of the caller."""
        ...

    async def known_character_ids(self, world_id: uuid.UUID) -> set[uuid.UUID]:
        """Ids a fact may be about. Entity resolution happens before persistence, so a
        fact never points at a character that does not exist."""
        ...

    async def load_initial_facts(self, world_id: uuid.UUID) -> list[SetFact]:
        """The world template's starting facts, parsed.

        `SetFact` rather than a separate seed type: a template fact is a mutation
        waiting for a session to apply it, and giving it its own model would mean two
        shapes to validate and two ways for one of them to drift.

        Implementations parse the stored documents and raise on a malformed one -- the
        same contract as `WorldSnapshot.rules`.
        """
        ...

    async def get_fact(
        self, session_id: uuid.UUID, subject: FactSubject, canonical_property: str
    ) -> WorldFact | None:
        """The current value, or None when nothing is established.

        None means *absent*, which is not the same as a fact whose value is null. The
        caller has to keep those apart; see `world_facts.values`.
        """
        ...

    async def set_fact(self, fact: NewFact) -> uuid.UUID:
        """Insert or replace in place, returning the fact's id.

        Replacement keeps the existing row: one current value per subject and
        property is the invariant, and it is enforced by a unique index rather than by
        this method remembering to delete first.
        """
        ...

    async def remove_fact(
        self, session_id: uuid.UUID, subject: FactSubject, canonical_property: str
    ) -> bool:
        """Delete the current value. True if a row went away."""
        ...

    async def add_event(self, event: NewEvent) -> uuid.UUID:
        """The event that caused this change, written before the facts point at it."""
        ...

    async def bump_state_revision(self, session_id: uuid.UUID) -> int:
        """Advance the session's state revision by one and return the new value.

        Read-then-write inside the caller's transaction. Nothing here makes it safe
        against a concurrent writer, and nothing needs to yet -- a single-player
        application commits one request at a time. `expected_revision` is the hook for
        when that stops being true.
        """
        ...


class SpatialPort(SpatialReaderPort, TurnUnitOfWorkPort, Protocol):
    """What changing spatial reality needs, and nothing else.

    Narrow in the same way `SessionClockPort` and `WorldStatePort` are. This can read
    the graph, add to it and write per-session state. It cannot touch the transcript,
    the relationships, the clock or the facts, and the signature is what says so.
    """

    async def get_session(self, session_id: uuid.UUID) -> SessionSnapshot | None: ...

    async def get_world(self, world_id: uuid.UUID) -> WorldSnapshot | None: ...

    async def add_location(self, location: NewLocation) -> uuid.UUID:
        """Write a definition and return the id the application minted for it."""
        ...

    async def add_connection(self, connection: NewConnection) -> uuid.UUID: ...

    async def add_zone(self, zone: NewZone) -> uuid.UUID: ...

    async def set_location_state(self, state: LocationStateWrite) -> uuid.UUID:
        """Insert or replace this session's state for one place, in place.

        In place, like `set_fact`: one current state per session and location is the
        invariant, enforced by a unique constraint rather than by this method
        remembering to delete first.
        """
        ...

    async def set_connection_state(self, state: ConnectionStateWrite) -> uuid.UUID: ...


class SituationPort(SpatialReaderPort, SituationReaderPort, TurnUnitOfWorkPort, Protocol):
    """What changing ongoing processes needs, and nothing else.

    Bases listed spatial-then-situation, matching `StoryContextReaderPort`, because
    `TurnGatewayPort` inherits from both and Python cannot linearise two parents that
    disagree about the order of a shared base. Cosmetic here; an unresolvable MRO there.

    Narrow in the same way `SessionClockPort`, `WorldStatePort` and `SpatialPort` are.
    This can read situations, start them, move them and attach participants. It cannot
    touch the transcript, the relationships, the clock or the facts, and it cannot
    *write* geography -- the signature is what says so.

    It can *read* geography, via `SpatialReaderPort`, because a situation points at a
    primary location and that pointer has to be checked against what this session can
    actually see. A siege of a castle another save invented is not a situation with a
    bad foreign key; it is a siege of nowhere.
    """

    async def get_session(self, session_id: uuid.UUID) -> SessionSnapshot | None: ...

    async def get_world(self, world_id: uuid.UUID) -> WorldSnapshot | None:
        """The world, for its rules: how autonomous the world is meant to be decides
        what a progression pass is allowed to do without the player."""
        ...

    async def known_character_ids(self, world_id: uuid.UUID) -> set[uuid.UUID]:
        """Ids a participant may be. Characters are the only entity type this
        application can currently resolve; factions have no table yet."""
        ...

    async def load_initial_situations(self, world_id: uuid.UUID) -> list[StartSituation]:
        """The world template's starting processes, parsed.

        `StartSituation` rather than a separate seed type, for the reason
        `load_initial_facts` returns `SetFact`: a template situation is a mutation
        waiting for a session to apply it, and giving it its own model would mean two
        shapes to validate and two ways for one of them to drift.

        Implementations parse the stored documents and raise on a malformed one -- the
        same contract as `WorldSnapshot.rules`.
        """
        ...

    async def add_situation(self, situation: NewSituation) -> uuid.UUID:
        """Write a situation and return the id the application minted for it."""
        ...

    async def update_situation(self, update: SituationUpdate) -> None:
        """Store an already-resolved update. Deciding it -- reading the current value,
        applying deltas, clamping, validating the transition -- belongs to the
        application, and doing any of it here would put game rules in an adapter."""
        ...

    async def add_participant(self, participant: NewParticipant) -> uuid.UUID:
        """Attach a participant, or return the existing row's id if it is already
        attached. One entity in one role once; a caller re-stating a known participant
        is not an error."""
        ...


class StateStorePort(WorldStatePort, SpatialPort, SituationPort, Protocol):
    """What `state_service` needs to apply one batch of changes.

    Facts, space and situations together, because a batch may contain all three and a
    siege progression that raised the intensity but left the gate standing would be
    exactly the half-applied outcome the batch exists to prevent. Everything else -- the
    transcript, the relationships, the clock -- is still out of reach.
    """


class ProgressionPort(StateStorePort, Protocol):
    """What applying a situation progression needs.

    `StateStorePort` plus one method: a progression may decide when it is worth looking
    at this process again, and writing that down is scheduling. Everything else it
    does -- moving the situation, changing a location, establishing a fact, starting a
    child process -- is an ordinary state mutation and needs nothing new.

    Deliberately not all of `SessionClockPort`: a progression schedules, it does not
    move the clock. Advancing time is what *causes* progressions, and a resolver that
    could advance time could evaluate itself forever.
    """

    async def add_scheduled_event(self, event: NewScheduledEvent) -> uuid.UUID: ...


class TurnGatewayPort(
    StoryContextReaderPort,
    TurnPersistencePort,
    StateStorePort,
    TurnUnitOfWorkPort,
    Protocol,
):
    """The ports a turn needs, as one object, because one transaction spans them all.

    Functions still declare the narrowest port they need -- `build_story_context`
    takes only a reader, `advance_time` takes only `SessionClockPort`, and
    `stage_state_change` takes only `WorldStatePort`. This exists so the turn use case,
    which genuinely needs all of them against the same transaction, takes one argument
    instead of four that would have to be the same object anyway.

    `WorldStatePort` joined when the Story Director gained the ability to propose
    facts: a turn now reads current truth for its context, and writes the proposals
    that survive review, inside the transaction that already covers the transcript.

    `SpatialPort` joined for the same reason one step later. A turn reads where it is
    happening to build its context, and may write a location the story just
    established -- both inside that same transaction, so a failed turn takes the new
    bookshop with it rather than leaving geography nobody visited.

    `SituationPort` joined next, and the pattern is now the pattern: a turn reads what
    is under way to build its context, and may start a process the story just set in
    motion. Same transaction, so a failed turn takes the riot with it.
    """
