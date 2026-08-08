"""Provider status endpoint and the Ollama adapter's failure reporting."""

from __future__ import annotations

import httpx
import pytest

from app.config import ImageProvider, Settings, StoryProvider
from app.domain.errors import StoryGenerationError
from app.infrastructure.images.factory import build_image_generator
from app.infrastructure.story.factory import build_story_generator
from app.infrastructure.story.ollama import OllamaStoryGenerator


async def test_status_endpoint_reports_mock_and_disabled(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/api/v1/ai/status")
    assert response.status_code == 200
    body = response.json()
    assert body["story"]["provider"] == "mock"
    assert body["story"]["state"] == "ready"
    assert body["image"]["provider"] == "disabled"
    assert body["image"]["state"] == "disabled"


def test_factories_honour_configuration() -> None:
    story = build_story_generator(
        Settings(story_provider=StoryProvider.OLLAMA, ollama_model="llama3.1:8b")
    )
    assert story.name == "ollama"
    assert build_story_generator(Settings(story_provider=StoryProvider.MOCK)).name == "mock"
    assert build_image_generator(Settings(image_provider=ImageProvider.MOCK)).name == "mock"
    assert build_image_generator(Settings(image_provider=ImageProvider.COMFYUI)).name == "comfyui"


def test_ollama_without_model_fails_startup_validation() -> None:
    """A misconfiguration must be loud, not silently downgraded to mock."""
    settings = Settings(story_provider=StoryProvider.OLLAMA, ollama_model="")
    with pytest.raises(ValueError, match="OLLAMA_MODEL"):
        settings.validate_runtime()


def test_mock_provider_needs_no_model() -> None:
    Settings(story_provider=StoryProvider.MOCK).validate_runtime()


async def test_ollama_status_unreachable_names_the_cause() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    generator = OllamaStoryGenerator(Settings(ollama_model="llama3.1:8b"), client=client)
    status = await generator.status()
    assert status.state == "unreachable"
    assert "ollama serve" in status.detail
    await client.aclose()


async def test_ollama_status_detects_model_not_installed() -> None:
    def tags(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(tags))
    generator = OllamaStoryGenerator(Settings(ollama_model="llama3.1:8b"), client=client)
    status = await generator.status()
    assert status.state == "misconfigured"
    assert "ollama pull llama3.1:8b" in status.detail
    await client.aclose()


async def test_ollama_status_ready_when_model_present() -> None:
    def tags(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(tags))
    generator = OllamaStoryGenerator(Settings(ollama_model="llama3.1:8b"), client=client)
    status = await generator.status()
    assert status.state == "ready"
    await client.aclose()


async def test_ollama_status_accepts_bare_model_name() -> None:
    """Users write OLLAMA_MODEL=llama3.1; Ollama reports llama3.1:latest."""

    def tags(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3.1:latest"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(tags))
    generator = OllamaStoryGenerator(Settings(ollama_model="llama3.1"), client=client)
    assert (await generator.status()).state == "ready"
    await client.aclose()


async def test_ollama_malformed_json_raises_typed_error(make_story_context) -> None:
    def bad_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not json at all"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(bad_json))
    generator = OllamaStoryGenerator(Settings(ollama_model="m"), client=client)
    with pytest.raises(StoryGenerationError, match="not valid JSON"):
        await generator.generate_turn(make_story_context())
    await client.aclose()


async def test_ollama_schema_violation_raises_typed_error(make_story_context) -> None:
    """Structured output constrains the model but does not guarantee it."""

    def wrong_shape(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"content": '{"dialogue": [], "narration": ""}'}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrong_shape))
    generator = OllamaStoryGenerator(Settings(ollama_model="m"), client=client)
    with pytest.raises(StoryGenerationError, match="did not match the TurnGeneration contract"):
        await generator.generate_turn(make_story_context())
    await client.aclose()


async def test_ollama_timeout_suggests_a_remedy(make_story_context) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    generator = OllamaStoryGenerator(Settings(ollama_model="m"), client=client)
    with pytest.raises(StoryGenerationError, match="OLLAMA_TIMEOUT_SECONDS"):
        await generator.generate_turn(make_story_context())
    await client.aclose()


async def test_ollama_valid_response_is_parsed(make_story_context) -> None:
    payload = (
        '{"narration": "The door opens.", "dialogue": [], '
        '"suggested_actions": ["Go in", "Wait", "Leave"], "memory_candidates": [], '
        '"relationship_changes": [], "world_events": [], '
        '"visual_cue": {"generate": false, "scene_prompt": null, '
        '"character_ids": [], "reason": null}}'
    )

    def ok(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = request.read().decode()
        assert '"format"' in body  # structured output is requested
        return httpx.Response(200, json={"message": {"content": payload}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    generator = OllamaStoryGenerator(Settings(ollama_model="m"), client=client)
    generation = await generator.generate_turn(make_story_context())
    assert generation.narration == "The door opens."
    assert len(generation.suggested_actions) == 3
    await client.aclose()
