"""Configuration-driven selection of the story provider."""

from __future__ import annotations

from app.application.generation_policy import GenerationPolicy
from app.application.llm_metrics import GenerationPurpose
from app.application.ports import StoryGeneratorPort
from app.config import Settings, StoryProvider
from app.infrastructure.story.mock import MockStoryGenerator
from app.infrastructure.story.ollama import OllamaStoryGenerator


def build_generation_policy(settings: Settings) -> GenerationPolicy:
    """Turn settings into the resolved generation policy.

    Here rather than in the application because this is where configuration is allowed
    to be read. `GenerationPolicy` knows what a budget *is* and refuses to be built
    without one for every purpose; this function knows which environment variable holds
    it. Keeping the two apart is what lets the application depend on the policy without
    depending on `Settings`.
    """
    return GenerationPolicy(
        context_window=settings.ollama_num_ctx,
        temperature=settings.ollama_temperature,
        max_output_tokens={
            GenerationPurpose.STORY_TURN: settings.story_max_output_tokens,
            GenerationPurpose.OUTCOME_NARRATION: settings.narration_max_output_tokens,
        },
    )


def build_story_generator(settings: Settings) -> StoryGeneratorPort:
    match settings.story_provider:
        case StoryProvider.MOCK:
            return MockStoryGenerator()
        case StoryProvider.OLLAMA:
            return OllamaStoryGenerator(settings, build_generation_policy(settings))
