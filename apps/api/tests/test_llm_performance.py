"""Performance measurement: the arithmetic, the isolation, and the diagnostics API.

Three things are being defended here.

The arithmetic, because every derived number in a metrics record is a place where a
missing measurement can quietly become a zero, and a zero reads as a fact.

The isolation, because these records sit next to game state that looks superficially
similar -- both append-only, both timestamped -- and the difference is authority. A
metric must never move `state_revision`, never become a `GameEvent`, and never be fed
back into a prompt.

And the diagnostics endpoints, because they are the only reason any of it is reachable.

Nothing here needs Ollama.
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.application.llm_metrics import (
    GenerationDoneReason,
    GenerationPurpose,
    GenerationStatus,
    LlmGenerationMetrics,
    TurnPerformanceMetrics,
    failed_generation_metrics,
    ns_to_ms,
    summarize_generations,
    tokens_per_second,
)
from app.application.ports import TurnGenerationResult
from app.application.story_context import StoryContext
from app.domain.errors import StoryGenerationError
from app.infrastructure.metrics import InMemoryLlmMetricsRecorder
from app.infrastructure.story.mock import MockStoryGenerator
from tests.conftest import override_story_generator


def _metrics(**overrides: object) -> LlmGenerationMetrics:
    data: dict[str, object] = {
        "provider": "ollama",
        "model": "llama3.1:8b",
        "purpose": GenerationPurpose.STORY_TURN,
    }
    data.update(overrides)
    return LlmGenerationMetrics(**data)  # type: ignore[arg-type]


# -- unit conversion and derived rates (spec 12, 13, 64) -----------------------


def test_nanoseconds_become_milliseconds() -> None:
    assert ns_to_ms(12_345_678_901) == 12_345.679
    assert ns_to_ms(0) == 0.0


@pytest.mark.parametrize("value", [None, -1, "500", True, {"ns": 1}])
def test_a_duration_that_is_not_a_duration_is_none(value: object) -> None:
    """This runs on JSON decoded from another process. It must not raise, and it must
    not coerce nonsense into a number somebody will later read as a measurement."""
    assert ns_to_ms(value) is None


def test_throughput_is_none_rather_than_infinite_when_no_time_passed() -> None:
    """A prompt served from Ollama's cache genuinely reports a zero duration.

    "No rate" is the honest answer. A zero would read as "evaluated at 0 tokens/sec",
    which is the opposite of what happened.
    """
    assert tokens_per_second(1500, 0.0) is None
    assert tokens_per_second(1500, None) is None
    assert tokens_per_second(None, 1000.0) is None
    assert tokens_per_second(0, 1000.0) is None


def test_throughput_is_tokens_over_seconds() -> None:
    assert tokens_per_second(300, 10_000.0) == 30.0
    assert tokens_per_second(1500, 1_000.0) == 1500.0


def test_the_two_rates_use_their_own_durations() -> None:
    metrics = _metrics(
        prompt_tokens=1500,
        prompt_eval_ms=1_000.0,
        generated_tokens=300,
        generation_ms=10_000.0,
    )
    assert metrics.prompt_tokens_per_second == 1500.0
    assert metrics.generation_tokens_per_second == 30.0


def test_context_utilization_is_measured_against_the_configured_window() -> None:
    metrics = _metrics(prompt_tokens=4096, configured_context_window=8192)
    assert metrics.prompt_context_utilization == 0.5


def test_context_utilization_may_exceed_one_and_is_not_clamped() -> None:
    """The provider reporting more prompt tokens than the window we asked for is worth
    seeing, not worth hiding."""
    metrics = _metrics(prompt_tokens=9000, configured_context_window=8192)
    assert metrics.prompt_context_utilization is not None
    assert metrics.prompt_context_utilization > 1.0


def test_context_utilization_is_none_without_both_numbers() -> None:
    assert _metrics(configured_context_window=8192).prompt_context_utilization is None
    assert _metrics(prompt_tokens=100).prompt_context_utilization is None


# -- budget termination is observable (spec 9, 64) -----------------------------


def test_a_length_stop_is_a_budget_stop() -> None:
    assert _metrics(done_reason=GenerationDoneReason.LENGTH).output_budget_reached is True


def test_hitting_the_count_is_a_budget_stop_even_if_the_provider_says_otherwise() -> None:
    """Not every provider reports a budget stop as one. The counts still do."""
    metrics = _metrics(
        done_reason=GenerationDoneReason.STOP,
        generated_tokens=256,
        configured_max_output_tokens=256,
    )
    assert metrics.output_budget_reached is True


def test_a_normal_completion_is_not_a_budget_stop() -> None:
    metrics = _metrics(
        done_reason=GenerationDoneReason.STOP,
        generated_tokens=120,
        configured_max_output_tokens=512,
    )
    assert metrics.output_budget_reached is False


def test_an_unknown_ending_with_no_counts_is_not_claimed_as_a_budget_stop() -> None:
    assert _metrics().output_budget_reached is False


# -- turn latency split (spec 23) ----------------------------------------------


def test_the_turn_split_separates_the_model_from_everything_else() -> None:
    turn = TurnPerformanceMetrics(
        session_id=uuid.uuid4(),
        turn_index=3,
        total_turn_ms=60_000.0,
        story_generation_ms=58_000.0,
        llm_call_count=1,
    )
    assert turn.non_llm_application_ms == 2_000.0


def test_a_negative_split_is_clamped_rather_than_reported() -> None:
    """Two clocks, measured at different places. A negative is noise, not a fact."""
    turn = TurnPerformanceMetrics(
        session_id=uuid.uuid4(),
        total_turn_ms=100.0,
        story_generation_ms=100.5,
        llm_call_count=1,
    )
    assert turn.non_llm_application_ms == 0.0


# -- failures are measurements too (spec 67) -----------------------------------


def test_a_failed_call_fabricates_no_token_counts() -> None:
    error = StoryGenerationError(
        "timed out",
        provider="ollama",
        model="llama3.1:8b",
        error_code="timeout",
        elapsed_ms=120_000.0,
    )

    metrics = failed_generation_metrics(error, purpose=GenerationPurpose.STORY_TURN)

    assert metrics.status is GenerationStatus.ERROR
    assert metrics.done_reason is GenerationDoneReason.ERROR
    assert metrics.provider_metrics_available is False
    assert metrics.prompt_tokens is None
    assert metrics.generated_tokens is None
    assert metrics.total_ms is None
    assert metrics.error_code == "timeout"
    assert metrics.client_elapsed_ms == 120_000.0


def test_a_failed_call_falls_back_to_the_callers_stopwatch() -> None:
    error = StoryGenerationError("boom", provider="ollama")
    metrics = failed_generation_metrics(
        error, purpose=GenerationPurpose.STORY_TURN, fallback_elapsed_ms=42.0
    )
    assert metrics.client_elapsed_ms == 42.0
    assert metrics.model == "unknown"


# -- summaries (spec 50) -------------------------------------------------------


def test_a_summary_excludes_records_nobody_measured() -> None:
    """A mock's plausible stand-ins and a failed call's absences would both drag every
    figure toward nonsense, and a dev server running the mock is where this is read."""
    records = [
        _metrics(total_ms=1000.0, prompt_tokens=100, generated_tokens=50),
        _metrics(provider="mock", provider_metrics_available=False, generated_tokens=9999),
        _metrics(total_ms=3000.0, prompt_tokens=300, generated_tokens=150),
    ]

    summary = summarize_generations(records)

    assert summary.sample_count == 3
    assert summary.measured_sample_count == 2
    assert summary.latest_total_ms == 1000.0  # newest first
    assert summary.average_total_ms == 2000.0
    assert summary.max_total_ms == 3000.0
    assert summary.max_generated_tokens == 150  # not the mock's 9999


def test_a_summary_of_nothing_reports_nothing_rather_than_zero() -> None:
    summary = summarize_generations([])
    assert summary.sample_count == 0
    assert summary.average_total_ms is None
    assert summary.max_prompt_tokens is None


def test_a_summary_counts_budget_stops_and_errors() -> None:
    records = [
        _metrics(done_reason=GenerationDoneReason.LENGTH),
        _metrics(status=GenerationStatus.ERROR, provider_metrics_available=False),
        _metrics(done_reason=GenerationDoneReason.STOP),
    ]
    summary = summarize_generations(records)
    assert summary.budget_reached_count == 1
    assert summary.error_count == 1


# -- the recorder (spec 20, 52-55) ---------------------------------------------


def test_the_buffer_is_bounded_and_newest_first() -> None:
    recorder = InMemoryLlmMetricsRecorder(buffer_size=3, slow_call_threshold_ms=30_000)
    for index in range(5):
        recorder.record_generation(_metrics(generated_tokens=index))

    kept = recorder.recent_generations(limit=10)

    assert [record.generated_tokens for record in kept] == [4, 3, 2]


def test_records_can_be_narrowed_to_one_session() -> None:
    recorder = InMemoryLlmMetricsRecorder(buffer_size=10, slow_call_threshold_ms=30_000)
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    recorder.record_generation(_metrics(session_id=mine, generated_tokens=1))
    recorder.record_generation(_metrics(session_id=theirs, generated_tokens=2))
    recorder.record_generation(_metrics(session_id=mine, generated_tokens=3))

    kept = recorder.recent_generations(limit=10, session_id=mine)

    assert [record.generated_tokens for record in kept] == [3, 1]


def test_one_log_line_per_generation_naming_the_purpose(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = InMemoryLlmMetricsRecorder(buffer_size=10, slow_call_threshold_ms=30_000)
    with caplog.at_level(logging.INFO, logger="app.llm.performance"):
        recorder.record_generation(
            _metrics(prompt_tokens=1500, generated_tokens=300, total_ms=9000.0)
        )

    assert len(caplog.records) == 1
    assert "story_turn" in caplog.text
    assert "1500" in caplog.text


def test_a_slow_call_is_logged_at_warning(caplog: pytest.LogCaptureFixture) -> None:
    recorder = InMemoryLlmMetricsRecorder(buffer_size=10, slow_call_threshold_ms=1_000)
    with caplog.at_level(logging.INFO, logger="app.llm.performance"):
        recorder.record_generation(_metrics(total_ms=5_000.0))

    assert caplog.records[0].levelno == logging.WARNING


def test_a_budget_stop_is_logged_loudly(caplog: pytest.LogCaptureFixture) -> None:
    recorder = InMemoryLlmMetricsRecorder(buffer_size=10, slow_call_threshold_ms=30_000)
    with caplog.at_level(logging.INFO, logger="app.llm.performance"):
        recorder.record_generation(_metrics(done_reason=GenerationDoneReason.LENGTH))

    assert caplog.records[0].levelno >= logging.WARNING
    assert "budget" in caplog.text.lower()


def test_a_missing_measurement_is_never_logged_as_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    recorder = InMemoryLlmMetricsRecorder(buffer_size=10, slow_call_threshold_ms=30_000)
    with caplog.at_level(logging.INFO, logger="app.llm.performance"):
        recorder.record_generation(_metrics())

    assert "0 tokens" not in caplog.text
    assert "-" in caplog.text


# -- observability never breaks the game (spec 19) -----------------------------


class BrokenRecorder:
    """A recorder that fails at everything, because telemetry does fail."""

    def record_generation(self, metrics: LlmGenerationMetrics) -> None:
        raise RuntimeError("the metrics backend is down")

    def record_turn(self, metrics: TurnPerformanceMetrics) -> None:
        raise RuntimeError("the metrics backend is down")


async def test_a_broken_recorder_does_not_break_a_turn(
    app_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Generation is primary. Observability is secondary, and says so by staying quiet
    in the response and loud in the log."""
    session = await _bootstrap(app_client)
    transport = app_client._transport
    assert isinstance(transport, ASGITransport)
    transport.app.state.llm_metrics_recorder = BrokenRecorder()  # type: ignore[union-attr]

    with caplog.at_level(logging.WARNING):
        response = await app_client.post(
            f"/api/v1/sessions/{session}/turns", json={"action": "I look around."}
        )

    assert response.status_code == 200, response.text
    assert "metrics backend is down" in caplog.text


# -- metrics are not game state (spec 15, 16, 65) ------------------------------


class ContextRecordingGenerator:
    """Plays an ordinary turn and keeps every context it was handed."""

    name = "context-recording"

    def __init__(self) -> None:
        self._inner = MockStoryGenerator()
        self.contexts: list[StoryContext] = []

    async def generate_turn(self, context: StoryContext) -> TurnGenerationResult:
        self.contexts.append(context)
        return await self._inner.generate_turn(context)

    async def status(self) -> object:  # pragma: no cover - not exercised
        return await self._inner.status()


async def test_no_performance_data_ever_reaches_the_prompt(app_client: AsyncClient) -> None:
    """A model told that its last reply took fifty-eight seconds would write about it."""
    session = await _bootstrap(app_client)
    generator = ContextRecordingGenerator()
    override_story_generator(app_client, generator)

    for _ in range(2):
        assert (
            await app_client.post(
                f"/api/v1/sessions/{session}/turns", json={"action": "I look around."}
            )
        ).status_code == 200

    assert len(generator.contexts) == 2
    for context in generator.contexts:
        rendered = context.model_dump_json()
        for forbidden in ("prompt_eval", "eval_count", "tokens_per_second", "total_ms", "load_ms"):
            assert forbidden not in rendered
        assert "metrics" not in StoryContext.model_fields


async def test_recording_metrics_does_not_move_the_world_state_revision(
    app_client: AsyncClient,
) -> None:
    """The line between a technical record and a fact about the world."""
    session = await _bootstrap(app_client)
    before = (await app_client.get(f"/api/v1/sessions/{session}/world-state")).json()

    assert (
        await app_client.post(
            f"/api/v1/sessions/{session}/turns", json={"action": "I look at the ceiling."}
        )
    ).status_code == 200

    after = (await app_client.get(f"/api/v1/sessions/{session}/world-state")).json()
    performance = (await app_client.get(f"/api/v1/dev/sessions/{session}/llm-performance")).json()

    # Something was definitely recorded...
    assert performance["summary"]["sample_count"] >= 1
    # ...and none of it was a change to the world.
    assert after["revision"] == before["revision"]


async def test_metrics_do_not_become_events_or_memories(app_client: AsyncClient) -> None:
    """History records what happened in the world, not what happened to the process."""
    session = await _bootstrap(app_client)
    # "steal" is one of the tokens the mock treats as consequential, so this turn
    # actually writes history. A quiet action leaves none, and a test that asserted
    # over an empty list would pass without ever looking at an event.
    quiet = await app_client.post(
        f"/api/v1/sessions/{session}/turns", json={"action": "I look around the room."}
    )
    assert quiet.json()["events_created"] == 0
    await app_client.post(
        f"/api/v1/sessions/{session}/turns", json={"action": "I steal the ledger."}
    )

    events = (await app_client.get(f"/api/v1/sessions/{session}/events")).json()["events"]
    memories = (await app_client.get(f"/api/v1/sessions/{session}/memories")).json()

    # Two turns, two generations, and history counts only the one that changed something.
    performance = (await app_client.get(f"/api/v1/dev/sessions/{session}/llm-performance")).json()
    assert performance["summary"]["sample_count"] == 2
    assert len(events) == 1

    for record in (*events, *memories):
        rendered = json.dumps(record).lower()
        for forbidden in ("token", "ollama", "num_predict", "tokens_per_second", "prompt_eval"):
            assert forbidden not in rendered


# -- the diagnostics endpoints (spec 48-50) ------------------------------------


async def test_the_performance_endpoint_reports_the_turn_it_just_played(
    app_client: AsyncClient,
) -> None:
    session = await _bootstrap(app_client)
    await app_client.post(f"/api/v1/sessions/{session}/turns", json={"action": "I look around."})

    body = (await app_client.get("/api/v1/dev/llm/performance")).json()

    assert body["summary"]["sample_count"] >= 1
    assert body["generations"][0]["purpose"] == "story_turn"
    # The mock says outright that it measured nothing, so the summary reports nothing.
    assert body["summary"]["measured_sample_count"] == 0
    assert body["summary"]["average_total_ms"] is None


async def test_one_llm_call_per_turn_is_measured_not_assumed(app_client: AsyncClient) -> None:
    """The number a later epic has to justify moving."""
    session = await _bootstrap(app_client)
    for _ in range(3):
        await app_client.post(f"/api/v1/sessions/{session}/turns", json={"action": "I wait."})

    body = (await app_client.get(f"/api/v1/dev/sessions/{session}/llm-performance")).json()

    assert len(body["turns"]) == 3
    assert {record["llm_call_count"] for record in body["turns"]} == {1}
    assert [record["turn_index"] for record in body["turns"]] == [3, 2, 1]


async def test_the_turn_split_is_reported_per_turn(app_client: AsyncClient) -> None:
    session = await _bootstrap(app_client)
    await app_client.post(f"/api/v1/sessions/{session}/turns", json={"action": "I look around."})

    turn = (await app_client.get(f"/api/v1/dev/sessions/{session}/llm-performance")).json()[
        "turns"
    ][0]

    assert turn["total_turn_ms"] > 0
    assert turn["story_generation_ms"] >= 0
    assert turn["non_llm_application_ms"] >= 0
    assert turn["total_turn_ms"] >= turn["story_generation_ms"]


async def test_the_session_view_excludes_other_sessions(app_client: AsyncClient) -> None:
    mine = await _bootstrap(app_client)
    theirs = await _bootstrap(app_client)
    await app_client.post(f"/api/v1/sessions/{mine}/turns", json={"action": "I look around."})
    await app_client.post(f"/api/v1/sessions/{theirs}/turns", json={"action": "I look around."})

    body = (await app_client.get(f"/api/v1/dev/sessions/{mine}/llm-performance")).json()

    assert body["session_id"] == mine
    assert len(body["generations"]) == 1
    assert all(record["session_id"] == mine for record in body["generations"])


async def test_the_endpoint_never_returns_a_prompt(app_client: AsyncClient) -> None:
    """Metrics carry sizes, not text. Persisting the prompt for telemetry would
    duplicate it, and duplicating it is how it leaks."""
    session = await _bootstrap(app_client)
    action = "I whisper the passphrase Mockingbird to the guard."
    await app_client.post(f"/api/v1/sessions/{session}/turns", json={"action": action})

    raw = (await app_client.get(f"/api/v1/dev/sessions/{session}/llm-performance")).text

    assert "Mockingbird" not in raw
    assert "World rules" not in raw


async def test_the_page_limit_is_bounded(app_client: AsyncClient) -> None:
    assert (await app_client.get("/api/v1/dev/llm/performance?limit=1000")).status_code == 422
    assert (await app_client.get("/api/v1/dev/llm/performance?limit=0")).status_code == 422
    assert (await app_client.get("/api/v1/dev/llm/performance?limit=5")).status_code == 200


async def test_an_unknown_session_is_empty_rather_than_a_404(app_client: AsyncClient) -> None:
    """This reads a buffer, not the database. A 404 would mean "nothing generated yet"
    as often as it meant "no such session"."""
    body = (await app_client.get(f"/api/v1/dev/sessions/{uuid.uuid4()}/llm-performance")).json()
    assert body["summary"]["sample_count"] == 0
    assert body["generations"] == []


async def _bootstrap(client: AsyncClient) -> str:
    world = (await client.post("/api/v1/worlds", json={"name": "W", "genre": "fantasy"})).json()
    await client.post(
        f"/api/v1/worlds/{world['id']}/characters",
        json={"name": "Elena", "personality": "sarcastic"},
    )
    session = (
        await client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()
    return str(session["id"])
