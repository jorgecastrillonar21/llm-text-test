from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.api.deps import DbSession, StoryGen, TurnGateway, WorldStateStore
from app.api.schemas import (
    MemoryRead,
    MessageRead,
    RelationshipRead,
    SessionCreate,
    SessionDetail,
    SessionRead,
    SessionTimeRead,
    TurnRequest,
    TurnResponse,
    WorldFactRead,
    WorldStateRead,
)
from app.application.situation_service import materialize_initial_situations
from app.application.spatial_service import materialize_initial_spatial_state
from app.application.state_service import materialize_initial_facts
from app.application.turn_service import execute_turn
from app.domain.errors import NotFoundError, ValidationError
from app.domain.world_facts import FactKind, FactSubject, FactSubjectType
from app.domain.world_time import FictionalDateTime
from app.infrastructure.db import models

router = APIRouter(tags=["sessions"])

FACT_PAGE_LIMIT = 200
"""Ceiling for one read of a session's facts. High enough that no real session hits
it today, present so that a session which one day does cannot return an unbounded
response. `WorldStateRead.truncated` is how a caller finds out it did."""


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
async def create_session(
    payload: SessionCreate, db: DbSession, store: WorldStateStore
) -> models.GameSession:
    """Start a session, and give it the world's starting truth and geography.

    One transaction: flush so the seeding can read the session it is seeding, then
    commit all of it together. A session that exists without the truths its world
    declared, or without a state row for the places in it, would be a world the player
    is playing a different version of, and there is no retry that fixes it afterwards.

    Facts, spatial state and situations are seeded by different services because they
    are different kinds of thing: one is a batch of mutations that moves the state
    revision, one is materialising defaults that no event caused, and one is starting
    the processes a world was already running before anyone played it.

    Geography before situations, because a seeded situation may be centred on a place
    and the location has to be visible before the siege of it can be written.
    """
    world = await db.get(models.World, payload.world_id)
    if world is None:
        raise NotFoundError("World", payload.world_id)
    session = models.GameSession(**payload.model_dump())
    db.add(session)
    await db.flush()

    # Same AsyncSession behind the port, so it sees the row above without committing.
    await materialize_initial_facts(store, session_id=session.id)
    await materialize_initial_spatial_state(store, session_id=session.id)
    await materialize_initial_situations(store, session_id=session.id)

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
        {
            **SessionRead.model_validate(session).model_dump(),
            "world": world,
            # Projected on every read from the session's clock and the world's start
            # date. Nothing caches it, so it cannot go stale.
            "time": SessionTimeRead.project(
                session.elapsed_minutes,
                FictionalDateTime.model_validate(world.initial_datetime),
            ),
        }
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


@router.get("/sessions/{session_id}/world-state/facts", response_model=WorldStateRead)
async def list_world_facts(
    session_id: uuid.UUID,
    store: WorldStateStore,
    subject_type: FactSubjectType | None = Query(default=None),
    subject_id: uuid.UUID | None = Query(default=None),
    kind: FactKind | None = Query(default=None),
    min_importance: int | None = Query(default=None, ge=1, le=5),
    limit: int = Query(default=FACT_PAGE_LIMIT, ge=1, le=FACT_PAGE_LIMIT),
) -> WorldStateRead:
    """What is currently true in this session.

    Read-only, and the only fact endpoint that is not development-only. Changing state
    is something game systems do through `app.application.state_service`; there is
    deliberately no gameplay CRUD over facts, because a client that could write one
    could write `system.alive` and the whole authority model would be decoration.

    `subject_id` narrows within a `subject_type`; sending it alone is a 422, since an
    id without a type is not a subject.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    subject = _requested_subject(subject_type, subject_id)
    # One past the ceiling, so a full page can be reported as truncated rather than
    # silently looking like the whole of the world's state.
    facts = await store.load_facts(
        session_id, subject=subject, kind=kind, min_importance=min_importance, limit=limit + 1
    )
    return WorldStateRead(
        session_id=session_id,
        state_revision=session.state_revision,
        facts=[WorldFactRead.of(fact) for fact in facts[:limit]],
        truncated=len(facts) > limit,
    )


def _requested_subject(
    subject_type: FactSubjectType | None, subject_id: uuid.UUID | None
) -> FactSubject | None:
    if subject_type is None:
        if subject_id is not None:
            raise ValidationError("'subject_id' needs a 'subject_type' to mean anything.")
        return None
    try:
        # FactSubject enforces the rest: the world takes no id, everything else needs
        # one. Translated here because a pydantic error raised inside a handler is a
        # 500, and a bad query string is the caller's problem, not the server's.
        return FactSubject(type=subject_type, id=subject_id)
    except PydanticValidationError as exc:
        raise ValidationError(str(exc).splitlines()[0]) from exc


@router.post("/sessions/{session_id}/turns", response_model=TurnResponse)
async def submit_turn(
    session_id: uuid.UUID, payload: TurnRequest, gateway: TurnGateway, generator: StoryGen
) -> TurnResponse:
    """Run one turn. Atomic: a provider failure rolls the whole turn back."""
    result = await execute_turn(
        gateway, session_id=session_id, action=payload.action, generator=generator
    )
    return TurnResponse.model_validate(result.model_dump())
