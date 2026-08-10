"""Narration happens after the world has already decided.

The order is the whole point: resolve, commit, *then* describe. So a provider that is
down is a missing paragraph and not a missing outcome -- the siege still advanced, the
fact is still established, the revision still moved. Retrying only re-narrates.

The inverse -- narration deciding mechanics -- is what these tests exist to make
impossible to reintroduce quietly. A refusal must read as a refusal in prose, and no
amount of re-narration may turn it into something that happened.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import NarrationRequest
from app.application.narration_service import narrate_resolution, narration_for
from app.application.resolution_service import ResolutionRequest, resolve
from app.application.situation_service import start_situation
from app.application.story_context import OutcomeContext
from app.domain.enums import MessageRole
from app.domain.errors import NotFoundError, StoryGenerationError
from app.domain.resolution import (
    AdvanceTimeCommand,
    ProgressSituationCommand,
    ResolutionDisposition,
    ResolutionSourceType,
)
from app.domain.world_rules import default_world_rules
from app.domain.world_rules.enums import TimeProgression
from app.domain.world_situations import SituationCategory, SituationScope, StartSituation
from app.domain.world_time import TimeAdvanceReason
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway
from app.infrastructure.story.mock import MockStoryGenerator

from .conftest import FailingStoryGenerator, override_story_generator

PROVIDER_DOWN = StoryGenerationError(
    "Cannot reach the story provider.", provider="failing", retryable=True
)


class _CountingNarrator(MockStoryGenerator):
    """The mock, plus a tally. Test double -- it narrates exactly as the mock does."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def narrate_outcome(self, context: OutcomeContext):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().narrate_outcome(context)


async def _session_with_a_committed_outcome(
    db_session: AsyncSession, make_world
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, SqlAlchemyTurnGateway]:
    """A siege that has genuinely advanced, and the resolution that advanced it."""
    world = make_world(rules_json=default_world_rules().model_dump(mode="json"))
    db_session.add(world)
    await db_session.flush()
    session = models.GameSession(
        world_id=world.id, title="S", player_name="Rin", current_location="the wall"
    )
    db_session.add(session)
    await db_session.commit()

    store = SqlAlchemyTurnGateway(db_session)
    situation_id = await start_situation(
        store,
        session_id=session.id,
        mutation=StartSituation(
            category=SituationCategory.CONFLICT,
            subtype="siege",
            title="Siege of Asterfall",
            intensity=40,
            threat=70,
            momentum=60,
            importance=4,
            scope=SituationScope.REGIONAL,
        ),
        started_at=0,
    )
    await store.set_elapsed_minutes(session.id, 360)
    await db_session.commit()

    result = await resolve(
        store,
        request=ResolutionRequest(
            session_id=session.id,
            command=ProgressSituationCommand(situation_id=situation_id),
            idempotency_key="progress:1",
            source_type=ResolutionSourceType.SITUATION_PROGRESSION,
            source_id=situation_id,
        ),
    )
    assert result.resolution.disposition is ResolutionDisposition.APPLIED
    return session.id, situation_id, result.resolution.id, store


async def _count(db_session: AsyncSession, model: type, session_id: uuid.UUID) -> int:
    rows = await db_session.execute(
        select(func.count()).select_from(model).where(model.session_id == session_id)
    )
    return int(rows.scalar_one())


# ---------------------------------------------------------------------------
# A failed narration is a missing paragraph, not a missing outcome
# ---------------------------------------------------------------------------


async def test_a_provider_failure_leaves_the_committed_outcome_exactly_as_it_was(
    db_session: AsyncSession, make_world
) -> None:
    session_id, situation_id, resolution_id, store = await _session_with_a_committed_outcome(
        db_session, make_world
    )
    advanced = await store.get_situation(session_id, situation_id)
    assert advanced is not None
    resolutions = await _count(db_session, models.Resolution, session_id)
    events = await _count(db_session, models.GameEvent, session_id)
    session = await store.get_session(session_id)
    assert session is not None
    revision = session.state_revision

    with pytest.raises(StoryGenerationError):
        await narrate_resolution(
            store,
            FailingStoryGenerator(PROVIDER_DOWN),
            session_id=session_id,
            resolution_id=resolution_id,
        )
    await db_session.rollback()

    # The world is where the resolution left it.
    still_there = await store.get_situation(session_id, situation_id)
    assert still_there is not None and still_there.intensity == advanced.intensity
    assert await _count(db_session, models.Resolution, session_id) == resolutions
    assert await _count(db_session, models.GameEvent, session_id) == events
    after = await store.get_session(session_id)
    assert after is not None and after.state_revision == revision
    # And what is missing is exactly the prose.
    assert await narration_for(store, session_id=session_id, resolution_id=resolution_id) is None


async def test_retrying_narration_writes_prose_and_nothing_else(
    db_session: AsyncSession, make_world
) -> None:
    """The retry is a second attempt at describing, never a second attempt at deciding.

    This is the line the spec draws between regenerating narration -- allowed, cheap,
    idempotent in the only sense that matters -- and rerunning mechanical resolution,
    which must never happen because something upstream failed.
    """
    session_id, _, resolution_id, store = await _session_with_a_committed_outcome(
        db_session, make_world
    )
    with pytest.raises(StoryGenerationError):
        await narrate_resolution(
            store,
            FailingStoryGenerator(PROVIDER_DOWN),
            session_id=session_id,
            resolution_id=resolution_id,
        )
    await db_session.rollback()
    before = await store.get_session(session_id)
    assert before is not None

    result = await narrate_resolution(
        store, MockStoryGenerator(), session_id=session_id, resolution_id=resolution_id
    )

    assert result.generated is True
    assert result.narration
    assert result.resolution_id == resolution_id
    # One resolution, still. The retry described the outcome; it did not reach one.
    assert await _count(db_session, models.Resolution, session_id) == 1
    after = await store.get_session(session_id)
    assert after is not None
    assert after.state_revision == before.state_revision
    assert after.turn_index == before.turn_index
    assert after.elapsed_minutes == before.elapsed_minutes


async def test_narrating_twice_returns_the_stored_prose_without_calling_the_provider(
    db_session: AsyncSession, make_world
) -> None:
    """Reading is not generating. A client polling for narration must not be able to
    bill a model run per poll, or write a message per poll."""
    session_id, _, resolution_id, store = await _session_with_a_committed_outcome(
        db_session, make_world
    )
    narrator = _CountingNarrator()

    first = await narrate_resolution(
        store, narrator, session_id=session_id, resolution_id=resolution_id
    )
    second = await narrate_resolution(
        store, narrator, session_id=session_id, resolution_id=resolution_id
    )

    assert narrator.calls == 1
    assert second.generated is False
    assert second.narration == first.narration
    assert second.message_id == first.message_id
    assert await _count(db_session, models.Message, session_id) == 1


async def test_regenerating_replaces_the_paragraph_rather_than_appending_one(
    db_session: AsyncSession, make_world
) -> None:
    """A rewrite of the same outcome, not a second thing that happened. Appending would
    put two descriptions of one moment in the transcript, and the next turn would read
    both as canon."""
    session_id, _, resolution_id, store = await _session_with_a_committed_outcome(
        db_session, make_world
    )
    narrator = _CountingNarrator()
    first = await narrate_resolution(
        store, narrator, session_id=session_id, resolution_id=resolution_id
    )

    again = await narrate_resolution(
        store, narrator, session_id=session_id, resolution_id=resolution_id, regenerate=True
    )

    assert narrator.calls == 2
    assert again.generated is True
    assert again.message_id == first.message_id
    assert await _count(db_session, models.Message, session_id) == 1
    assert await _count(db_session, models.Resolution, session_id) == 1


async def test_narration_of_something_that_was_never_resolved_is_a_missing_thing(
    db_session: AsyncSession, make_world
) -> None:
    session_id, _, _, store = await _session_with_a_committed_outcome(db_session, make_world)

    with pytest.raises(NotFoundError):
        await narrate_resolution(
            store, MockStoryGenerator(), session_id=session_id, resolution_id=uuid.uuid4()
        )


# ---------------------------------------------------------------------------
# The narrator describes the verdict; it does not reach one
# ---------------------------------------------------------------------------


async def test_a_refusal_is_narrated_as_a_refusal(db_session: AsyncSession, make_world) -> None:
    """A rejected resolution still gets prose -- the player is owed an explanation of
    why nothing happened -- and the prose describes the refusal it was given."""
    rules = default_world_rules().model_dump(mode="json")
    simulation = rules["simulation"]
    assert isinstance(simulation, dict)
    simulation["time_progression"] = TimeProgression.PAUSED.value
    world = make_world(rules_json=rules)
    db_session.add(world)
    await db_session.flush()
    session = models.GameSession(world_id=world.id, title="S", player_name="Rin")
    db_session.add(session)
    await db_session.commit()
    store = SqlAlchemyTurnGateway(db_session)

    refused = await resolve(
        store,
        request=ResolutionRequest(
            session_id=session.id,
            command=AdvanceTimeCommand(minutes=120, reason=TimeAdvanceReason.SIMULATION),
            idempotency_key="tick:1",
            source_type=ResolutionSourceType.SYSTEM,
        ),
    )
    assert refused.resolution.disposition is ResolutionDisposition.REJECTED

    narrated = await narrate_resolution(
        store, MockStoryGenerator(), session_id=session.id, resolution_id=refused.resolution.id
    )

    assert narrated.narration
    # Still rejected after being described. Narration reads the record; it cannot edit it.
    stored = await store.get_resolution(session.id, refused.resolution.id)
    assert stored is not None
    assert stored.disposition is ResolutionDisposition.REJECTED
    assert stored.state_revision_after == stored.state_revision_before
    session_after = await store.get_session(session.id)
    assert session_after is not None and session_after.elapsed_minutes == 0


async def test_the_prose_is_attached_to_the_resolution_it_describes(
    db_session: AsyncSession, make_world
) -> None:
    """`messages.resolution_id`, not a copy of the text in `resolutions`.

    The record says what happened mechanically and the message says how it read. One
    table owns each, so a rewrite of the prose cannot drift from the audit trail.
    """
    session_id, _, resolution_id, store = await _session_with_a_committed_outcome(
        db_session, make_world
    )

    result = await narrate_resolution(
        store, MockStoryGenerator(), session_id=session_id, resolution_id=resolution_id
    )

    row = await db_session.get(models.Message, result.message_id)
    assert row is not None
    assert row.resolution_id == resolution_id
    assert row.role == MessageRole.NARRATOR.value
    assert row.content == result.narration


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------


async def test_the_endpoint_reports_a_provider_failure_and_then_succeeds_on_retry(
    app_client: AsyncClient, db_session: AsyncSession, make_world
) -> None:
    """A failure the client can act on: 502, named provider, retryable -- and the retry
    against a working provider returns the prose without anything having re-resolved."""
    session_id, _, resolution_id, _store = await _session_with_a_committed_outcome(
        db_session, make_world
    )
    override_story_generator(app_client, FailingStoryGenerator(PROVIDER_DOWN))  # type: ignore[arg-type]

    failed = await app_client.post(
        f"/api/v1/sessions/{session_id}/resolutions/{resolution_id}/narration", json={}
    )

    assert failed.status_code == 502
    body = failed.json()
    assert body["error"] == "story_generation_failed"
    assert body["provider"] == "failing"
    assert body["retryable"] is True

    override_story_generator(app_client, MockStoryGenerator())
    retried = await app_client.post(
        f"/api/v1/sessions/{session_id}/resolutions/{resolution_id}/narration", json={}
    )

    assert retried.status_code == 200
    assert retried.json()["narration"]
    assert retried.json()["resolution_id"] == str(resolution_id)
    # The mechanical trail is untouched by either call.
    listed = await app_client.get(f"/api/v1/sessions/{session_id}/resolutions")
    assert len(listed.json()["resolutions"]) == 1


async def test_the_endpoint_has_no_way_to_ask_for_a_second_resolution(
    app_client: AsyncClient, db_session: AsyncSession, make_world
) -> None:
    """`regenerate` re-narrates. There is deliberately no flag beside it that re-resolves:
    re-running mechanics because prose disappointed someone would let a player reroll an
    outcome by pressing a button labelled "try again"."""
    session_id, _, resolution_id, _store = await _session_with_a_committed_outcome(
        db_session, make_world
    )
    path = f"/api/v1/sessions/{session_id}/resolutions/{resolution_id}/narration"

    # The request body is one boolean, and it is about prose.
    assert set(NarrationRequest.model_fields) == {"regenerate"}

    await app_client.post(path, json={"regenerate": True})
    await app_client.post(path, json={"regenerate": True})

    listed = await app_client.get(f"/api/v1/sessions/{session_id}/resolutions")
    assert len(listed.json()["resolutions"]) == 1
    messages = await app_client.get(f"/api/v1/sessions/{session_id}/messages")
    assert len(messages.json()) == 1
