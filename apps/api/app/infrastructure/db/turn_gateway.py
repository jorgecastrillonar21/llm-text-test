"""SQLAlchemy implementation of the application's persistence ports.

One adapter satisfies every port a session request needs, because they all run in
one transaction: the reads that build the context, the writes that record a turn's
outcome, and the clock the time service moves have to see the same uncommitted
state. Splitting it into four objects over one database session would be four names
for the same thing.

Queries and row-to-DTO mapping live here. Retrieval *policy* -- how many
messages, how many memories, what gets into StoryContext -- stays in the
application layer; this module only honours the ordering each port documents.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute

from app.application.persistence import (
    CharacterRecord,
    ConnectionStateWrite,
    LocationStateWrite,
    MemoryRecord,
    NewConnection,
    NewEvent,
    NewFact,
    NewLocation,
    NewMemory,
    NewMessage,
    NewParticipant,
    NewScheduledEvent,
    NewSituation,
    NewZone,
    RelationshipRecord,
    ScheduledEventRecord,
    SessionSnapshot,
    SituationUpdate,
    TranscriptMessage,
    WorldSnapshot,
)
from app.domain.errors import NotFoundError
from app.domain.relationships import RelationshipVector
from app.domain.world_facts import FactKind, FactSubject, SetFact, WorldFact
from app.domain.world_locations import (
    LocationConnection,
    LocationConnectionState,
    LocationDefinition,
    LocationState,
    LocationZone,
    PhysicalDistance,
)
from app.domain.world_rules import parse_world_rules
from app.domain.world_situations import (
    ParticipantEntityType,
    Situation,
    SituationCategory,
    SituationParticipant,
    SituationScope,
    SituationStatus,
    StartSituation,
)
from app.domain.world_time import FictionalDateTime, ScheduledEventStatus
from app.infrastructure.db import models


def _to_world_fact(row: models.WorldFact) -> WorldFact:
    """A stored row, validated back into a domain fact.

    Validated on the way out, every read, for the same reason `rules_json` is: a
    hand-edited or half-migrated row raises here rather than reaching a language model
    as a truth nobody wrote.
    """
    return WorldFact(
        id=row.id,
        session_id=row.session_id,
        kind=row.kind,
        subject=FactSubject(type=row.subject_type, id=row.subject_id),
        property=row.property,
        value=row.value,
        importance=row.importance,
        current_value_since=row.current_value_since,
        authority=row.authority,
        source_event_id=row.source_event_id,
        tags=tuple(row.tags or ()),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _subject_criteria(subject: FactSubject) -> list[ColumnElement[bool]]:
    """Match one subject, including the world's null id.

    `.is_(None)` rather than `== None`: the equality form renders `= NULL`, which is
    never true, and would silently return no rows for every world-scoped fact.
    """
    criteria: list[ColumnElement[bool]] = [models.WorldFact.subject_type == subject.type]
    if subject.id is None:
        criteria.append(models.WorldFact.subject_id.is_(None))
    else:
        criteria.append(models.WorldFact.subject_id == subject.id)
    return criteria


def _to_location(row: models.LocationDefinition) -> LocationDefinition:
    return LocationDefinition(
        id=row.id,
        world_id=row.world_id,
        origin_session_id=row.origin_session_id,
        name=row.name,
        description=row.description,
        category=row.category,
        subtype=row.subtype,
        scale=row.scale,
        parent_location_id=row.parent_location_id,
        importance=row.importance,
        tags=tuple(row.tags or ()),
        spatial_metadata=dict(row.spatial_metadata or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_connection(row: models.LocationConnection) -> LocationConnection:
    # Stored as two nullable columns and rebuilt as one value object, because a number
    # with no unit is not a distance. The model refuses the half-populated pair; the
    # columns cannot, so the reassembly is where that invariant is restored.
    distance = (
        PhysicalDistance(value=row.distance_value, unit=row.distance_unit)
        if row.distance_value is not None and row.distance_unit is not None
        else None
    )
    return LocationConnection(
        id=row.id,
        world_id=row.world_id,
        origin_session_id=row.origin_session_id,
        from_location_id=row.from_location_id,
        to_location_id=row.to_location_id,
        bidirectional=row.bidirectional,
        category=row.category,
        subtype=row.subtype,
        physical_distance=distance,
        base_travel_minutes=row.base_travel_minutes,
        importance=row.importance,
        tags=tuple(row.tags or ()),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_zone(row: models.LocationZone) -> LocationZone:
    return LocationZone(
        id=row.id,
        location_id=row.location_id,
        name=row.name,
        category=row.category,
        description=row.description,
        importance=row.importance,
        tags=tuple(row.tags or ()),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_location_state(row: models.LocationState) -> LocationState:
    return LocationState(
        id=row.id,
        session_id=row.session_id,
        location_id=row.location_id,
        condition=row.condition,
        accessibility=row.accessibility,
        security_level=row.security_level,
        local_danger_modifier=row.local_danger_modifier,
        owner_entity_id=row.owner_entity_id,
        controller_entity_id=row.controller_entity_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_connection_state(row: models.LocationConnectionState) -> LocationConnectionState:
    return LocationConnectionState(
        id=row.id,
        session_id=row.session_id,
        connection_id=row.connection_id,
        condition=row.condition,
        accessibility=row.accessibility,
        traversal_modifier=row.traversal_modifier,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _visible_to(
    session_id: uuid.UUID | None, column: InstrumentedAttribute[uuid.UUID | None]
) -> ColumnElement[bool]:
    """Template rows, plus this session's own. `None` asks for the template alone.

    Written once and reused by every spatial query, because it is the leakage rule: an
    adapter that forgets it on one query hands another save's geography to this one and
    nothing anywhere reports a problem.

    The `None` case is spelled out rather than left to fall out of `column == None`,
    which SQL renders as `= NULL` and is never true. It would happen to give the right
    answer here, for the wrong reason, and the next person to read it would have to
    work that out.
    """
    if session_id is None:
        return column.is_(None)
    return or_(column.is_(None), column == session_id)


def _to_situation(row: models.Situation) -> Situation:
    """A stored row, validated back into a domain situation.

    Validated on the way out, every read, like `_to_world_fact` and `_to_location`: a
    hand-edited row with intensity 400 or a `resolved` status and no `resolved_at`
    raises here rather than reaching a prompt or a resolver.
    """
    return Situation(
        id=row.id,
        session_id=row.session_id,
        category=row.category,
        subtype=row.subtype,
        title=row.title,
        description=row.description,
        status=row.status,
        intensity=row.intensity,
        threat=row.threat,
        momentum=row.momentum,
        importance=row.importance,
        scope=row.scope,
        primary_location_id=row.primary_location_id,
        parent_situation_id=row.parent_situation_id,
        started_at=row.started_at,
        last_progressed_at=row.last_progressed_at,
        resolved_at=row.resolved_at,
        source_event_id=row.source_event_id,
        situation_metadata=row.situation_metadata,
        tags=tuple(row.tags),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_participant(row: models.SituationParticipant) -> SituationParticipant:
    return SituationParticipant(
        id=row.id,
        situation_id=row.situation_id,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        role=row.role,
        created_at=row.created_at,
    )


def _ordered_situations(query: Select[tuple[models.Situation]]) -> Select[tuple[models.Situation]]:
    """The one ordering every situation read uses.

    Important first, so a truncating caller keeps what matters. Most recently progressed
    next, so a live siege outranks a stale one at the same importance. Title last, so
    the order is *total* -- this feeds a prompt, and a set that reshuffles between two
    reads of an unchanged session is a prompt that will not cache.
    """
    return query.order_by(
        models.Situation.importance.desc(),
        models.Situation.last_progressed_at.desc(),
        models.Situation.title.asc(),
    )


def _to_scheduled_event(row: models.ScheduledEvent) -> ScheduledEventRecord:
    return ScheduledEventRecord(
        id=row.id,
        session_id=row.session_id,
        due_at=row.due_at,
        type=row.type,
        payload=dict(row.payload or {}),
        status=row.status,
        interrupt_player_action=row.interrupt_player_action,
    )


class SqlAlchemyTurnGateway:
    """Implements StoryContextReaderPort, TurnPersistencePort, TurnUnitOfWorkPort,
    SessionClockPort, WorldStatePort and SpatialPort.

    Named for the turn because that is the transaction it was shaped around. The
    session clock, the fact store and the spatial graph joined later and run inside the
    same one, which is why they are here rather than in adapters that would have to
    re-map the same rows and could not see each other's uncommitted writes.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    # -- StoryContextReaderPort -------------------------------------------------

    async def load_characters(self, world_id: uuid.UUID, *, limit: int) -> list[CharacterRecord]:
        rows = (
            await self._db.execute(
                select(models.Character)
                .where(models.Character.world_id == world_id)
                .order_by(models.Character.created_at)
                .limit(limit)
            )
        ).scalars()
        return [
            CharacterRecord(
                id=row.id,
                name=row.name,
                description=row.description,
                appearance=row.appearance,
                personality=row.personality,
                backstory=row.backstory,
                speech_style=row.speech_style,
                goals=list(row.goals or []),
                secrets=list(row.secrets or []),
            )
            for row in rows
        ]

    async def load_recent_messages(
        self, session_id: uuid.UUID, *, limit: int
    ) -> list[TranscriptMessage]:
        rows = (
            (
                await self._db.execute(
                    select(models.Message)
                    .where(models.Message.session_id == session_id)
                    .order_by(models.Message.turn_index.desc(), models.Message.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        # Newest N selected, then reversed: the caller wants oldest-first.
        return [
            TranscriptMessage(
                turn_index=row.turn_index,
                role=row.role,
                speaker_character_id=row.speaker_character_id,
                content=row.content,
            )
            for row in reversed(rows)
        ]

    async def load_memories(self, session_id: uuid.UUID, *, limit: int) -> list[MemoryRecord]:
        rows = (
            await self._db.execute(
                select(models.Memory)
                .where(models.Memory.session_id == session_id)
                .order_by(models.Memory.importance.desc(), models.Memory.created_at.desc())
                .limit(limit)
            )
        ).scalars()
        return [
            MemoryRecord(
                kind=row.kind,
                summary=row.summary,
                importance=row.importance,
                character_id=row.character_id,
            )
            for row in rows
        ]

    async def load_relationships(self, session_id: uuid.UUID) -> list[RelationshipRecord]:
        rows = (
            await self._db.execute(
                select(models.Relationship).where(models.Relationship.session_id == session_id)
            )
        ).scalars()
        return [
            RelationshipRecord(
                character_id=row.character_id,
                trust=row.trust,
                affection=row.affection,
                respect=row.respect,
                fear=row.fear,
            )
            for row in rows
        ]

    # -- TurnPersistencePort ----------------------------------------------------

    async def get_session(self, session_id: uuid.UUID) -> SessionSnapshot | None:
        row = await self._db.get(models.GameSession, session_id)
        if row is None:
            return None
        return SessionSnapshot(
            id=row.id,
            world_id=row.world_id,
            title=row.title,
            player_name=row.player_name,
            player_description=row.player_description,
            current_location=row.current_location,
            summary=row.summary,
            turn_index=row.turn_index,
            elapsed_minutes=row.elapsed_minutes,
            state_revision=row.state_revision,
        )

    async def get_world(self, world_id: uuid.UUID) -> WorldSnapshot | None:
        row = await self._db.get(models.World, world_id)
        if row is None:
            return None
        return WorldSnapshot(
            id=row.id,
            name=row.name,
            description=row.description,
            genre=row.genre,
            setting=row.setting,
            language=row.language,
            # Validated on the way out, every read. A hand-edited or half-migrated
            # rules_json raises here rather than reaching the Story Director as a
            # document nobody wrote.
            rules=parse_world_rules(row.rules_json),
            initial_datetime=FictionalDateTime.model_validate(row.initial_datetime),
        )

    async def known_character_ids(self, world_id: uuid.UUID) -> set[uuid.UUID]:
        rows = await self._db.execute(
            select(models.Character.id).where(models.Character.world_id == world_id)
        )
        return set(rows.scalars())

    async def add_message(self, message: NewMessage) -> uuid.UUID:
        row = models.Message(
            session_id=message.session_id,
            turn_index=message.turn_index,
            role=message.role,
            speaker_character_id=message.speaker_character_id,
            content=message.content,
        )
        self._db.add(row)
        # Flush, never commit: the staged player action has to be readable by the
        # context queries below while a failed turn still rolls the whole thing back.
        await self._db.flush()
        return row.id

    async def add_memory(self, memory: NewMemory) -> None:
        self._db.add(
            models.Memory(
                session_id=memory.session_id,
                character_id=memory.character_id,
                kind=memory.kind,
                summary=memory.summary,
                importance=memory.importance,
            )
        )

    async def add_event(self, event: NewEvent) -> uuid.UUID:
        row = models.GameEvent(
            session_id=event.session_id,
            turn_index=event.turn_index,
            occurred_at=event.occurred_at,
            event_sequence=await self._next_event_sequence(event.session_id),
            type=event.type,
            description=event.description,
        )
        self._db.add(row)
        # Flushed immediately so the next event in this turn sees this one when it
        # asks for the next number. Without it a turn's events would all be handed
        # the same sequence and the unique constraint would fail the whole turn.
        # The flush is also what makes the id below real rather than pending.
        await self._db.flush()
        return row.id

    async def get_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID
    ) -> RelationshipRecord | None:
        row = await self._find_relationship(session_id, character_id)
        if row is None:
            return None
        return RelationshipRecord(
            character_id=row.character_id,
            trust=row.trust,
            affection=row.affection,
            respect=row.respect,
            fear=row.fear,
        )

    async def save_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID, vector: RelationshipVector
    ) -> None:
        row = await self._find_relationship(session_id, character_id)
        if row is None:
            row = models.Relationship(session_id=session_id, character_id=character_id)
            self._db.add(row)
        row.trust = vector.trust
        row.affection = vector.affection
        row.respect = vector.respect
        row.fear = vector.fear
        await self._db.flush()

    async def set_turn_index(self, session_id: uuid.UUID, turn_index: int) -> None:
        row = await self._db.get(models.GameSession, session_id)
        if row is None:  # pragma: no cover - the caller loaded it moments earlier
            raise LookupError(f"GameSession {session_id} vanished mid-turn")
        row.turn_index = turn_index
        await self._db.flush()

    # -- SessionClockPort -------------------------------------------------------

    async def set_elapsed_minutes(self, session_id: uuid.UUID, elapsed_minutes: int) -> None:
        row = await self._db.get(models.GameSession, session_id)
        if row is None:  # pragma: no cover - the caller loaded it moments earlier
            raise LookupError(f"GameSession {session_id} vanished mid-advance")
        row.elapsed_minutes = elapsed_minutes
        await self._db.flush()

    async def add_scheduled_event(self, event: NewScheduledEvent) -> uuid.UUID:
        row = models.ScheduledEvent(
            session_id=event.session_id,
            due_at=event.due_at,
            type=event.type,
            payload=dict(event.payload),
            status=ScheduledEventStatus.PENDING,
            interrupt_player_action=event.interrupt_player_action,
        )
        self._db.add(row)
        await self._db.flush()
        return row.id

    async def get_scheduled_event(self, event_id: uuid.UUID) -> ScheduledEventRecord | None:
        row = await self._db.get(models.ScheduledEvent, event_id)
        return None if row is None else _to_scheduled_event(row)

    async def load_due_scheduled_events(
        self, session_id: uuid.UUID, *, through: int
    ) -> list[ScheduledEventRecord]:
        rows = (
            await self._db.execute(
                select(models.ScheduledEvent)
                .where(
                    models.ScheduledEvent.session_id == session_id,
                    models.ScheduledEvent.status == ScheduledEventStatus.PENDING,
                    models.ScheduledEvent.due_at <= through,
                )
                # Chronological, then by insertion, so two events due in the same
                # fictional minute always resolve in the order they were scheduled.
                .order_by(models.ScheduledEvent.due_at, models.ScheduledEvent.created_at)
            )
        ).scalars()
        return [_to_scheduled_event(row) for row in rows]

    async def set_scheduled_event_status(
        self, event_id: uuid.UUID, status: ScheduledEventStatus
    ) -> None:
        row = await self._db.get(models.ScheduledEvent, event_id)
        if row is None:  # pragma: no cover - the caller loaded it moments earlier
            raise LookupError(f"ScheduledEvent {event_id} vanished mid-advance")
        row.status = status
        await self._db.flush()

    # -- WorldStatePort ---------------------------------------------------------

    async def load_initial_facts(self, world_id: uuid.UUID) -> list[SetFact]:
        row = await self._db.get(models.World, world_id)
        if row is None:
            return []
        # Validated on the way out, like rules_json. A template written by hand or by
        # an older build fails here rather than materialising a fact nobody wrote.
        return [SetFact.model_validate(document) for document in row.initial_facts or []]

    async def load_facts(
        self,
        session_id: uuid.UUID,
        *,
        subject: FactSubject | None = None,
        kind: FactKind | None = None,
        min_importance: int | None = None,
        limit: int,
    ) -> list[WorldFact]:
        query = select(models.WorldFact).where(models.WorldFact.session_id == session_id)
        if subject is not None:
            query = query.where(*_subject_criteria(subject))
        if kind is not None:
            query = query.where(models.WorldFact.kind == kind)
        if min_importance is not None:
            query = query.where(models.WorldFact.importance >= min_importance)
        rows = (
            await self._db.execute(
                query.order_by(
                    models.WorldFact.importance.desc(),
                    models.WorldFact.current_value_since.desc(),
                    # Total order, so equally important facts do not shuffle between
                    # two reads of an unchanged session. See the port's contract.
                    models.WorldFact.property.asc(),
                ).limit(limit)
            )
        ).scalars()
        return [_to_world_fact(row) for row in rows]

    async def get_fact(
        self, session_id: uuid.UUID, subject: FactSubject, canonical_property: str
    ) -> WorldFact | None:
        row = await self._find_fact(session_id, subject, canonical_property)
        return None if row is None else _to_world_fact(row)

    async def set_fact(self, fact: NewFact) -> uuid.UUID:
        row = await self._find_fact(fact.session_id, fact.subject, fact.property)
        if row is None:
            row = models.WorldFact(
                session_id=fact.session_id,
                subject_type=fact.subject.type,
                subject_id=fact.subject.id,
                property=fact.property,
            )
            self._db.add(row)
        # Updated in place on the existing row: the fact's identity is its subject and
        # property, so a new value is the same fact with a different value. Replacing
        # the row would break every source_event_id pointing at it and would make
        # created_at mean "since the last edit".
        row.kind = fact.kind
        row.value = fact.value
        row.importance = fact.importance
        row.current_value_since = fact.current_value_since
        row.authority = fact.authority
        row.source_event_id = fact.source_event_id
        row.tags = list(fact.tags)
        await self._db.flush()
        return row.id

    async def remove_fact(
        self, session_id: uuid.UUID, subject: FactSubject, canonical_property: str
    ) -> bool:
        row = await self._find_fact(session_id, subject, canonical_property)
        if row is None:
            return False
        await self._db.delete(row)
        await self._db.flush()
        return True

    async def bump_state_revision(self, session_id: uuid.UUID) -> int:
        row = await self._db.get(models.GameSession, session_id)
        if row is None:  # pragma: no cover - the caller loaded it moments earlier
            raise LookupError(f"GameSession {session_id} vanished mid-mutation")
        row.state_revision += 1
        await self._db.flush()
        return row.state_revision

    # -- SpatialPort ------------------------------------------------------------

    async def load_locations(
        self, session_id: uuid.UUID | None, *, world_id: uuid.UUID, limit: int
    ) -> list[LocationDefinition]:
        rows = (
            await self._db.execute(
                select(models.LocationDefinition)
                .where(
                    models.LocationDefinition.world_id == world_id,
                    _visible_to(session_id, models.LocationDefinition.origin_session_id),
                )
                # Important first so a truncating caller keeps what matters; name last
                # so the order is total and two reads of one world agree.
                .order_by(
                    models.LocationDefinition.importance.desc(),
                    models.LocationDefinition.name.asc(),
                )
                .limit(limit)
            )
        ).scalars()
        return [_to_location(row) for row in rows]

    async def get_location(
        self, session_id: uuid.UUID | None, location_id: uuid.UUID
    ) -> LocationDefinition | None:
        row = (
            await self._db.execute(
                select(models.LocationDefinition).where(
                    models.LocationDefinition.id == location_id,
                    _visible_to(session_id, models.LocationDefinition.origin_session_id),
                )
            )
        ).scalar_one_or_none()
        return None if row is None else _to_location(row)

    async def load_connections(
        self, session_id: uuid.UUID | None, *, world_id: uuid.UUID, limit: int
    ) -> list[LocationConnection]:
        rows = (
            await self._db.execute(
                select(models.LocationConnection)
                .where(
                    models.LocationConnection.world_id == world_id,
                    _visible_to(session_id, models.LocationConnection.origin_session_id),
                )
                .order_by(
                    models.LocationConnection.importance.desc(),
                    models.LocationConnection.id.asc(),
                )
                .limit(limit)
            )
        ).scalars()
        return [_to_connection(row) for row in rows]

    async def get_connection(
        self, session_id: uuid.UUID | None, connection_id: uuid.UUID
    ) -> LocationConnection | None:
        row = (
            await self._db.execute(
                select(models.LocationConnection).where(
                    models.LocationConnection.id == connection_id,
                    _visible_to(session_id, models.LocationConnection.origin_session_id),
                )
            )
        ).scalar_one_or_none()
        return None if row is None else _to_connection(row)

    async def load_zones(self, location_id: uuid.UUID) -> list[LocationZone]:
        rows = (
            await self._db.execute(
                select(models.LocationZone)
                .where(models.LocationZone.location_id == location_id)
                .order_by(
                    models.LocationZone.importance.desc(),
                    models.LocationZone.name.asc(),
                )
            )
        ).scalars()
        return [_to_zone(row) for row in rows]

    async def load_location_states(self, session_id: uuid.UUID) -> list[LocationState]:
        rows = (
            await self._db.execute(
                select(models.LocationState).where(models.LocationState.session_id == session_id)
            )
        ).scalars()
        return [_to_location_state(row) for row in rows]

    async def load_connection_states(self, session_id: uuid.UUID) -> list[LocationConnectionState]:
        rows = (
            await self._db.execute(
                select(models.LocationConnectionState).where(
                    models.LocationConnectionState.session_id == session_id
                )
            )
        ).scalars()
        return [_to_connection_state(row) for row in rows]

    async def get_location_state(
        self, session_id: uuid.UUID, location_id: uuid.UUID
    ) -> LocationState | None:
        row = await self._find_location_state(session_id, location_id)
        return None if row is None else _to_location_state(row)

    async def get_connection_state(
        self, session_id: uuid.UUID, connection_id: uuid.UUID
    ) -> LocationConnectionState | None:
        row = await self._find_connection_state(session_id, connection_id)
        return None if row is None else _to_connection_state(row)

    async def add_location(self, location: NewLocation) -> uuid.UUID:
        row = models.LocationDefinition(
            world_id=location.world_id,
            origin_session_id=location.origin_session_id,
            name=location.name,
            description=location.description,
            category=location.category,
            subtype=location.subtype,
            scale=location.scale,
            parent_location_id=location.parent_location_id,
            importance=location.importance,
            tags=list(location.tags),
            spatial_metadata=dict(location.spatial_metadata),
        )
        self._db.add(row)
        # Flushed so the id is real and so a location created mid-turn is visible to
        # the containment checks of anything created after it in the same turn.
        await self._db.flush()
        return row.id

    async def add_connection(self, connection: NewConnection) -> uuid.UUID:
        distance = connection.physical_distance
        row = models.LocationConnection(
            world_id=connection.world_id,
            origin_session_id=connection.origin_session_id,
            from_location_id=connection.from_location_id,
            to_location_id=connection.to_location_id,
            bidirectional=connection.bidirectional,
            category=connection.category,
            subtype=connection.subtype,
            distance_value=None if distance is None else distance.value,
            distance_unit=None if distance is None else distance.unit,
            base_travel_minutes=connection.base_travel_minutes,
            importance=connection.importance,
            tags=list(connection.tags),
        )
        self._db.add(row)
        await self._db.flush()
        return row.id

    async def add_zone(self, zone: NewZone) -> uuid.UUID:
        row = models.LocationZone(
            location_id=zone.location_id,
            name=zone.name,
            category=zone.category,
            description=zone.description,
            importance=zone.importance,
            tags=list(zone.tags),
        )
        self._db.add(row)
        await self._db.flush()
        return row.id

    async def set_location_state(self, state: LocationStateWrite) -> uuid.UUID:
        row = await self._find_location_state(state.session_id, state.location_id)
        if row is None:
            row = models.LocationState(session_id=state.session_id, location_id=state.location_id)
            self._db.add(row)
        # Updated in place on the existing row, like `set_fact`: the state's identity
        # is the session and the place, so a new condition is the same state with a
        # different condition. Replacing the row would reset `created_at` to mean
        # "since the last edit".
        row.condition = state.condition
        row.accessibility = state.accessibility
        row.security_level = state.security_level
        row.local_danger_modifier = state.local_danger_modifier
        row.owner_entity_id = state.owner_entity_id
        row.controller_entity_id = state.controller_entity_id
        await self._db.flush()
        return row.id

    async def set_connection_state(self, state: ConnectionStateWrite) -> uuid.UUID:
        row = await self._find_connection_state(state.session_id, state.connection_id)
        if row is None:
            row = models.LocationConnectionState(
                session_id=state.session_id, connection_id=state.connection_id
            )
            self._db.add(row)
        row.condition = state.condition
        row.accessibility = state.accessibility
        row.traversal_modifier = state.traversal_modifier
        await self._db.flush()
        return row.id

    # -- SituationPort ----------------------------------------------------------

    async def load_initial_situations(self, world_id: uuid.UUID) -> list[StartSituation]:
        row = await self._db.get(models.World, world_id)
        if row is None:
            return []
        # Validated on the way out, like `initial_facts` and `rules_json`. A template
        # written by hand or by an older build fails here rather than materialising a
        # war nobody declared.
        return [
            StartSituation.model_validate(document) for document in row.initial_situations or []
        ]

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
        query = select(models.Situation).where(models.Situation.session_id == session_id)
        if statuses is not None:
            # An empty set is a caller asking for nothing, and returning everything
            # would be the most dangerous possible reading of that.
            query = query.where(models.Situation.status.in_(sorted(statuses)))
        if category is not None:
            query = query.where(models.Situation.category == category)
        if scope is not None:
            query = query.where(models.Situation.scope == scope)
        if primary_location_id is not None:
            query = query.where(models.Situation.primary_location_id == primary_location_id)
        rows = (await self._db.execute(_ordered_situations(query).limit(limit))).scalars()
        return [_to_situation(row) for row in rows]

    async def get_situation(
        self, session_id: uuid.UUID, situation_id: uuid.UUID
    ) -> Situation | None:
        row = await self._find_situation(session_id, situation_id)
        return None if row is None else _to_situation(row)

    async def load_participants(
        self, situation_ids: Sequence[uuid.UUID]
    ) -> list[SituationParticipant]:
        if not situation_ids:
            # `IN ()` is a query that can only return nothing; not making it is cheaper
            # and, on some backends, the difference between valid and invalid SQL.
            return []
        rows = (
            await self._db.execute(
                select(models.SituationParticipant)
                .where(models.SituationParticipant.situation_id.in_(list(situation_ids)))
                .order_by(
                    models.SituationParticipant.situation_id.asc(),
                    models.SituationParticipant.role.asc(),
                    models.SituationParticipant.entity_id.asc(),
                )
            )
        ).scalars()
        return [_to_participant(row) for row in rows]

    async def load_situations_for_entity(
        self,
        session_id: uuid.UUID,
        *,
        entity_id: uuid.UUID,
        entity_type: ParticipantEntityType | None = None,
        statuses: frozenset[SituationStatus] | None = None,
        limit: int,
    ) -> list[Situation]:
        participation = select(models.SituationParticipant.situation_id).where(
            models.SituationParticipant.entity_id == entity_id
        )
        if entity_type is not None:
            participation = participation.where(
                models.SituationParticipant.entity_type == entity_type
            )

        query = select(models.Situation).where(
            models.Situation.session_id == session_id,
            models.Situation.id.in_(participation),
        )
        if statuses is not None:
            query = query.where(models.Situation.status.in_(sorted(statuses)))
        rows = (await self._db.execute(_ordered_situations(query).limit(limit))).scalars()
        return [_to_situation(row) for row in rows]

    async def add_situation(self, situation: NewSituation) -> uuid.UUID:
        row = models.Situation(
            session_id=situation.session_id,
            category=situation.category,
            subtype=situation.subtype,
            title=situation.title,
            description=situation.description,
            status=situation.status,
            intensity=situation.intensity,
            threat=situation.threat,
            momentum=situation.momentum,
            importance=situation.importance,
            scope=situation.scope,
            primary_location_id=situation.primary_location_id,
            parent_situation_id=situation.parent_situation_id,
            started_at=situation.started_at,
            # A brand-new process has just been looked at, by definition. Starting this
            # at zero would make every situation's first interval run from the beginning
            # of the session.
            last_progressed_at=situation.started_at,
            resolved_at=None,
            source_event_id=situation.source_event_id,
            situation_metadata=dict(situation.situation_metadata),
            tags=list(situation.tags),
        )
        self._db.add(row)
        # Flushed so the id is real, and so a situation started mid-turn is visible to
        # the cycle checks of anything created after it in the same turn.
        await self._db.flush()
        return row.id

    async def update_situation(self, update: SituationUpdate) -> None:
        row = (
            await self._db.execute(
                select(models.Situation).where(models.Situation.id == update.situation_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Situation", update.situation_id)
        # Updated in place, like `set_fact` and `set_location_state`: a situation's
        # identity is the process, and a new intensity is the same process with a
        # different intensity. Replacing the row would orphan its participants.
        row.intensity = update.intensity
        row.threat = update.threat
        row.momentum = update.momentum
        row.importance = update.importance
        row.status = update.status
        row.last_progressed_at = update.last_progressed_at
        row.resolved_at = update.resolved_at
        row.situation_metadata = dict(update.situation_metadata)
        await self._db.flush()

    async def add_participant(self, participant: NewParticipant) -> uuid.UUID:
        existing = (
            await self._db.execute(
                select(models.SituationParticipant).where(
                    models.SituationParticipant.situation_id == participant.situation_id,
                    models.SituationParticipant.entity_type == participant.entity_type,
                    models.SituationParticipant.entity_id == participant.entity_id,
                    models.SituationParticipant.role == participant.role,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Idempotent rather than an integrity error. A caller re-stating a known
            # participant has not done anything wrong, and the unique constraint is
            # there to keep the data honest, not to punish a second mention.
            return existing.id

        row = models.SituationParticipant(
            situation_id=participant.situation_id,
            entity_type=participant.entity_type,
            entity_id=participant.entity_id,
            role=participant.role,
        )
        self._db.add(row)
        await self._db.flush()
        return row.id

    # -- TurnUnitOfWorkPort -----------------------------------------------------

    async def commit(self) -> None:
        await self._db.commit()

    # -- internals --------------------------------------------------------------

    async def _next_event_sequence(self, session_id: uuid.UUID) -> int:
        """One past the highest sequence this session has used.

        Derived rather than kept in a counter column, so there is no second number
        that can disagree with the rows themselves. The unique constraint on
        (session_id, event_sequence) turns any race into a failed transaction rather
        than two events quietly claiming the same position.
        """
        highest = (
            await self._db.execute(
                select(func.max(models.GameEvent.event_sequence)).where(
                    models.GameEvent.session_id == session_id
                )
            )
        ).scalar()
        return (highest or 0) + 1

    async def _find_fact(
        self, session_id: uuid.UUID, subject: FactSubject, canonical_property: str
    ) -> models.WorldFact | None:
        return (
            await self._db.execute(
                select(models.WorldFact).where(
                    models.WorldFact.session_id == session_id,
                    models.WorldFact.property == canonical_property,
                    *_subject_criteria(subject),
                )
            )
        ).scalar_one_or_none()

    async def _find_location_state(
        self, session_id: uuid.UUID, location_id: uuid.UUID
    ) -> models.LocationState | None:
        return (
            await self._db.execute(
                select(models.LocationState).where(
                    models.LocationState.session_id == session_id,
                    models.LocationState.location_id == location_id,
                )
            )
        ).scalar_one_or_none()

    async def _find_connection_state(
        self, session_id: uuid.UUID, connection_id: uuid.UUID
    ) -> models.LocationConnectionState | None:
        return (
            await self._db.execute(
                select(models.LocationConnectionState).where(
                    models.LocationConnectionState.session_id == session_id,
                    models.LocationConnectionState.connection_id == connection_id,
                )
            )
        ).scalar_one_or_none()

    async def _find_situation(
        self, session_id: uuid.UUID, situation_id: uuid.UUID
    ) -> models.Situation | None:
        """Scoped to the session on purpose: a situation belonging to another save must
        be indistinguishable from one that does not exist."""
        return (
            await self._db.execute(
                select(models.Situation).where(
                    models.Situation.id == situation_id,
                    models.Situation.session_id == session_id,
                )
            )
        ).scalar_one_or_none()

    async def _find_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID
    ) -> models.Relationship | None:
        return (
            await self._db.execute(
                select(models.Relationship).where(
                    models.Relationship.session_id == session_id,
                    models.Relationship.character_id == character_id,
                )
            )
        ).scalar_one_or_none()
