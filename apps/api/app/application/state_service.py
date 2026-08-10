"""Changing what is true, and only ever from here.

This is the single door to `world_facts`. Nothing else writes them: not a router, not
the turn service directly, and certainly not the story provider. A caller says what it
wants the world to become and who is asking; this decides whether that may happen,
and makes it happen atomically if so.

# The pipeline

    resolution decides something happened
            |
    build a GameEvent + a StateMutationBatch
            |
    validate the whole batch          <- nothing has been written yet
            |
    persist the event
    apply every mutation
    increment the state revision
            |
    commit

Validation runs to completion before the first write. That ordering is the reason the
"one mutation failed" case is usually not a rollback at all -- it is a refusal, and
the transaction never started doing anything to undo. The transaction is still the
guarantee, because a failure *after* validation (a constraint, a lost connection) must
not leave an event claiming something that did not happen to the world.

# What is not here

No retries, no queue, no compensation logic. A failed batch raises, the request's
transaction unwinds, and the caller decides what to do. Anything cleverer would need
to know what a partial world change means, and nothing does.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.application.persistence import (
    ConnectionStateWrite,
    LocationStateWrite,
    NewEvent,
    NewFact,
    SituationUpdate,
    StateStorePort,
)
from app.application.situation_service import start_situation
from app.domain.errors import (
    FactPolicyError,
    NotFoundError,
    StaleStateError,
    ValidationError,
)
from app.domain.state_mutations import (
    StateMutation,
    StateMutationBatch,
    require_no_conflicts,
)
from app.domain.world_facts import (
    FactAuthority,
    FactSubject,
    FactSubjectType,
    RemoveFact,
    SetFact,
    WorldFact,
    check_rules_compatibility,
    location_dedicated_owner,
    require_permitted,
    requires_source_event,
    resolve_policy,
)
from app.domain.world_locations import (
    LocationConnectionState,
    LocationState,
    UpdateConnectionState,
    UpdateLocationState,
)
from app.domain.world_rules import WorldRules
from app.domain.world_situations import (
    ResolveSituation,
    SituationDeltas,
    StartSituation,
    UpdateSituation,
    apply_deltas,
    require_transition,
)

INITIAL_FACTS_EVENT = "world_state.seeded"
"""GameEvent type for template materialisation. One event for the whole seed, not one
per fact: the thing that happened is "this session began", and a row per starting
truth would bury the first real event in the history."""


class StateChangeEvent(BaseModel):
    """The GameEvent a batch of mutations is the consequence of.

    Type and description only. The session, the turn and the fictional timestamp are
    not the caller's to supply -- they come from the session being changed, and a
    caller that could set them could write an event into a turn that never happened.
    """

    model_config = ConfigDict(frozen=True)

    type: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)


class AppliedMutation(BaseModel):
    """One change that landed, reported back in the shape the batch identified it by.

    `scope` and `target` are exactly what `mutation.target()` returned, so there is one
    definition of "which thing did this touch" rather than a second one written for the
    response. For a fact that reads `character:<id>` / `narrative.birthplace`; for
    spatial state, `location_state` / `<id>`.
    """

    model_config = ConfigDict(frozen=True)

    op: Literal[
        "set_fact",
        "remove_fact",
        "update_location_state",
        "update_connection_state",
        "start_situation",
        "update_situation",
        "resolve_situation",
    ]
    scope: str
    target: str

    entity_id: uuid.UUID | None = None
    """The id of something this mutation brought into existence, when it made one.

    Only `start_situation` fills it, and only because the caller has no other way to
    learn it: the batch it submitted had no id in it, by design. Everything else in a
    batch names a thing that already existed.
    """


class StateChangeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    revision: int
    """The session's state revision *after* this batch. Monotonic: a caller that sees
    it go up knows something changed, and it never goes down."""

    event_id: uuid.UUID | None
    applied: list[AppliedMutation]


async def stage_state_change(
    store: StateStorePort,
    *,
    session_id: uuid.UUID,
    batch: StateMutationBatch,
    event: StateChangeEvent | None = None,
) -> StateChangeResult:
    """Validate and apply a batch inside the caller's transaction, without committing.

    For callers whose unit of work is larger than this -- a turn, which also writes
    messages, memories and relationships, and commits once at the end. If the caller
    fails afterwards, its rollback takes these changes with it.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    world = await store.get_world(session.world_id)
    if world is None:  # FK guarantees this, but the type checker does not.
        raise NotFoundError("World", session.world_id)

    if batch.expected_revision is not None and batch.expected_revision != session.state_revision:
        raise StaleStateError(expected=batch.expected_revision, actual=session.state_revision)

    if event is None and requires_source_event(batch.authority):
        raise ValidationError(
            f"Authority {batch.authority.value!r} must name the event that caused this "
            "change. A mechanical mutation with no event is a world that cannot answer "
            "why something is true."
        )

    known_character_ids = await store.known_character_ids(world.id)
    checked = await _validate(
        store,
        session_id=session_id,
        rules=world.rules,
        batch=batch,
        known_character_ids=known_character_ids,
    )

    # -- past this line the batch is going to happen ---------------------------

    event_id: uuid.UUID | None = None
    if event is not None:
        # Written first so the facts below can point at it.
        event_id = await store.add_event(
            NewEvent(
                session_id=session_id,
                turn_index=session.turn_index,
                occurred_at=session.elapsed_minutes,
                type=event.type,
                description=event.description,
            )
        )

    applied: list[AppliedMutation] = []
    for mutation, _current in checked:
        entity_id: uuid.UUID | None = None
        if isinstance(mutation, SetFact):
            await store.set_fact(
                NewFact(
                    session_id=session_id,
                    kind=mutation.kind,
                    subject=mutation.subject,
                    property=mutation.property,
                    value=mutation.value,
                    importance=mutation.importance,
                    # When this value became true in the story, not when the row was
                    # written. Both matter, and they are different numbers.
                    current_value_since=session.elapsed_minutes,
                    authority=batch.authority,
                    source_event_id=event_id,
                    tags=mutation.tags,
                )
            )
        elif isinstance(mutation, RemoveFact):
            await store.remove_fact(session_id, mutation.subject, mutation.property)
        elif isinstance(mutation, UpdateLocationState):
            await _apply_location_update(store, session_id=session_id, mutation=mutation)
        elif isinstance(mutation, UpdateConnectionState):
            await _apply_connection_update(store, session_id=session_id, mutation=mutation)
        elif isinstance(mutation, StartSituation):
            entity_id = await start_situation(
                store,
                session_id=session_id,
                mutation=mutation,
                # The fictional instant the process began, not the row's write time. A
                # siege that starts during a turn starts at that turn's minute.
                started_at=session.elapsed_minutes,
                source_event_id=event_id,
            )
        else:
            await _apply_situation_change(
                store, session_id=session_id, mutation=mutation, at=session.elapsed_minutes
            )

        scope, target = mutation.target()
        applied.append(
            AppliedMutation(op=mutation.op, scope=scope, target=target, entity_id=entity_id)
        )

    revision = await store.bump_state_revision(session_id)

    return StateChangeResult(
        session_id=session_id, revision=revision, event_id=event_id, applied=applied
    )


async def apply_state_change(
    store: StateStorePort,
    *,
    session_id: uuid.UUID,
    batch: StateMutationBatch,
    event: StateChangeEvent | None = None,
) -> StateChangeResult:
    """`stage_state_change`, then commit.

    For callers whose whole unit of work this is. The split exists because a turn is
    not one of them, and a service that always committed would make a turn's atomicity
    depend on nothing else failing after the facts were written.
    """
    result = await stage_state_change(store, session_id=session_id, batch=batch, event=event)
    await store.commit()
    return result


async def materialize_initial_facts(
    store: StateStorePort, *, session_id: uuid.UUID
) -> StateChangeResult | None:
    """Copy the world template's starting facts into a new session.

    The template is a starting configuration, not live state. Each session gets its own
    rows from it and diverges immediately: killing the king in one session leaves the
    template, and every other session, untouched. Nothing here writes back to the
    world, and there is deliberately no code path that could.

    Returns None when the world defines no starting facts, which is the common case.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    seeds = await store.load_initial_facts(session.world_id)
    if not seeds:
        return None

    return await stage_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(authority=FactAuthority.SEED, mutations=list(seeds)),
        event=StateChangeEvent(
            type=INITIAL_FACTS_EVENT,
            description=f"Session began with {len(seeds)} established fact(s) from the world.",
        ),
    )


async def _validate(
    store: StateStorePort,
    *,
    session_id: uuid.UUID,
    rules: WorldRules,
    batch: StateMutationBatch,
    known_character_ids: set[uuid.UUID],
) -> list[tuple[StateMutation, WorldFact | None]]:
    """Check every mutation against policy, entities, storage and the world's rules.

    Returns each mutation paired with the fact it currently replaces -- None for the
    spatial ones, which replace no fact -- so the caller does not read twice. Raises on
    the first problem: a batch is all-or-nothing, so collecting further failures would
    be reporting on work that was never going to happen.
    """
    require_no_conflicts(batch)

    checked: list[tuple[StateMutation, WorldFact | None]] = []
    for mutation in batch.mutations:
        if isinstance(mutation, UpdateLocationState | UpdateConnectionState):
            await _validate_spatial(store, session_id=session_id, mutation=mutation)
            checked.append((mutation, None))
            continue

        if isinstance(mutation, StartSituation):
            # Nothing to pre-check: `start_situation` validates the parent, the
            # location and the participants at the moment it writes, and it has to --
            # a batch may start a war and then a siege inside it, and the parent does
            # not exist until the first one lands.
            checked.append((mutation, None))
            continue

        if isinstance(mutation, UpdateSituation | ResolveSituation):
            await _validate_situation_change(store, session_id=session_id, mutation=mutation)
            checked.append((mutation, None))
            continue

        _require_resolvable_subject(mutation.subject, known_character_ids)
        require_permitted(
            batch.authority,
            resolve_policy(mutation.property),
            canonical_property=mutation.property,
        )
        _require_not_owned_elsewhere(mutation.subject, mutation.property)

        current = await store.get_fact(session_id, mutation.subject, mutation.property)
        if isinstance(mutation, RemoveFact) and current is None:
            raise ValidationError(
                f"Nothing to remove: {mutation.subject.key} has no {mutation.property!r}. "
                "Removing a property means withdrawing an established one -- a caller that "
                "believes it is there and is wrong should find out."
            )

        check_rules_compatibility(rules, mutation=mutation, current=current)
        checked.append((mutation, current))

    return checked


async def _validate_spatial(
    store: StateStorePort,
    *,
    session_id: uuid.UUID,
    mutation: UpdateLocationState | UpdateConnectionState,
) -> None:
    """The target must exist and be visible to this session.

    Visibility is the check that matters: another session's generated geography is
    not "somewhere I may not write", it is somewhere this session cannot see at all,
    and the adapter's filter makes it indistinguishable from missing.

    Authority is not re-checked here -- `StateMutationBatch` refuses to be constructed
    with a spatial mutation under a narrative authority, and `require_no_conflicts`
    re-checks it for objects that skipped validation.
    """
    if isinstance(mutation, UpdateLocationState):
        if await store.get_location(session_id, mutation.location_id) is None:
            raise NotFoundError("Location", mutation.location_id)
        return
    if await store.get_connection(session_id, mutation.connection_id) is None:
        raise NotFoundError("LocationConnection", mutation.connection_id)


async def _validate_situation_change(
    store: StateStorePort,
    *,
    session_id: uuid.UUID,
    mutation: UpdateSituation | ResolveSituation,
) -> None:
    """The situation must exist in this session, and the move must be a legal one.

    Checked before anything is written, so a batch that tries to resolve a war that
    ended last week is a refusal rather than a rollback. The transition rules live in
    the domain; this reads the current status and asks.
    """
    situation = await store.get_situation(session_id, mutation.situation_id)
    if situation is None:
        raise NotFoundError("Situation", mutation.situation_id)

    target = (
        mutation.resolution_status
        if isinstance(mutation, ResolveSituation)
        else mutation.resulting_status
    )
    if target is not None:
        require_transition(situation.status, target)
    elif not situation.is_live:
        # An update with no status change is still a change, and a concluded process
        # does not take them. Caught here rather than by a transition check, which
        # would have nothing to check.
        raise ValidationError(
            f"Situation {situation.title!r} is {situation.status.value} and cannot be "
            "updated. A concluded process stays as it ended."
        )


async def _apply_situation_change(
    store: StateStorePort,
    *,
    session_id: uuid.UUID,
    mutation: UpdateSituation | ResolveSituation,
    at: int,
) -> None:
    """Read the authoritative values, apply the deltas to *them*, clamp, write.

    Read here rather than trusted from the caller, for the same reason facts have no
    `previous_value` and spatial updates are partial: the freshest read is the one
    inside this transaction, and the most likely caller is a resolver that ran against
    state a player action has since changed.
    """
    situation = await store.get_situation(session_id, mutation.situation_id)
    if situation is None:  # Validated above; the type checker does not know that.
        raise NotFoundError("Situation", mutation.situation_id)

    if isinstance(mutation, ResolveSituation):
        await store.update_situation(
            SituationUpdate(
                situation_id=situation.id,
                intensity=situation.intensity,
                threat=situation.threat,
                momentum=situation.momentum,
                importance=situation.importance,
                status=mutation.resolution_status,
                last_progressed_at=max(situation.last_progressed_at, at),
                # The fictional instant it ended. Never null for a terminal status --
                # the domain model refuses that pairing, and this is where it is set.
                resolved_at=at,
                situation_metadata=dict(situation.situation_metadata),
            )
        )
        return

    intensity, threat, momentum = apply_deltas(
        situation,
        SituationDeltas(
            intensity_delta=mutation.intensity_delta,
            threat_delta=mutation.threat_delta,
            momentum_delta=mutation.momentum_delta,
        ),
    )
    await store.update_situation(
        SituationUpdate(
            situation_id=situation.id,
            intensity=intensity,
            threat=threat,
            momentum=momentum,
            importance=(
                situation.importance if mutation.importance is None else mutation.importance
            ),
            status=mutation.resulting_status or situation.status,
            last_progressed_at=max(situation.last_progressed_at, at),
            # Still live: an update cannot end a process, so the ending stays absent.
            resolved_at=None,
            situation_metadata=(
                dict(situation.situation_metadata)
                if mutation.situation_metadata is None
                else dict(mutation.situation_metadata)
            ),
        )
    )


async def _apply_location_update(
    store: StateStorePort, *, session_id: uuid.UUID, mutation: UpdateLocationState
) -> None:
    """Merge a partial update onto the current state and write the whole row.

    Read here rather than trusted from the caller, for the same reason facts have no
    `previous_value`: the freshest read is the one inside this transaction.

    A place with no state row yet -- geography created before this session started, or
    a materialisation that has not run -- gets the defaults merged into, so a mutation
    against it is a change rather than an error.
    """
    current = await store.get_location_state(session_id, mutation.location_id)
    base = current or LocationState(
        id=uuid.uuid4(),
        session_id=session_id,
        location_id=mutation.location_id,
        created_at=dt.datetime.now(dt.UTC),
        updated_at=dt.datetime.now(dt.UTC),
    )
    await store.set_location_state(
        LocationStateWrite(
            session_id=session_id,
            location_id=mutation.location_id,
            condition=mutation.condition or base.condition,
            accessibility=mutation.accessibility or base.accessibility,
            # `or` would treat a deliberate 0 as "unset", and 0 is the most common
            # value either of these takes.
            security_level=(
                base.security_level if mutation.security_level is None else mutation.security_level
            ),
            local_danger_modifier=(
                base.local_danger_modifier
                if mutation.local_danger_modifier is None
                else mutation.local_danger_modifier
            ),
            owner_entity_id=(
                None if mutation.clear_owner else (mutation.owner_entity_id or base.owner_entity_id)
            ),
            controller_entity_id=(
                None
                if mutation.clear_controller
                else (mutation.controller_entity_id or base.controller_entity_id)
            ),
        )
    )


async def _apply_connection_update(
    store: StateStorePort, *, session_id: uuid.UUID, mutation: UpdateConnectionState
) -> None:
    current = await store.get_connection_state(session_id, mutation.connection_id)
    base = current or LocationConnectionState(
        id=uuid.uuid4(),
        session_id=session_id,
        connection_id=mutation.connection_id,
        created_at=dt.datetime.now(dt.UTC),
        updated_at=dt.datetime.now(dt.UTC),
    )
    await store.set_connection_state(
        ConnectionStateWrite(
            session_id=session_id,
            connection_id=mutation.connection_id,
            condition=mutation.condition or base.condition,
            accessibility=mutation.accessibility or base.accessibility,
            traversal_modifier=(
                base.traversal_modifier
                if mutation.traversal_modifier is None
                else mutation.traversal_modifier
            ),
        )
    )


def _require_resolvable_subject(subject: FactSubject, known_character_ids: set[uuid.UUID]) -> None:
    """A fact must be about something that exists.

    Characters are the only entity this application can currently resolve, so they are
    the only ones checked. Locations are checked separately by the proposal reviewer,
    which has the session in hand; factions have no table yet and their ids are
    accepted on trust. That is recorded here rather than silently tolerated, because an
    unverifiable id is a fact that may be about nothing.
    """
    if subject.type is not FactSubjectType.CHARACTER:
        return
    if subject.id not in known_character_ids:
        raise NotFoundError("Character", subject.id)


def _require_not_owned_elsewhere(subject: FactSubject, canonical_property: str) -> None:
    """Refuse a fact that duplicates something a dedicated model already owns.

    The policy registry catches the obvious spellings for any subject. This catches the
    one that depends on what the fact is about: `world.condition` of a sword is an
    ordinary truth, and of a location it is `location_states.condition` with a fact's
    clothes on. Two rows claiming whether the bridge is standing is exactly the
    situation dedicated models exist to prevent.
    """
    if subject.type is not FactSubjectType.LOCATION:
        return
    owner = location_dedicated_owner(canonical_property)
    if owner is not None:
        raise FactPolicyError(
            f"{canonical_property!r} is not a fact about a location: {owner} "
            "Use UpdateLocationState. Narrative truths about a place -- what it is known "
            "for, what happened here -- are still facts."
        )
