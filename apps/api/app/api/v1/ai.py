from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ImageGen, StoryGen
from app.api.schemas import AiStatusResponse, ProviderStatusRead

router = APIRouter(tags=["ai"])


@router.get("/ai/status", response_model=AiStatusResponse)
async def ai_status(story: StoryGen, image: ImageGen) -> AiStatusResponse:
    """Diagnostic view of both providers. Never raises: an unreachable provider is
    reported as state=unreachable rather than failing the request."""
    return AiStatusResponse(
        story=ProviderStatusRead.model_validate((await story.status()).model_dump()),
        image=ProviderStatusRead.model_validate((await image.status()).model_dump()),
    )
