"""The turn use case driven entirely through its ports, with no database.

If `execute_turn` can run against a dictionary, the application layer genuinely
does not depend on SQLAlchemy -- which is the claim `test_architecture.py` makes
statically and this file makes behaviourally.
"""

from __future__ import annotations

import uuid

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
    NewMemory,
    NewMessage,
    RelationshipRecord,
    SessionSnapshot,
    TranscriptMessage,
    WorldSnapshot,
)
from app.application.story_context import StoryContext
from app.application.turn_service import execute_turn
from app.domain.enums import Language, MemoryKind
from app.domain.errors import NotFoundError, ValidationError
from app.domain.relationships import RelationshipVector

SESSION_ID = uuid.uuid4()
WORLD_ID = uuid.uuid4()
ELENA_ID = uuid.uuid4()


class FakeTurnGateway:
    """In-memory TurnGatewayPort. Records what the use case asked it to do."""

    def __init__(self, *, player_name: str = "Rin", turn_index: int = 0) -> None:
        self.session = SessionSnapshot(
            id=SESSION_ID,
            world_id=WORLD_ID,
            title="Run",
            player_name=player_name,
            player_description="",
            current_location="a town",
            summary="",
            turn_index=turn_index,
        )
        self.world = WorldSnapshot(
            id=WORLD_ID,
            name="W",
            description="",
            genre="fantasy",
            setting="a town",
            language=Language.EN,
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
        self.memories: list[NewMemory] = []
        self.events: list[NewEvent] = []
        self.relationships: dict[uuid.UUID, RelationshipVector] = {}
        self.turn_index_writes: list[int] = []
        self.commits = 0

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

    # -- writes -----------------------------------------------------------------

    async def add_message(self, message: NewMessage) -> uuid.UUID:
        self.messages.append(message)
        return uuid.uuid4()

    async def add_memory(self, memory: NewMemory) -> None:
        self.memories.append(memory)

    async def add_event(self, event: NewEvent) -> None:
        self.events.append(event)

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
            world_events=[WorldEvent(type="arrival", description="Rin reached the door.")],
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
