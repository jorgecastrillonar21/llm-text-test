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

from pydantic import BaseModel, ConfigDict

from app.application.event_service import record_events
from app.application.persistence import (
    ConnectionStateWrite,
    LocationStateWrite,
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
from app.domain.resolution import EventCandidate, EventCategory
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

INITIAL_FACTS_EVENT = "world_state_seeded"
"""GameEvent subtype for template materialisation. One event for the whole seed, not
one per fact: the thing that happened is "this session began", and a row per starting
truth would bury the first real event in the history."""


class ChangeCause(BaseModel):
    """Why a batch is being applied, as things that already exist.

    Both halves are optional and they answer different questions:

        resolution_id   which mechanical verdict decided this
        event_id        which world-significant happening this followed from

    A siege progression that quietly raised an intensity has the first and not the
    second, because nothing happened that history should record. A collapsing bridge
    has both. Seeding a session has only the event, because nothing resolved anything.

    Neither is minted here. `stage_state_change` used to accept an event description
    and write the row itself, which meant the one place that could write history was
    also the place that applied mutations -- and every caller wanting an event had to
    make a state change to get one. Events are `event_service`'s now; this takes the id.
    """

    model_config = ConfigDict(frozen=True)

    resolution_id: uuid.UUID | None = None
    event_id: uuid.UUID | None = None

    @property
    def is_accounted_for(self) -> bool:
        """Whether this batch can answer "why did this happen?" at all."""
        return self.resolution_id is not None or self.event_id is not None


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
    cause: ChangeCause | None = None,
    moves_revision: bool = True,
) -> StateChangeResult:
    """Validate and apply a batch inside the caller's transaction, without committing.

    For callers whose unit of work is larger than this -- a turn, which also writes
    messages, memories and relationships, and commits once at the end. If the caller
    fails afterwards, its rollback takes these changes with it.

    `moves_revision=False` is for the two callers whose batch is not, by itself, one
    logical state change:

    * A turn. It may also create a place and start a situation, neither of which comes
      through here, and the whole turn is one resolution -- so it bumps once itself,
      afterwards, for everything together. See `turn_service.execute_turn`.
    * Session initialization. Materializing a template into a new session is not a
      change to that session's world; it *is* that world, arriving. A new session sits
      at revision 0 whether or not its template declared anything.

    Nothing else should pass it. The revision is a per-resolution counter, and a caller
    that turned it off because a second bump was inconvenient would be inventing the
    second revision mechanism this design exists to avoid.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    world = await store.get_world(session.world_id)
    if world is None:  # FK guarantees this, but the type checker does not.
        raise NotFoundError("World", session.world_id)

    if batch.expected_revision is not None and batch.expected_revision != session.state_revision:
        raise StaleStateError(expected=batch.expected_revision, actual=session.state_revision)

    cause = cause or ChangeCause()
    if not cause.is_accounted_for and requires_source_event(batch.authority):
        raise ValidationError(
            f"Authority {batch.authority.value!r} must name what caused this change -- "
            "the resolution that decided it, or the event it followed from. A mechanical "
            "mutation with neither is a world that cannot answer why something is true."
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

    event_id = cause.event_id
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

    revision = (
        await store.bump_state_revision(session_id) if moves_revision else session.state_revision
    )

    return StateChangeResult(
        session_id=session_id, revision=revision, event_id=event_id, applied=applied
    )


async def apply_state_change(
    store: StateStorePort,
    *,
    session_id: uuid.UUID,
    batch: StateMutationBatch,
    cause: ChangeCause | None = None,
) -> StateChangeResult:
    """`stage_state_change`, then commit.

    For callers whose whole unit of work this is. The split exists because a turn is
    not one of them, and a service that always committed would make a turn's atomicity
    depend on nothing else failing after the facts were written.
    """
    result = await stage_state_change(store, session_id=session_id, batch=batch, cause=cause)
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

    The state revision does not move. A session that has just been initialized is at
    revision 0 -- the world exactly as its template declared it -- and it stays there
    whether the template held forty facts or none. Bumping here would have made the
    starting revision depend on how much content the world's author wrote, so two
    sessions that had equally never been played would disagree about how many times
    their reality had changed.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    seeds = await store.load_initial_facts(session.world_id)
    if not seeds:
        return None

    # Written first, so the facts below can point at it as their cause. Its policy caps
    # it at importance 1: it explains why these things were already true, and nothing
    # else, so it must never compete for space in a narration prompt.
    written = await record_events(
        store,
        session_id=session_id,
        turn_index=session.turn_index,
        occurred_at=session.elapsed_minutes,
        candidates=[
            EventCandidate(
                category=EventCategory.SYSTEM,
                subtype=INITIAL_FACTS_EVENT,
                summary=(f"Session began with {len(seeds)} established fact(s) from the world."),
                payload={"fact_count": len(seeds)},
            )
        ],
    )

    return await stage_state_change(
        store,
        session_id=session_id,
        batch=StateMutationBatch(authority=FactAuthority.SEED, mutations=list(seeds)),
        cause=ChangeCause(event_id=written[0] if written else None),
        moves_revision=False,
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
            await _validate_situation_start(store, session_id=session_id, mutation=mutation)
            checked.append((mutation, None))
            continue

        if isinstance(mutation, UpdateSituation | ResolveSituation):
            await _validate_situation_change(store, session_id=session_id, mutation=mutation)
            checked.append((mutation, None))
            continue

        await _require_resolvable_subject(
            store,
            session_id=session_id,
            subject=mutation.subject,
            known_character_ids=known_character_ids,
        )
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


async def _validate_situation_start(
    store: StateStorePort,
    *,
    session_id: uuid.UUID,
    mutation: StartSituation,
) -> None:
    """A nested situation hangs off a parent that already existed before this batch.

    `StartSituation` carries no id -- the id is minted at write time, deliberately, so
    that nothing outside this module chooses situation identity. The consequence was
    documented backwards for a while: a batch cannot start a war and then start a siege
    *inside* it, because there is no way to write down "the war two mutations ago". The
    id does not exist until the row does, and a batch has no vocabulary for referring to
    one of its own results.

    So the V1 contract is the one the data model can actually express. A batch that
    wants a tree writes the root, commits, and starts the children against the id it
    got back. A local-reference mechanism would be a mutation scripting language, and
    no use case has asked for one.

    Checked here rather than only inside `start_situation` so a batch naming a parent
    that is not there is refused before anything is written, like every other mutation.
    `start_situation` still re-checks at write time -- it is reachable directly, by
    session materialisation, and its check covers cycles this one cannot produce.
    """
    if mutation.parent_situation_id is None:
        return
    if await store.get_situation(session_id, mutation.parent_situation_id) is None:
        raise NotFoundError("Situation", mutation.parent_situation_id)


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


async def _require_resolvable_subject(
    store: StateStorePort,
    *,
    session_id: uuid.UUID,
    subject: FactSubject,
    known_character_ids: set[uuid.UUID],
) -> None:
    """A fact must be about something that exists, and that this session can see.

    Every door into a state change comes through here. The proposal reviewer checks
    location subjects too, and keeps doing so -- it can reject one claim and let the
    turn continue, which is worth more than a refusal -- but it only guards the Story
    Director. Resolution, ADMIN, ENGINE and the dev router reach `stage_state_change`
    without passing it, and until now every one of those could write a fact about a
    location id that named nothing.

    Locations are read session-scoped, the same way `_validate_spatial` reads them:
    another session's generated geography is not "somewhere I may not write about", it
    is invisible, and "not visible" and "not there" have to be the same answer.

    FACTION and OTHER have no owning domain yet. Rather than accept a UUID that nothing
    can check while claiming every fact refers to something real, V1 refuses them at
    this boundary. `FactSubjectType` keeps the members -- they are the vocabulary
    Factions will need -- and the refusal is deleted when the table that resolves them
    exists. No caller writes one today.
    """
    if subject.type is FactSubjectType.WORLD:
        # The session's own world, and the subject model refuses to carry an id for it,
        # so there is nothing here that could dangle.
        return

    if subject.type is FactSubjectType.CHARACTER:
        if subject.id not in known_character_ids:
            raise NotFoundError("Character", subject.id)
        return

    if subject.type is FactSubjectType.LOCATION:
        # `subject.id` cannot be None here -- the subject model requires one for every
        # type but WORLD -- but it is narrowed rather than asserted, so the type checker
        # is satisfied without a runtime claim that could only ever be redundant.
        location_id = subject.id
        if location_id is None or await store.get_location(session_id, location_id) is None:
            raise NotFoundError("Location", location_id)
        return

    raise ValidationError(
        f"A {subject.type.value!r} fact subject cannot be verified: nothing in this "
        "system resolves one yet, and a fact about an id that names nothing is worse "
        "than no fact. Use a world fact until the owning domain exists."
    )


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
