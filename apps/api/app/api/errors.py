"""Maps domain errors to consistent HTTP responses."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.schemas import ErrorResponse
from app.domain.errors import (
    ImageGenerationError,
    NotFoundError,
    StaleStateError,
    StoryGenerationError,
    ValidationError,
)

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(error="not_found", detail=str(exc)).model_dump(),
        )

    @app.exception_handler(ValidationError)
    async def _invalid(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=ErrorResponse(error="invalid_request", detail=str(exc)).model_dump(),
        )

    @app.exception_handler(StaleStateError)
    async def _stale(_: Request, exc: StaleStateError) -> JSONResponse:
        # 409, not 422: the request was fine, the world simply moved underneath it.
        # The useful next step is re-read and retry, which is what a conflict means.
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ErrorResponse(error="stale_state", detail=str(exc)).model_dump(),
        )

    @app.exception_handler(StoryGenerationError)
    async def _story_failed(_: Request, exc: StoryGenerationError) -> JSONResponse:
        # 502: the failure is in an upstream provider, not in the client's request.
        logger.error("Story generation failed via %s: %s", exc.provider, exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=ErrorResponse(
                error="story_generation_failed",
                detail=str(exc),
                provider=exc.provider,
                retryable=exc.retryable,
            ).model_dump(),
        )

    @app.exception_handler(ImageGenerationError)
    async def _image_failed(_: Request, exc: ImageGenerationError) -> JSONResponse:
        logger.error("Image generation failed via %s: %s", exc.provider, exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=ErrorResponse(
                error="image_generation_failed", detail=str(exc), provider=exc.provider
            ).model_dump(),
        )
