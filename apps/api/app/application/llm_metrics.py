"""What one call to a language model cost, and how it ended.

These are *technical* records. A `LlmGenerationMetrics` is not a `GameEvent`, not part
of `WorldState`, not a `Memory` and not a `Resolution`: nothing here moves
`state_revision`, nothing here is written inside a resolution's transaction, and nothing
here is ever assembled back into a `StoryContext`. A model told that its last reply took
fifty-eight seconds would start writing about it.

The separation is worth stating because the two kinds of record look similar from a
distance -- both are append-only, both are timestamped, both describe something that
happened. The difference is authority. Deleting every metric in this process changes
nothing about what is true in any world; deleting one `GameEvent` changes history.

# Why tokens and not characters

A character budget is a budget in the wrong unit. The model does not see characters, it
sees tokens, and the ratio between them moves with the language, the tokenizer and the
content -- measured Spanish prose in this project sits near 3.0 characters per token
while a JSON document full of uuids sits far lower. A limit expressed in characters is
therefore a different limit for every world, which is the one thing a limit must not be.

HTTP-level byte caps still exist elsewhere and are a different concern: they protect the
transport, not the context window.

# Units

Ollama reports nanoseconds. Nothing outside the adapter should have to know that, so
everything here is milliseconds, and the conversion happens once at the boundary.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.domain.errors import StoryGenerationError


class GenerationPurpose(StrEnum):
    """Why the model was called.

    A closed set, deliberately -- not a free-text analytics label. Every metric record
    carries one, because "generation is slow" is not an actionable sentence until you
    know which of the calls a turn makes was the slow one.

    Only the purposes this application can actually distinguish today are listed. Later
    epics will add `npc_dialogue`, `intent_interpretation`, `memory_consolidation` and
    `npc_autonomy`; adding a member here without the call site that produces it would
    put a value in the data that no measurement can ever contain.
    """

    STORY_TURN = "story_turn"
    """The Story Director generating a turn: the one call on the critical path."""

    OUTCOME_NARRATION = "outcome_narration"
    """Describing a resolution the game has already committed. Off the critical path
    in the sense that matters -- the world has already changed when this runs -- but
    still synchronous in the request that asks for it."""


class GenerationDoneReason(StrEnum):
    """How generation ended, as far as the provider was willing to say."""

    STOP = "stop"
    """The model finished on its own. The only reason that means the output is whole."""

    LENGTH = "length"
    """The output token budget ran out. The text is cut off mid-thought, and treating
    this as a normal completion is how a truncated narrative reaches a player."""

    CONTEXT = "context"
    """The context window ran out."""

    ERROR = "error"
    """The request failed. There is no output, and no token counts are invented."""

    UNKNOWN = "unknown"
    """The provider did not say, or said something this version does not recognise.
    Distinct from `stop` on purpose: "it finished cleanly" is a claim, and a claim
    nobody made should not be attributed to the provider."""


class GenerationStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class GenerationOptions(BaseModel):
    """What the application asked the provider to do, in provider-neutral terms.

    `num_predict` and `num_ctx` are Ollama's spellings and stay in the Ollama adapter.
    A second provider will map these same three fields onto whatever it calls them,
    and the application will not have to learn a new vocabulary to configure it.
    """

    model_config = ConfigDict(frozen=True)

    max_output_tokens: int | None = None
    """The output budget. None means "provider default", which for local models is
    usually unlimited -- acceptable for a one-off, wrong as a standing configuration."""

    context_window: int
    temperature: float


def ns_to_ms(nanoseconds: object) -> float | None:
    """Provider nanoseconds as milliseconds, or None when the provider said nothing.

    Non-integers and negatives return None rather than raising or coercing. This runs on
    a dict decoded from someone else's JSON, and a metrics helper that can fail the
    generation it is measuring has the priorities backwards.
    """
    if isinstance(nanoseconds, bool) or not isinstance(nanoseconds, int | float):
        return None
    if nanoseconds < 0:
        return None
    return round(float(nanoseconds) / 1_000_000, 3)


def tokens_per_second(tokens: int | None, milliseconds: float | None) -> float | None:
    """Throughput, or None when it is not a number anybody should read.

    Zero duration is the case that matters. It happens for real -- a prompt served
    entirely from Ollama's cache reports `prompt_eval_duration: 0` -- and the honest
    answer is "no rate", not infinity and not zero. A zero would read as "the prompt
    evaluated at 0 tokens/sec", which is the opposite of what happened.
    """
    if tokens is None or milliseconds is None:
        return None
    if tokens <= 0 or milliseconds <= 0:
        return None
    return round(tokens / (milliseconds / 1000), 2)


class LlmGenerationMetrics(BaseModel):
    """One call to a model: what went in, what came out, and how long each part took.

    Every field is optional except the identifying ones, because every field is
    something a provider might not report. A metric that is absent is `None`; it is
    never zero and never estimated. `prompt_tokens` in particular is the provider's own
    count of what it evaluated -- not a character count divided by a guess -- which is
    what makes it usable as the primary prompt-size measurement.
    """

    model_config = ConfigDict(frozen=True)

    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    provider: str
    model: str
    purpose: GenerationPurpose
    status: GenerationStatus = GenerationStatus.OK

    session_id: uuid.UUID | None = None
    """Attached by the application, not the provider. A provider is handed a
    `StoryContext` and has no business knowing which save it belongs to; the service
    that called it does. Absent on paths where no session is in scope."""

    provider_metrics_available: bool = True
    """False when the record is a stand-in rather than a measurement -- the mock
    provider, or a failed request. Nothing downstream should have to infer that from a
    pattern of Nones, and a summary that averaged mock numbers into real ones would be
    quietly wrong."""

    # -- input
    prompt_tokens: int | None = None
    configured_context_window: int | None = None

    # -- output
    generated_tokens: int | None = None
    configured_max_output_tokens: int | None = None
    done_reason: GenerationDoneReason = GenerationDoneReason.UNKNOWN

    # -- timing, milliseconds
    total_ms: float | None = None
    """The provider's own total. Not the same as `client_elapsed_ms`: the difference is
    transport, serialization and this process's own scheduling."""

    load_ms: float | None = None
    """Time spent making the model resident. Large on the first call after a cold start
    or after `keep_alive` expired, near zero on a warm one -- which is what makes this
    the field to look at before blaming the model for a slow turn."""

    prompt_eval_ms: float | None = None
    generation_ms: float | None = None

    time_to_first_token_ms: float | None = None
    """Only measurable when the response is streamed. None on the non-streaming path,
    and deliberately not derived from `total_ms` minus something -- an invented TTFT
    would make a latency regression invisible in the one number meant to catch it."""

    client_elapsed_ms: float | None = None
    """Wall clock around the provider call, measured by the application."""

    error_code: str | None = None
    """Short, stable, and safe to log: `connect_error`, `timeout`, `http_500`. Never
    the response body, which can contain the prompt."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prompt_tokens_per_second(self) -> float | None:
        return tokens_per_second(self.prompt_tokens, self.prompt_eval_ms)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def generation_tokens_per_second(self) -> float | None:
        return tokens_per_second(self.generated_tokens, self.generation_ms)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def prompt_context_utilization(self) -> float | None:
        """Prompt tokens as a fraction of the configured window, 0..1+.

        A ratio against `num_ctx`, not against anything the model knows about itself.
        It says how close this prompt came to the budget this application set, which is
        the number that predicts a truncated context; it says nothing about attention,
        KV cache pressure or what the weights were trained on.

        Can exceed 1.0. That is not a bug in the arithmetic -- it is the provider
        reporting that it evaluated more than the window this application asked for,
        which is worth seeing rather than clamping away.
        """
        if self.prompt_tokens is None or not self.configured_context_window:
            return None
        return round(self.prompt_tokens / self.configured_context_window, 4)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def output_budget_reached(self) -> bool:
        """Whether the output stopped because it ran out of budget rather than ideas.

        Two independent signals, either of which is enough. `done_reason == length` is
        the provider saying so and is authoritative when present; the count comparison
        catches providers that report a budget stop as a normal one. A false positive is
        possible -- a model that happens to finish on exactly the last permitted token --
        and is a fair trade for never missing a truncated turn.
        """
        if self.done_reason is GenerationDoneReason.LENGTH:
            return True
        budget = self.configured_max_output_tokens
        return (
            budget is not None
            and budget > 0
            and self.generated_tokens is not None
            and self.generated_tokens >= budget
        )

    def for_session(self, session_id: uuid.UUID | None) -> Self:
        """The same record, attributed to a save.

        Frozen models are replaced rather than mutated, so the provider's answer and the
        application's annotation of it can never be confused for one edit.
        """
        if session_id is None or session_id == self.session_id:
            return self
        return self.model_copy(update={"session_id": session_id})


def failed_generation_metrics(
    error: StoryGenerationError,
    *,
    purpose: GenerationPurpose,
    session_id: uuid.UUID | None = None,
    fallback_elapsed_ms: float | None = None,
) -> LlmGenerationMetrics:
    """A metrics record for a call that failed.

    A failure is a measurement. An epic that starts timing out on half its turns should
    be visible in the performance record and not only in a stack trace, and "how long did
    it take to fail" separates a refused connection from a ninety-second timeout -- the
    same exception type, entirely different problems.

    What is deliberately absent is token counts. A failed call generated nothing, and a
    zero here would average into throughput as though the model had produced zero tokens
    very quickly. `provider_metrics_available=False` says outright that nothing in this
    record came from the provider's own accounting.

    The elapsed time prefers the adapter's own figure and falls back to the caller's.
    The adapter's is tighter -- it starts at the request rather than around the whole
    call -- but a caller that timed the attempt should not lose the number because a
    failure path forgot to fill one in.
    """
    return LlmGenerationMetrics(
        provider=error.provider,
        model=error.model or "unknown",
        purpose=purpose,
        status=GenerationStatus.ERROR,
        session_id=session_id,
        provider_metrics_available=False,
        done_reason=GenerationDoneReason.ERROR,
        client_elapsed_ms=error.elapsed_ms if error.elapsed_ms is not None else fallback_elapsed_ms,
        error_code=error.error_code,
    )


class TurnPerformanceMetrics(BaseModel):
    """Where a turn's wall clock actually went.

    This exists to answer one question and it is worth being blunt about which: when a
    turn takes sixty seconds, was that fifty-eight seconds of Ollama and two of us, or
    thirty and thirty? Those have nothing in common as problems, and without this record
    the only way to tell them apart is a stopwatch and a guess.

    Measured at the application boundary -- around `execute_turn`, and around the
    provider call inside it. Individual fact and location queries are deliberately not
    instrumented: this epic is establishing which half the time is in, and per-query
    timing is what you reach for after that answer says the database.
    """

    model_config = ConfigDict(frozen=True)

    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    session_id: uuid.UUID
    turn_index: int | None = None

    total_turn_ms: float
    story_generation_ms: float
    """Wall clock around the provider call, including transport. Compare with the
    provider's own `total_ms` to see what the transport cost."""

    llm_call_count: int
    """How many model calls this turn made. One, in this build, on every path that
    succeeds -- and the number that later epics have to justify moving. An epic that
    quietly makes it four has changed the cost of playing the game by four times, and
    this is the field that says so."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def non_llm_application_ms(self) -> float:
        """Everything that was not waiting for a model: context assembly, the reviews,
        the writes, the commit. Clamped at zero -- the two measurements are taken by
        different clocks and a negative would be measurement noise reported as fact."""
        return round(max(0.0, self.total_turn_ms - self.story_generation_ms), 3)


class LlmPerformanceSummary(BaseModel):
    """An aggregate over recent generations. Latest, average and worst.

    Three numbers rather than one because they answer different questions: the latest is
    "is it slow right now", the average is "is it slow in general", and the max is "how
    bad does it get" -- which on local hardware is usually a cold model load and is the
    figure a player would describe as the app hanging.

    Every field is optional, because a summary over records that reported nothing has
    nothing to report. Averages are taken over the records that actually carried the
    field, so a provider that reports durations but not token counts still yields a
    useful latency summary.
    """

    model_config = ConfigDict(frozen=True)

    sample_count: int
    """Records the summary was built from, after filtering. Zero is a valid answer and
    means nobody has generated anything yet -- not that generation is instant."""

    measured_sample_count: int
    """Of those, how many came from a provider that reports its own metrics. Mock and
    failed records are counted in `sample_count` and excluded from every figure below,
    so a summary taken on a mock-backed dev server reads 0 rather than reading fast."""

    latest_total_ms: float | None = None
    average_total_ms: float | None = None
    max_total_ms: float | None = None

    latest_prompt_tokens: int | None = None
    average_prompt_tokens: float | None = None
    max_prompt_tokens: int | None = None

    latest_generated_tokens: int | None = None
    average_generated_tokens: float | None = None
    max_generated_tokens: int | None = None

    average_generation_tokens_per_second: float | None = None
    max_prompt_context_utilization: float | None = None

    budget_reached_count: int = 0
    """Generations that stopped at the output budget. Any non-zero value here is worth
    acting on: for a schema-constrained turn it means truncated JSON and a failed turn,
    not merely a shorter one."""

    error_count: int = 0


def summarize_generations(records: list[LlmGenerationMetrics]) -> LlmPerformanceSummary:
    """Aggregate recent generation records, newest first.

    Only records whose numbers came from a provider are aggregated. A mock's plausible
    stand-ins and a failed call's absent ones would both drag every figure toward
    nonsense, and a summary that silently mixed them would be most misleading exactly
    where it is most used -- a dev server running the mock.
    """
    measured = [record for record in records if record.provider_metrics_available]
    totals = _present(measured, lambda record: record.total_ms)
    prompts = _present(measured, lambda record: record.prompt_tokens)
    generated = _present(measured, lambda record: record.generated_tokens)
    return LlmPerformanceSummary(
        sample_count=len(records),
        measured_sample_count=len(measured),
        latest_total_ms=_first(totals),
        average_total_ms=_mean(totals),
        max_total_ms=max(totals, default=None),
        latest_prompt_tokens=_first(prompts),
        average_prompt_tokens=_mean(prompts),
        max_prompt_tokens=max(prompts, default=None),
        latest_generated_tokens=_first(generated),
        average_generated_tokens=_mean(generated),
        max_generated_tokens=max(generated, default=None),
        average_generation_tokens_per_second=_mean(
            _present(measured, lambda record: record.generation_tokens_per_second)
        ),
        max_prompt_context_utilization=max(
            _present(measured, lambda record: record.prompt_context_utilization), default=None
        ),
        budget_reached_count=sum(1 for record in records if record.output_budget_reached),
        error_count=sum(1 for record in records if record.status is GenerationStatus.ERROR),
    )


def _present[T: (int, float)](
    records: list[LlmGenerationMetrics], extract: Callable[[LlmGenerationMetrics], T | None]
) -> list[T]:
    """The values that exist. A missing measurement is skipped, never read as zero.

    Generic over the numeric type so `max_prompt_tokens` stays an `int` and
    `max_total_ms` stays a `float`, without an `Any` in the middle erasing both.
    """
    return [value for value in (extract(record) for record in records) if value is not None]


def _first[T: (int, float)](values: list[T]) -> T | None:
    """The most recent value, given a newest-first list."""
    return values[0] if values else None


def _mean(values: list[int] | list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None
