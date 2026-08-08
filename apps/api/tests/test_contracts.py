"""TurnGeneration validation: the boundary that untrusted model output must cross."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.application.contracts import (
    MemoryCandidate,
    RelationshipChange,
    TurnGeneration,
)
from app.domain.enums import MemoryKind


def test_minimal_valid_generation() -> None:
    generation = TurnGeneration(narration="Something happens.")
    assert generation.dialogue == []
    assert generation.visual_cue.generate is False


def test_narration_is_required() -> None:
    with pytest.raises(ValidationError):
        TurnGeneration.model_validate({})


def test_empty_narration_rejected() -> None:
    with pytest.raises(ValidationError):
        TurnGeneration.model_validate({"narration": ""})


@pytest.mark.parametrize("importance", [0, 6, -1, 100])
def test_memory_importance_must_be_1_to_5(importance: int) -> None:
    with pytest.raises(ValidationError):
        MemoryCandidate(kind=MemoryKind.FACT, summary="x", importance=importance)


@pytest.mark.parametrize("delta", [6, -6, 50, -100])
def test_relationship_deltas_outside_contract_are_rejected(delta: int) -> None:
    """Out-of-range deltas are a contract violation, not something to clamp silently."""
    with pytest.raises(ValidationError):
        RelationshipChange(character_id=uuid.uuid4(), trust_delta=delta)


def test_suggested_actions_are_trimmed_and_capped() -> None:
    generation = TurnGeneration(
        narration="n",
        suggested_actions=["  one  ", "", "   ", "two", "three", "four", "five"],
    )
    assert generation.suggested_actions == ["one", "two", "three", "four"]


def test_unknown_fields_are_ignored_not_fatal() -> None:
    """Models add chatter; extra keys must not break a turn."""
    generation = TurnGeneration.model_validate(
        {"narration": "n", "mood": "tense", "confidence": 0.9}
    )
    assert generation.narration == "n"


def test_json_schema_is_generatable_for_ollama_structured_output() -> None:
    schema = TurnGeneration.model_json_schema()
    assert schema["type"] == "object"
    assert "narration" in schema["properties"]
    assert "narration" in schema["required"]


def test_the_schema_requires_suggested_actions() -> None:
    """Regression: an optional field is one a grammar-constrained model may skip.

    mistral:7b omitted the key in every world tested, so the UI silently lost its
    suggestion chips. `required` is what takes that choice away from the model.
    """
    assert "suggested_actions" in TurnGeneration.model_json_schema()["required"]


def test_a_response_without_suggestions_is_still_a_playable_turn() -> None:
    """The schema demands them; validation must not throw away good prose over them."""
    generation = TurnGeneration.model_validate({"narration": "The door opens."})

    assert generation.suggested_actions == []


def test_a_visual_cue_that_asks_for_nothing_is_not_a_request() -> None:
    cue = TurnGeneration.model_validate(
        {"narration": "n", "visual_cue": {"generate": True, "scene_prompt": "   "}}
    ).visual_cue

    assert cue.generate is False


def test_an_unrequested_visual_cue_does_not_keep_a_payload() -> None:
    """Observed with mistral:7b: generate=false alongside a filled-in scene_prompt."""
    cue = TurnGeneration.model_validate(
        {
            "narration": "n",
            "visual_cue": {
                "generate": False,
                "scene_prompt": "A rooftop at sunset",
                "character_ids": [str(uuid.uuid4())],
            },
        }
    ).visual_cue

    assert cue.scene_prompt is None
    assert cue.character_ids == []
