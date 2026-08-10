"""Turning what a resolver decided into what the world did.

    progress_situation   ->  SituationProgressionResult    nothing written
            |
    assemble one StateMutationBatch
            |
    stage_state_change   ->  one GameEvent, one revision bump, every change
            |
    schedule the next evaluation, if the resolver asked for one

The split is the point. A resolver is pure and testable and knows nothing about
transactions; this module knows nothing about fire or sieges. Between them there is one
place where a decision becomes a commitment, and it is the same place every other state
change goes through.

# One batch, or nothing

A siege progressing raises its own intensity, breaches a gate, closes a crossing and
starts a food crisis. Those are one outcome. They go into one `StateMutationBatch`, are
validated to completion before the first write, and share one `state_revision` bump. A
failure anywhere -- an invalid transition, a location this session cannot see, a
constraint -- refuses the whole thing, and the revision does not move.

# Nothing here runs on its own

There is no loop, no worker, no queue and no cron. `evaluate_and_apply` runs when
something calls it: a developer endpoint today, a future SimulationEngine tomorrow.
Fictional time is the only clock that matters, and it only moves when the application
moves it.

# The scheduling gap, stated plainly

A resolver may ask for the next evaluation to happen at a given fictional minute, and
this writes a real `ScheduledEvent` for it. **Nothing dispatches that event yet.**
`advance_time` will find it due and mark it processed, because processing a scheduled
event means "resolve it", and the thing that would resolve a `situation.progress` event
is the SimulationEngine -- explicitly out of scope for this task. The row is written so
the schedule is real and so the wiring has one obvious place to happen; until then, a
scheduled progression is a note nobody reads. See docs/world-state-situations.md.
"""

from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel, ConfigDict

from app.application.persistence import NewScheduledEvent, ProgressionPort
from app.application.situation_service import progress_situation
from app.application.state_service import (
    StateChangeEvent,
    StateChangeResult,
    stage_state_change,
)
from app.domain.errors import NotFoundError
from app.domain.situation_progression import SituationProgressionResult
from app.domain.state_mutations import StateMutation, StateMutationBatch
from app.domain.world_facts import FactAuthority
from app.domain.world_situations import (
    ResolveSituation,
    Situation,
    SituationProgressionRequest,
    UpdateSituation,
    is_terminal,
)

logger = logging.getLogger(__name__)

SITUATION_PROGRESSED_EVENT = "situation.progressed"
"""The GameEvent type for a progression that had no more specific event of its own.

A resolver that produces `EAST_GATE_BREACHED` says something about the world. One that
nudges an intensity by four does not, and forcing it to invent an event name would fill
a session's history with noise. When a resolver supplies its own events, the first one
becomes the batch's event and the rest are recorded alongside it.
"""

SITUATION_PROGRESS_DUE_EVENT = "situation.progress"
"""ScheduledEvent type for "look at this process again". Payload carries the situation
id. Nothing dispatches it yet -- see the module docstring."""


class ProgressionOutcome(BaseModel):
    """What actually happened, as distinct from what was decided."""

    model_config = ConfigDict(frozen=True)

    situation_id: uuid.UUID
    result: SituationProgressionResult
    applied: StateChangeResult | None = None
    """None when the result changed nothing. A dormant conspiracy evaluated over six
    hours did nothing, and a revision bump saying so would be a lie about activity."""

    scheduled_event_id: uuid.UUID | None = None
    created_situation_ids: list[uuid.UUID] = []


async def evaluate_and_apply(
    store: ProgressionPort,
    *,
    session_id: uuid.UUID,
    request: SituationProgressionRequest,
    authority: FactAuthority = FactAuthority.SIMULATION,
) -> ProgressionOutcome:
    """Progress one situation and commit everything it caused, atomically.

    `authority` defaults to `SIMULATION` because that is what an unattended world
    moving on its own *is*. A developer endpoint passes `ADMIN`; a future resolved
    player action would pass `PLAYER_RESOLUTION`. The Story Director cannot pass
    anything -- the batch refuses to be constructed under its authority, and it has no
    path to this function anyway.
    """
    situation = await store.get_situation(session_id, request.situation_id)
    if situation is None:
        raise NotFoundError("Situation", request.situation_id)

    result = await progress_situation(store, session_id=session_id, request=request)

    if result.is_noop():
        # No event, no revision bump, no scheduled row. A pass that decided nothing
        # happened should leave a session indistinguishable from one where it was never
        # asked -- otherwise "has anything changed?" stops being answerable.
        logger.debug(
            "Progression of %s over %d min changed nothing.",
            situation.title,
            request.elapsed_minutes,
        )
        return ProgressionOutcome(situation_id=situation.id, result=result)

    batch = StateMutationBatch(
        authority=authority,
        mutations=_assemble(situation, result, at=request.to_time),
    )
    applied = await stage_state_change(
        store,
        session_id=session_id,
        batch=batch,
        event=_event_for(situation, result),
    )

    scheduled_event_id = await _schedule_next(store, session_id=session_id, result=result)

    await store.commit()
    return ProgressionOutcome(
        situation_id=situation.id,
        result=result,
        applied=applied,
        scheduled_event_id=scheduled_event_id,
        # The ids of the processes this one started, which the caller has no other way
        # to learn: the batch it submitted named none of them.
        created_situation_ids=[
            entry.entity_id
            for entry in applied.applied
            if entry.op == "start_situation" and entry.entity_id is not None
        ],
    )


def _assemble(
    situation: Situation, result: SituationProgressionResult, *, at: int
) -> list[StateMutation]:
    """The result as one ordered list of mutations.

    Order matters in exactly one way: the situation's own change comes first, so that a
    reader of the batch sees what this progression was *about* before what it knocked
    over. Nothing depends on it mechanically -- the batch is validated as a whole and
    applied as a whole.
    """
    mutations: list[StateMutation] = [_own_change(situation, result, at=at)]
    mutations.extend(result.state_mutations)
    mutations.extend(
        # Children are linked to their cause here rather than by the resolver, which has
        # no reason to know its own id and every reason not to be trusted with it.
        start.model_copy(update={"parent_situation_id": situation.id})
        if start.parent_situation_id is None
        else start
        for start in result.new_situations
    )
    return mutations


def _own_change(
    situation: Situation, result: SituationProgressionResult, *, at: int
) -> StateMutation:
    """The mutation that moves the situation this pass was about.

    A terminal status change becomes `ResolveSituation`, which records the ending; every
    other outcome becomes `UpdateSituation`. The deltas ride along with the resolve
    where they can -- they cannot, because resolving preserves the numbers as they
    stood, which is the honest record of how a process was when it ended.
    """
    if result.status_change is not None and is_terminal(result.status_change):
        return ResolveSituation(
            situation_id=situation.id,
            resolution_status=result.status_change,
            reason=result.resolution_reason,
        )

    return UpdateSituation(
        situation_id=situation.id,
        intensity_delta=result.deltas.intensity_delta,
        threat_delta=result.deltas.threat_delta,
        momentum_delta=result.deltas.momentum_delta,
        resulting_status=result.status_change,
        reason=result.notes[:300],
        # `last_progressed_at` is not on the mutation: the service stamps it from the
        # session's fictional clock, so a resolver cannot claim a process was evaluated
        # at a time it was not.
    )


def _event_for(situation: Situation, result: SituationProgressionResult) -> StateChangeEvent:
    """The GameEvent this batch is the consequence of.

    A resolver's own first event when it has one -- `EAST_GATE_BREACHED` is a better
    history entry than "the siege progressed" -- and a generic one otherwise.
    """
    if result.generated_events:
        first = result.generated_events[0]
        return StateChangeEvent(type=first.type, description=first.description)
    return StateChangeEvent(
        type=SITUATION_PROGRESSED_EVENT,
        description=f"{situation.title}: {result.notes or 'progressed'}"[:500],
    )


async def _schedule_next(
    store: ProgressionPort, *, session_id: uuid.UUID, result: SituationProgressionResult
) -> uuid.UUID | None:
    """Write the resolver's next-evaluation request, if it made one.

    Absolute session time, straight through -- the resolver already converted whatever
    cadence it had in mind into a position on the clock, because a row saying "in six
    hours" would mean something different every time it was read.
    """
    if result.next_progression_at is None:
        return None
    return await store.add_scheduled_event(
        NewScheduledEvent(
            session_id=session_id,
            due_at=result.next_progression_at,
            type=SITUATION_PROGRESS_DUE_EVENT,
            payload={"situation_id": str(result.situation_id)},
            # Never interrupts. A world quietly re-evaluating a fire is not a reason to
            # cut a player's action short; the events that should interrupt are the ones
            # a resolver emits, not the housekeeping that produces them.
            interrupt_player_action=False,
        )
    )
