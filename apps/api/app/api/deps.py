"""Request-scoped dependencies.

Engine, session factory and providers are created once during lifespan and stored
on app.state -- no module-level globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.persistence import (
    NarrationStorePort,
    ResolutionStorePort,
    SessionClockPort,
    SituationPort,
    SpatialPort,
    StateStorePort,
    TurnGatewayPort,
)
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


def get_session_clock(session: Annotated[AsyncSession, Depends(get_db)]) -> SessionClockPort:
    """The same adapter, seen through the narrower port the time service needs.

    A separate dependency rather than reusing `TurnGateway` so the signature says
    what advancing time is allowed to touch: the clock, the scheduled events and the
    audit trail, and not the transcript or the relationships.
    """
    return SqlAlchemyTurnGateway(session)


def get_world_state_store(session: Annotated[AsyncSession, Depends(get_db)]) -> StateStorePort:
    """The same adapter again, seen through the port that changes what is true.

    A third dependency for a third use case, for the reason the second one exists: the
    signature is the documentation. Reading and writing state must not be able to reach
    the transcript, and this is what says so at the boundary.

    Facts and space together, because one batch may change both -- a collapsing bridge
    is a connection, a location and a danger modifier -- and splitting them would split
    the transaction that makes it one event.
    """
    return SqlAlchemyTurnGateway(session)


def get_spatial_store(session: Annotated[AsyncSession, Depends(get_db)]) -> SpatialPort:
    """The narrowest spatial view, for handlers that only read or grow the graph.

    Separate from `WorldStateStore` so a locations endpoint cannot reach the fact
    store: reading where things are and rewriting what is true are different powers.
    """
    return SqlAlchemyTurnGateway(session)


def get_situation_store(session: Annotated[AsyncSession, Depends(get_db)]) -> SituationPort:
    """The narrowest situation view, for handlers that only read what is under way.

    Separate from `WorldStateStore` for the reason `SpatialStore` is: reading what the
    world is currently doing and rewriting what is true are different powers, and a
    read endpoint should not hold the second one.
    """
    return SqlAlchemyTurnGateway(session)


def get_resolution_store(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ResolutionStorePort:
    """Everything one resolution can touch, behind this request's transaction.

    The widest of these dependencies, and it has to be: a resolution commits its record,
    the events it caused, the mutations it caused and the clock it moved as one unit.
    Splitting it would leave a world that changed with nothing recording why.

    Handed to `resolve()`, never to a resolver. Resolvers receive a `ResolutionContext`
    and return an outcome; they have no port and cannot acquire one.
    """
    return SqlAlchemyTurnGateway(session)


def get_narration_store(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> NarrationStorePort:
    """The narrowest of them all: reads, plus one message.

    Deliberately not `ResolutionStore`. Narration runs after the outcome is committed
    and describes it; a port that could also write a fact or move the clock would let a
    provider's paragraph change the thing it was supposed to be describing.
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
SessionClock = Annotated[SessionClockPort, Depends(get_session_clock)]
WorldStateStore = Annotated[StateStorePort, Depends(get_world_state_store)]
SpatialStore = Annotated[SpatialPort, Depends(get_spatial_store)]
SituationStore = Annotated[SituationPort, Depends(get_situation_store)]
ResolutionStore = Annotated[ResolutionStorePort, Depends(get_resolution_store)]
NarrationStore = Annotated[NarrationStorePort, Depends(get_narration_store)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]
StoryGen = Annotated[StoryGeneratorPort, Depends(get_story_generator)]
ImageGen = Annotated[ImageGeneratorPort, Depends(get_image_generator)]
