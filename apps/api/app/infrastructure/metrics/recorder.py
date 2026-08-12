"""Where LLM performance records actually go.

Two sinks, both deliberately small:

**A structured log line per generation.** One record, on one line, with the numbers a
developer needs to answer "why was that turn slow" without attaching a debugger. Slow
calls, exhausted budgets and near-full context windows are raised to `warning` so they
surface in an ordinary console without anyone having to go looking.

**A bounded in-memory ring buffer.** The last `LLM_METRICS_BUFFER_SIZE` records, served
by the dev diagnostics endpoints. In-process and lost on restart, which is the correct
lifetime for a developer's recent history and the reason there is no table, no migration
and no external collector. A metrics platform is a real thing to want and not a thing
Epic 1 needs; when it is wanted, it arrives as another implementation of
`LlmMetricsRecorderPort`, and nothing in the application changes.

No prompts. No generated text. The buffer holds counts, durations and identifiers, so a
diagnostics endpoint cannot become a way to read someone's story, and the log cannot
become a second copy of the prompt corpus. See `docs/llm-performance-baseline.md`.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque

from app.application.llm_metrics import (
    GenerationStatus,
    LlmGenerationMetrics,
    TurnPerformanceMetrics,
)

logger = logging.getLogger("app.llm.performance")

_HIGH_CONTEXT_UTILIZATION = 0.9
"""Warn above this. Not a limit -- Ollama will happily evaluate a full window -- but the
point past which the next few turns of history are likely to start pushing the head of
the prompt out, silently."""


class InMemoryLlmMetricsRecorder:
    """Logs every record and keeps the most recent ones for diagnostics.

    Thread-safe because the buffer is read by request handlers and written by whatever
    worker finished a generation, and `deque` is only atomic per-operation. The lock is
    held for appends and snapshot copies -- microseconds, off the generation path.
    """

    def __init__(self, *, buffer_size: int, slow_call_threshold_ms: float) -> None:
        self._generations: deque[LlmGenerationMetrics] = deque(maxlen=buffer_size)
        self._turns: deque[TurnPerformanceMetrics] = deque(maxlen=buffer_size)
        self._slow_call_threshold_ms = slow_call_threshold_ms
        self._lock = threading.Lock()

    def record_generation(self, metrics: LlmGenerationMetrics) -> None:
        with self._lock:
            self._generations.append(metrics)
        logger.log(
            self._level_for(metrics),
            "llm.generation %s",
            self._summarize(metrics),
            extra={"llm_generation": metrics.model_dump(mode="json")},
        )

    def record_turn(self, metrics: TurnPerformanceMetrics) -> None:
        with self._lock:
            self._turns.append(metrics)
        logger.info(
            "llm.turn session=%s turn=%s total_ms=%.1f story_ms=%.1f app_ms=%.1f llm_calls=%d",
            metrics.session_id,
            metrics.turn_index,
            metrics.total_turn_ms,
            metrics.story_generation_ms,
            metrics.non_llm_application_ms,
            metrics.llm_call_count,
            extra={"turn_performance": metrics.model_dump(mode="json")},
        )

    def recent_generations(
        self, *, limit: int, session_id: uuid.UUID | None = None
    ) -> list[LlmGenerationMetrics]:
        """Most recent first, optionally for one session."""
        with self._lock:
            records = list(self._generations)
        if session_id is not None:
            records = [record for record in records if record.session_id == session_id]
        records.reverse()
        return records[:limit]

    def recent_turns(
        self, *, limit: int, session_id: uuid.UUID | None = None
    ) -> list[TurnPerformanceMetrics]:
        with self._lock:
            records = list(self._turns)
        if session_id is not None:
            records = [record for record in records if record.session_id == session_id]
        records.reverse()
        return records[:limit]

    def _level_for(self, metrics: LlmGenerationMetrics) -> int:
        """Raise the level when a record is trying to tell someone something.

        A failed call, a truncated output or a context window nearly full are all things
        that stay invisible at `info` on a busy console and all things that explain a
        broken turn. A merely slow call gets the same treatment: on local hardware slow
        is the normal failure mode, and noticing it is the entire point of this epic.
        """
        if metrics.status is GenerationStatus.ERROR:
            return logging.ERROR
        if metrics.output_budget_reached:
            return logging.WARNING
        utilization = metrics.prompt_context_utilization
        if utilization is not None and utilization >= _HIGH_CONTEXT_UTILIZATION:
            return logging.WARNING
        total = metrics.total_ms if metrics.total_ms is not None else metrics.client_elapsed_ms
        if total is not None and total >= self._slow_call_threshold_ms:
            return logging.WARNING
        return logging.INFO

    def _summarize(self, metrics: LlmGenerationMetrics) -> str:
        """One line, ordered so the usual questions are answered left to right.

        Deliberately hand-built rather than a dict dump: this is what a human reads in a
        terminal. The full record goes to `extra` for anything machine-shaped.
        """
        parts = [
            f"purpose={metrics.purpose.value}",
            f"provider={metrics.provider}",
            f"model={metrics.model}",
            f"status={metrics.status.value}",
        ]
        if metrics.session_id is not None:
            parts.append(f"session={metrics.session_id}")
        if not metrics.provider_metrics_available:
            parts.append("provider_metrics=unavailable")
        if metrics.error_code:
            parts.append(f"error={metrics.error_code}")
        parts.extend(
            [
                f"prompt_tokens={_num(metrics.prompt_tokens)}",
                f"generated_tokens={_num(metrics.generated_tokens)}",
                f"budget={_num(metrics.configured_max_output_tokens)}",
                f"budget_reached={str(metrics.output_budget_reached).lower()}",
                f"done={metrics.done_reason.value}",
                f"context_window={_num(metrics.configured_context_window)}",
                f"context_utilization={_num(metrics.prompt_context_utilization)}",
                f"total_ms={_num(metrics.total_ms)}",
                f"load_ms={_num(metrics.load_ms)}",
                f"prompt_eval_ms={_num(metrics.prompt_eval_ms)}",
                f"generation_ms={_num(metrics.generation_ms)}",
                f"prompt_tps={_num(metrics.prompt_tokens_per_second)}",
                f"generation_tps={_num(metrics.generation_tokens_per_second)}",
                f"client_ms={_num(metrics.client_elapsed_ms)}",
            ]
        )
        if metrics.time_to_first_token_ms is not None:
            parts.append(f"ttft_ms={metrics.time_to_first_token_ms}")
        parts.append(f"request={metrics.request_id}")
        return " ".join(parts)


def _num(value: float | int | None) -> str:
    """`-` for a missing measurement, never `0`.

    The distinction matters more here than anywhere: a zero that meant "not reported"
    would drag every average toward it and make a provider that reports nothing look
    infinitely fast.
    """
    return "-" if value is None else str(value)
