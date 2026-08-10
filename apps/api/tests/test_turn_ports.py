"""The turn use case driven entirely through its ports, with no database.

If `execute_turn` can run against a dictionary, the application layer genuinely
does not depend on SQLAlchemy -- which is the claim `test_architecture.py` makes
statically and this file makes behaviourally.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

import pytest

from app.application.contracts import (
    DialogueLine,
    MemoryCandidate,
    RelationshipChange,
    TurnGeneration,
    WorldEvent,
)
from app.application.persistence import (
    CharacterRecord,
    MemoryRecord,
    NewEvent,
    NewFact,
    NewMemory,
    NewMessage,
    NewParticipant,
    NewResolution,
    NewSituation,
    RelationshipRecord,
    SessionSnapshot,
    SituationUpdate,
    StoredMessage,
    TranscriptMessage,
    WorldSnapshot,
)
from app.application.story_context import StoryContext
from app.application.turn_service import execute_turn
from app.domain.enums import Language, MemoryKind
from app.domain.errors import NotFoundError, ValidationError
from app.domain.relationships import RelationshipVector
from app.domain.resolution import (
    EventCategory,
    GameEvent,
    Resolution,
    ResolutionSourceType,
)
from app.domain.world_facts import FactKind, FactSubject, SetFact, WorldFact
from app.domain.world_locations import (
    LocationConnection,
    LocationConnectionState,
    LocationDefinition,
    LocationState,
    LocationZone,
)
from app.domain.world_rules import default_world_rules
from app.domain.world_situations import (
    ParticipantEntityType,
    Situation,
    SituationCategory,
    SituationParticipant,
    SituationScope,
    SituationStatus,
)
from app.domain.world_time import DEFAULT_INITIAL_DATETIME

SESSION_ID = uuid.uuid4()
WORLD_ID = uuid.uuid4()
ELENA_ID = uuid.uuid4()


class FakeTurnGateway:
    """In-memory TurnGatewayPort. Records what the use case asked it to do."""

    def __init__(
        self, *, player_name: str = "Rin", turn_index: int = 0, elapsed_minutes: int = 0
    ) -> None:
        self.session = SessionSnapshot(
            id=SESSION_ID,
            world_id=WORLD_ID,
            title="Run",
            player_name=player_name,
            player_description="",
            current_location="a town",
            summary="",
            turn_index=turn_index,
            elapsed_minutes=elapsed_minutes,
            state_revision=0,
        )
        self.world = WorldSnapshot(
            id=WORLD_ID,
            name="W",
            description="",
            genre="fantasy",
            setting="a town",
            language=Language.EN,
            rules=default_world_rules(),
            initial_datetime=DEFAULT_INITIAL_DATETIME,
        )
        self.characters = [
            CharacterRecord(
                id=ELENA_ID,
                name="Elena",
                description="A mage.",
                appearance="",
                personality="dry",
                backstory="",
                speech_style="",
                goals=[],
                secrets=[],
            )
        ]
        self.messages: list[NewMessage] = []
        self.message_ids: list[uuid.UUID] = []
        self.memories: list[NewMemory] = []
        self.events: list[GameEvent] = []
        self.resolutions: list[Resolution] = []
        self.relationships: dict[uuid.UUID, RelationshipVector] = {}
        self.turn_index_writes: list[int] = []
        self.commits = 0

        # Keyed the way the unique index is keyed, so this fake cannot hold two
        # current values for one subject and property either.
        self.facts: dict[tuple[str, str], WorldFact] = {}
        self.initial_facts: list[SetFact] = []
        self.locations: list[LocationDefinition] = []
        self.situations: list[Situation] = []
        self.participants: list[NewParticipant] = []
        self.state_revision = 0

    # -- reads ------------------------------------------------------------------

    async def load_characters(self, world_id: uuid.UUID, *, limit: int) -> list[CharacterRecord]:
        return self.characters[:limit]

    async def load_recent_messages(
        self, session_id: uuid.UUID, *, limit: int
    ) -> list[TranscriptMessage]:
        staged = [
            TranscriptMessage(
                turn_index=m.turn_index,
                role=m.role,
                speaker_character_id=m.speaker_character_id,
                content=m.content,
            )
            for m in self.messages
        ]
        return staged[-limit:]

    async def load_memories(self, session_id: uuid.UUID, *, limit: int) -> list[MemoryRecord]:
        return []

    async def load_relationships(self, session_id: uuid.UUID) -> list[RelationshipRecord]:
        return [
            RelationshipRecord(
                character_id=cid,
                trust=v.trust,
                affection=v.affection,
                respect=v.respect,
                fear=v.fear,
            )
            for cid, v in self.relationships.items()
        ]

    async def get_session(self, session_id: uuid.UUID) -> SessionSnapshot | None:
        return self.session if session_id == self.session.id else None

    async def get_world(self, world_id: uuid.UUID) -> WorldSnapshot | None:
        return self.world if world_id == self.world.id else None

    async def known_character_ids(self, world_id: uuid.UUID) -> set[uuid.UUID]:
        return {c.id for c in self.characters}

    async def load_facts(
        self,
        session_id: uuid.UUID,
        *,
        subject: FactSubject | None = None,
        kind: FactKind | None = None,
        min_importance: int | None = None,
        limit: int,
    ) -> list[WorldFact]:
        found = [
            fact
            for fact in self.facts.values()
            if (subject is None or fact.subject == subject)
            and (kind is None or fact.kind is kind)
            and (min_importance is None or fact.importance >= min_importance)
        ]
        # The same total order the port's contract demands of a real adapter.
        found.sort(key=lambda f: (-f.importance, -f.current_value_since, f.property))
        return found[:limit]

    async def load_initial_facts(self, world_id: uuid.UUID) -> list[SetFact]:
        return list(self.initial_facts)

    # -- spatial reads ----------------------------------------------------------
    #
    # A world with no geography, which is the ordinary case and the one this file
    # exists to prove still works: a turn must run without a spatial graph, without a
    # database, and without noticing that either is missing.

    async def load_locations(
        self, session_id: uuid.UUID, *, world_id: uuid.UUID, limit: int
    ) -> list[LocationDefinition]:
        return list(self.locations)[:limit]

    async def get_location(
        self, session_id: uuid.UUID, location_id: uuid.UUID
    ) -> LocationDefinition | None:
        return next((place for place in self.locations if place.id == location_id), None)

    async def load_connections(
        self, session_id: uuid.UUID, *, world_id: uuid.UUID, limit: int
    ) -> list[LocationConnection]:
        return []

    async def get_connection(
        self, session_id: uuid.UUID, connection_id: uuid.UUID
    ) -> LocationConnection | None:
        return None

    async def load_zones(self, location_id: uuid.UUID) -> list[LocationZone]:
        return []

    async def load_location_states(self, session_id: uuid.UUID) -> list[LocationState]:
        return []

    async def load_connection_states(self, session_id: uuid.UUID) -> list[LocationConnectionState]:
        return []

    async def get_location_state(
        self, session_id: uuid.UUID, location_id: uuid.UUID
    ) -> LocationState | None:
        return None

    async def get_connection_state(
        self, session_id: uuid.UUID, connection_id: uuid.UUID
    ) -> LocationConnectionState | None:
        return None

    # -- situation reads --------------------------------------------------------
    #
    # A session with nothing under way, which is the ordinary case and the one this
    # file exists to prove still works.

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
        found = [
            situation
            for situation in self.situations
            if (statuses is None or situation.status in statuses)
            and (category is None or situation.category is category)
            and (scope is None or situation.scope is scope)
            and (
                primary_location_id is None or situation.primary_location_id == primary_location_id
            )
        ]
        # The same total order the port's contract demands of a real adapter.
        found.sort(key=lambda s: (-s.importance, -s.last_progressed_at, s.title.casefold()))
        return found[:limit]

    async def get_situation(
        self, session_id: uuid.UUID, situation_id: uuid.UUID
    ) -> Situation | None:
        return next((s for s in self.situations if s.id == situation_id), None)

    async def load_participants(
        self, situation_ids: Sequence[uuid.UUID]
    ) -> list[SituationParticipant]:
        return []

    async def load_situations_for_entity(
        self,
        session_id: uuid.UUID,
        *,
        entity_id: uuid.UUID,
        entity_type: ParticipantEntityType | None = None,
        statuses: frozenset[SituationStatus] | None = None,
        limit: int,
    ) -> list[Situation]:
        return []

    async def get_fact(
        self, session_id: uuid.UUID, subject: FactSubject, canonical_property: str
    ) -> WorldFact | None:
        return self.facts.get((subject.key, canonical_property))

    # -- history and resolution reads -------------------------------------------

    async def load_events(
        self,
        session_id: uuid.UUID,
        *,
        min_importance: int | None = None,
        categories: frozenset[EventCategory] | None = None,
        subtypes: frozenset[str] | None = None,
        since: int | None = None,
        limit: int,
    ) -> list[GameEvent]:
        found = [
            event
            for event in self.events
            if (min_importance is None or event.importance >= min_importance)
            and (categories is None or event.category in categories)
            and (subtypes is None or event.subtype in subtypes)
            and (since is None or event.occurred_at >= since)
        ]
        # The total order the port's contract demands: recent first.
        found.sort(key=lambda e: (-e.occurred_at, -e.sequence))
        return found[:limit]

    async def get_event(self, session_id: uuid.UUID, event_id: uuid.UUID) -> GameEvent | None:
        return next((event for event in self.events if event.id == event_id), None)

    async def load_events_for_resolution(self, resolution_id: uuid.UUID) -> list[GameEvent]:
        found = [event for event in self.events if event.resolution_id == resolution_id]
        found.sort(key=lambda e: (e.occurred_at, e.sequence))
        return found

    async def count_events_since(
        self,
        session_id: uuid.UUID,
        *,
        subtype: str,
        since: int,
        primary_location_id: uuid.UUID | None = None,
    ) -> int:
        return sum(
            1
            for event in self.events
            if event.subtype == subtype
            and event.occurred_at >= since
            and event.primary_location_id == primary_location_id
        )

    async def get_resolution(
        self, session_id: uuid.UUID, resolution_id: uuid.UUID
    ) -> Resolution | None:
        return next((entry for entry in self.resolutions if entry.id == resolution_id), None)

    async def find_resolution_by_key(
        self, session_id: uuid.UUID, idempotency_key: str
    ) -> Resolution | None:
        # Keyed exactly the way the unique index is, so this fake cannot hold two
        # records under one key either.
        return next(
            (
                entry
                for entry in self.resolutions
                if entry.session_id == session_id and entry.idempotency_key == idempotency_key
            ),
            None,
        )

    async def load_resolutions(
        self,
        session_id: uuid.UUID,
        *,
        source_type: ResolutionSourceType | None = None,
        limit: int,
    ) -> list[Resolution]:
        found = [
            entry
            for entry in self.resolutions
            if entry.session_id == session_id
            and (source_type is None or entry.source_type is source_type)
        ]
        found.sort(key=lambda entry: (-entry.occurred_at, -entry.created_at.timestamp()))
        return found[:limit]

    async def load_turn_messages(
        self, session_id: uuid.UUID, turn_index: int
    ) -> list[StoredMessage]:
        return [
            StoredMessage(
                id=message_id,
                turn_index=message.turn_index,
                role=message.role,
                speaker_character_id=message.speaker_character_id,
                content=message.content,
            )
            for message_id, message in zip(self.message_ids, self.messages, strict=True)
            if message.turn_index == turn_index
        ]

    # -- writes -----------------------------------------------------------------

    async def set_fact(self, fact: NewFact) -> uuid.UUID:
        key = (fact.subject.key, fact.property)
        existing = self.facts.get(key)
        now = dt.datetime.now(dt.UTC)
        stored = WorldFact(
            id=existing.id if existing is not None else uuid.uuid4(),
            session_id=fact.session_id,
            kind=fact.kind,
            subject=fact.subject,
            property=fact.property,
            value=fact.value,
            importance=fact.importance,
            current_value_since=fact.current_value_since,
            authority=fact.authority,
            source_event_id=fact.source_event_id,
            tags=fact.tags,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self.facts[key] = stored
        return stored.id

    async def remove_fact(
        self, session_id: uuid.UUID, subject: FactSubject, canonical_property: str
    ) -> bool:
        return self.facts.pop((subject.key, canonical_property), None) is not None

    async def bump_state_revision(self, session_id: uuid.UUID) -> int:
        self.state_revision += 1
        return self.state_revision

    async def add_message(self, message: NewMessage) -> uuid.UUID:
        message_id = uuid.uuid4()
        self.messages.append(message)
        self.message_ids.append(message_id)
        return message_id

    async def add_memory(self, memory: NewMemory) -> None:
        self.memories.append(memory)

    async def add_event(self, event: NewEvent) -> uuid.UUID:
        stored = GameEvent(
            id=uuid.uuid4(),
            session_id=event.session_id,
            resolution_id=event.resolution_id,
            turn_index=event.turn_index,
            category=event.category,
            subtype=event.subtype,
            summary=event.summary,
            occurred_at=event.occurred_at,
            # The adapter assigns this; so does the fake, and for the same reason --
            # `occurred_at` ties are the normal case and something has to break them.
            sequence=len(self.events) + 1,
            importance=event.importance,
            primary_location_id=event.primary_location_id,
            caused_by_event_id=event.caused_by_event_id,
            payload=event.payload,
            created_at=dt.datetime.now(dt.UTC),
        )
        self.events.append(stored)
        return stored.id

    async def add_resolution(self, resolution: NewResolution) -> uuid.UUID:
        stored = Resolution(
            id=uuid.uuid4(),
            session_id=resolution.session_id,
            source_type=resolution.source_type,
            source_id=resolution.source_id,
            parent_resolution_id=resolution.parent_resolution_id,
            idempotency_key=resolution.idempotency_key,
            disposition=resolution.disposition,
            reason_code=resolution.reason_code,
            resolver_name=resolution.resolver_name,
            resolver_version=resolution.resolver_version,
            state_revision_before=resolution.state_revision_before,
            state_revision_after=resolution.state_revision_after,
            occurred_at=resolution.occurred_at,
            turn_index=resolution.turn_index,
            event_count=resolution.event_count,
            mutation_count=resolution.mutation_count,
            created_at=dt.datetime.now(dt.UTC),
        )
        self.resolutions.append(stored)
        return stored.id

    async def get_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID
    ) -> RelationshipRecord | None:
        vector = self.relationships.get(character_id)
        if vector is None:
            return None
        return RelationshipRecord(
            character_id=character_id,
            trust=vector.trust,
            affection=vector.affection,
            respect=vector.respect,
            fear=vector.fear,
        )

    async def save_relationship(
        self, session_id: uuid.UUID, character_id: uuid.UUID, vector: RelationshipVector
    ) -> None:
        self.relationships[character_id] = vector

    async def set_turn_index(self, session_id: uuid.UUID, turn_index: int) -> None:
        self.turn_index_writes.append(turn_index)

    async def add_situation(self, situation: NewSituation) -> uuid.UUID:
        now = dt.datetime.now(dt.UTC)
        stored = Situation(
            id=uuid.uuid4(),
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
            last_progressed_at=situation.started_at,
            source_event_id=situation.source_event_id,
            situation_metadata=situation.situation_metadata,
            tags=situation.tags,
            created_at=now,
            updated_at=now,
        )
        self.situations.append(stored)
        return stored.id

    async def update_situation(self, update: SituationUpdate) -> None:
        for index, current in enumerate(self.situations):
            if current.id != update.situation_id:
                continue
            self.situations[index] = current.model_copy(
                update={
                    "intensity": update.intensity,
                    "threat": update.threat,
                    "momentum": update.momentum,
                    "importance": update.importance,
                    "status": update.status,
                    "last_progressed_at": update.last_progressed_at,
                    "resolved_at": update.resolved_at,
                    "situation_metadata": update.situation_metadata,
                }
            )
            return

    async def add_participant(self, participant: NewParticipant) -> uuid.UUID:
        self.participants.append(participant)
        return uuid.uuid4()

    async def commit(self) -> None:
        self.commits += 1


class RecordingGenerator:
    """Returns a fixed generation and keeps the context it was handed."""

    name = "recording"

    def __init__(self, generation: TurnGeneration) -> None:
        self._generation = generation
        self.seen: StoryContext | None = None

    async def generate_turn(self, context: StoryContext) -> TurnGeneration:
        self.seen = context
        return self._generation

    async def status(self) -> object:  # pragma: no cover - not exercised
        raise NotImplementedError


class ExplodingGenerator:
    name = "exploding"

    async def generate_turn(self, context: StoryContext) -> TurnGeneration:
        raise RuntimeError("provider exploded")

    async def status(self) -> object:  # pragma: no cover - not exercised
        raise NotImplementedError


def _generation(**overrides: object) -> TurnGeneration:
    data: dict[str, object] = {
        "narration": "The door opens.",
        "suggested_actions": ["Go in", "Wait", "Leave"],
    }
    data.update(overrides)
    return TurnGeneration.model_validate(data)


async def test_a_full_turn_runs_with_no_database_at_all() -> None:
    gateway = FakeTurnGateway()
    generator = RecordingGenerator(
        _generation(
            dialogue=[DialogueLine(character_id=ELENA_ID, speaker="Elena", text="You came.")],
            memory_candidates=[
                MemoryCandidate(kind=MemoryKind.FACT, summary="Rin arrived.", importance=3)
            ],
            relationship_changes=[RelationshipChange(character_id=ELENA_ID, trust_delta=2)],
            world_events=[
                WorldEvent(
                    category=EventCategory.ACTION,
                    subtype="arrival",
                    summary="Rin reached the door.",
                )
            ],
        )
    )

    result = await execute_turn(
        gateway, session_id=SESSION_ID, action="I open the door.", generator=generator
    )

    assert result.turn_index == 1
    assert [m.role for m in result.messages] == ["player", "narrator", "character"]
    assert result.memories_created == 1
    assert result.events_created == 1
    assert gateway.turn_index_writes == [1]
    assert gateway.relationships[ELENA_ID].trust == 2


async def test_the_provider_sees_the_players_action_already_staged() -> None:
    """The action is written before generation so it is part of the transcript."""
    gateway = FakeTurnGateway()
    generator = RecordingGenerator(_generation())

    await execute_turn(gateway, session_id=SESSION_ID, action="I knock twice.", generator=generator)

    assert generator.seen is not None
    assert [m.content for m in generator.seen.recent_messages] == ["I knock twice."]
    assert generator.seen.player_action == "I knock twice."


async def test_a_successful_turn_commits_exactly_once() -> None:
    gateway = FakeTurnGateway()

    await execute_turn(
        gateway,
        session_id=SESSION_ID,
        action="I wait.",
        generator=RecordingGenerator(_generation()),
    )

    assert gateway.commits == 1


async def test_a_provider_failure_never_commits() -> None:
    """Atomicity from the application's side: nothing is made durable.

    Undoing the staged writes belongs to whoever owns the transaction scope; what
    this layer must guarantee is that it never told anyone to keep them.
    """
    gateway = FakeTurnGateway()

    with pytest.raises(RuntimeError, match="provider exploded"):
        await execute_turn(
            gateway, session_id=SESSION_ID, action="I try.", generator=ExplodingGenerator()
        )

    assert gateway.commits == 0
    assert gateway.turn_index_writes == []


async def test_dialogue_attributed_to_the_player_is_dropped_before_persistence() -> None:
    gateway = FakeTurnGateway(player_name="Rin")
    generator = RecordingGenerator(
        _generation(
            dialogue=[
                DialogueLine(speaker="Rin", text="I never trust coincidences."),
                DialogueLine(character_id=ELENA_ID, speaker="Elena", text="Neither do I."),
            ]
        )
    )

    await execute_turn(gateway, session_id=SESSION_ID, action="I ask.", generator=generator)

    stored = [m.content for m in gateway.messages]
    assert "I never trust coincidences." not in stored
    assert "Neither do I." in stored


async def test_an_unknown_session_is_reported_before_anything_is_written() -> None:
    gateway = FakeTurnGateway()

    with pytest.raises(NotFoundError):
        await execute_turn(
            gateway,
            session_id=uuid.uuid4(),
            action="I look around.",
            generator=RecordingGenerator(_generation()),
        )

    assert gateway.messages == []
    assert gateway.commits == 0


@pytest.mark.parametrize("action", ["", "   ", "\n\t "])
async def test_an_empty_action_is_rejected_without_touching_the_gateway(action: str) -> None:
    gateway = FakeTurnGateway()

    with pytest.raises(ValidationError):
        await execute_turn(
            gateway,
            session_id=SESSION_ID,
            action=action,
            generator=RecordingGenerator(_generation()),
        )

    assert gateway.messages == []
    assert gateway.commits == 0
