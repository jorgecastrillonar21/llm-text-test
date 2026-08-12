"""Ports to external AI systems.

Application services depend on these Protocols only. Concrete Ollama/ComfyUI
clients live in app.infrastructure and are selected by configuration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.application.contracts import OutcomeNarration, TurnGeneration
from app.application.llm_metrics import LlmGenerationMetrics, TurnPerformanceMetrics
from app.application.story_context import OutcomeContext, StoryContext

ProviderState = Literal["ready", "unreachable", "misconfigured", "disabled"]


class ProviderStatus(BaseModel):
    provider: str
    state: ProviderState
    detail: str = ""
    model: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.state == "ready"


class ImageGenerationRequest(BaseModel):
    scene_prompt: str
    seed: int | None = None
    negative_prompt: str = ""


class ImageGenerationResult(BaseModel):
    """Identifies submitted work. Retrieving finished pixels is Phase 4."""

    job_id: str
    provider: str
    status: Literal["queued", "completed", "mocked"]
    detail: str = ""


class TurnGenerationResult(BaseModel):
    """A generated turn, and what producing it cost.

    The wrapper exists so performance data can reach observability without the
    application ever touching a provider's response JSON. `generation` is the only field
    any game mechanic may read; nothing downstream branches on `metrics`, and a provider
    that reports none still plays a perfectly ordinary turn.
    """

    model_config = ConfigDict(frozen=True)

    generation: TurnGeneration
    metrics: LlmGenerationMetrics | None = None
    """None when the provider reported nothing measurable. Optional rather than a
    zero-filled record: absent measurements and measured zeros are different facts."""


class OutcomeNarrationResult(BaseModel):
    """Prose for a committed outcome, and what producing it cost."""

    model_config = ConfigDict(frozen=True)

    narration: OutcomeNarration
    metrics: LlmGenerationMetrics | None = None


class StoryGeneratorPort(Protocol):
    name: str

    async def generate_turn(self, context: StoryContext) -> TurnGenerationResult: ...

    async def narrate_outcome(self, context: OutcomeContext) -> OutcomeNarrationResult:
        """Describe an outcome the game has already committed.

        A separate method rather than a flag on `generate_turn`, because the two are
        different jobs with different contracts: one proposes and is reviewed, the other
        describes what survived review and is not. Failure raises `StoryGenerationError`
        like any provider failure -- and unlike a failed turn, a failed narration cannot
        take the outcome down with it, because the outcome was committed before this was
        called. See `app.application.narration_service`.
        """
        ...

    async def status(self) -> ProviderStatus: ...


class StoryChunk(BaseModel):
    """One piece of a streamed generation.

    Text only, plus the terminal marker and the metrics that arrive with it. There is
    deliberately no partial `TurnGeneration` here: a half-decoded structured document is
    not a smaller version of a valid one, and a consumer given one would be given the
    opportunity to act on a proposal the model had not finished making.
    """

    model_config = ConfigDict(frozen=True)

    text: str = ""
    done: bool = False
    metrics: LlmGenerationMetrics | None = None
    """Present on the final chunk only. Streaming is the only transport that can
    measure time to first token, so this is the record that carries one."""


@runtime_checkable
class StreamingStoryGeneratorPort(Protocol):
    """The optional half of the story port, for providers that can stream.

    Separate from `StoryGeneratorPort` because it is genuinely optional: the mock cannot
    stream in any meaningful sense, and a caller must be able to ask "can this provider
    stream?" rather than call and hope. `isinstance` against this protocol is the check,
    which is what `runtime_checkable` is for.

    Nothing in the HTTP API consumes this yet, and that is the intended state for this
    epic -- transporting chunks to a browser is a change to the request/response shape of
    the whole turn endpoint, and the point here was the seam, not the redesign. What the
    seam buys immediately is `time_to_first_token_ms`, which cannot be measured any other
    way and cannot be honestly derived from a total.
    """

    name: str

    def stream_turn(self, context: StoryContext) -> AsyncIterator[StoryChunk]:
        """Yield the turn as it is produced, ending with a chunk carrying metrics.

        Not `async def`: an async generator function returns its iterator immediately,
        and declaring it `async` here would demand `await` at every call site for a
        value that is never a coroutine.
        """
        ...


class LlmMetricsRecorderPort(Protocol):
    """Where performance records go.

    Deliberately synchronous and deliberately allowed to be lossy. Recording is not
    allowed to fail a turn -- see `record_generation_safely` -- and an implementation
    that needed to be awaited would put telemetry on the same critical path as the work
    it measures.
    """

    def record_generation(self, metrics: LlmGenerationMetrics) -> None: ...

    def record_turn(self, metrics: TurnPerformanceMetrics) -> None: ...


class ImageGeneratorPort(Protocol):
    name: str

    async def status(self) -> ProviderStatus: ...

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...
