"""Ports to external AI systems.

Application services depend on these Protocols only. Concrete Ollama/ComfyUI
clients live in app.infrastructure and are selected by configuration.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.application.contracts import TurnGeneration
from app.application.story_context import StoryContext

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


class StoryGeneratorPort(Protocol):
    name: str

    async def generate_turn(self, context: StoryContext) -> TurnGeneration: ...

    async def status(self) -> ProviderStatus: ...


class ImageGeneratorPort(Protocol):
    name: str

    async def status(self) -> ProviderStatus: ...

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...
