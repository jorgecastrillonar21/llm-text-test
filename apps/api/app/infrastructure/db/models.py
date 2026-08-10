"""SQLAlchemy models. These never cross the API boundary; routers return DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
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
from app.domain.world_locations import (
    ConnectionCategory,
    LocationAccessibility,
    LocationCategory,
    LocationCondition,
    LocationScale,
)
from app.domain.world_rules import default_world_rules
from app.domain.world_situations import (
    ParticipantEntityType,
    SituationCategory,
    SituationScope,
    SituationStatus,
)
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
    # The same idea for ongoing processes: what a world already has under way before
    # anyone plays it. A besieged capital, a failing ward network, a contested throne.
    #
    # Stored as `StartSituation` documents rather than as situation rows, because a
    # situation belongs to a session and this is a template -- there is no world-scoped
    # situation to copy from. Each session materialises its own, gets its own ids, and
    # diverges immediately; a session that lifts the siege does not lift it for anyone
    # else. Nothing writes this column after creation.
    initial_situations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
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


class LocationDefinition(Base):
    """A place that persistently exists. See app.domain.world_locations.

    # Template rows are shared, not copied

    `origin_session_id IS NULL` marks reusable world geography that every session of
    that world reads. A non-null value marks canon that gameplay invented inside one
    save, and no other save may see it. There is deliberately no per-session copy of
    template definitions: ten sessions read the same rows and differ only in their
    `location_states`.

    That is also why the leakage rule cannot be a foreign key. Visibility is
    "template, or mine" -- a disjunction no single FK expresses -- so every query that
    loads definitions filters on it, and the spatial gateway is the only place that
    filter is written.

    # Containment is a column, not a table

    One `parent_location_id` gives at most one direct parent for free, which is half
    the tree invariant. The other half -- no cycles -- cannot be a constraint in SQLite
    or PostgreSQL, so it lives in `world_locations.hierarchy.check_parent` and runs
    before every write that sets containment.
    """

    __tablename__ = "location_definitions"
    __table_args__ = (
        # The two reads this table has: everything visible to a session, and the
        # children of a place. Both filter on origin, so it leads the first index.
        Index("ix_location_definitions_world_origin", "world_id", "origin_session_id"),
        Index("ix_location_definitions_parent", "parent_location_id"),
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_location_definitions_importance"),
        CheckConstraint(
            "parent_location_id IS NULL OR parent_location_id <> id",
            name="ck_location_definitions_not_self_parent",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    world_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    origin_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    category: Mapped[LocationCategory] = mapped_column(String(20), nullable=False)
    subtype: Mapped[str | None] = mapped_column(String(60), nullable=True)
    scale: Mapped[LocationScale] = mapped_column(String(20), nullable=False)

    # SET NULL rather than CASCADE: deleting a container must not delete what was
    # inside it. A district that loses its city becomes a root, which is recoverable;
    # a district that vanishes with it takes canon nobody asked to remove.
    parent_location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("location_definitions.id", ondelete="SET NULL"), nullable=True
    )

    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Optional, and never required. Real measurements for the rare world that has
    # them; see `world_locations.definitions.check_spatial_metadata`.
    spatial_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class LocationZone(Base):
    """A named area inside a location. Lighter than a location, on purpose.

    No state, no connections, no children, not a travel destination. A zone exists so a
    scene can say "by the fireplace" without minting a definition -- and a
    `location_states` row in every session -- for a hearth.
    """

    __tablename__ = "location_zones"
    __table_args__ = (
        UniqueConstraint("location_id", "name", name="uq_location_zones_name"),
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_location_zones_importance"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("location_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class LocationConnection(Base):
    """A declared traversal between two places.

    Never inferred. Sharing a parent does not connect two rooms, and a cellar inside a
    tavern is not reachable from it until somebody writes the stairs down.

    Endpoints are `RESTRICT`, unlike the containment link above: a connection with one
    end missing is not a degraded edge but a broken one, and keeping it would leave the
    graph claiming a route to nowhere.
    """

    __tablename__ = "location_connections"
    __table_args__ = (
        # Traversal is looked up from one end at a time, and a bidirectional edge is
        # found from either -- so both endpoints are indexed rather than a composite.
        Index("ix_location_connections_from", "from_location_id"),
        Index("ix_location_connections_to", "to_location_id"),
        Index("ix_location_connections_world_origin", "world_id", "origin_session_id"),
        CheckConstraint(
            "from_location_id <> to_location_id", name="ck_location_connections_distinct_ends"
        ),
        CheckConstraint(
            "base_travel_minutes IS NULL OR base_travel_minutes >= 0",
            name="ck_location_connections_travel_nonnegative",
        ),
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_location_connections_importance"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    world_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    origin_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=True
    )

    from_location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("location_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    to_location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("location_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    # False is honoured, never quietly reversed: a drop shaft, a waterfall and a
    # one-way portal all go somewhere you cannot come back from.
    bidirectional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category: Mapped[ConnectionCategory] = mapped_column(String(20), nullable=False)
    subtype: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # Distance and duration are stored separately and neither is derived from the
    # other. A portal is four thousand kilometres and one minute.
    distance_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    base_travel_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class LocationState(Base):
    """What is currently true about one place, in one session.

    One row per (session, location), enforced -- two rows would be two answers to "is
    the bridge standing?".

    Definitions are shared across saves and this is not, which is the whole reason the
    two tables are separate. Deleting a session takes its states; the geography it was
    played on stays exactly where it was.
    """

    __tablename__ = "location_states"
    __table_args__ = (
        UniqueConstraint("session_id", "location_id", name="uq_location_states_session_location"),
        CheckConstraint(
            "security_level BETWEEN 0 AND 100", name="ck_location_states_security_range"
        ),
        CheckConstraint(
            "local_danger_modifier BETWEEN -100 AND 100", name="ck_location_states_danger_range"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("location_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Two independent axes. `destroyed` + `open` is the ruins somebody can still walk
    # into, and a definition is never deleted for reaching `destroyed`.
    condition: Mapped[LocationCondition] = mapped_column(
        String(20), default=LocationCondition.INTACT, nullable=False
    )
    accessibility: Mapped[LocationAccessibility] = mapped_column(
        String(20), default=LocationAccessibility.OPEN, nullable=False
    )

    security_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Shifts the world's baseline danger rather than replacing it; a per-location copy
    # of the whole danger configuration would be free to drift from the world's rules.
    local_danger_modifier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # No foreign key: an owner may be a character today and a faction when factions
    # exist, and a column that can point at two tables cannot constrain either.
    owner_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    controller_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class LocationConnectionState(Base):
    """Whether one traversal can currently be made, in one session.

    Usually the state that decides whether somewhere is reachable. A castle stays
    `open` after its gate is barred -- what changed is the edge -- so anything asking
    "can I get in?" has to read this table and not only `location_states`.
    """

    __tablename__ = "location_connection_states"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "connection_id", name="uq_connection_states_session_connection"
        ),
        CheckConstraint(
            "traversal_modifier BETWEEN -100 AND 100", name="ck_connection_states_modifier_range"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("location_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )

    condition: Mapped[LocationCondition] = mapped_column(
        String(20), default=LocationCondition.INTACT, nullable=False
    )
    accessibility: Mapped[LocationAccessibility] = mapped_column(
        String(20), default=LocationAccessibility.OPEN, nullable=False
    )
    traversal_modifier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Situation(Base):
    """An ongoing process in one session. See app.domain.world_situations.

    # Session-scoped, with no template tier

    Unlike `location_definitions` there is no shared form of this row. Geography is a
    property of the world and ten saves read the same places; a war is something that
    happened in one of them. A world may *seed* a starting situation, but what gets
    written is a row belonging to that session, and `ON DELETE CASCADE` means deleting
    the save takes its history with it.

    # Concluded rows stay

    Nothing deletes a situation for reaching `resolved` or `cancelled`. That is why the
    table is `situations` and not `active_situations`: "what is going on right now" is a
    query with `status IN (...)`, and a table that held only live rows would have
    nowhere to put the siege that ended last week -- which is most of what makes a
    session worth reading afterwards.

    # The check constraints, and the one that cannot be one

    Bounds, temporal ordering and self-parentage are all expressible here and are
    enforced here, so a hand-edited row cannot produce an intensity of 400 or a siege
    that resolved before it started. Cycles in `parent_situation_id` are not expressible
    in SQLite or PostgreSQL, so that rule lives in
    `world_situations.hierarchy.check_parent_situation` and runs before every write that
    sets the link.
    """

    __tablename__ = "situations"
    __table_args__ = (
        # "What is going on in this session" -- the read every context build makes.
        Index("ix_situations_session_status", "session_id", "status"),
        Index("ix_situations_session_category", "session_id", "category"),
        # "What is happening at this place", for location-driven relevance.
        Index("ix_situations_session_location", "session_id", "primary_location_id"),
        Index("ix_situations_parent", "parent_situation_id"),
        CheckConstraint("intensity BETWEEN 0 AND 100", name="ck_situations_intensity_range"),
        CheckConstraint("threat BETWEEN 0 AND 100", name="ck_situations_threat_range"),
        CheckConstraint("momentum BETWEEN -100 AND 100", name="ck_situations_momentum_range"),
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_situations_importance_range"),
        CheckConstraint("started_at >= 0", name="ck_situations_started_at_nonnegative"),
        CheckConstraint(
            "last_progressed_at >= started_at", name="ck_situations_progress_after_start"
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= started_at",
            name="ck_situations_resolved_after_start",
        ),
        CheckConstraint(
            "parent_situation_id IS NULL OR parent_situation_id <> id",
            name="ck_situations_not_self_parent",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    category: Mapped[SituationCategory] = mapped_column(String(20), nullable=False)
    # The open axis. `conflict/siege`, `project/bridge_reconstruction`. No enum could
    # hold the second half of that across genres, so it is a normalised identifier.
    subtype: Mapped[str | None] = mapped_column(String(60), nullable=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[SituationStatus] = mapped_column(
        String(20), default=SituationStatus.ACTIVE, nullable=False
    )

    # Three independent measures. A festival is intensity 90 / threat 5; a siege is
    # 80 / 90. Collapsing them into one `severity` column is what makes every positive
    # process in the game look like a problem.
    intensity: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    threat: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    momentum: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # A fourth, and also independent: how much prompt budget and simulation attention
    # this deserves. A vast distant storm can be intensity 100, importance 1.
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    scope: Mapped[SituationScope] = mapped_column(
        String(20), default=SituationScope.LOCAL, nullable=False
    )

    # SET NULL rather than CASCADE: a location being deleted must not delete the war
    # that was fought over it. Null is also the honest value for a manhunt, which
    # follows a person rather than a place.
    primary_location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("location_definitions.id", ondelete="SET NULL"), nullable=True
    )
    # Causal only. Resolving the parent does not resolve the child -- see
    # `world_situations.hierarchy` for why that has to stay a separate decision.
    parent_situation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("situations.id", ondelete="SET NULL"), nullable=True
    )

    # Session elapsed minutes throughout, from Time V1. `created_at` below is the row's
    # birthday and carries no simulation meaning; nothing reads a wall clock to decide
    # how long a siege has lasted.
    started_at: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_progressed_at: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    resolved_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # SET NULL for the same reason facts use it: losing the event that explains a
    # situation must not delete the situation. Provenance can decay; the process cannot.
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_events.id", ondelete="SET NULL"), nullable=True
    )

    # Small, flat and scalar-only, validated at the domain boundary. Never a place to
    # keep state another domain owns: a bridge's condition lives in
    # `location_connection_states`, not in a reconstruction project's metadata.
    situation_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class SituationParticipant(Base):
    """Who is taking part in a process, and in what capacity.

    Normalised rather than a JSON array on `situations`, because the query that makes
    this table worth having is the reverse one: "every situation involving this
    character". An array would turn that into a table scan with a JSON predicate, and it
    is the query StoryContext selection and future simulation prioritisation both need.

    # A reference, never a copy

    Nothing here describes what a participant *can do*. No soldiers, no money, no
    influence, no health. Those belong to whatever domain owns the entity, and a copy
    here would be a second answer that drifts from the first.

    # No foreign key on `entity_id`

    It may point at a character today and a faction when factions exist, and a column
    that can address two tables cannot constrain either. `entity_type` says which, and
    the application resolves characters against the world -- factions are accepted on
    trust and that is recorded rather than quietly tolerated.
    """

    __tablename__ = "situation_participants"
    __table_args__ = (
        # One entity, one role, once. The same character may be both `investigator` and
        # `target` -- that is a story -- so the key is the pair, not the entity.
        UniqueConstraint(
            "situation_id",
            "entity_type",
            "entity_id",
            "role",
            name="uq_situation_participants_entity_role",
        ),
        # The reverse lookup this table exists for.
        Index("ix_situation_participants_entity", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    situation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("situations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    entity_type: Mapped[ParticipantEntityType] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # Open vocabulary, normalised like a subtype: `attacker`, `defender`, `investigator`,
    # `target`, `organizer`, `beneficiary`. An enum here would be a list nobody could
    # extend without a migration.
    role: Mapped[str] = mapped_column(String(60), nullable=False)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
