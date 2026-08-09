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
from app.domain.world_rules import WorldRules
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


class StoryContextReaderPort(FactReaderPort, Protocol):
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


class TurnGatewayPort(
    StoryContextReaderPort, TurnPersistencePort, WorldStatePort, TurnUnitOfWorkPort, Protocol
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
    """
