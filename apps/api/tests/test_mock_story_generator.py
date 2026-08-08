"""The mock provider must be deterministic, valid, and language-aware."""

from __future__ import annotations

import uuid

import pytest

from app.application.story_context import (
    CharacterContext,
    PlayerContext,
    SessionContext,
    StoryContext,
    WorldContext,
)
from app.domain.enums import Language
from app.infrastructure.story.mock import MockStoryGenerator

ELENA_ID = uuid.uuid4()
KAEL_ID = uuid.uuid4()


def build_context(action: str, language: Language = Language.EN, turn: int = 0) -> StoryContext:
    return StoryContext(
        world=WorldContext(
            id=uuid.uuid4(),
            name="Test World",
            description="d",
            genre="fantasy",
            setting="the capital",
            language=language,
        ),
        player=PlayerContext(name="Rin", description="a traveller"),
        session=SessionContext(
            id=uuid.uuid4(), title="s", current_location="the market", summary="", turn_index=turn
        ),
        relevant_characters=[
            CharacterContext(
                id=ELENA_ID,
                name="Elena",
                description="mage",
                appearance="",
                personality="sarcastic, cautious",
                backstory="",
                speech_style="dry",
            ),
            CharacterContext(
                id=KAEL_ID,
                name="Kael",
                description="swordsman",
                appearance="",
                personality="reckless",
                backstory="",
                speech_style="loud",
            ),
        ],
        player_action=action,
    )


async def test_produces_valid_turn_with_suggestions() -> None:
    generation = await MockStoryGenerator().generate_turn(build_context("I look around."))
    assert generation.narration
    assert 3 <= len(generation.suggested_actions) <= 4
    assert generation.dialogue


async def test_is_deterministic() -> None:
    generator = MockStoryGenerator()
    first = await generator.generate_turn(build_context("I greet the mage."))
    second = await generator.generate_turn(build_context("I greet the mage."))
    assert first.model_dump() == second.model_dump()


async def test_character_named_in_action_is_the_speaker() -> None:
    generation = await MockStoryGenerator().generate_turn(
        build_context("I ask Kael about the debt.")
    )
    assert generation.dialogue[0].character_id == KAEL_ID
    assert generation.dialogue[0].speaker == "Kael"


async def test_significant_action_creates_durable_memory_and_event() -> None:
    generation = await MockStoryGenerator().generate_turn(
        build_context("I promise Elena I will find the traitor.")
    )
    assert generation.memory_candidates
    assert generation.world_events
    assert 1 <= generation.memory_candidates[0].importance <= 5


async def test_small_talk_creates_no_memory() -> None:
    """Trivial turns must not pollute long-term memory."""
    generation = await MockStoryGenerator().generate_turn(build_context("I look at the sky."))
    assert generation.memory_candidates == []


async def test_friendly_and_hostile_actions_move_relationship_opposite_ways() -> None:
    friendly = await MockStoryGenerator().generate_turn(build_context("I thank Elena."))
    hostile = await MockStoryGenerator().generate_turn(build_context("I insult Elena."))
    assert friendly.relationship_changes[0].trust_delta > 0
    assert hostile.relationship_changes[0].trust_delta < 0


@pytest.mark.parametrize(
    ("language", "needle"), [(Language.EN, "Narrator"), (Language.ES, "El aire")]
)
async def test_narration_follows_world_language(language: Language, needle: str) -> None:
    generation = await MockStoryGenerator().generate_turn(
        build_context("I look around.", language=language)
    )
    haystack = generation.narration + " ".join(generation.suggested_actions)
    if language is Language.ES:
        assert needle in haystack
    else:
        assert "El aire" not in haystack


async def test_spanish_suggestions_are_spanish() -> None:
    generation = await MockStoryGenerator().generate_turn(
        build_context("Miro a mi alrededor.", language=Language.ES)
    )
    assert any("Preguntar" in action for action in generation.suggested_actions)


async def test_status_reports_ready_and_names_itself_a_mock() -> None:
    status = await MockStoryGenerator().status()
    assert status.state == "ready"
    assert "mock" in status.detail.lower()
