"""The resolution pipeline, against a real database and the real ports.

    Command -> context at revision N -> resolver -> outcome -> validate
            -> BEGIN  record, events, mutations, clock  COMMIT

Everything asserted here is a property of that transaction, so none of it can be
observed in memory: that a retry writes nothing a second time, that one failing
mutation takes the record and its events down with it, that a refusal leaves the world
byte-for-byte as it was. The pure half -- what a verdict may claim about itself, what
policy does to a proposed importance -- is in `test_resolution.py`.

# The scripted resolver

Two real resolvers exist, and neither can produce the outcomes some of these tests
need: a batch of three mutations with one that cannot work, or a resolver that raises
before the transaction opens. `_ScriptedResolver` is installed over `advance_time` for
the length of one test through the same `register_resolver` any future system would
use, and the fixture puts the real one back. It is a test double and is named as one.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.context_builder import (
    LANDMARK_EVENT_LIMIT,
    RECENT_EVENT_LIMIT,
    build_story_context,
)
from app.application.event_service import record_events
from app.application.persistence import EventWriterPort, ResolutionStorePort
from app.application.resolution_service import ResolutionRequest, ResolutionResult, resolve
from app.application.resolvers import known_resolvers, register_resolver
from app.application.situation_service import start_situation
from app.application.time_service import advance_time, schedule_event
from app.domain.errors import NotFoundError, StaleStateError
from app.domain.resolution import (
    AdvanceTimeCommand,
    Command,
    EventCandidate,
    EventCategory,
    ProgressSituationCommand,
    ResolutionContext,
    ResolutionDisposition,
    ResolutionOutcome,
    ResolutionSourceType,
)
from app.domain.world_facts import WORLD_SUBJECT, FactSubject, FactSubjectType, SetFact
from app.domain.world_rules import default_world_rules
from app.domain.world_rules.enums import TimeProgression
from app.domain.world_situations import (
    SituationCategory,
    SituationScope,
    StartSituation,
    UpdateSituation,
)
from app.domain.world_time import (
    ScheduledEventStatus,
    TimeAdvanceReason,
    TimeAdvanceRequest,
)
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway
from app.infrastructure.story.rendering import render_context

APP_ROOT = Path(__file__).resolve().parents[1] / "app"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


class _ScriptedResolver:
    """Test double. Returns the outcome a test handed it, or raises what it handed.

    Also counts its calls, which is how "a replay never resolves again" and "a stale
    revision is refused before anything runs" become assertions rather than inferences.
    """

    name = "scripted"
    version = "1"

    def __init__(self, result: ResolutionOutcome | Exception) -> None:
        self._result = result
        self.calls = 0

    def resolve(self, command: Command, context: ResolutionContext) -> ResolutionOutcome:
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def scripted() -> Iterator[Callable[[ResolutionOutcome | Exception], _ScriptedResolver]]:
    """Install a scripted resolver over `advance_time`, and restore the real one after."""
    original = known_resolvers()

    def _install(result: ResolutionOutcome | Exception) -> _ScriptedResolver:
        resolver = _ScriptedResolver(result)
        register_resolver("advance_time", resolver)
        return resolver

    yield _install
    for kind, resolver in original.items():
        register_resolver(kind, resolver)


def _paused_rules() -> dict[str, object]:
    """A world whose clock only moves for an authored timeskip or a developer."""
    document = default_world_rules().model_dump(mode="json")
    simulation = document["simulation"]
    assert isinstance(simulation, dict)
    simulation["time_progression"] = TimeProgression.PAUSED.value
    return document


async def _world_and_session(
    db_session: AsyncSession,
    make_world,
    *,
    rules_json: dict[str, object] | None = None,
    elapsed_minutes: int = 0,
) -> tuple[uuid.UUID, uuid.UUID]:
    # `rules_json` spelled out because the column default only applies at INSERT, and
    # these tests read `world.rules` through the gateway before that happens.
    world = make_world(rules_json=rules_json or default_world_rules().model_dump(mode="json"))
    db_session.add(world)
    await db_session.flush()
    session = models.GameSession(
        world_id=world.id,
        title="S",
        player_name="Rin",
        current_location="somewhere",
        elapsed_minutes=elapsed_minutes,
    )
    db_session.add(session)
    await db_session.commit()
    return world.id, session.id


def _siege(**overrides: object) -> StartSituation:
    data: dict[str, object] = {
        "category": SituationCategory.CONFLICT,
        "subtype": "siege",
        "title": "Siege of Asterfall",
        "intensity": 40,
        "threat": 70,
        "momentum": 60,
        "importance": 4,
        "scope": SituationScope.REGIONAL,
    }
    data.update(overrides)
    return StartSituation(**data)  # type: ignore[arg-type]


def _tick(minutes: int = 0, reason: TimeAdvanceReason = TimeAdvanceReason.DEBUG) -> Command:
    return AdvanceTimeCommand(minutes=minutes, reason=reason)


async def _resolve(
    store: ResolutionStorePort,
    session_id: uuid.UUID,
    command: Command,
    *,
    key: str,
    source_type: ResolutionSourceType = ResolutionSourceType.SYSTEM,
    source_id: uuid.UUID | None = None,
    parent_resolution_id: uuid.UUID | None = None,
    expected_revision: int | None = None,
) -> ResolutionResult:
    return await resolve(
        store,
        request=ResolutionRequest(
            session_id=session_id,
            command=command,
            idempotency_key=key,
            source_type=source_type,
            source_id=source_id,
            parent_resolution_id=parent_resolution_id,
            expected_revision=expected_revision,
        ),
    )


async def _count(db_session: AsyncSession, model: type, session_id: uuid.UUID) -> int:
    rows = await db_session.execute(
        select(func.count()).select_from(model).where(model.session_id == session_id)
    )
    return int(rows.scalar_one())


async def _revision(store: ResolutionStorePort, session_id: uuid.UUID) -> int:
    session = await store.get_session(session_id)
    assert session is not None
    return session.state_revision


async def _elapsed(store: ResolutionStorePort, session_id: uuid.UUID) -> int:
    session = await store.get_session(session_id)
    assert session is not None
    return session.elapsed_minutes


async def _siege_under_way(
    db_session: AsyncSession, make_world, *, at: int = 360
) -> tuple[uuid.UUID, uuid.UUID, SqlAlchemyTurnGateway]:
    """A session with one live situation started at minute zero and a clock at `at`."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(), started_at=0
    )
    if at:
        await store.set_elapsed_minutes(session_id, at)
    await db_session.commit()
    return session_id, situation_id, store


# ---------------------------------------------------------------------------
# The record: what one resolution writes down about itself
# ---------------------------------------------------------------------------


async def test_an_applied_resolution_records_the_verdict_and_the_revision_it_moved(
    db_session: AsyncSession, make_world
) -> None:
    session_id, situation_id, store = await _siege_under_way(db_session, make_world)

    result = await _resolve(
        store,
        session_id,
        ProgressSituationCommand(situation_id=situation_id),
        key="progress:1",
        source_type=ResolutionSourceType.SITUATION_PROGRESSION,
        source_id=situation_id,
    )

    record = result.resolution
    assert record.disposition is ResolutionDisposition.APPLIED
    # Which formula decided this, and which revision of it.
    assert (record.resolver_name, record.resolver_version) == ("situation_progression", "1")
    assert (record.state_revision_before, record.state_revision_after) == (0, 1)
    assert record.mutation_count >= 1
    # Who asked, and what specifically.
    assert record.source_type is ResolutionSourceType.SITUATION_PROGRESSION
    assert record.source_id == situation_id
    assert record.idempotency_key == "progress:1"
    assert record.turn_index == 0


async def test_the_record_is_stamped_in_fictional_time_not_on_the_wall_clock(
    db_session: AsyncSession, make_world
) -> None:
    """`occurred_at` is a session minute, twenty days into a story that has been played
    over an afternoon. Two resolutions reached in the same fictional minute carry the
    same one, however far apart in wall time the rows were actually written."""
    _, session_id = await _world_and_session(db_session, make_world, elapsed_minutes=29022)
    store = SqlAlchemyTurnGateway(db_session)

    first = await _resolve(store, session_id, _tick(), key="tick:1")
    second = await _resolve(store, session_id, _tick(), key="tick:2")

    assert first.resolution.occurred_at == 29022
    assert second.resolution.occurred_at == 29022
    # Written now, in a story that is three weeks old. The two are not the same number
    # and are not the same kind of number.
    assert first.resolution.created_at.year >= 2024
    assert await _elapsed(store, session_id) == 29022


async def test_a_rejected_resolution_is_the_world_saying_no_and_is_recorded_as_such(
    db_session: AsyncSession, make_world
) -> None:
    """A rule refusing an action is gameplay, not an error. It is persisted, it carries
    a reason code a machine can group by, and it changed nothing."""
    _, session_id = await _world_and_session(db_session, make_world, rules_json=_paused_rules())
    store = SqlAlchemyTurnGateway(db_session)

    result = await _resolve(
        store, session_id, _tick(120, TimeAdvanceReason.SIMULATION), key="tick:1"
    )

    record = result.resolution
    assert record.disposition is ResolutionDisposition.REJECTED
    assert record.reason_code == "time_progression_forbidden"
    assert record.state_revision_before == record.state_revision_after
    assert (record.event_count, record.mutation_count) == (0, 0)
    assert await _elapsed(store, session_id) == 0
    assert await _count(db_session, models.Resolution, session_id) == 1


async def test_a_no_effect_resolution_is_recorded_and_moves_nothing(
    db_session: AsyncSession, make_world
) -> None:
    """Legitimate, and nothing happened: the clock has not moved since this process was
    last looked at, so there is no interval to evaluate."""
    session_id, situation_id, store = await _siege_under_way(db_session, make_world, at=0)

    result = await _resolve(
        store,
        session_id,
        ProgressSituationCommand(situation_id=situation_id),
        key="progress:1",
        source_type=ResolutionSourceType.SITUATION_PROGRESSION,
    )

    record = result.resolution
    assert record.disposition is ResolutionDisposition.NO_EFFECT
    assert record.reason_code == "already_progressed"
    assert not record.changed_state
    assert await _revision(store, session_id) == 0


async def test_a_resolution_can_record_the_resolution_it_belongs_to(
    db_session: AsyncSession, make_world
) -> None:
    """Nothing produces children yet. The link is stored and read back, so the first
    compound action does not have to add a column to a table full of history."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    parent = await _resolve(store, session_id, _tick(), key="tick:parent")
    child = await _resolve(
        store,
        session_id,
        _tick(),
        key="tick:child",
        parent_resolution_id=parent.resolution.id,
    )

    stored = await store.get_resolution(session_id, child.resolution.id)
    assert stored is not None
    assert stored.parent_resolution_id == parent.resolution.id


async def test_a_resolution_from_another_session_reads_as_missing(
    db_session: AsyncSession, make_world
) -> None:
    world_id, session_id = await _world_and_session(db_session, make_world)
    other = models.GameSession(world_id=world_id, title="B", player_name="Other")
    db_session.add(other)
    await db_session.flush()
    other_id = other.id
    store = SqlAlchemyTurnGateway(db_session)

    result = await _resolve(store, session_id, _tick(), key="tick:1")
    await db_session.commit()

    assert await store.get_resolution(other_id, result.resolution.id) is None
    # And the key is scoped too, so two saves may both hold a `turn:1`.
    assert await store.find_resolution_by_key(other_id, "tick:1") is None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_the_same_idempotency_key_twice_resolves_exactly_once(
    db_session: AsyncSession, make_world
) -> None:
    """The whole checklist in one test, because these six facts are one property.

    Submitting the same key twice must leave: one record, one set of events, one
    application of the mutations, one revision increment -- and the second call must
    return the first call's result rather than a second verdict.
    """
    session_id, situation_id, store = await _siege_under_way(db_session, make_world)
    command = ProgressSituationCommand(situation_id=situation_id)

    first = await _resolve(
        store,
        session_id,
        command,
        key="scheduled:abc",
        source_type=ResolutionSourceType.SITUATION_PROGRESSION,
    )
    intensity_after_first = await store.get_situation(session_id, situation_id)
    events_after_first = await _count(db_session, models.GameEvent, session_id)

    second = await _resolve(
        store,
        session_id,
        command,
        key="scheduled:abc",
        source_type=ResolutionSourceType.SITUATION_PROGRESSION,
    )

    # 6. the second call returns what the first one decided
    assert second.replayed is True
    assert second.resolution.id == first.resolution.id
    # No resolver ran, so there is no outcome object and no batch result to report.
    assert second.outcome is None
    assert second.state_change is None
    # 2. one record
    assert await _count(db_session, models.Resolution, session_id) == 1
    # 3. one set of events
    assert await _count(db_session, models.GameEvent, session_id) == events_after_first
    assert len(second.events) == len(first.events)
    # 4. the mutations were applied once
    unchanged = await store.get_situation(session_id, situation_id)
    assert intensity_after_first is not None and unchanged is not None
    assert unchanged.intensity == intensity_after_first.intensity
    assert unchanged.momentum == intensity_after_first.momentum
    assert unchanged.last_progressed_at == intensity_after_first.last_progressed_at
    # 5. one revision increment
    assert await _revision(store, session_id) == 1


async def test_a_replay_never_runs_the_resolver_again(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """The property that makes a retry free.

    Nothing in this build calls a language model or draws a random number inside a
    resolver -- but both are coming, and "the second call did not resolve" is the thing
    that will stop a retried turn rolling different dice.
    """
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    resolver = scripted(
        ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            state_mutations=(
                SetFact(subject=WORLD_SUBJECT, property="world.season", value="winter"),
            ),
        )
    )

    await _resolve(store, session_id, _tick(), key="turn:once")
    await _resolve(store, session_id, _tick(), key="turn:once")

    assert resolver.calls == 1


async def test_a_replay_does_not_advance_the_clock_a_second_time(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    await _resolve(store, session_id, _tick(90), key="tick:once")
    replay = await _resolve(store, session_id, _tick(90), key="tick:once")

    assert replay.replayed is True
    assert await _elapsed(store, session_id) == 90


async def test_two_different_keys_are_two_resolutions(db_session: AsyncSession, make_world) -> None:
    """The other half of the rule. Idempotency that collapsed genuinely distinct
    requests would make a player's second action a replay of their first."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    first = await _resolve(store, session_id, _tick(30), key="tick:1")
    second = await _resolve(store, session_id, _tick(30), key="tick:2")

    assert first.resolution.id != second.resolution.id
    assert second.replayed is False
    assert await _elapsed(store, session_id) == 60


# ---------------------------------------------------------------------------
# State revision
# ---------------------------------------------------------------------------


async def test_a_batch_of_several_mutations_still_moves_the_revision_once(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """One committed resolution moves the revision exactly once, whatever it touched.

    Three mutations across two domains is still one change to the world's version --
    the revision counts resolutions, not writes, which is what makes it usable as an
    optimistic-concurrency token.
    """
    session_id, situation_id, store = await _siege_under_way(db_session, make_world)
    scripted(
        ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            state_mutations=(
                SetFact(subject=WORLD_SUBJECT, property="world.season", value="winter"),
                SetFact(subject=WORLD_SUBJECT, property="world.political_status", value="tense"),
                UpdateSituation(situation_id=situation_id, intensity_delta=5),
            ),
        )
    )

    result = await _resolve(store, session_id, _tick(), key="batch:1")

    assert result.resolution.mutation_count == 3
    assert (result.resolution.state_revision_before, result.resolution.state_revision_after) == (
        0,
        1,
    )
    assert await _revision(store, session_id) == 1


async def test_a_resolution_that_only_wrote_history_leaves_the_revision_alone(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """History and authoritative state are different things. An event is not a mutation,
    so the revision -- which exists to detect state going stale -- does not move."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    scripted(
        ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            events=(
                EventCandidate(
                    category=EventCategory.WORLD,
                    subtype="secret_discovered",
                    summary="Rin found the letter behind the panel.",
                ),
            ),
        )
    )

    result = await _resolve(store, session_id, _tick(), key="history:1")

    assert result.resolution.event_count == 1
    assert not result.resolution.changed_state
    assert await _revision(store, session_id) == 0


async def test_a_stale_expected_revision_is_refused_before_the_resolver_runs(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """A caller holding a stale view of the world is told so, loudly. Silently applying
    its decision would commit a verdict reached against state that has since changed."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    resolver = scripted(ResolutionOutcome(disposition=ResolutionDisposition.NO_EFFECT))

    with pytest.raises(StaleStateError):
        await _resolve(store, session_id, _tick(), key="stale:1", expected_revision=99)

    assert resolver.calls == 0
    assert await _count(db_session, models.Resolution, session_id) == 0


async def test_a_matching_expected_revision_is_allowed_through(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    result = await _resolve(store, session_id, _tick(), key="fresh:1", expected_revision=0)

    assert result.resolution.state_revision_before == 0


# ---------------------------------------------------------------------------
# Transactional atomicity
# ---------------------------------------------------------------------------


async def test_one_failing_mutation_takes_the_record_and_its_events_with_it(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """A resolution is all or nothing.

    The outcome here is the shape the spec asks for: a GameEvent plus several
    StateMutations, one of which cannot work. The record is written first because the
    events carry its foreign key, so all three are already staged when the bad mutation
    raises -- and none of them may survive.
    """
    session_id, situation_id, store = await _siege_under_way(db_session, make_world)
    before_revision = await _revision(store, session_id)
    before_events = await _count(db_session, models.GameEvent, session_id)
    intact = await store.get_situation(session_id, situation_id)
    assert intact is not None

    scripted(
        ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            events=(
                EventCandidate(
                    category=EventCategory.WORLD,
                    subtype="secret_discovered",
                    summary="The tunnel under the wall is real.",
                ),
            ),
            state_mutations=(
                SetFact(subject=WORLD_SUBJECT, property="world.tunnel", value=True),
                UpdateSituation(situation_id=situation_id, intensity_delta=25),
                # The one that cannot work: nothing by this id exists.
                UpdateSituation(situation_id=uuid.uuid4(), intensity_delta=10),
            ),
        )
    )

    with pytest.raises(NotFoundError):
        await _resolve(store, session_id, _tick(), key="atomic:1")
    # What a request would do on the way out. Everything below reads what survived it.
    await db_session.rollback()

    assert await _count(db_session, models.Resolution, session_id) == 0
    assert await store.find_resolution_by_key(session_id, "atomic:1") is None
    assert await _count(db_session, models.GameEvent, session_id) == before_events
    assert await store.get_fact(session_id, WORLD_SUBJECT, "world.tunnel") is None
    unchanged = await store.get_situation(session_id, situation_id)
    assert unchanged is not None and unchanged.intensity == intact.intensity
    assert await _revision(store, session_id) == before_revision


async def test_a_technical_failure_is_not_a_gameplay_rejection(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """A resolver that raises leaves no verdict at all.

    Persisting a `rejected` here would be a lie the audit trail can never recover from:
    it would read as the world having refused the action, and a player told "the world
    said no" when the truth is "a bug in our code" is being told something false.
    """
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    scripted(RuntimeError("the resolver exploded"))

    with pytest.raises(RuntimeError, match="exploded"):
        await _resolve(store, session_id, _tick(60), key="turn:boom")
    await db_session.rollback()

    assert await _count(db_session, models.Resolution, session_id) == 0
    assert await _count(db_session, models.GameEvent, session_id) == 0
    assert await _revision(store, session_id) == 0
    assert await _elapsed(store, session_id) == 0


async def test_a_retry_after_a_technical_failure_can_resolve_for_real(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """The other half of the previous test. Nothing was recorded under the key, so the
    same submission arriving again is a first attempt rather than a replay of a crash."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    scripted(RuntimeError("the provider was unreachable"))

    with pytest.raises(RuntimeError):
        await _resolve(store, session_id, _tick(60), key="turn:retry")
    await db_session.rollback()

    scripted(
        ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            state_mutations=(
                SetFact(subject=WORLD_SUBJECT, property="world.season", value="spring"),
            ),
        )
    )
    result = await _resolve(store, session_id, _tick(60), key="turn:retry")

    assert result.replayed is False
    assert result.resolution.disposition is ResolutionDisposition.APPLIED
    assert await _count(db_session, models.Resolution, session_id) == 1


# ---------------------------------------------------------------------------
# GameEvent ordering
# ---------------------------------------------------------------------------


async def test_the_events_of_one_resolution_share_a_minute_and_are_sequenced(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """Ties on the fictional minute are the normal case, so ordering needs a second key
    -- and it is a counter, not invented seconds on a clock that only has minutes."""
    _, session_id = await _world_and_session(db_session, make_world, elapsed_minutes=500)
    store = SqlAlchemyTurnGateway(db_session)
    scripted(
        ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            events=tuple(
                EventCandidate(
                    category=EventCategory.WORLD,
                    subtype="secret_discovered",
                    summary=f"Discovery {n}.",
                )
                for n in (1, 2, 3)
            ),
        )
    )

    result = await _resolve(store, session_id, _tick(), key="events:1")

    assert [event.occurred_at for event in result.events] == [500, 500, 500]
    sequences = [event.sequence for event in result.events]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == 3


async def test_the_sequence_keeps_climbing_across_resolutions(
    db_session: AsyncSession, make_world, scripted
) -> None:
    _, session_id = await _world_and_session(db_session, make_world, elapsed_minutes=100)
    store = SqlAlchemyTurnGateway(db_session)
    scripted(
        ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            events=(
                EventCandidate(
                    category=EventCategory.WORLD,
                    subtype="secret_discovered",
                    summary="Something came to light.",
                ),
            ),
        )
    )

    first = await _resolve(store, session_id, _tick(), key="events:1")
    await store.set_elapsed_minutes(session_id, 900)
    await db_session.commit()
    second = await _resolve(store, session_id, _tick(), key="events:2")

    assert first.events[0].sequence < second.events[0].sequence
    assert first.events[0].occurred_at == 100
    assert second.events[0].occurred_at == 900


async def test_history_is_read_newest_fictional_minute_first(
    db_session: AsyncSession, make_world
) -> None:
    """The technical `created_at` does not decide the order. These three rows are
    written oldest-first in wall time and deliberately out of order in fictional time."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)

    for minute in (900, 100, 500):
        await record_events(
            store,
            session_id=session_id,
            turn_index=0,
            occurred_at=minute,
            candidates=[
                EventCandidate(
                    category=EventCategory.WORLD,
                    subtype="secret_discovered",
                    summary=f"Minute {minute}.",
                )
            ],
        )
    await db_session.commit()

    events = await store.load_events(session_id, limit=10)

    assert [event.occurred_at for event in events] == [900, 500, 100]


# ---------------------------------------------------------------------------
# Event significance and persistence policy
# ---------------------------------------------------------------------------


async def test_policy_decides_what_history_keeps_whatever_the_proposer_wanted(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """Four candidates from something that thinks everything is important.

    A door that was opened is dropped even at importance 5. A major death is raised to
    the top of the scale even when it was filed at 1. An unregistered subtype is kept
    and capped below landmark. The proposer only ever suggests.
    """
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    scripted(
        ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            events=(
                EventCandidate(
                    category=EventCategory.ACTION,
                    subtype="door_opened",
                    summary="Rin opened the storeroom door.",
                    importance=5,
                ),
                EventCandidate(
                    category=EventCategory.WORLD,
                    subtype="secret_discovered",
                    summary="The tunnel under the wall is real.",
                ),
                EventCandidate(
                    category=EventCategory.CHARACTER,
                    subtype="major_character_died",
                    summary="King Aldric is dead.",
                    importance=1,
                ),
                EventCandidate(
                    category=EventCategory.WORLD,
                    subtype="east_gate_breached",
                    summary="The east gate gave way.",
                    importance=5,
                ),
            ),
        )
    )

    result = await _resolve(store, session_id, _tick(), key="policy:1")

    kept = {event.subtype: event.importance for event in result.events}
    assert "door_opened" not in kept
    assert set(kept) == {"secret_discovered", "major_character_died", "east_gate_breached"}
    assert kept["major_character_died"] == 5
    assert kept["east_gate_breached"] == 3
    # The record counts what history kept, not what was offered.
    assert result.resolution.event_count == 3


async def test_a_resolution_that_produced_no_history_still_leaves_a_record(
    db_session: AsyncSession, make_world
) -> None:
    """The point of separating the two trails. A situation pass that nudged an intensity
    produced no history worth reading, and the mechanical question -- why did this
    number change? -- is still answerable."""
    session_id, situation_id, store = await _siege_under_way(db_session, make_world)

    result = await _resolve(
        store,
        session_id,
        ProgressSituationCommand(situation_id=situation_id),
        key="progress:1",
        source_type=ResolutionSourceType.SITUATION_PROGRESSION,
    )

    assert result.resolution.disposition is ResolutionDisposition.APPLIED
    assert result.resolution.event_count == 0
    assert await _count(db_session, models.GameEvent, session_id) == 0
    assert await _count(db_session, models.Resolution, session_id) == 1


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_no_application_code_updates_or_deletes_a_persisted_event() -> None:
    """History is append-only, and this is the check that keeps it that way.

    A correction is a new event. The moment one code path edits a stored row, "what has
    happened in this story?" stops being answerable, because the answer becomes
    whatever the last writer decided it should have been.
    """
    forbidden = (
        "update(models.GameEvent",
        "delete(models.GameEvent",
        "update(GameEvent",
        "delete(GameEvent",
    )
    offenders = [
        f"{path.relative_to(APP_ROOT)}: {pattern}"
        for path in APP_ROOT.rglob("*.py")
        for pattern in forbidden
        if pattern in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"history must be append-only, found: {offenders}"


def test_the_writing_port_can_only_append() -> None:
    surface = {name for name in dir(EventWriterPort) if not name.startswith("_")}
    assert "add_event" in surface
    assert not {name for name in surface if any(verb in name for verb in ("update", "delete"))}


async def test_a_correction_is_a_second_event_pointing_at_the_first(
    db_session: AsyncSession, make_world
) -> None:
    session_id, _, store = await _siege_under_way(db_session, make_world, at=0)

    written = await record_events(
        store,
        session_id=session_id,
        turn_index=0,
        occurred_at=100,
        candidates=[
            EventCandidate(
                category=EventCategory.WORLD,
                subtype="bridge_collapsed",
                summary="The bridge came down.",
            )
        ],
    )
    await db_session.commit()
    original = await store.get_event(session_id, written[0])
    assert original is not None

    await record_events(
        store,
        session_id=session_id,
        turn_index=0,
        occurred_at=4000,
        candidates=[
            EventCandidate(
                category=EventCategory.WORLD,
                subtype="bridge_repaired",
                summary="The bridge is passable again.",
                caused_by_event_id=written[0],
            )
        ],
    )
    await db_session.commit()

    # The first row is exactly what it was. The valley was cut off for a while, and it
    # stays cut off in the history however the story turned out.
    assert await store.get_event(session_id, written[0]) == original
    assert await _count(db_session, models.GameEvent, session_id) == 2
    repair = (await store.load_events(session_id, limit=1))[0]
    assert repair.caused_by_event_id == written[0]


# ---------------------------------------------------------------------------
# Scheduled events
# ---------------------------------------------------------------------------


async def test_a_scheduled_event_is_processed_once_however_often_the_clock_moves(
    db_session: AsyncSession, make_world
) -> None:
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    scheduled_id = await schedule_event(
        store, session_id=session_id, event_type="shop_closes", delay_minutes=30
    )

    first = await advance_time(
        store,
        session_id=session_id,
        request=TimeAdvanceRequest(requested_minutes=60, reason=TimeAdvanceReason.DEBUG),
    )
    second = await advance_time(
        store,
        session_id=session_id,
        request=TimeAdvanceRequest(requested_minutes=60, reason=TimeAdvanceReason.DEBUG),
    )

    assert first.processed_event_ids == [scheduled_id]
    assert second.processed_event_ids == []
    record = await store.get_scheduled_event(scheduled_id)
    assert record is not None and record.status is ScheduledEventStatus.PROCESSED


async def test_processing_one_scheduled_event_twice_resolves_it_once(
    db_session: AsyncSession, make_world
) -> None:
    """What a dispatcher will do when there is one: derive the key from the scheduled
    event's own id, so the same due item arriving twice cannot progress a fire twice."""
    session_id, situation_id, store = await _siege_under_way(db_session, make_world)
    scheduled_id = await schedule_event(
        store, session_id=session_id, event_type="situation.progress", delay_minutes=0
    )
    key = f"scheduled:{scheduled_id}"
    command = ProgressSituationCommand(situation_id=situation_id)

    first = await _resolve(
        store,
        session_id,
        command,
        key=key,
        source_type=ResolutionSourceType.SCHEDULED_EVENT,
        source_id=scheduled_id,
    )
    after_first = await store.get_situation(session_id, situation_id)
    second = await _resolve(
        store,
        session_id,
        command,
        key=key,
        source_type=ResolutionSourceType.SCHEDULED_EVENT,
        source_id=scheduled_id,
    )

    assert second.replayed is True
    assert second.resolution.id == first.resolution.id
    assert await _count(db_session, models.Resolution, session_id) == 1
    unchanged = await store.get_situation(session_id, situation_id)
    assert after_first is not None and unchanged is not None
    assert unchanged.intensity == after_first.intensity
    assert await _revision(store, session_id) == 1


# ---------------------------------------------------------------------------
# What reaches the Story Director
# ---------------------------------------------------------------------------


async def _write_event(
    store: EventWriterPort,
    session_id: uuid.UUID,
    *,
    subtype: str,
    summary: str,
    at: int,
    importance: int | None = None,
) -> None:
    await record_events(
        store,
        session_id=session_id,
        turn_index=0,
        occurred_at=at,
        candidates=[
            EventCandidate(
                category=EventCategory.WORLD,
                subtype=subtype,
                summary=summary,
                importance=importance,
            )
        ],
    )


async def test_the_director_gets_landmarks_and_what_just_happened(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """History reaches the prompt in two bands, both bounded.

    A siege that began forty turns ago still shapes every scene; what happened in the
    last hour is what a character would actually mention. Neither band is the whole
    history, and nothing here grows with how long the save has been played.
    """
    world_id, session_id = await _world_and_session(db_session, make_world)
    db_session.add(make_character(world_id))
    store = SqlAlchemyTurnGateway(db_session)

    await _write_event(
        store,
        session_id,
        subtype="major_character_died",
        summary="King Aldric is dead.",
        at=10,
    )
    await _write_event(
        store,
        session_id,
        subtype="world_state_seeded",
        summary="This save began with established truth.",
        at=0,
    )
    for n in range(RECENT_EVENT_LIMIT + 4):
        await _write_event(
            store,
            session_id,
            subtype="east_gate_breached",
            summary=f"Skirmish {n}.",
            at=100 + n,
            importance=3,
        )
    await store.set_elapsed_minutes(session_id, 500)
    await db_session.commit()

    session = await store.get_session(session_id)
    world = await store.get_world(world_id)
    assert session is not None and world is not None
    context = await build_story_context(
        store, session=session, world=world, player_action="I look around."
    )

    summaries = {entry.summary for entry in context.history.landmarks + context.history.recent}
    # Prioritised: the landmark is there even though sixteen things happened since.
    assert "King Aldric is dead." in {entry.summary for entry in context.history.landmarks}
    # Bounded, and the oldest skirmishes fall out rather than the newest.
    assert len(context.history.landmarks) <= LANDMARK_EVENT_LIMIT
    assert len(context.history.recent) <= RECENT_EVENT_LIMIT
    assert "Skirmish 0." not in summaries
    assert f"Skirmish {RECENT_EVENT_LIMIT + 3}." in summaries
    # Importance 1, and deliberately below every retrieval that matters.
    assert "This save began with established truth." not in summaries


async def test_the_mechanical_trail_is_not_dumped_into_the_prompt(
    db_session: AsyncSession, make_world, make_character
) -> None:
    """`resolutions` is an audit table, not world history.

    A resolution that nudged an intensity is not something a narrator should be told
    about, and idempotency keys and resolver versions are engine bookkeeping. The
    Story Director sees what happened, not how the engine decided it.
    """
    world_id, session_id = await _world_and_session(db_session, make_world)
    db_session.add(make_character(world_id))
    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store, session_id=session_id, mutation=_siege(), started_at=0
    )
    await store.set_elapsed_minutes(session_id, 360)
    await db_session.commit()

    await _resolve(
        store,
        session_id,
        ProgressSituationCommand(situation_id=situation_id),
        key="scheduled:9f2c1a",
        source_type=ResolutionSourceType.SITUATION_PROGRESSION,
    )

    session = await store.get_session(session_id)
    world = await store.get_world(world_id)
    assert session is not None and world is not None
    context = await build_story_context(
        store, session=session, world=world, player_action="I look around."
    )

    assert not hasattr(context, "resolutions")
    assert context.history.landmarks == []
    assert context.history.recent == []
    rendered = render_context(context)
    assert "scheduled:9f2c1a" not in rendered
    assert "situation_progression" not in rendered


async def test_a_fact_established_by_a_resolution_points_back_at_it(
    db_session: AsyncSession, make_world, scripted
) -> None:
    """Why is this true? A fact names the resolution that established it, and the event
    history kept when history kept one."""
    _, session_id = await _world_and_session(db_session, make_world)
    store = SqlAlchemyTurnGateway(db_session)
    subject = FactSubject(type=FactSubjectType.WORLD, id=None)
    assert subject == WORLD_SUBJECT
    scripted(
        ResolutionOutcome(
            disposition=ResolutionDisposition.APPLIED,
            events=(
                EventCandidate(
                    category=EventCategory.WORLD,
                    subtype="secret_discovered",
                    summary="The tunnel under the wall is real.",
                ),
            ),
            state_mutations=(SetFact(subject=WORLD_SUBJECT, property="world.tunnel", value=True),),
        )
    )

    result = await _resolve(store, session_id, _tick(), key="cause:1")

    fact = await store.get_fact(session_id, WORLD_SUBJECT, "world.tunnel")
    assert fact is not None
    assert fact.source_event_id == result.events[0].id
    assert result.events[0].resolution_id == result.resolution.id
