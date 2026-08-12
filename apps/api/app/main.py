"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.schemas import HealthResponse
from app.api.v1 import dev
from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.infrastructure.db.engine import create_engine, create_session_factory, verify_schema
from app.infrastructure.images.factory import build_image_generator
from app.infrastructure.metrics import InMemoryLlmMetricsRecorder
from app.infrastructure.story.factory import build_story_generator

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # Fails fast on impossible configuration (e.g. ollama selected with no model).
    settings.validate_runtime()

    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.story_generator = build_story_generator(settings)
    app.state.image_generator = build_image_generator(settings)
    # One per application, because its buffer is the process's recent history. Built in
    # lifespan alongside the providers rather than per request: a recorder rebuilt for
    # each call would have nothing to remember.
    app.state.llm_metrics_recorder = InMemoryLlmMetricsRecorder(
        buffer_size=settings.llm_metrics_buffer_size,
        slow_call_threshold_ms=settings.llm_slow_call_threshold_ms,
    )

    if not await verify_schema(engine):
        logger.warning(
            "Database schema not found at %s. Run: pnpm db:migrate", settings.database_url
        )

    logger.info(
        "Ready. story_provider=%s image_provider=%s",
        settings.story_provider,
        settings.image_provider,
    )
    try:
        yield
    finally:
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    logging.basicConfig(level=resolved.log_level.upper())

    app = FastAPI(
        title="OOC Clone API",
        description="Local-first AI-driven interactive anime/RPG engine.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router)

    # Mounted per application instance rather than folded into `api_router`, which is
    # a module-level singleton: conditionally including a route into it would leak
    # into every app built afterwards in the same process.
    if resolved.dev_endpoints_enabled:
        app.include_router(dev.router, prefix="/api/v1")

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app_env=resolved.app_env,
            database_ready=await verify_schema(app.state.engine),
        )

    return app


app = create_app()
