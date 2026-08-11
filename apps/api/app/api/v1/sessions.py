from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.api.deps import (
    DbSession,
    NarrationStore,
    StoryGen,
    TurnGateway,
    WorldStateReader,
    WorldStateStore,
)
from app.api.schemas import (
    EventListRead,
    MemoryRead,
    MessageRead,
    NarrationRequest,
    NarrationResponse,
    RelationshipRead,
    ResolutionListRead,
    SessionCreate,
    SessionDetail,
    SessionRead,
    SessionTimeRead,
    TurnRequest,
    TurnResponse,
    WorldFactRead,
    WorldStateRead,
)
from app.application.narration_service import narrate_resolution
from app.application.situation_service import materialize_initial_situations
from app.application.spatial_service import materialize_initial_spatial_state
from app.application.state_service import materialize_initial_facts
from app.application.turn_service import execute_turn
from app.application.world_state_service import (
    CurrentWorldSnapshot,
    SnapshotScope,
    build_snapshot,
)
from app.domain.errors import NotFoundError, ValidationError
from app.domain.resolution import EventCategory, ResolutionSourceType
from app.domain.world_facts import FactKind, FactSubject, FactSubjectType
from app.domain.world_time import FictionalDateTime
from app.infrastructure.db import models

router = APIRouter(tags=["sessions"])

FACT_PAGE_LIMIT = 200
"""Ceiling for one read of a session's facts. High enough that no real session hits
it today, present so that a session which one day does cannot return an unbounded
response. `WorldStateRead.truncated` is how a caller finds out it did."""

RESOLUTION_PAGE_LIMIT = 100
EVENT_PAGE_LIMIT = 200
"""Ceilings for the two history reads, for the reason `FACT_PAGE_LIMIT` exists. Both
are audit views: a client showing "what has happened" wants the last screenful, and
nothing in this application has a reason to pull a whole session's history at once."""

PUBLIC_SNAPSHOT_SCOPES = frozenset(
    {SnapshotScope.MINIMAL, SnapshotScope.RELEVANT, SnapshotScope.REGIONAL}
)
"""The scopes a normal gameplay client may ask for.

`full_debug` is missing on purpose. It reads every fact, every place, every situation
and the whole schedule, which is a database dump wearing a JSON hat -- fine for an
operator looking at one session, wrong as something a game screen can trigger by
passing a query string. It lives on the development router instead, which is off
unless the environment says otherwise.
"""


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


@router.get("/sessions/{session_id}/world-state", response_model=CurrentWorldSnapshot)
async def read_world_state_snapshot(
    session_id: uuid.UUID,
    reader: WorldStateReader,
    scope: SnapshotScope = Query(default=SnapshotScope.MINIMAL),
) -> CurrentWorldSnapshot:
    """A composed view of this session's world at one revision.

    Read-only, and there is deliberately no `PUT` or `PATCH` beside it. The root is not
    a document a client can send back: changing what is true goes through a resolution
    and a typed mutation, and an endpoint that accepted a whole world would be a way
    around every rule the domains enforce on the way in.

    `scope` decides how much comes back and defaults to `minimal`, because the
    expensive answer should never be the accidental one. `full_debug` is refused here;
    see `PUBLIC_SNAPSHOT_SCOPES` and the development router.

    Not a context source. What a language model sees is decided by
    `app.application.story_context`, and having more of the world available through
    this endpoint has never meant sending more of it to a provider.
    """
    if scope not in PUBLIC_SNAPSHOT_SCOPES:
        raise ValidationError(
            f"Scope {scope.value!r} is not available here. Ask for one of "
            f"{sorted(item.value for item in PUBLIC_SNAPSHOT_SCOPES)}."
        )
    return await build_snapshot(reader, session_id=session_id, scope=scope)


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
    """Run one turn. Atomic: a provider failure rolls the whole turn back.

    Idempotent when the client sends a `client_action_id`: the same id twice returns the
    turn that was played, with no second model run, no duplicate messages and no second
    set of events. The id is the client's to mint and must survive its own retries --
    see `TurnRequest.client_action_id`.
    """
    result = await execute_turn(
        gateway,
        session_id=session_id,
        action=payload.action,
        generator=generator,
        client_action_id=payload.client_action_id,
    )
    return TurnResponse.model_validate(result.model_dump())


@router.get("/sessions/{session_id}/resolutions", response_model=ResolutionListRead)
async def list_resolutions(
    session_id: uuid.UUID,
    store: NarrationStore,
    source_type: ResolutionSourceType | None = Query(default=None),
    limit: int = Query(default=RESOLUTION_PAGE_LIMIT, ge=1, le=RESOLUTION_PAGE_LIMIT),
) -> ResolutionListRead:
    """The mechanical trail: what was resolved in this session, most recent first.

    Read-only, and there is deliberately no write counterpart. A resolution is created
    by resolving something; an endpoint that could post one would let a client assert
    that the world changed without anything having decided that it did.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)
    return ResolutionListRead(
        resolutions=await store.load_resolutions(session_id, source_type=source_type, limit=limit)
    )


@router.get("/sessions/{session_id}/events", response_model=EventListRead)
async def list_events(
    session_id: uuid.UUID,
    store: NarrationStore,
    category: EventCategory | None = Query(default=None),
    min_importance: int = Query(default=1, ge=1, le=5),
    limit: int = Query(default=EVENT_PAGE_LIMIT, ge=1, le=EVENT_PAGE_LIMIT),
) -> EventListRead:
    """What has happened in this session, most recent in fictional time first.

    Read-only, and bounded by `limit` rather than pageable: this is a history view, not
    an export, and an endpoint that could stream every event of a long session is the
    one a client would use to build a prompt out of all of them.

    History is append-only. There is no edit and no delete -- a correction is a new
    event that a game system decides to write.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)
    return EventListRead(
        events=await store.load_events(
            session_id,
            min_importance=min_importance,
            categories=frozenset({category}) if category is not None else None,
            limit=limit,
        )
    )


@router.post(
    "/sessions/{session_id}/resolutions/{resolution_id}/narration",
    response_model=NarrationResponse,
)
async def narrate_resolution_endpoint(
    session_id: uuid.UUID,
    resolution_id: uuid.UUID,
    payload: NarrationRequest,
    store: NarrationStore,
    generator: StoryGen,
) -> NarrationResponse:
    """Get -- or retry, or regenerate -- the prose describing a committed resolution.

    A POST rather than a GET because it can call a language model and write a message.
    Safe to retry regardless: without `regenerate` an outcome that already has narration
    returns it unchanged and no provider runs.

    Nothing here can re-resolve anything. If the provider fails, the failure is reported
    as a provider failure and the outcome it was going to describe is exactly as
    committed as it was before the call.
    """
    result = await narrate_resolution(
        store,
        generator,
        session_id=session_id,
        resolution_id=resolution_id,
        regenerate=payload.regenerate,
    )
    return NarrationResponse.model_validate(result.model_dump())
