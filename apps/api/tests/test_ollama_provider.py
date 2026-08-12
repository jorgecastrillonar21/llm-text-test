"""The Ollama adapter's generation options, metrics capture and streaming transport.

Every test here runs against `httpx.MockTransport`. Nothing in this file needs Ollama
installed, running, or reachable, which is the point: a performance contract that could
only be checked on a machine with a GPU would never be checked in CI.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.application.generation_policy import GenerationPolicy
from app.application.llm_metrics import GenerationDoneReason, GenerationPurpose
from app.application.story_context import OutcomeContext
from app.config import Settings
from app.domain.errors import StoryGenerationError
from app.domain.resolution import ResolutionDisposition
from app.infrastructure.story.factory import build_generation_policy
from app.infrastructure.story.ollama import OllamaStoryGenerator

TURN_JSON = (
    '{"narration": "The door opens.", "dialogue": [], "suggested_actions": [], '
    '"memory_candidates": [], "relationship_changes": [], "world_events": [], '
    '"visual_cue": {"generate": false}}'
)

NARRATION_JSON = '{"narration": "The lock gives way."}'


def _generator(
    handler: Any, **overrides: Any
) -> tuple[OllamaStoryGenerator, httpx.AsyncClient, list[dict[str, Any]]]:
    """An adapter wired to a stub transport, plus the request bodies it sent."""
    seen: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        body = request.read()
        if body:  # `status()` issues a bodyless GET.
            seen.append(json.loads(body.decode()))
        return handler(request)

    settings = Settings(ollama_model="m", **overrides)
    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return (
        OllamaStoryGenerator(settings, build_generation_policy(settings), client=client),
        (client),
        seen,
    )


def _answers(body: dict[str, Any]) -> Any:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return handler


def _streams(*frames: dict[str, Any]) -> Any:
    """Ollama's newline-delimited JSON, as a streamed response."""
    payload = "\n".join(json.dumps(frame) for frame in frames).encode()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    return handler


def _outcome_context(make_story_context: Any) -> OutcomeContext:
    context = make_story_context()
    return OutcomeContext(
        world=context.world,
        player=context.player,
        time=context.time,
        disposition=ResolutionDisposition.APPLIED,
        reason_code="lock_picked",
        resolver="deterministic",
        events=[],
    )


# -- the output budget actually reaches the provider (spec 62) -----------------


async def test_the_configured_output_budget_is_sent_as_num_predict(make_story_context) -> None:
    generator, client, seen = _generator(_answers({"message": {"content": TURN_JSON}}))

    await generator.generate_turn(make_story_context())

    assert seen[0]["options"]["num_predict"] == Settings().story_max_output_tokens
    await client.aclose()


async def test_a_changed_budget_changes_the_request(make_story_context) -> None:
    """The budget is configuration, not a literal somewhere in the adapter."""
    generator, client, seen = _generator(
        _answers({"message": {"content": TURN_JSON}}), story_max_output_tokens=333
    )

    await generator.generate_turn(make_story_context())

    assert seen[0]["options"]["num_predict"] == 333
    await client.aclose()


async def test_narration_gets_its_own_smaller_budget(make_story_context) -> None:
    """One paragraph and a whole turn document are not the same job."""
    generator, client, seen = _generator(_answers({"message": {"content": NARRATION_JSON}}))

    await generator.narrate_outcome(_outcome_context(make_story_context))

    settings = Settings()
    assert seen[0]["options"]["num_predict"] == settings.narration_max_output_tokens
    assert settings.narration_max_output_tokens < settings.story_max_output_tokens
    await client.aclose()


async def test_the_context_window_is_sent_and_recorded(make_story_context) -> None:
    generator, client, seen = _generator(
        _answers({"message": {"content": TURN_JSON}, "prompt_eval_count": 1000}),
        ollama_num_ctx=4096,
    )

    result = await generator.generate_turn(make_story_context())

    assert seen[0]["options"]["num_ctx"] == 4096
    assert result.metrics is not None
    assert result.metrics.configured_context_window == 4096
    assert result.metrics.prompt_context_utilization == pytest.approx(1000 / 4096, abs=1e-4)
    await client.aclose()


async def test_keep_alive_is_omitted_unless_configured(make_story_context) -> None:
    """Unset must mean Ollama's own default, not this file's opinion of one."""
    generator, client, seen = _generator(_answers({"message": {"content": TURN_JSON}}))
    await generator.generate_turn(make_story_context())
    assert "keep_alive" not in seen[0]
    await client.aclose()

    generator, client, seen = _generator(
        _answers({"message": {"content": TURN_JSON}}), ollama_keep_alive="30m"
    )
    await generator.generate_turn(make_story_context())
    assert seen[0]["keep_alive"] == "30m"
    await client.aclose()


# -- what the provider reported (spec 11-14, 27) -------------------------------


async def test_ollama_durations_and_counts_become_application_metrics(
    make_story_context,
) -> None:
    generator, client, _ = _generator(
        _answers(
            {
                "model": "llama3.1:8b",
                "message": {"content": TURN_JSON},
                "done": True,
                "done_reason": "stop",
                "total_duration": 12_000_000_000,
                "load_duration": 2_000_000_000,
                "prompt_eval_count": 1500,
                "prompt_eval_duration": 1_000_000_000,
                "eval_count": 300,
                "eval_duration": 9_000_000_000,
            }
        )
    )

    metrics = (await generator.generate_turn(make_story_context())).metrics
    assert metrics is not None

    # Nanoseconds in, milliseconds out, converted once at this boundary.
    assert metrics.total_ms == 12_000
    assert metrics.load_ms == 2_000
    assert metrics.prompt_eval_ms == 1_000
    assert metrics.generation_ms == 9_000

    assert metrics.prompt_tokens == 1500
    assert metrics.generated_tokens == 300
    assert metrics.model == "llama3.1:8b"
    assert metrics.purpose is GenerationPurpose.STORY_TURN
    assert metrics.done_reason is GenerationDoneReason.STOP
    assert metrics.provider_metrics_available is True

    # Derived, not reported.
    assert metrics.prompt_tokens_per_second == 1500.0
    assert metrics.generation_tokens_per_second == pytest.approx(33.33, abs=0.01)
    await client.aclose()


async def test_a_silent_provider_yields_nones_not_zeroes(make_story_context) -> None:
    """Absent measurements and measured zeroes are different facts."""
    generator, client, _ = _generator(_answers({"message": {"content": TURN_JSON}}))

    metrics = (await generator.generate_turn(make_story_context())).metrics

    assert metrics is not None
    assert metrics.prompt_tokens is None
    assert metrics.generated_tokens is None
    assert metrics.total_ms is None
    assert metrics.prompt_tokens_per_second is None
    assert metrics.done_reason is GenerationDoneReason.UNKNOWN
    # The application's own wall clock is always there, because we measured it ourselves.
    assert metrics.client_elapsed_ms is not None
    await client.aclose()


async def test_an_unrecognised_done_reason_is_unknown_not_stop(make_story_context) -> None:
    generator, client, _ = _generator(
        _answers({"message": {"content": TURN_JSON}, "done_reason": "something_new"})
    )
    metrics = (await generator.generate_turn(make_story_context())).metrics
    assert metrics is not None
    assert metrics.done_reason is GenerationDoneReason.UNKNOWN
    await client.aclose()


async def test_the_purpose_distinguishes_the_two_call_sites(make_story_context) -> None:
    generator, client, _ = _generator(_answers({"message": {"content": NARRATION_JSON}}))
    result = await generator.narrate_outcome(_outcome_context(make_story_context))
    assert result.metrics is not None
    assert result.metrics.purpose is GenerationPurpose.OUTCOME_NARRATION
    await client.aclose()


# -- natural completion vs a budget stop (spec 63) -----------------------------


async def test_a_natural_completion_is_not_reported_as_a_budget_stop(
    make_story_context,
) -> None:
    generator, client, _ = _generator(
        _answers(
            {
                "message": {"content": TURN_JSON},
                "done": True,
                "done_reason": "stop",
                "eval_count": 120,
            }
        ),
        story_max_output_tokens=512,
    )

    metrics = (await generator.generate_turn(make_story_context())).metrics

    assert metrics is not None
    assert metrics.done_reason is GenerationDoneReason.STOP
    assert metrics.output_budget_reached is False
    await client.aclose()


async def test_a_budget_stop_fails_the_turn_and_says_the_budget_was_the_cause(
    make_story_context,
) -> None:
    """Schema-constrained output that runs out of budget arrives broken, not short.

    Reporting that as "the model does not follow schemas" sends the reader to the
    wrong problem, so the error names the budget and the error code says so too.
    """
    truncated = TURN_JSON[:40]  # As it arrives: cut mid-document, closing braces missing.
    generator, client, _ = _generator(
        _answers(
            {
                "message": {"content": truncated},
                "done": True,
                "done_reason": "length",
                "eval_count": 64,
            }
        ),
        story_max_output_tokens=64,
    )

    with pytest.raises(StoryGenerationError) as caught:
        await generator.generate_turn(make_story_context())

    assert "STORY_MAX_OUTPUT_TOKENS" in str(caught.value)
    assert "64-token budget" in str(caught.value)
    assert caught.value.error_code == "budget_exhausted"
    await client.aclose()


async def test_a_genuinely_malformed_reply_is_not_blamed_on_the_budget(
    make_story_context,
) -> None:
    """The other half of the same distinction: no budget signal, no budget claim."""
    generator, client, _ = _generator(
        _answers({"message": {"content": "not json"}, "done_reason": "stop", "eval_count": 3})
    )

    with pytest.raises(StoryGenerationError) as caught:
        await generator.generate_turn(make_story_context())

    assert caught.value.error_code == "invalid_json"
    assert "STORY_MAX_OUTPUT_TOKENS" not in str(caught.value)
    await client.aclose()


# -- failures still carry a measurement (spec 67) ------------------------------


async def test_a_transport_failure_carries_an_elapsed_time_and_a_code(
    make_story_context,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    generator, client, _ = _generator(refuse)

    with pytest.raises(StoryGenerationError) as caught:
        await generator.generate_turn(make_story_context())

    assert caught.value.error_code == "connect_error"
    assert caught.value.model == "m"
    assert caught.value.elapsed_ms is not None and caught.value.elapsed_ms >= 0
    await client.aclose()


async def test_an_http_error_names_the_status(make_story_context) -> None:
    generator, client, _ = _generator(lambda _r: httpx.Response(500, text="boom"))

    with pytest.raises(StoryGenerationError) as caught:
        await generator.generate_turn(make_story_context())

    assert caught.value.error_code == "http_500"
    assert caught.value.retryable is False
    await client.aclose()


# -- streaming and time to first token (spec 42-46) ----------------------------


async def test_streaming_is_off_by_default_and_reports_no_first_token_time(
    make_story_context,
) -> None:
    """TTFT is not derivable from a completed request, so it stays None rather than
    being invented from total duration."""
    generator, client, seen = _generator(_answers({"message": {"content": TURN_JSON}}))

    result = await generator.generate_turn(make_story_context())

    assert generator.supports_streaming() is False
    assert seen[0]["stream"] is False
    assert result.metrics is not None
    assert result.metrics.time_to_first_token_ms is None
    await client.aclose()


async def test_streaming_produces_the_same_turn_and_a_measured_ttft(
    make_story_context,
) -> None:
    frames = [{"message": {"content": piece}} for piece in (TURN_JSON[:30], TURN_JSON[30:])]
    frames.append(
        {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "eval_count": 90,
            "eval_duration": 3_000_000_000,
        }
    )
    generator, client, seen = _generator(_streams(*frames), ollama_streaming_enabled=True)

    result = await generator.generate_turn(make_story_context())

    assert seen[0]["stream"] is True
    assert result.generation.narration == "The door opens."
    assert result.metrics is not None
    assert result.metrics.time_to_first_token_ms is not None
    assert result.metrics.generated_tokens == 90
    await client.aclose()


async def test_stream_turn_yields_text_then_a_final_metrics_chunk(make_story_context) -> None:
    frames = [
        {"message": {"content": "The door "}},
        {"message": {"content": "opens."}},
        {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 4},
    ]
    generator, client, _ = _generator(_streams(*frames), ollama_streaming_enabled=True)

    chunks = [chunk async for chunk in generator.stream_turn(make_story_context())]

    assert [chunk.text for chunk in chunks if not chunk.done] == ["The door ", "opens."]
    assert chunks[-1].done is True
    assert chunks[-1].metrics is not None
    assert chunks[-1].metrics.generated_tokens == 4
    assert chunks[-1].metrics.time_to_first_token_ms is not None
    await client.aclose()


async def test_an_unparseable_stream_frame_is_skipped_not_fatal(make_story_context) -> None:
    """One lost fragment is not a lost generation."""
    body = "\n".join(
        [
            json.dumps({"message": {"content": TURN_JSON}}),
            "{not json",
            json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop"}),
        ]
    ).encode()

    generator, client, _ = _generator(
        lambda _r: httpx.Response(200, content=body), ollama_streaming_enabled=True
    )

    result = await generator.generate_turn(make_story_context())
    assert result.generation.narration == "The door opens."
    await client.aclose()


# -- the policy is the only source of the numbers (spec 7) ---------------------


def test_a_policy_must_budget_every_purpose() -> None:
    """Adding a purpose means deciding what it is allowed to produce."""
    with pytest.raises(ValueError, match="missing an output budget"):
        GenerationPolicy(
            context_window=8192,
            temperature=0.7,
            max_output_tokens={GenerationPurpose.STORY_TURN: 512},
        )


def test_a_policy_has_no_spelling_of_unlimited() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        GenerationPolicy(
            context_window=8192,
            temperature=0.7,
            max_output_tokens={
                GenerationPurpose.STORY_TURN: 512,
                GenerationPurpose.OUTCOME_NARRATION: 0,
            },
        )


def test_the_factory_builds_the_policy_from_settings() -> None:
    policy = build_generation_policy(
        Settings(ollama_num_ctx=16384, story_max_output_tokens=700, narration_max_output_tokens=90)
    )
    assert policy.context_window == 16384
    assert policy.options_for(GenerationPurpose.STORY_TURN).max_output_tokens == 700
    assert policy.options_for(GenerationPurpose.OUTCOME_NARRATION).max_output_tokens == 90


async def test_the_status_report_names_the_generation_configuration() -> None:
    """The one place a developer can see what the provider was actually configured with."""

    def tags(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "m:latest"}]})

    generator, client, _ = _generator(tags, ollama_num_ctx=16384, ollama_keep_alive="30m")

    status = await generator.status()

    assert status.state == "ready"
    assert status.extra["context_window"] == "16384"
    assert status.extra["keep_alive"] == "30m"
    assert status.extra["streaming"] == "off"
    await client.aclose()
