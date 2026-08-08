"""Configuration-driven selection of the story provider."""

from __future__ import annotations

from app.application.ports import StoryGeneratorPort
from app.config import Settings, StoryProvider
from app.infrastructure.story.mock import MockStoryGenerator
from app.infrastructure.story.ollama import OllamaStoryGenerator


def build_story_generator(settings: Settings) -> StoryGeneratorPort:
    match settings.story_provider:
        case StoryProvider.MOCK:
            return MockStoryGenerator()
        case StoryProvider.OLLAMA:
            return OllamaStoryGenerator(settings)
