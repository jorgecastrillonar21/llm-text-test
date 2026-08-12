"""Simulation time through the HTTP surface, the database and the prompt.

`test_world_time.py` covers the rules in isolation. This file checks the parts that
only fail when something real is wired up: that the clock survives a reload, that a
turn does not move it, that events land in a stable order, and that the Story
Director is told what time it is.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.context_builder import build_story_context
from app.config import ImageProvider, Settings, StoryProvider
from app.domain.resolution import EventCategory
from app.infrastructure.db import models
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway
from app.infrastructure.story.rendering import render_context

RIVERWOOD_START = {"year": 842, "month": 5, "day": 13, "hour": 13, "minute": 0}
DAY = 24 * 60


async def bootstrap(client: AsyncClient, **world_fields: object) -> tuple[dict, dict]:
    world = (
        await client.post(
            "/api/v1/worlds",
            json={"name": "W", "genre": "fantasy", "language": "en", **world_fields},
        )
    ).json()
    await client.post(
        f"/api/v1/worlds/{world['id']}/characters",
        json={"name": "Elena", "personality": "sarcastic, cautious"},
    )
    session = (
        await client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()
    return world, session


async def advance(client: AsyncClient, session_id: str, **body: object) -> dict:
    response = await client.post(
        f"/api/v1/dev/sessions/{session_id}/advance-time",
        json={"reason": "debug", **body},
    )
    assert response.status_code == 200, response.text
    result: dict = response.json()
    return result


# ---------------------------------------------------------------------------
# A world's starting date
# ---------------------------------------------------------------------------


async def test_a_world_without_a_start_date_gets_the_first_morning_of_year_one(
    app_client: AsyncClient,
) -> None:
    world, _ = await bootstrap(app_client)

    assert world["initial_datetime"] == {"year": 1, "month": 1, "day": 1, "hour": 8, "minute": 0}


async def test_a_world_keeps_the_start_date_it_was_created_with(app_client: AsyncClient) -> None:
    world, _ = await bootstrap(app_client, initial_datetime=RIVERWOOD_START)

    reloaded = (await app_client.get(f"/api/v1/worlds/{world['id']}")).json()
    assert reloaded["initial_datetime"] == RIVERWOOD_START


async def test_a_start_date_the_calendar_does_not_have_is_rejected(
    app_client: AsyncClient,
) -> None:
    response = await app_client.post(
        "/api/v1/worlds",
        json={
            "name": "Impossible",
            "language": "en",
            "initial_datetime": {"year": 1, "month": 2, "day": 30, "hour": 0, "minute": 0},
        },
    )

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# The session clock
# ---------------------------------------------------------------------------


async def test_a_new_session_starts_at_minute_zero_of_its_worlds_start_date(
    app_client: AsyncClient,
) -> None:
    _, session = await bootstrap(app_client, initial_datetime=RIVERWOOD_START)

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()

    assert detail["elapsed_minutes"] == 0
    assert detail["time"]["elapsed_minutes"] == 0
    assert detail["time"]["display"] == {
        "date": "13 May, 842",
        "time": "13:00",
        "period": "afternoon",
        "elapsed": "0 minutes",
    }


async def test_time_survives_a_reload(app_client: AsyncClient) -> None:
    """The save-game guarantee, for the clock: written once, read back the same."""
    _, session = await bootstrap(app_client, initial_datetime=RIVERWOOD_START)

    await advance(app_client, session["id"], requested_minutes=20 * DAY + 222)

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["elapsed_minutes"] == 29022
    assert detail["time"]["display"]["date"] == "2 June, 842"
    assert detail["time"]["display"]["time"] == "16:42"
    assert detail["time"]["display"]["elapsed"] == "20 days, 3 hours"


async def test_advancing_reports_what_it_did(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)

    result = await advance(app_client, session["id"], requested_minutes=480)

    assert result == {
        "requested_minutes": 480,
        "advanced_minutes": 480,
        "started_at": 0,
        "ended_at": 480,
        "interrupted": False,
        "interruption": None,
        "due_event_ids": [],
    }


async def test_the_clock_cannot_be_asked_to_run_backward_over_http(
    app_client: AsyncClient,
) -> None:
    _, session = await bootstrap(app_client)
    await advance(app_client, session["id"], requested_minutes=600)

    response = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/advance-time",
        json={"reason": "debug", "requested_minutes": -60},
    )

    assert response.status_code == 422
    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["elapsed_minutes"] == 600


# ---------------------------------------------------------------------------
# Turns and time are independent
# ---------------------------------------------------------------------------


async def test_turns_do_not_move_the_clock(app_client: AsyncClient) -> None:
    """Four exchanges, still the same fictional minute.

    This is the property the whole design rests on: nothing infers time from how
    many messages exist.
    """
    _, session = await bootstrap(app_client)

    for _ in range(4):
        response = await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"action": "I ask Elena what she meant."},
        )
        assert response.status_code == 200, response.text

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["turn_index"] == 4
    assert detail["elapsed_minutes"] == 0


async def test_one_action_can_cost_more_than_every_turn_before_it(
    app_client: AsyncClient,
) -> None:
    _, session = await bootstrap(app_client)
    await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns", json={"action": "I set out for the capital."}
    )

    await advance(app_client, session["id"], requested_minutes=6 * 30 * DAY)

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["turn_index"] == 1
    assert detail["elapsed_minutes"] == 259200


# ---------------------------------------------------------------------------
# Event timestamps and ordering
# ---------------------------------------------------------------------------


async def _events(db: AsyncSession, session_id: uuid.UUID) -> list[models.GameEvent]:
    rows = await db.execute(
        select(models.GameEvent)
        .where(models.GameEvent.session_id == session_id)
        .order_by(models.GameEvent.occurred_at, models.GameEvent.event_sequence)
    )
    return list(rows.scalars())


async def test_events_are_stamped_with_the_fictional_time_they_happened_at(
    db_session: AsyncSession, make_world
) -> None:
    from app.application.persistence import NewEvent

    world = make_world()
    db_session.add(world)
    await db_session.flush()
    session = models.GameSession(
        world_id=world.id, title="Run", player_name="Rin", elapsed_minutes=28980
    )
    db_session.add(session)
    await db_session.flush()

    gateway = SqlAlchemyTurnGateway(db_session)
    await gateway.add_event(
        NewEvent(
            session_id=session.id,
            turn_index=412,
            occurred_at=28980,
            category=EventCategory.ACTION,
            subtype="arrival",
            summary="Rin reached the gate.",
            importance=2,
        )
    )
    await gateway.commit()

    (stored,) = await _events(db_session, session.id)
    assert stored.occurred_at == 28980
    assert stored.turn_index == 412


async def test_events_in_the_same_minute_keep_a_deterministic_order(
    db_session: AsyncSession, make_world
) -> None:
    """Ties are the normal case, so the tiebreak has to be real.

    No fake seconds: the order comes from a per-session counter that only ever
    increases.
    """
    from app.application.persistence import NewEvent

    world = make_world()
    db_session.add(world)
    await db_session.flush()
    session = models.GameSession(
        world_id=world.id, title="Run", player_name="Rin", elapsed_minutes=28980
    )
    db_session.add(session)
    await db_session.flush()

    gateway = SqlAlchemyTurnGateway(db_session)
    for name in ("first", "second", "third"):
        await gateway.add_event(
            NewEvent(
                session_id=session.id,
                turn_index=412,
                occurred_at=28980,
                category=EventCategory.ACTION,
                subtype=name,
                summary=name,
                importance=2,
            )
        )
    await gateway.commit()

    stored = await _events(db_session, session.id)
    assert [event.subtype for event in stored] == ["first", "second", "third"]
    assert [event.event_sequence for event in stored] == [1, 2, 3]
    assert {event.occurred_at for event in stored} == {28980}


async def test_the_sequence_keeps_climbing_across_fictional_minutes(
    db_session: AsyncSession, make_world
) -> None:
    """The counter is per session, not per minute. A later minute never restarts it,
    so `(occurred_at, sequence)` is a total order over a session's whole history."""
    from app.application.persistence import NewEvent

    world = make_world()
    db_session.add(world)
    await db_session.flush()
    session = models.GameSession(world_id=world.id, title="Run", player_name="Rin")
    db_session.add(session)
    await db_session.flush()

    gateway = SqlAlchemyTurnGateway(db_session)
    for minute, name in ((60, "first"), (60, "second"), (120, "third")):
        await gateway.add_event(
            NewEvent(
                session_id=session.id,
                turn_index=0,
                occurred_at=minute,
                category=EventCategory.ACTION,
                subtype=name,
                summary=name,
                importance=2,
            )
        )
    await gateway.commit()

    stored = await _events(db_session, session.id)
    assert [event.event_sequence for event in stored] == [1, 2, 3]
    assert [event.occurred_at for event in stored] == [60, 60, 120]


async def test_advancing_the_clock_writes_no_history(app_client: AsyncClient) -> None:
    """The clock moving is bookkeeping. `time_advanced` is registered
    `EventPersistence.NONE`, and the audit trail for "why did the clock jump?" is the
    `ResolutionRecord` of whatever asked -- see docs/event-resolution.md."""
    _, session = await bootstrap(app_client)

    await advance(app_client, session["id"], requested_minutes=60)
    await advance(app_client, session["id"], requested_minutes=60)

    transport = app_client._transport
    factory = transport.app.state.session_factory  # type: ignore[union-attr]
    async with factory() as db:
        stored = await _events(db, uuid.UUID(session["id"]))
    assert stored == []

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["elapsed_minutes"] == 120


# ---------------------------------------------------------------------------
# Scheduled events
# ---------------------------------------------------------------------------


async def test_a_delay_is_stored_as_an_absolute_due_time(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    await advance(app_client, session["id"], requested_minutes=1000)

    response = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/scheduled-events",
        json={"type": "rent_due", "delay_minutes": 3 * DAY},
    )

    assert response.status_code == 201, response.text
    assert response.json()["due_at"] == 1000 + 4320
    assert response.json()["status"] == "pending"


async def test_an_interrupting_event_cuts_an_advance_short_end_to_end(
    app_client: AsyncClient,
) -> None:
    _, session = await bootstrap(app_client)
    scheduled = (
        await app_client.post(
            f"/api/v1/dev/sessions/{session['id']}/scheduled-events",
            json={
                "type": "fire_in_the_stables",
                "delay_minutes": 192,
                "interrupt_player_action": True,
            },
        )
    ).json()

    result = await advance(app_client, session["id"], requested_minutes=480)

    assert result["advanced_minutes"] == 192
    assert result["interrupted"] is True
    assert result["interruption"]["event_id"] == scheduled["id"]
    assert result["due_event_ids"] == [scheduled["id"]]

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["elapsed_minutes"] == 192


async def test_work_the_clock_reached_is_readable_and_not_processed(
    app_client: AsyncClient,
) -> None:
    """End to end, the correction: advancing marks work due and leaves it findable.

    Nothing in the application can land a caravan, so nothing pretends one landed. The
    row stays on the backlog endpoint until an owner executes it.
    """
    _, session = await bootstrap(app_client)
    scheduled = (
        await app_client.post(
            f"/api/v1/dev/sessions/{session['id']}/scheduled-events",
            json={"type": "caravan_arrives", "delay_minutes": 100},
        )
    ).json()

    await advance(app_client, session["id"], requested_minutes=480)

    owed = (
        await app_client.get(f"/api/v1/dev/sessions/{session['id']}/scheduled-events/due")
    ).json()
    assert [event["id"] for event in owed] == [scheduled["id"]]
    assert owed[0]["status"] == "due"

    snapshot = (await app_client.get(f"/api/v1/dev/sessions/{session['id']}/world-state")).json()
    assert snapshot["counts"]["due_scheduled_events"] == 1
    assert snapshot["counts"]["pending_scheduled_events"] == 0


async def test_due_work_can_be_called_off_over_http(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    scheduled = (
        await app_client.post(
            f"/api/v1/dev/sessions/{session['id']}/scheduled-events",
            json={"type": "shop_closes", "delay_minutes": 10},
        )
    ).json()
    await advance(app_client, session["id"], requested_minutes=60)

    response = await app_client.delete(f"/api/v1/dev/scheduled-events/{scheduled['id']}")

    assert response.status_code == 204, response.text
    owed = (
        await app_client.get(f"/api/v1/dev/sessions/{session['id']}/scheduled-events/due")
    ).json()
    assert owed == []


async def test_cancelling_an_event_twice_is_refused(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    scheduled = (
        await app_client.post(
            f"/api/v1/dev/sessions/{session['id']}/scheduled-events",
            json={"type": "shop_closes", "delay_minutes": 10},
        )
    ).json()
    await app_client.delete(f"/api/v1/dev/scheduled-events/{scheduled['id']}")

    response = await app_client.delete(f"/api/v1/dev/scheduled-events/{scheduled['id']}")

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# The Story Director's view
# ---------------------------------------------------------------------------


async def test_the_director_is_told_the_date_and_hour_not_a_minute_count(
    db_session: AsyncSession, make_world, make_character
) -> None:
    world = make_world(initial_datetime=RIVERWOOD_START)
    db_session.add(world)
    await db_session.flush()
    db_session.add(make_character(world.id))
    session = models.GameSession(
        world_id=world.id, title="Run", player_name="Rin", elapsed_minutes=29022
    )
    db_session.add(session)
    await db_session.flush()

    gateway = SqlAlchemyTurnGateway(db_session)
    snapshot = await gateway.get_session(session.id)
    world_snapshot = await gateway.get_world(world.id)
    assert snapshot is not None and world_snapshot is not None

    context = await build_story_context(
        gateway, session=snapshot, world=world_snapshot, player_action="I look at the sky."
    )

    assert context.time.calendar_date == "2 June, 842"
    assert context.time.clock == "16:42"
    assert context.time.period == "afternoon"
    assert context.time.elapsed_since_start == "20 days, 3 hours"

    rendered = render_context(context)
    assert "Now: 2 June, 842, 16:42 (afternoon)" in rendered
    # The raw counter is deliberately not in the prompt.
    assert "29022" not in rendered


# ---------------------------------------------------------------------------
# The developer endpoints are not always there
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("app_env", ["production", "staging", "typo"])
def test_dev_endpoints_are_off_outside_development(app_env: str) -> None:
    """An allowlist, so an unrecognised APP_ENV switches them off rather than on."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        story_provider=StoryProvider.MOCK,
        image_provider=ImageProvider.DISABLED,
        app_env=app_env,
    )

    assert settings.dev_endpoints_enabled is False


def test_dev_endpoints_are_on_in_development_and_test() -> None:
    for app_env in ("development", "test", "  TEST  "):
        settings = Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            story_provider=StoryProvider.MOCK,
            image_provider=ImageProvider.DISABLED,
            app_env=app_env,
        )
        assert settings.dev_endpoints_enabled is True


async def test_the_dev_router_is_not_mounted_in_production() -> None:
    from app.main import create_app

    app = create_app(
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            story_provider=StoryProvider.MOCK,
            image_provider=ImageProvider.DISABLED,
            app_env="production",
        )
    )

    paths = set(app.openapi()["paths"])

    assert not any(path.startswith("/api/v1/dev") for path in paths)
    # Guards the guard: the rest of the API is still there.
    assert "/api/v1/worlds" in paths
