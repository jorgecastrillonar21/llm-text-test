"""The one door through which anything mechanically happens.

    Trigger
        |
    Command                       validated, typed, naming real ids
        |
    load ResolutionContext        at revision N, bounded by the command
        |
    Resolver                      pure: context in, outcome out
        |
    ResolutionOutcome             a value; nothing has been written
        |
    validate                      revision, rules, the whole projected change
        |
    BEGIN TRANSACTION
        ResolutionRecord          the verdict, exactly once
        significant GameEvents    what history keeps, pointing at it
        StateMutationBatch        every authoritative change, together
        time advance              routed to the time service, never applied here
        scheduled events          what to look at later
        state_revision            moves once, or not at all
    COMMIT
        |
    narration                     afterwards, describing what already happened

No partial world reality may remain. A failure anywhere before COMMIT leaves a session
indistinguishable from one where the command was never submitted, and a failure after
it is a narration problem rather than a gameplay one.

# Three dispositions, and none of them is `success`

    applied     the attempt happened and changed something
    rejected    the world's rules refused it; nothing was attempted
    no_effect   legitimate, and nothing changed -- an already-open door

`failure` is deliberately absent. A lockpick that snapped is an attempt that *happened*
and is `applied` with an outcome the player did not want; calling that a failure would
put it in the same bucket as "you cannot do that", and the two need different prose,
different history and different consequences.

# Technical failures are not gameplay outcomes

A database error, a bad command, a resolver crash: those propagate. Nothing writes a
`rejected` row to describe them. A `rejected` resolution is a permanent claim that the
world's rules said no, and manufacturing one out of a timeout would put a lie in the
audit trail that the next resolution reads back as fact.

# Idempotency is the pipeline, not a decoration

`(session_id, idempotency_key)` is unique in the database. A retry finds the existing
record and returns it: no resolver runs, no event is written, no mutation is applied,
the clock does not move twice. That is why the key is the caller's to choose and must
survive a transport retry unchanged -- see `client_action_id` in the API layer.
"""

from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.application.event_service import select_events, write_events
from app.application.persistence import (
    NewResolution,
    NewScheduledEvent,
    ResolutionStorePort,
    SessionSnapshot,
    WorldSnapshot,
)
from app.application.resolvers import Resolver, resolver_for
from app.application.state_service import ChangeCause, StateChangeResult, stage_state_change
from app.application.time_service import stage_scheduled_event_completion, stage_time_advance
from app.domain.errors import NotFoundError, StaleStateError, ValidationError
from app.domain.resolution import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    Command,
    GameEvent,
    Resolution,
    ResolutionContext,
    ResolutionDisposition,
    ResolutionOutcome,
    ResolutionSourceType,
)
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import FactAuthority
from app.domain.world_time import TimeAdvanceResult

logger = logging.getLogger(__name__)

_AUTHORITY_FOR_SOURCE: dict[ResolutionSourceType, FactAuthority] = {
    ResolutionSourceType.PLAYER_ACTION: FactAuthority.PLAYER_RESOLUTION,
    ResolutionSourceType.SCHEDULED_EVENT: FactAuthority.SIMULATION,
    ResolutionSourceType.SITUATION_PROGRESSION: FactAuthority.SIMULATION,
    ResolutionSourceType.NPC_ACTION: FactAuthority.SIMULATION,
    ResolutionSourceType.FACTION_ACTION: FactAuthority.SIMULATION,
    ResolutionSourceType.WORLD_SIMULATION: FactAuthority.SIMULATION,
    ResolutionSourceType.SYSTEM: FactAuthority.ENGINE,
    ResolutionSourceType.ADMIN: FactAuthority.ADMIN,
}
"""What each kind of trigger is allowed to write with.

Derived rather than passed in, so a caller cannot claim engine authority for a
narrative source by filling in a field. `FactAuthority.STORY_DIRECTOR` is deliberately
absent from the values: the language model has no `ResolutionSourceType`, because it
does not resolve anything. Its proposals go through `fact_proposals`, which is a
different door with a much narrower opening.

`PLAYER_ACTION` maps to `PLAYER_RESOLUTION` and not to something broader. Player
privilege, where a world wants it, belongs in that world's rules -- an engine that
quietly gave the player more reach than the simulation would be privilege by accident.
"""


class ResolutionRequest(BaseModel):
    """One command, plus everything about *who is asking* that the command must not say.

    A `Command` describes an attempt. Everything here describes its provenance, and it
    is separate for a reason: a resolver reads the command and must not be able to read
    -- let alone influence -- the authority its outcome will be written under.
    """

    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    command: Command

    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)
    """The caller's stable name for this attempt. Unique per session.

    Stable across transport retries, which is the entire point: a new key for the same
    intended action is a second action, and the pipeline has no way to tell -- and must
    not guess -- that the two were meant to be one.
    """

    source_type: ResolutionSourceType
    source_id: uuid.UUID | None = None
    """What triggered this, when it is a row somewhere: the scheduled event, the
    situation, the message. Not a foreign key -- the referent lives in a different table
    per source type, and a polymorphic FK would be a join nobody can write."""

    parent_resolution_id: uuid.UUID | None = None
    """The resolution that caused this one, for cascades. A collapsing bridge resolving
    into a drowning is two resolutions, and this is the link between them."""

    completes_scheduled_event_id: uuid.UUID | None = None
    """The due ScheduledEvent this resolution is the execution of, when it is one.

    This is the dispatch half of the schedule's lifecycle. A time advance only ever marks
    an event DUE -- Time has no idea what `situation.progress` means -- so something has
    to run the work and say it ran. A caller doing that names the event here, and the
    acknowledgement is staged inside this resolution's transaction, after the mutations:
    the world change and "that work is done" commit together or neither does.

    Distinct from `source_id`, which says what *prompted* the resolution and claims
    nothing. Naming an event that is not DUE is refused, which is what stops a retry
    under a fresh idempotency key from executing the same fictional moment twice.
    """

    expected_revision: int | None = None
    """Refuse if the world moved since the caller read it.

    Optional, because most callers resolve inside a single request that read the state
    a moment earlier. Supplied by the ones that did not -- a queued action, a retried
    submission -- and the answer to a mismatch is `StaleStateError`, never a silent
    apply against state the decision was not made from.
    """


class ResolutionResult(BaseModel):
    """What was committed, as a value the caller can report or narrate from."""

    model_config = ConfigDict(frozen=True)

    resolution: Resolution
    events: list[GameEvent] = Field(default_factory=list)
    """The events history kept, which may be fewer than the resolver proposed and is
    often none. Loaded back rather than assembled, so this is what the database has."""

    outcome: ResolutionOutcome | None = None
    """What the resolver decided, for narration. None on a replay: the outcome was a
    value in a process that has already finished, and reconstructing one from persisted
    rows would be inventing a decision rather than recalling it."""

    state_change: StateChangeResult | None = None
    time_advance: TimeAdvanceResult | None = None
    scheduled_event_ids: list[uuid.UUID] = Field(default_factory=list)
    created_situation_ids: list[uuid.UUID] = Field(default_factory=list)

    completed_scheduled_event_id: uuid.UUID | None = None
    """The due ScheduledEvent this resolution executed and acknowledged, when it did."""

    replayed: bool = False
    """Whether this came back from the idempotency check rather than from resolving.

    Surfaced rather than hidden, because a replay is a different thing from a fresh
    resolution and a caller that narrates one should know it is narrating history.
    """

    @property
    def disposition(self) -> ResolutionDisposition:
        return self.resolution.disposition

    @property
    def changed_state(self) -> bool:
        return self.resolution.changed_state


async def resolve(store: ResolutionStorePort, *, request: ResolutionRequest) -> ResolutionResult:
    """Resolve one command and commit everything it caused, atomically.

    The whole pipeline: replay check, bounded context, pure resolver, validation, one
    transaction, one revision bump. Raises rather than inventing a disposition when
    something is technically wrong -- a caller that gets an exception knows nothing
    happened, and a caller that gets a `ResolutionResult` knows exactly what did.
    """
    session, world = await _load_session(store, request.session_id)

    replay = await store.find_resolution_by_key(request.session_id, request.idempotency_key)
    if replay is not None:
        logger.info(
            "Resolution %s replayed for session %s (key %r); nothing re-run.",
            replay.id,
            request.session_id,
            request.idempotency_key,
        )
        return ResolutionResult(
            resolution=replay,
            events=await store.load_events_for_resolution(replay.id),
            replayed=True,
        )

    if (
        request.expected_revision is not None
        and request.expected_revision != session.state_revision
    ):
        raise StaleStateError(expected=request.expected_revision, actual=session.state_revision)

    context = await _load_context(store, session=session, world=world, command=request.command)
    resolver = resolver_for(request.command.kind)
    outcome = resolver.resolve(request.command, context)

    return await _commit(
        store,
        request=request,
        session=session,
        resolver=resolver,
        outcome=outcome,
    )


async def _load_session(
    store: ResolutionStorePort, session_id: uuid.UUID
) -> tuple[SessionSnapshot, WorldSnapshot]:
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)
    world = await store.get_world(session.world_id)
    if world is None:  # FK guarantees this, but the type checker does not.
        raise NotFoundError("World", session.world_id)
    return session, world


async def _load_context(
    store: ResolutionStorePort,
    *,
    session: SessionSnapshot,
    world: WorldSnapshot,
    command: Command,
) -> ResolutionContext:
    """Load exactly what the command asked for, and nothing else.

    The alternative -- a context object that eagerly loads a session's world so one
    resolver can read one number -- costs more with every turn played, and the cost is
    paid by every command whether it needed the data or not.
    """
    wanted = command.context_request()

    situations = []
    for situation_id in wanted.situation_ids:
        situation = await store.get_situation(session.id, situation_id)
        if situation is None:
            raise NotFoundError("Situation", situation_id)
        situations.append(situation)

    locations = []
    for location_id in wanted.location_ids:
        location = await store.get_location(session.id, location_id)
        if location is None:
            raise NotFoundError("Location", location_id)
        locations.append(location)

    # One batched query rather than one per situation, which is how an N+1 gets written
    # into a resolution loop.
    participants = await store.load_participants([s.id for s in situations]) if situations else []

    return ResolutionContext(
        session_id=session.id,
        world_id=world.id,
        turn_index=session.turn_index,
        now=session.elapsed_minutes,
        state_revision=session.state_revision,
        rules=world.rules,
        situations=tuple(situations),
        participants=tuple(participants),
        locations=tuple(locations),
    )


async def _commit(
    store: ResolutionStorePort,
    *,
    request: ResolutionRequest,
    session: SessionSnapshot,
    resolver: Resolver,
    outcome: ResolutionOutcome,
) -> ResolutionResult:
    """Write the verdict and everything it caused, in one transaction.

    Order is load-bearing. The record goes first because the events carry its foreign
    key; the events go before the mutations because a fact names the event that
    established it; the clock moves last because a mutation is a consequence of the
    world as it stood when the resolver looked at it, not of the minute it lands on.
    """
    revision_before = session.state_revision
    # `bump_state_revision` moves by exactly one, and `stage_state_change` calls it
    # exactly once per batch -- so the number the record claims is knowable before the
    # batch runs, which is what lets the record be written once, fully formed. Verified
    # against reality below, before the commit that would make a wrong claim permanent.
    revision_after = revision_before + 1 if outcome.changes_state else revision_before

    selected = (
        await select_events(
            store,
            session_id=session.id,
            occurred_at=session.elapsed_minutes,
            candidates=outcome.events,
        )
        if outcome.events
        else []
    )

    resolution_id = await store.add_resolution(
        NewResolution(
            session_id=session.id,
            source_type=request.source_type,
            source_id=request.source_id,
            parent_resolution_id=request.parent_resolution_id,
            idempotency_key=request.idempotency_key,
            disposition=outcome.disposition,
            reason_code=outcome.reason_code,
            resolver_name=resolver.name,
            resolver_version=resolver.version,
            state_revision_before=revision_before,
            state_revision_after=revision_after,
            # The fictional minute the verdict was reached. A time advance in this same
            # resolution moves the clock afterwards; the decision was still made here.
            occurred_at=session.elapsed_minutes,
            turn_index=session.turn_index,
            event_count=len(selected),
            mutation_count=len(outcome.state_mutations),
        )
    )

    event_ids = await write_events(
        store,
        session_id=session.id,
        turn_index=session.turn_index,
        occurred_at=session.elapsed_minutes,
        reviewed=selected,
        resolution_id=resolution_id,
    )

    state_change: StateChangeResult | None = None
    if outcome.state_mutations:
        state_change = await stage_state_change(
            store,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=_AUTHORITY_FOR_SOURCE[request.source_type],
                mutations=list(outcome.state_mutations),
                expected_revision=revision_before,
            ),
            cause=ChangeCause(
                resolution_id=resolution_id,
                # The first kept event, when history kept one. A fact points at what
                # established it; a progression that produced no event still has the
                # resolution, which is the other half of "why is this true?".
                event_id=event_ids[0] if event_ids else None,
            ),
        )
        if state_change.revision != revision_after:
            # Unreachable unless the revision contract changed underneath this. Raised
            # before the commit, so a record that would have lied about the world's
            # version never becomes permanent.
            raise ValidationError(
                f"Resolution recorded revision {revision_after} but the batch produced "
                f"{state_change.revision}. One committed resolution moves the state "
                "revision exactly once."
            )

    time_advance: TimeAdvanceResult | None = None
    if outcome.time_advance is not None:
        time_advance = await stage_time_advance(
            store, session_id=session.id, request=outcome.time_advance
        )

    scheduled_event_ids = [
        await store.add_scheduled_event(
            NewScheduledEvent(
                session_id=session.id,
                due_at=scheduled.due_at,
                type=scheduled.type,
                payload=dict(scheduled.payload),
                interrupt_player_action=scheduled.interrupt_player_action,
            )
        )
        for scheduled in outcome.scheduled_events
    ]

    if request.completes_scheduled_event_id is not None:
        # Last, and deliberately: the work this event owned is everything above. Saying
        # "done" before doing it is precisely the ordering that let a crashed advance
        # leave PROCESSED rows behind for work nothing had run.
        await stage_scheduled_event_completion(
            store,
            session_id=session.id,
            event_id=request.completes_scheduled_event_id,
        )

    await store.commit()

    resolution = await store.get_resolution(session.id, resolution_id)
    if resolution is None:  # Just committed; unreachable without a storage fault.
        raise NotFoundError("Resolution", resolution_id)

    return ResolutionResult(
        resolution=resolution,
        events=await store.load_events_for_resolution(resolution_id),
        outcome=outcome,
        state_change=state_change,
        time_advance=time_advance,
        scheduled_event_ids=scheduled_event_ids,
        completed_scheduled_event_id=request.completes_scheduled_event_id,
        created_situation_ids=[
            entry.entity_id
            for entry in (state_change.applied if state_change else [])
            if entry.op == "start_situation" and entry.entity_id is not None
        ],
    )
