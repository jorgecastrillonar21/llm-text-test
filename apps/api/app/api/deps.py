"""Request-scoped dependencies.

Engine, session factory and providers are created once during lifespan and stored
on app.state -- no module-level globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.persistence import TurnGatewayPort
from app.application.ports import ImageGeneratorPort, StoryGeneratorPort
from app.config import Settings
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped session that never commits implicitly.

    Committing in this dependency's teardown would be wrong: FastAPI closes
    `yield` dependencies *after* the response has been sent, so a client that
    immediately re-reads could observe state from before its own write landed.
    Write endpoints therefore commit explicitly, inside the handler.

    Teardown only guarantees that a failed request leaves nothing half-written.
    """
    async with request.app.state.session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_turn_gateway(session: Annotated[AsyncSession, Depends(get_db)]) -> TurnGatewayPort:
    """Bind the SQLAlchemy adapter to this request's transaction.

    This is the only place the turn use case and the ORM meet. `execute_turn`
    receives the port; rollback on failure stays with `get_db`, whose teardown
    owns the session it created.
    """
    return SqlAlchemyTurnGateway(session)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_story_generator(request: Request) -> StoryGeneratorPort:
    generator: StoryGeneratorPort = request.app.state.story_generator
    return generator


def get_image_generator(request: Request) -> ImageGeneratorPort:
    generator: ImageGeneratorPort = request.app.state.image_generator
    return generator


DbSession = Annotated[AsyncSession, Depends(get_db)]
TurnGateway = Annotated[TurnGatewayPort, Depends(get_turn_gateway)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]
StoryGen = Annotated[StoryGeneratorPort, Depends(get_story_generator)]
ImageGen = Annotated[ImageGeneratorPort, Depends(get_image_generator)]
