"""Test fixtures. Every test gets an isolated temporary SQLite database."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import StoryGeneratorPort
from app.application.rules_projection import project_world_rules
from app.application.story_context import (
    PlayerContext,
    SessionContext,
    StoryContext,
    WorldContext,
)
from app.config import ImageProvider, Settings, StoryProvider
from app.domain.enums import Language
from app.domain.world_rules import default_world_rules
from app.infrastructure.db.base import Base
from app.infrastructure.db.engine import create_engine, create_session_factory
from app.infrastructure.db.models import Character, World
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    db_path = (tmp_path / "test.db").as_posix()
    return Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        story_provider=StoryProvider.MOCK,
        image_provider=ImageProvider.DISABLED,
        app_env="test",
    )


@pytest.fixture
async def app_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """App wired against a fresh database.

    Schema is created directly from metadata here rather than by running Alembic:
    the migration is verified separately by `test_migrations.py`, and doing it per
    test would dominate the runtime.
    """
    app = create_app(settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        engine = app.state.engine
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield client


@pytest.fixture
async def db_session(settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = create_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def make_story_context():
    """Minimal StoryContext for exercising story providers directly."""

    def _make(action: str = "I look around.") -> StoryContext:
        return StoryContext(
            world=WorldContext(
                id=uuid.uuid4(),
                name="W",
                description="",
                genre="fantasy",
                setting="a town",
                language=Language.EN,
            ),
            world_rules=project_world_rules(default_world_rules()),
            player=PlayerContext(name="Rin", description=""),
            session=SessionContext(
                id=uuid.uuid4(), title="s", current_location="", summary="", turn_index=0
            ),
            player_action=action,
        )

    return _make


@pytest.fixture
def make_world():
    def _make(**overrides: object) -> World:
        data: dict[str, object] = {
            "name": f"World {uuid.uuid4().hex[:6]}",
            "description": "A test world.",
            "genre": "fantasy",
            "setting": "a quiet town",
        }
        data.update(overrides)
        return World(**data)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def make_character():
    def _make(world_id: uuid.UUID, **overrides: object) -> Character:
        data: dict[str, object] = {
            "world_id": world_id,
            "name": "Elena",
            "description": "A mage.",
            "personality": "sarcastic, cautious",
            "speech_style": "dry",
            "goals": ["find the truth"],
            "secrets": ["knows the player"],
        }
        data.update(overrides)
        return Character(**data)  # type: ignore[arg-type]

    return _make


class FailingStoryGenerator:
    """Story provider that always fails, for transaction-rollback tests."""

    name = "failing"

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def generate_turn(self, context: object) -> object:
        raise self._error

    async def status(self) -> object:  # pragma: no cover - not exercised
        raise NotImplementedError


def override_story_generator(client: AsyncClient, generator: StoryGeneratorPort) -> None:
    """Swap the provider on the live app instance."""
    transport = client._transport
    assert isinstance(transport, ASGITransport)
    transport.app.state.story_generator = generator  # type: ignore[union-attr]
