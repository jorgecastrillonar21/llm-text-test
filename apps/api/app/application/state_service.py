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

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.application.persistence import NewEvent, NewFact, WorldStatePort
from app.domain.errors import NotFoundError, StaleStateError, ValidationError
from app.domain.world_facts import (
    FactAuthority,
    FactMutation,
    FactSubject,
    FactSubjectType,
    RemoveFact,
    SetFact,
    StateMutationBatch,
    WorldFact,
    check_rules_compatibility,
    require_no_conflicts,
    require_permitted,
    requires_source_event,
    resolve_policy,
)
from app.domain.world_rules import WorldRules

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
    model_config = ConfigDict(frozen=True)

    op: Literal["set_fact", "remove_fact"]
    subject: FactSubject
    property: str


class StateChangeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    revision: int
    """The session's state revision *after* this batch. Monotonic: a caller that sees
    it go up knows something changed, and it never goes down."""

    event_id: uuid.UUID | None
    applied: list[AppliedMutation]


async def stage_state_change(
    store: WorldStatePort,
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
        else:
            await store.remove_fact(session_id, mutation.subject, mutation.property)
        applied.append(
            AppliedMutation(op=mutation.op, subject=mutation.subject, property=mutation.property)
        )

    revision = await store.bump_state_revision(session_id)

    return StateChangeResult(
        session_id=session_id, revision=revision, event_id=event_id, applied=applied
    )


async def apply_state_change(
    store: WorldStatePort,
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
    store: WorldStatePort, *, session_id: uuid.UUID
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
    store: WorldStatePort,
    *,
    session_id: uuid.UUID,
    rules: WorldRules,
    batch: StateMutationBatch,
    known_character_ids: set[uuid.UUID],
) -> list[tuple[FactMutation, WorldFact | None]]:
    """Check every mutation against policy, entities, storage and the world's rules.

    Returns each mutation paired with the fact it currently replaces, so the caller
    does not read twice. Raises on the first problem: a batch is all-or-nothing, so
    collecting further failures would be reporting on work that was never going to
    happen.
    """
    require_no_conflicts(batch)

    checked: list[tuple[FactMutation, WorldFact | None]] = []
    for mutation in batch.mutations:
        _require_resolvable_subject(mutation.subject, known_character_ids)
        require_permitted(
            batch.authority,
            resolve_policy(mutation.property),
            canonical_property=mutation.property,
        )

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


def _require_resolvable_subject(subject: FactSubject, known_character_ids: set[uuid.UUID]) -> None:
    """A fact must be about something that exists.

    Characters are the only entity this application can currently resolve, so they are
    the only ones checked. Locations and factions have no tables yet; their ids are
    accepted on trust and will become checkable when those systems arrive. That is
    recorded here rather than silently tolerated, because an unverifiable id is a fact
    that may be about nothing.
    """
    if subject.type is not FactSubjectType.CHARACTER:
        return
    if subject.id not in known_character_ids:
        raise NotFoundError("Character", subject.id)
