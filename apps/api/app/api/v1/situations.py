"""Reading what a session currently has under way.

    GET /sessions/{id}/situations        with filters
    GET /sessions/{id}/situations/{id}   one process and its participants

Session-scoped without exception, and the path says so. Unlike geography there is no
template tier to read: a situation belongs to one save, and an endpoint with no session
in its path could not return one without choosing a save on the caller's behalf.

# Read-only, deliberately

There is no POST, no PATCH and no DELETE on this router. Starting a process, moving one
and ending one are `StartSituation`, `UpdateSituation` and `ResolveSituation` -- typed
mutations applied by `state_service` inside an event and a transaction, so that a siege
that ends has a reason, a timestamp and a revision bump attached to it. A REST endpoint
that set `intensity = 90` would have none of those, and would be the exact hole the
mutation boundary exists to close.

The one write path near situations is the developer progression endpoint, which lives
on the `/dev` router where every other debug tool lives and is labelled as such there.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import SituationStore
from app.api.schemas import SituationListRead, SituationRead
from app.application.situation_service import MAX_SITUATIONS, situations_involving
from app.domain.errors import NotFoundError
from app.domain.world_situations import (
    LIVE_STATUSES,
    ParticipantEntityType,
    Situation,
    SituationCategory,
    SituationParticipant,
    SituationScope,
    SituationStatus,
)

router = APIRouter(tags=["situations"])


def _read(situation: Situation, participants: list[SituationParticipant]) -> SituationRead:
    return SituationRead(
        situation=situation,
        participants=[p for p in participants if p.situation_id == situation.id],
    )


@router.get("/sessions/{session_id}/situations", response_model=SituationListRead)
async def list_situations(
    session_id: uuid.UUID,
    store: SituationStore,
    status: SituationStatus | None = Query(default=None),
    live_only: bool = Query(
        default=True,
        description=(
            "Only processes that can still move. False includes resolved and cancelled "
            "ones, which is what a history view wants."
        ),
    ),
    category: SituationCategory | None = Query(default=None),
    scope: SituationScope | None = Query(default=None),
    location_id: uuid.UUID | None = Query(
        default=None, description="Only situations centred on this place."
    ),
    participant_id: uuid.UUID | None = Query(
        default=None, description="Only situations this entity is taking part in."
    ),
    participant_type: ParticipantEntityType | None = Query(default=None),
) -> SituationListRead:
    """What is going on in this session.

    `live_only` defaults to true because "what is happening" is the question almost
    every caller has, and a session's concluded processes accumulate forever. An
    explicit `status` overrides it: asking for `resolved` and getting nothing because a
    default filter excluded it would be the worst kind of empty response.
    """
    statuses = _statuses(status, live_only)

    if participant_id is not None:
        situations = await situations_involving(
            store,
            session_id=session_id,
            entity_id=participant_id,
            entity_type=participant_type,
            live_only=statuses is not None and statuses == LIVE_STATUSES,
        )
        # Filtered in Python rather than pushed into the participant query: this is a
        # diagnostics endpoint over a bounded list, and a second set of optional
        # predicates in the adapter would earn its complexity from nobody.
        situations = [
            situation
            for situation in situations
            if (category is None or situation.category is category)
            and (scope is None or situation.scope is scope)
            and (location_id is None or situation.primary_location_id == location_id)
            and (statuses is None or situation.status in statuses)
        ]
    else:
        situations = await store.load_situations(
            session_id,
            statuses=statuses,
            category=category,
            scope=scope,
            primary_location_id=location_id,
            limit=MAX_SITUATIONS,
        )

    participants = await store.load_participants([situation.id for situation in situations])
    return SituationListRead(
        situations=[_read(situation, participants) for situation in situations]
    )


@router.get("/sessions/{session_id}/situations/{situation_id}", response_model=SituationRead)
async def get_situation(
    session_id: uuid.UUID, situation_id: uuid.UUID, store: SituationStore
) -> SituationRead:
    """One process, whole, with everyone taking part in it.

    404 also covers "exists, but in another session" -- from here those are the same
    thing and should be.
    """
    situation = await store.get_situation(session_id, situation_id)
    if situation is None:
        raise NotFoundError("Situation", situation_id)
    participants = await store.load_participants([situation.id])
    return _read(situation, participants)


def _statuses(status: SituationStatus | None, live_only: bool) -> frozenset[SituationStatus] | None:
    """Turn the two filter knobs into one set, or None for "everything".

    An explicit status always wins. `live_only` is a convenience default, and a default
    that could silently contradict an explicit request is not a convenience.
    """
    if status is not None:
        return frozenset({status})
    return LIVE_STATUSES if live_only else None
