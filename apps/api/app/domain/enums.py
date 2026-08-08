"""Domain vocabulary shared by persistence, application services and the AI contract."""

from __future__ import annotations

from enum import StrEnum


class Language(StrEnum):
    """Narrative language of a world.

    Fixed when the world is created: a world's transcript, memories and summaries
    must stay in one language, so this is deliberately not switchable later.
    """

    EN = "en"
    ES = "es"


LANGUAGE_NAMES: dict[Language, str] = {
    Language.EN: "English",
    Language.ES: "Spanish (español)",
}


class MessageRole(StrEnum):
    PLAYER = "player"
    NARRATOR = "narrator"
    CHARACTER = "character"
    SYSTEM = "system"


class MemoryKind(StrEnum):
    EPISODIC = "episodic"
    FACT = "fact"
    RELATIONSHIP = "relationship"
    GOAL = "goal"
    WORLD = "world"
