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


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


class StoryContextReaderPort(Protocol):
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

    async def add_event(self, event: NewEvent) -> None:
        """Record something that happened.

        Implementations assign the per-session ordering key. Two events in the same
        fictional minute must come back in the order they were added, and choosing
        the number that guarantees that is a storage concern -- the same class of
        thing as a primary key, and not something a caller should have to pass.
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

    async def add_event(self, event: NewEvent) -> None:
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


class TurnGatewayPort(StoryContextReaderPort, TurnPersistencePort, TurnUnitOfWorkPort, Protocol):
    """The three ports as one object, because one transaction spans all of them.

    Functions still declare the narrowest port they need -- `build_story_context`
    takes only a reader, and `advance_time` takes only `SessionClockPort`. This exists
    so the turn use case, which genuinely needs all three against the same
    transaction, takes one argument instead of three that would have to be the same
    object anyway.
    """
