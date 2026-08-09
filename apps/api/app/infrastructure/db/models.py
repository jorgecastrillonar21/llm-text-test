"""SQLAlchemy models. These never cross the API boundary; routers return DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.domain.enums import Language, MemoryKind, MessageRole
from app.domain.relationships import AXIS_MAX, AXIS_MIN
from app.domain.world_facts import FactAuthority, FactKind, FactSubjectType
from app.domain.world_rules import default_world_rules
from app.domain.world_time import DEFAULT_INITIAL_DATETIME, ScheduledEventStatus
from app.infrastructure.db.base import Base
from app.infrastructure.db.types import UtcDateTime, utcnow


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _default_rules_json() -> dict[str, Any]:
    """Balanced defaults, so a world created without rules still has valid ones.

    `dict[str, Any]` because that is what a JSON column round-trips to; the shape is
    re-established by `parse_world_rules` on every read.
    """
    return default_world_rules().model_dump(mode="json")


def _default_initial_datetime() -> dict[str, Any]:
    """The fictional instant a session's `elapsed_minutes = 0` corresponds to."""
    return DEFAULT_INITIAL_DATETIME.model_dump(mode="json")


class World(Base):
    __tablename__ = "worlds"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    genre: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    setting: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Immutable after creation -- see Language docstring.
    language: Mapped[Language] = mapped_column(String(8), default=Language.EN, nullable=False)
    # The whole WorldRules document as one JSON column rather than a dozen tables.
    # It is static configuration read as a unit and never queried by field, so
    # normalising it would buy joins and nothing else. Validity is enforced at the
    # boundaries by parse_world_rules, not by the schema.
    rules_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=_default_rules_json, nullable=False
    )
    # The fictional date and time that every session of this world starts at. Five
    # small integers rather than five columns: they are read as one value, and a
    # world with a month but no year is not a thing anyone wants to represent.
    initial_datetime: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=_default_initial_datetime, nullable=False
    )
    # The world as a template: facts every new session of it starts with. Copied into
    # per-session rows when a session begins and never read again, so a session that
    # kills the king does not edit the world other sessions start from. Nothing writes
    # this column after creation -- there is deliberately no update path.
    initial_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    characters: Mapped[list[Character]] = relationship(
        back_populates="world", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[GameSession]] = relationship(
        back_populates="world", cascade="all, delete-orphan"
    )


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[uuid.UUID] = _uuid_pk()
    world_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    appearance: Mapped[str] = mapped_column(Text, default="", nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="", nullable=False)
    backstory: Mapped[str] = mapped_column(Text, default="", nullable=False)
    speech_style: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Free-form lists until the narrative layer needs richer structure.
    goals: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    secrets: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    world: Mapped[World] = relationship(back_populates="characters")


class GameSession(Base):
    __tablename__ = "game_sessions"
    __table_args__ = (
        CheckConstraint("elapsed_minutes >= 0", name="ck_game_sessions_elapsed_nonnegative"),
        CheckConstraint("state_revision >= 0", name="ck_game_sessions_revision_nonnegative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    world_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    player_name: Mapped[str] = mapped_column(String(200), nullable=False)
    player_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    current_location: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    # Rolling recap of turns older than the recent-message window. Phase 2 fills this.
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    turn_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # The authoritative simulation clock, counted from this session's own start.
    # BigInteger because it is the one number a long-running story keeps adding to,
    # and because a 32-bit column would quietly cap a world at roughly four thousand
    # fictional years. The hour, the date and the season are derived, never stored.
    elapsed_minutes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Bumped once per committed batch of state mutations, never decreased. It is a
    # change counter for this session's WorldState, not a version of the schema and
    # not a count of turns: a turn that changes nothing leaves it alone.
    state_revision: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    world: Mapped[World] = relationship(back_populates="sessions")
    messages: Mapped[list[Message]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session_turn", "session_id", "turn_index"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(String(20), nullable=False)
    speaker_character_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)

    session: Mapped[GameSession] = relationship(back_populates="messages")


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_memories_importance_range"),
        # Retrieval is "most important, then most recent" -- see context_builder.
        Index("ix_memories_session_importance", "session_id", "importance", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[MemoryKind] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)


class Relationship(Base):
    """One row per (session, character) pair describing how they regard the player."""

    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("session_id", "character_id", name="uq_relationship_session_character"),
        CheckConstraint(
            f"trust BETWEEN {AXIS_MIN} AND {AXIS_MAX} "
            f"AND affection BETWEEN {AXIS_MIN} AND {AXIS_MAX} "
            f"AND respect BETWEEN {AXIS_MIN} AND {AXIS_MAX} "
            f"AND fear BETWEEN {AXIS_MIN} AND {AXIS_MAX}",
            name="ck_relationships_axis_range",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    character_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trust: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    affection: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    respect: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fear: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class GameEvent(Base):
    """Something that happened, stamped with both of the times that matter."""

    __tablename__ = "game_events"
    __table_args__ = (
        Index("ix_game_events_session_turn", "session_id", "turn_index"),
        Index("ix_game_events_session_time", "session_id", "occurred_at", "event_sequence"),
        # A per-session counter has to actually be unique to be an ordering. Two
        # writers racing for the same number is a loud integrity error here rather
        # than two events that silently swap places on the next read.
        UniqueConstraint("session_id", "event_sequence", name="uq_game_events_session_sequence"),
        CheckConstraint("occurred_at >= 0", name="ck_game_events_occurred_at_nonnegative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # When it happened in the fiction. Independent of turn_index: a whole turn may
    # occupy one minute, and one turn may cover a season.
    occurred_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Ties are the normal case, not the exception -- everything in a turn usually
    # shares a minute -- so ordering needs a second key. A monotonic per-session
    # counter, rather than inventing seconds the game clock does not have.
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)


class WorldFact(Base):
    """One current objective truth in one session. See app.domain.world_facts.

    # The uniqueness constraint, and the trap in it

    There must be exactly one current value per (session, subject, property). The
    obvious constraint --

        UNIQUE (session_id, subject_type, subject_id, property)

    -- is wrong, and wrong in the quiet way. `subject_id` is NULL for facts about the
    world itself, and SQL says NULL is not equal to NULL, so every world-scoped row
    slips past a unique index that includes it. SQLite and PostgreSQL both behave this
    way. The result would be a table that enforces uniqueness for characters and
    silently permits `world.political_status = stable` next to
    `world.political_status = collapsed` -- exactly the contradiction the constraint
    exists to prevent, in the one place nobody would think to test.

    So there are two partial unique indexes instead, splitting on the null:

        subject_id IS NOT NULL  -> unique over (session, type, id, property)
        subject_id IS NULL      -> unique over (session, type, property)

    The alternative -- a sentinel UUID standing in for "the world" -- makes one index
    do the job, at the cost of a magic value that every query and every reader has to
    know about. A constraint the database understands is worth two lines of DDL.

    # `kind` is deliberately not part of the key

    A property is one logical thing. If `kind` were in the key, the same subject and
    property could exist once as `world_truth` and once as `gameplay_flag`, holding
    opposite values, both current. Kind classifies a fact; it does not identify one.
    """

    __tablename__ = "world_facts"
    __table_args__ = (
        Index(
            "uq_world_facts_entity_property",
            "session_id",
            "subject_type",
            "subject_id",
            "property",
            unique=True,
            sqlite_where=text("subject_id IS NOT NULL"),
            postgresql_where=text("subject_id IS NOT NULL"),
        ),
        Index(
            "uq_world_facts_world_property",
            "session_id",
            "subject_type",
            "property",
            unique=True,
            sqlite_where=text("subject_id IS NULL"),
            postgresql_where=text("subject_id IS NULL"),
        ),
        # "Everything currently true about this subject" is the read the context
        # builder and the debug view both make.
        Index("ix_world_facts_session_subject", "session_id", "subject_type", "subject_id"),
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_world_facts_importance_range"),
        CheckConstraint(
            "current_value_since >= 0", name="ck_world_facts_current_value_since_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[FactKind] = mapped_column(String(20), nullable=False)
    subject_type: Mapped[FactSubjectType] = mapped_column(String(20), nullable=False)
    # NULL exactly when the subject is the world itself. See the class docstring.
    subject_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    property: Mapped[str] = mapped_column(String(120), nullable=False)
    # `none_as_null=False` so a Python None is stored as JSON null rather than SQL
    # NULL. A fact whose value is nothing is still a fact, and the column staying NOT
    # NULL keeps "this row has no value at all" from being representable.
    value: Mapped[Any] = mapped_column(JSON(none_as_null=False), nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Session elapsed minutes, from Time V1: when this value became authoritative in
    # the fiction. Distinct from updated_at, which is when the row was written.
    current_value_since: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    authority: Mapped[FactAuthority] = mapped_column(String(30), nullable=False)
    # SET NULL rather than CASCADE: losing the event that explains a fact must not
    # delete the fact. Provenance can decay; current truth cannot.
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_events.id", ondelete="SET NULL"), nullable=True
    )
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class ScheduledEvent(Base):
    """Something due at a future point on the session clock.

    Fictional scheduling: nothing here runs on its own, and nothing fires while the
    application is closed. Pending rows are only ever looked at during an explicit
    time advance. See `app.domain.world_time.scheduling`.
    """

    __tablename__ = "scheduled_events"
    __table_args__ = (
        # The only query this table has: what is still pending, due by when.
        Index("ix_scheduled_events_pending", "session_id", "status", "due_at"),
        CheckConstraint("due_at >= 0", name="ck_scheduled_events_due_at_nonnegative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Absolute session time, never a delay. "In three days" is resolved when the row
    # is written, so the event does not change meaning depending on when it is read.
    due_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    # Genuinely arbitrary JSON: this table is generic infrastructure and the systems
    # that will give the payload a shape do not exist yet.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[ScheduledEventStatus] = mapped_column(
        String(20), default=ScheduledEventStatus.PENDING, nullable=False
    )
    interrupt_player_action: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
