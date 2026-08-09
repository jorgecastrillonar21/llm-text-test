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
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.domain.enums import Language, MemoryKind, MessageRole
from app.domain.relationships import RelationshipVector
from app.domain.world_rules import WorldRules

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
    type: str
    description: str


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

    async def add_event(self, event: NewEvent) -> None: ...

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


class TurnGatewayPort(StoryContextReaderPort, TurnPersistencePort, TurnUnitOfWorkPort, Protocol):
    """The three ports as one object, because one transaction spans all of them.

    Functions still declare the narrowest port they need -- `build_story_context`
    takes only a reader. This exists so the turn use case, which genuinely needs
    all three against the same transaction, takes one argument instead of three
    that would have to be the same object anyway.
    """
