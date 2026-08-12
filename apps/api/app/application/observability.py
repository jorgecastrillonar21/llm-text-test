"""Recording performance data without letting it break anything.

One rule, and everything in this module is it: **generation is primary, observability is
secondary**. A turn that produced a good narration and then failed to write a log line
is a successful turn with a missing log line. Propagating that failure would roll back
the player's turn -- the message, the events, the facts, the clock -- because a metrics
sink was full, which is an absurd trade and exactly the kind that happens by default when
telemetry is called inline.

So the calls here swallow. That is a deliberate exception to the project's rule against
swallowing exceptions, and it is bounded in two ways: it applies only to recording, and
it is never silent. Every swallowed failure is logged at warning with its traceback, so a
recorder that is broken is visible to the developer without being visible to the player.
"""

from __future__ import annotations

import logging

from app.application.llm_metrics import LlmGenerationMetrics, TurnPerformanceMetrics
from app.application.ports import LlmMetricsRecorderPort

logger = logging.getLogger(__name__)


def record_generation_safely(
    recorder: LlmMetricsRecorderPort | None, metrics: LlmGenerationMetrics | None
) -> None:
    """Hand one generation's metrics to the recorder. Never raises.

    Both arguments are optional because both absences are ordinary: a caller with no
    recorder wired (a test, a script) and a provider that reported nothing measurable.
    """
    if recorder is None or metrics is None:
        return
    try:
        recorder.record_generation(metrics)
    except Exception:  # Deliberate: see the module docstring.
        logger.warning(
            "Failed to record LLM generation metrics for request %s (purpose=%s). "
            "The generation itself succeeded and is unaffected.",
            metrics.request_id,
            metrics.purpose.value,
            exc_info=True,
        )


def record_turn_safely(
    recorder: LlmMetricsRecorderPort | None, metrics: TurnPerformanceMetrics | None
) -> None:
    """Hand one turn's latency breakdown to the recorder. Never raises."""
    if recorder is None or metrics is None:
        return
    try:
        recorder.record_turn(metrics)
    except Exception:  # Deliberate: see the module docstring.
        logger.warning(
            "Failed to record turn performance metrics for session %s. "
            "The turn itself succeeded and is unaffected.",
            metrics.session_id,
            exc_info=True,
        )
