from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import DbSession, StoryGen, TurnGateway
from app.api.schemas import (
    MemoryRead,
    MessageRead,
    RelationshipRead,
    SessionCreate,
    SessionDetail,
    SessionRead,
    TurnRequest,
    TurnResponse,
)
from app.application.turn_service import execute_turn
from app.domain.errors import NotFoundError
from app.infrastructure.db import models

router = APIRouter(tags=["sessions"])


@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(
    db: DbSession, world_id: uuid.UUID | None = Query(default=None)
) -> list[models.GameSession]:
    query = select(models.GameSession).order_by(models.GameSession.updated_at.desc())
    if world_id is not None:
        query = query.where(models.GameSession.world_id == world_id)
    rows = await db.execute(query)
    return list(rows.scalars())


@router.post("/sessions", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(payload: SessionCreate, db: DbSession) -> models.GameSession:
    world = await db.get(models.World, payload.world_id)
    if world is None:
        raise NotFoundError("World", payload.world_id)
    session = models.GameSession(**payload.model_dump())
    db.add(session)
    await db.commit()
    return session


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: uuid.UUID, db: DbSession) -> SessionDetail:
    session = await db.get(models.GameSession, session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)
    world = await db.get(models.World, session.world_id)
    if world is None:
        raise NotFoundError("World", session.world_id)
    return SessionDetail.model_validate(
        {**SessionRead.model_validate(session).model_dump(), "world": world}
    )


@router.get("/sessions/{session_id}/messages", response_model=list[MessageRead])
async def list_messages(session_id: uuid.UUID, db: DbSession) -> list[models.Message]:
    session = await db.get(models.GameSession, session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)
    rows = await db.execute(
        select(models.Message)
        .where(models.Message.session_id == session_id)
        .order_by(models.Message.turn_index, models.Message.created_at)
    )
    return list(rows.scalars())


@router.get("/sessions/{session_id}/memories", response_model=list[MemoryRead])
async def list_memories(session_id: uuid.UUID, db: DbSession) -> list[models.Memory]:
    session = await db.get(models.GameSession, session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)
    rows = await db.execute(
        select(models.Memory)
        .where(models.Memory.session_id == session_id)
        .order_by(models.Memory.importance.desc(), models.Memory.created_at.desc())
    )
    return list(rows.scalars())


@router.get("/sessions/{session_id}/relationships", response_model=list[RelationshipRead])
async def list_relationships(session_id: uuid.UUID, db: DbSession) -> list[models.Relationship]:
    session = await db.get(models.GameSession, session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)
    rows = await db.execute(
        select(models.Relationship).where(models.Relationship.session_id == session_id)
    )
    return list(rows.scalars())


@router.post("/sessions/{session_id}/turns", response_model=TurnResponse)
async def submit_turn(
    session_id: uuid.UUID, payload: TurnRequest, gateway: TurnGateway, generator: StoryGen
) -> TurnResponse:
    """Run one turn. Atomic: a provider failure rolls the whole turn back."""
    result = await execute_turn(
        gateway, session_id=session_id, action=payload.action, generator=generator
    )
    return TurnResponse.model_validate(result.model_dump())
