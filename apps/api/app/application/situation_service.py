"""Starting, reading and progressing the processes a session is running.

Three jobs, and deliberately not a fourth:

    start_situation      write a new process, with its participants
    load_situation_index the graph every parentage question needs
    progress_situation   ask a resolver what an interval did

What is not here is *applying* a progression. `UpdateSituation` and `ResolveSituation`
go through `state_service` with every other mutation, because a siege progressing
raises its intensity, breaches a gate and starts a food crisis together and all of them
belong to one event. A second write path for situations would mean a second transaction
boundary and a second revision counter.

# The resolver boundary

    (category, subtype) -> a registered resolver
    (category, None)    -> a registered resolver for the whole category
    otherwise           -> GenericProgressionResolver

A lookup table, not an `if subtype == ...` chain. `FireSituationResolver`,
`SiegeSituationResolver` and `InvestigationResolver` are all future work; when they
arrive they register themselves and this module does not change. That is the whole
reason the indirection exists at this stage, when there is exactly one resolver.

# The generic resolver is a placeholder and says so

`GenericProgressionResolver` moves intensity by momentum and lets momentum decay. It is
deterministic, bounded, and models nothing: it does not know that fires spread through
buildings, that sieges starve cities or that investigations follow evidence. It exists
so the boundary is exercised by real code and real tests rather than by a stub, and it
is labelled a placeholder everywhere it appears.

# No randomness, because there is no game RNG

Uncertain progression must come from a dedicated seeded RNG so that a session replays.
This project has none, so nothing here is stochastic. `random`, wall-clock time and
model sampling temperature are all disqualified -- the first two make a replay diverge
and the third makes token sampling decide whether a city starves.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.application.persistence import (
    NewParticipant,
    NewSituation,
    SituationPort,
    SituationReaderPort,
)
from app.domain.errors import NotFoundError, ValidationError
from app.domain.situation_progression import SituationProgressionResult
from app.domain.world_rules import WorldRules
from app.domain.world_situations import (
    LIVE_STATUSES,
    PROGRESSING_STATUSES,
    ParticipantEntityType,
    Situation,
    SituationCategory,
    SituationDeltas,
    SituationIndex,
    SituationParticipant,
    SituationProgressionRequest,
    SituationStatus,
    StartSituation,
    apply_deltas,
    check_parent_situation,
    momentum_drift,
    next_status_after,
)

logger = logging.getLogger(__name__)

MAX_SITUATIONS = 200
"""How many situations one session's index may hold.

A ceiling, not a page size, exactly like `MAX_GRAPH_SIZE` for geography. A session with
two hundred distinct ongoing processes has stopped being a story, and the right response
is a loud warning rather than a larger number.
"""

MAX_CONTEXT_SITUATIONS = 60
"""How many live situations a relevance pass will consider before selecting from them.
Bounded so context assembly cost does not grow with a session's history."""


class ProgressionContext(BaseModel):
    """Everything a resolver is allowed to see, assembled by the application.

    Handed in rather than fetched. A resolver that could query would be an adapter with
    opinions -- it would reach past the transaction boundary, make its own N+1s, and
    become impossible to test without a database. Whatever a future resolver needs gets
    added here, by someone who has to justify it.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    situation: Situation
    request: SituationProgressionRequest
    rules: WorldRules
    participants: tuple[SituationParticipant, ...] = ()

    @property
    def elapsed_minutes(self) -> int:
        return self.request.elapsed_minutes


class SituationResolver(Protocol):
    """Decides what an interval did to one process.

    Pure: context in, result out, no I/O and no writes. The application turns the
    result into one `StateMutationBatch` and commits it, or does not.
    """

    def resolve(self, context: ProgressionContext) -> SituationProgressionResult: ...


ESCALATION_REFERENCE = 40
"""The `danger.escalation_rate` at which drift runs at its nominal speed.

The balanced default. A world at 80 moves twice as fast, a world at 10 a quarter as
fast, and the arithmetic reads the actual resolved value rather than branching on a
preset name -- `if preset == "dark_fantasy"` would be a second rules system with one
entry.
"""

DRIFT_PER_HOUR = 10
"""How much of a process's momentum turns into intensity per fictional hour, as a
percentage, before the world's escalation rate scales it.

At `momentum = 50` and the reference escalation rate, six hours moves intensity by
thirty -- a siege that is going somewhere gets somewhere over a night, and one at
`momentum = 0` sits exactly where it was for a year.
"""

MOMENTUM_DECAY_PER_HOUR = 4
"""How fast momentum returns towards zero when nothing is driving it.

Processes lose steam. A fire that is spreading keeps spreading, but slower each hour
unless something feeds it -- and this is the arithmetic that stops an unattended world
from accelerating forever in whichever direction it happened to start.
"""

_CADENCE_MINUTES: dict[SituationCategory, int] = {
    SituationCategory.HAZARD: 15,
    SituationCategory.CONFLICT: 6 * 60,
    SituationCategory.INVESTIGATION: 24 * 60,
    SituationCategory.SOCIAL: 6 * 60,
    SituationCategory.POLITICAL: 3 * 24 * 60,
    SituationCategory.ECONOMIC: 3 * 24 * 60,
    SituationCategory.ENVIRONMENTAL: 24 * 60,
    SituationCategory.PROJECT: 7 * 24 * 60,
    SituationCategory.EVENT: 60,
    SituationCategory.OTHER: 24 * 60,
}
"""How often each kind of process is worth looking at again.

Fires in minutes, sieges in hours, wars and politics in days, construction in weeks.
This is the entire reason there is no universal tick: one cadence for all ten would be
wrong for nine of them, and the cheap wrong answer -- evaluate everything every
minute -- costs the most for the processes that need it least.

A suggestion, not a contract. A situation is never *required* to have a next
evaluation, and a resolver returning None is the ordinary answer for something that
moves only when it is acted on.
"""


class GenericProgressionResolver:
    """A placeholder resolver. Deterministic, bounded, and models nothing.

    ## This is not a simulation

    It knows one thing: a process with momentum drifts in the direction of its momentum,
    and momentum decays unless something sustains it. It does not know that fire spreads
    through timber, that sieges starve cities, that investigations need witnesses or
    that reconstruction needs materials. Every one of those is a specialised resolver
    that does not exist yet.

    What it does give is a real exercise of the boundary: the interval arithmetic, the
    clamping, the WorldRules read, the dormancy transition and the next-evaluation
    scheduling all run through this and are all tested through it.

    ## It can improve things

    Deliberately symmetric. Negative momentum shrinks intensity exactly as positive
    momentum grows it, so a fire brigade containing a blaze and a festival winding down
    are the same arithmetic with a different sign. Nothing in this drifts towards
    catastrophe on its own -- a world whose unattended processes only ever worsened
    would be one where the protagonist is the only thing that ever helps, which is the
    opposite of what world autonomy is for.
    """

    def resolve(self, context: ProgressionContext) -> SituationProgressionResult:
        situation = context.situation
        elapsed = context.elapsed_minutes

        if situation.status not in PROGRESSING_STATUSES:
            # Planned and dormant processes are real and reach context; they simply do
            # not move because time passed. Waking one is a decision, not a drift.
            return SituationProgressionResult(
                situation_id=situation.id,
                notes=f"{situation.status.value}: nothing progresses without a decision.",
            )

        if not context.rules.simulation.world_continues_without_player:
            # The world's own rules say it waits. Honoured as a refusal to move rather
            # than as a smaller movement: "the world does not act on its own" is not a
            # speed setting.
            return SituationProgressionResult(
                situation_id=situation.id,
                notes="world_continues_without_player is false; the world waits.",
            )

        rate = context.rules.danger.escalation_rate
        intensity_delta = momentum_drift(
            situation.momentum,
            elapsed,
            per_hour=max(1, DRIFT_PER_HOUR * rate // ESCALATION_REFERENCE),
        )
        momentum_delta = -_decay(situation.momentum, elapsed)

        deltas = SituationDeltas(
            intensity_delta=intensity_delta,
            # Threat is a judgement about danger, not a function of size. A festival
            # getting bigger does not get more dangerous, and a resolver that moved
            # threat with intensity would make it one.
            threat_delta=0,
            momentum_delta=momentum_delta,
        )
        intensity, _threat, _momentum = apply_deltas(situation, deltas)

        return SituationProgressionResult(
            situation_id=situation.id,
            deltas=deltas,
            status_change=next_status_after(situation.status, intensity),
            next_progression_at=_next_evaluation(situation, context.request.to_time),
            notes=(
                f"generic placeholder resolver: {elapsed} min at momentum "
                f"{situation.momentum}, escalation {rate}"
            ),
        )


def _decay(momentum: int, elapsed_minutes: int) -> int:
    """How much momentum bleeds off over an interval, signed towards zero.

    Never past zero: a process winding down stops winding down, it does not start
    winding up. That reversal would be this arithmetic inventing a plot twist.
    """
    if momentum == 0 or elapsed_minutes <= 0:
        return 0
    magnitude = int(abs(momentum) * (elapsed_minutes / 60) * MOMENTUM_DECAY_PER_HOUR / 100)
    magnitude = min(magnitude, abs(momentum))
    return magnitude if momentum > 0 else -magnitude


def _next_evaluation(situation: Situation, now: int) -> int:
    return now + _CADENCE_MINUTES.get(situation.category, _CADENCE_MINUTES[SituationCategory.OTHER])


_RESOLVERS: dict[tuple[SituationCategory, str | None], SituationResolver] = {}
"""The registry. Empty today, and that is the honest state of it.

Specialised resolvers register here. Until one does, every situation is handled by the
generic placeholder, which is what `resolver_for` falls back to.
"""

_GENERIC = GenericProgressionResolver()


def register_resolver(
    category: SituationCategory, subtype: str | None, resolver: SituationResolver
) -> None:
    """Attach a resolver to a category, or to one subtype within it.

    `subtype=None` claims the whole category. A registration for `(HAZARD, "fire")`
    takes precedence over one for `(HAZARD, None)`, which takes precedence over the
    generic placeholder.
    """
    _RESOLVERS[(category, subtype)] = resolver


def resolver_for(situation: Situation) -> SituationResolver:
    """The most specific registered resolver, or the generic placeholder."""
    specific = _RESOLVERS.get((situation.category, situation.subtype))
    if specific is not None:
        return specific
    return _RESOLVERS.get((situation.category, None), _GENERIC)


async def load_situation_index(
    reader: SituationReaderPort,
    *,
    session_id: uuid.UUID,
    statuses: frozenset[SituationStatus] | None = None,
) -> SituationIndex:
    """Every situation this session can see, in one object.

    Loaded whole and walked in memory, for the same reason the spatial graph is:
    parentage questions need more than one node at a time, and answering them one query
    at a time is how an N+1 gets written into a turn.

    `statuses=None` loads history too, which is what a parentage check needs -- a
    concluded war is still the cause of the siege beneath it.
    """
    situations = await reader.load_situations(session_id, statuses=statuses, limit=MAX_SITUATIONS)
    if len(situations) == MAX_SITUATIONS:
        logger.warning(
            "Session %s reached the %d-situation ceiling; parentage answers may be "
            "incomplete. This session has more ongoing processes than the design assumes.",
            session_id,
            MAX_SITUATIONS,
        )
    return SituationIndex(situations)


async def start_situation(
    store: SituationPort,
    *,
    session_id: uuid.UUID,
    mutation: StartSituation,
    started_at: int,
    source_event_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Write a new process and attach its participants.

    Everything that cannot be a column constraint is checked here: the session exists,
    the primary location is one this session can see, the parent is in this session and
    does not create a cycle, and character participants are characters that exist.

    The id is minted by the application. A caller never supplies one -- `StartSituation`
    has no field for it -- least of all the language model, which would be reproducing
    a uuid it read in a prompt.

    Staged, not committed. The caller owns the transaction, so a failed turn takes the
    riot with it rather than leaving a war nobody declared.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    # Visibility, not existence: another session's generated geography is not "somewhere
    # I may not point at", it is somewhere this session cannot see, and a siege of it
    # would be a siege of nowhere.
    location_id = mutation.primary_location_id
    if location_id is not None and await store.get_location(session_id, location_id) is None:
        raise NotFoundError("Location", location_id)

    if mutation.parent_situation_id is not None:
        index = await load_situation_index(store, session_id=session_id)
        check_parent_situation(
            index,
            # Validated as the situation it is about to become. The id is a placeholder:
            # nothing is in the index yet, so no cycle can run through it, and the real
            # id is minted below.
            child=_prospective(mutation, session_id=session_id, started_at=started_at),
            parent_id=mutation.parent_situation_id,
        )

    await _require_resolvable_participants(store, world_id=session.world_id, mutation=mutation)

    situation_id = await store.add_situation(
        NewSituation(
            session_id=session_id,
            category=mutation.category,
            subtype=mutation.subtype,
            title=mutation.title,
            description=mutation.description,
            status=mutation.status,
            intensity=mutation.intensity,
            threat=mutation.threat,
            momentum=mutation.momentum,
            importance=mutation.importance,
            scope=mutation.scope,
            primary_location_id=mutation.primary_location_id,
            parent_situation_id=mutation.parent_situation_id,
            started_at=started_at,
            source_event_id=source_event_id,
            situation_metadata=dict(mutation.situation_metadata),
            tags=mutation.tags,
        )
    )

    for participant in mutation.participants:
        await store.add_participant(
            NewParticipant(
                situation_id=situation_id,
                entity_type=participant.entity_type,
                entity_id=participant.entity_id,
                role=participant.role,
            )
        )

    return situation_id


async def materialize_initial_situations(store: SituationPort, *, session_id: uuid.UUID) -> int:
    """Give a new session the processes its world already had under way.

    The template is a starting configuration, not live state -- the same relationship
    `initial_facts` has to `world_facts`. Each session gets its own rows with its own
    ids and diverges immediately: lifting the siege in one session leaves the template,
    and every other session, untouched. Nothing here writes back to the world, and
    there is deliberately no code path that could.

    Seeded situations carry no `source_event_id`, which is the documented exception:
    nothing *happened* to start them, the world simply began that way. Gameplay-created
    situations should always name the event that caused them.

    Staged, not committed: session creation is one transaction and this is part of it.
    Returns how many were written, which is zero for a world with no starting processes
    and is the common case.
    """
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    seeds = await store.load_initial_situations(session.world_id)
    written = 0
    for seed in seeds:
        await start_situation(
            store,
            session_id=session_id,
            mutation=seed,
            # Minute zero: these were already under way when the session began.
            started_at=0,
        )
        written += 1
    return written


async def progress_situation(
    store: SituationPort,
    *,
    session_id: uuid.UUID,
    request: SituationProgressionRequest,
) -> SituationProgressionResult:
    """Ask the appropriate resolver what an interval did to one process.

    Reads only. Nothing here writes: the result is a decision, and turning it into a
    committed change is `state_service`'s job through the ordinary mutation batch. That
    split is what makes a progression atomic with everything else it causes.
    """
    situation = await store.get_situation(session_id, request.situation_id)
    if situation is None:
        raise NotFoundError("Situation", request.situation_id)

    session = await store.get_session(session_id)
    if session is None:  # FK guarantees this, but the type checker does not.
        raise NotFoundError("GameSession", session_id)
    world = await store.get_world(session.world_id)
    if world is None:
        raise NotFoundError("World", session.world_id)

    if not situation.is_live:
        raise ValidationError(
            f"Situation {situation.title!r} is {situation.status.value} and cannot progress. "
            "A concluded process stays concluded; start a new one caused by it instead."
        )

    if request.from_time < situation.started_at:
        raise ValidationError(
            f"Cannot progress {situation.title!r} from minute {request.from_time}: it did "
            f"not exist until {situation.started_at}."
        )

    participants = await store.load_participants([situation.id])
    resolver = resolver_for(situation)
    return resolver.resolve(
        ProgressionContext(
            situation=situation,
            request=request,
            rules=world.rules,
            participants=tuple(participants),
        )
    )


async def situations_involving(
    reader: SituationReaderPort,
    *,
    session_id: uuid.UUID,
    entity_id: uuid.UUID,
    entity_type: ParticipantEntityType | None = None,
    live_only: bool = True,
    limit: int = MAX_CONTEXT_SITUATIONS,
) -> list[Situation]:
    """Everything one entity is caught up in.

    The query the participant table exists for, and the one future simulation
    prioritisation will lean on hardest: what a player's allies and enemies are already
    involved in is most of what makes a world feel like it is watching.
    """
    return await reader.load_situations_for_entity(
        session_id,
        entity_id=entity_id,
        entity_type=entity_type,
        statuses=LIVE_STATUSES if live_only else None,
        limit=limit,
    )


async def _require_resolvable_participants(
    store: SituationPort, *, world_id: uuid.UUID, mutation: StartSituation
) -> None:
    """A character participant must be a character that exists.

    Characters are the only entity type this application can currently resolve.
    Factions have no table yet and their ids are accepted on trust; `OTHER` is the
    escape hatch and is accepted by definition. Recorded here rather than silently
    tolerated, because an unverifiable id is a participant who may be nobody.
    """
    characters = [
        participant
        for participant in mutation.participants
        if participant.entity_type is ParticipantEntityType.CHARACTER
    ]
    if not characters:
        return

    known = await store.known_character_ids(world_id)
    for participant in characters:
        if participant.entity_id not in known:
            raise NotFoundError("Character", participant.entity_id)


def _prospective(mutation: StartSituation, *, session_id: uuid.UUID, started_at: int) -> Situation:
    """The situation `mutation` will become, for validation that needs a whole one.

    A placeholder id and timestamps, because `check_parent_situation` reads session,
    identity and title, and building a throwaway is cheaper than teaching it a second
    shape.
    """
    now = dt.datetime.now(dt.UTC)
    return Situation(
        id=uuid.uuid4(),
        session_id=session_id,
        category=mutation.category,
        subtype=mutation.subtype,
        title=mutation.title,
        description=mutation.description,
        status=mutation.status,
        intensity=mutation.intensity,
        threat=mutation.threat,
        momentum=mutation.momentum,
        importance=mutation.importance,
        scope=mutation.scope,
        primary_location_id=mutation.primary_location_id,
        parent_situation_id=mutation.parent_situation_id,
        started_at=started_at,
        last_progressed_at=started_at,
        situation_metadata=dict(mutation.situation_metadata),
        tags=mutation.tags,
        created_at=now,
        updated_at=now,
    )
