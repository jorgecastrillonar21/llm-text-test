"""Non-ComfyUI image providers: disabled (default) and an explicit mock."""

from __future__ import annotations

import uuid

from app.application.ports import ImageGenerationRequest, ImageGenerationResult, ProviderStatus
from app.domain.errors import ImageGenerationError


class DisabledImageGenerator:
    name = "disabled"

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.name,
            state="disabled",
            detail="Image generation is off. Set IMAGE_PROVIDER=mock or comfyui to enable.",
        )

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        raise ImageGenerationError(
            "Image generation is disabled (IMAGE_PROVIDER=disabled).", provider=self.name
        )


class MockImageGenerator:
    """Records requests and returns a fake job id. Generates no pixels."""

    name = "mock"

    async def status(self) -> ProviderStatus:
        return ProviderStatus(
            provider=self.name,
            state="ready",
            detail="Mock image provider. No images are actually produced.",
        )

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        return ImageGenerationResult(
            job_id=f"mock-{uuid.uuid4()}",
            provider=self.name,
            status="mocked",
            detail=f"Would render: {request.scene_prompt[:120]}",
        )
