"""The situations HTTP surface, and the shape of what it deliberately does not offer.

Two kinds of test here. The ordinary kind exercises the read endpoints and the
developer progression tool. The other kind asserts what is *absent*: there is no way
over HTTP to set an intensity, resolve a siege, or reach another session's situations,
and a test that notices one appearing is the point.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.application.contracts import (
    MAX_SITUATION_PROPOSALS,
    SituationProposal,
    TurnGeneration,
)
from app.domain.world_situations import SituationCategory, SituationScope, StartSituation
from app.main import create_app


async def _world(client: AsyncClient, **overrides: object) -> dict:
    payload: dict[str, object] = {"name": "W", "genre": "fantasy", "setting": "a town"}
    payload.update(overrides)
    response = await client.post("/api/v1/worlds", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _session(client: AsyncClient, world_id: str, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "world_id": world_id,
        "title": "Run",
        "player_name": "Rin",
        "current_location": "somewhere",
    }
    payload.update(overrides)
    response = await client.post("/api/v1/sessions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _siege(**overrides: object) -> dict:
    data: dict[str, object] = {
        "category": SituationCategory.CONFLICT,
        "subtype": "siege",
        "title": "Siege of Asterfall",
        "intensity": 50,
        "threat": 70,
        "momentum": 40,
        "importance": 4,
        "scope": SituationScope.REGIONAL,
    }
    data.update(overrides)
    return StartSituation(**data).model_dump(mode="json")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_a_session_with_nothing_under_way_returns_an_empty_list(
    app_client: AsyncClient,
) -> None:
    world = await _world(app_client)
    session = await _session(app_client, world["id"])

    response = await app_client.get(f"/api/v1/sessions/{session['id']}/situations")
    assert response.status_code == 200
    assert response.json() == {"situations": []}


async def test_a_worlds_starting_processes_reach_a_new_session(
    app_client: AsyncClient,
) -> None:
    world = await _world(
        app_client,
        initial_situations=[
            _siege(),
            _siege(title="The grain shortage", category=SituationCategory.ECONOMIC, subtype=None),
        ],
    )
    session = await _session(app_client, world["id"])

    body = (await app_client.get(f"/api/v1/sessions/{session['id']}/situations")).json()
    titles = {entry["situation"]["title"] for entry in body["situations"]}
    assert titles == {"Siege of Asterfall", "The grain shortage"}
    # Session-scoped rows, not the template's.
    assert all(entry["situation"]["session_id"] == session["id"] for entry in body["situations"])


async def test_two_sessions_of_one_world_get_independent_copies(
    app_client: AsyncClient,
) -> None:
    world = await _world(app_client, initial_situations=[_siege()])
    first = await _session(app_client, world["id"])
    second = await _session(app_client, world["id"], title="Other")

    mine = (await app_client.get(f"/api/v1/sessions/{first['id']}/situations")).json()
    theirs = (await app_client.get(f"/api/v1/sessions/{second['id']}/situations")).json()

    assert mine["situations"][0]["situation"]["id"] != theirs["situations"][0]["situation"]["id"]


async def test_one_sessions_situation_is_a_404_from_another(app_client: AsyncClient) -> None:
    world = await _world(app_client, initial_situations=[_siege()])
    first = await _session(app_client, world["id"])
    second = await _session(app_client, world["id"], title="Other")

    mine = (await app_client.get(f"/api/v1/sessions/{first['id']}/situations")).json()
    situation_id = mine["situations"][0]["situation"]["id"]

    assert (
        await app_client.get(f"/api/v1/sessions/{first['id']}/situations/{situation_id}")
    ).status_code == 200
    # Exists, but not here. Indistinguishable from missing, and should be.
    assert (
        await app_client.get(f"/api/v1/sessions/{second['id']}/situations/{situation_id}")
    ).status_code == 404


async def test_an_unknown_situation_is_a_404(app_client: AsyncClient) -> None:
    world = await _world(app_client)
    session = await _session(app_client, world["id"])
    response = await app_client.get(f"/api/v1/sessions/{session['id']}/situations/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_situations_can_be_filtered_by_category_and_scope(
    app_client: AsyncClient,
) -> None:
    world = await _world(
        app_client,
        initial_situations=[
            _siege(),
            _siege(
                title="The harvest festival",
                category=SituationCategory.SOCIAL,
                subtype="festival",
                scope=SituationScope.LOCAL,
                threat=5,
            ),
        ],
    )
    session = await _session(app_client, world["id"])
    base = f"/api/v1/sessions/{session['id']}/situations"

    social = (await app_client.get(base, params={"category": "social"})).json()
    assert [e["situation"]["title"] for e in social["situations"]] == ["The harvest festival"]

    regional = (await app_client.get(base, params={"scope": "regional"})).json()
    assert [e["situation"]["title"] for e in regional["situations"]] == ["Siege of Asterfall"]


async def test_a_festival_and_a_siege_carry_the_same_intensity_and_different_danger(
    app_client: AsyncClient,
) -> None:
    """The reason there is no single `severity` column, asserted over the wire."""
    world = await _world(
        app_client,
        initial_situations=[
            _siege(title="The siege", intensity=80, threat=90),
            _siege(
                title="The festival",
                category=SituationCategory.SOCIAL,
                subtype="festival",
                intensity=80,
                threat=5,
            ),
        ],
    )
    session = await _session(app_client, world["id"])
    body = (await app_client.get(f"/api/v1/sessions/{session['id']}/situations")).json()
    by_title = {e["situation"]["title"]: e["situation"] for e in body["situations"]}

    assert by_title["The siege"]["intensity"] == by_title["The festival"]["intensity"] == 80
    assert by_title["The siege"]["threat"] == 90
    assert by_title["The festival"]["threat"] == 5


async def test_live_only_is_the_default_and_can_be_turned_off(
    app_client: AsyncClient, settings
) -> None:
    world = await _world(app_client, initial_situations=[_siege()])
    session = await _session(app_client, world["id"])
    base = f"/api/v1/sessions/{session['id']}/situations"

    situation_id = (await app_client.get(base)).json()["situations"][0]["situation"]["id"]

    resolved = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/world-state/changes",
        json={
            "batch": {
                "authority": "engine",
                "mutations": [
                    {
                        "op": "resolve_situation",
                        "situation_id": situation_id,
                        "resolution_status": "resolved",
                        "reason": "Relief arrived.",
                    }
                ],
            },
            "event": {"type": "SIEGE_LIFTED", "description": "The siege lifted."},
        },
    )
    assert resolved.status_code == 200, resolved.text

    assert (await app_client.get(base)).json()["situations"] == []
    history = (await app_client.get(base, params={"live_only": "false"})).json()
    assert [e["situation"]["status"] for e in history["situations"]] == ["resolved"]
    assert history["situations"][0]["situation"]["resolved_at"] is not None


async def test_an_explicit_status_filter_overrides_the_live_only_default(
    app_client: AsyncClient,
) -> None:
    world = await _world(
        app_client,
        initial_situations=[
            _siege(),
            _siege(title="The planned summit", category=SituationCategory.EVENT, status="planned"),
        ],
    )
    session = await _session(app_client, world["id"])
    base = f"/api/v1/sessions/{session['id']}/situations"

    planned = (await app_client.get(base, params={"status": "planned"})).json()
    assert [e["situation"]["title"] for e in planned["situations"]] == ["The planned summit"]


# ---------------------------------------------------------------------------
# Progression, through the developer endpoint
# ---------------------------------------------------------------------------


async def test_progression_needs_the_clock_to_have_moved(app_client: AsyncClient) -> None:
    """The interval is not the caller's to choose: it runs to where the session clock
    actually is, so a progression can only account for time the session has lived."""
    world = await _world(app_client, initial_situations=[_siege()])
    session = await _session(app_client, world["id"])
    base = f"/api/v1/sessions/{session['id']}/situations"
    situation_id = (await app_client.get(base)).json()["situations"][0]["situation"]["id"]

    first = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/situations/{situation_id}/progress", json={}
    )
    assert first.status_code == 200
    assert first.json()["changed"] is False

    advanced = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/advance-time",
        json={"requested_minutes": 360, "reason": "debug"},
    )
    assert advanced.status_code == 200

    second = (
        await app_client.post(
            f"/api/v1/dev/sessions/{session['id']}/situations/{situation_id}/progress", json={}
        )
    ).json()
    assert second["changed"] is True
    assert second["intensity_delta"] > 0
    assert second["state_revision"] is not None
    assert second["event_id"] is not None
    # Absolute session time, never a delay.
    assert second["next_progression_at"] is not None
    assert second["scheduled_event_id"] is not None

    after = (await app_client.get(f"{base}/{situation_id}")).json()["situation"]
    assert after["last_progressed_at"] == 360
    assert after["intensity"] > 50


async def test_progressing_twice_without_moving_the_clock_changes_nothing(
    app_client: AsyncClient,
) -> None:
    world = await _world(app_client, initial_situations=[_siege()])
    session = await _session(app_client, world["id"])
    base = f"/api/v1/sessions/{session['id']}/situations"
    situation_id = (await app_client.get(base)).json()["situations"][0]["situation"]["id"]

    await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/advance-time",
        json={"requested_minutes": 120, "reason": "debug"},
    )
    first = (
        await app_client.post(
            f"/api/v1/dev/sessions/{session['id']}/situations/{situation_id}/progress", json={}
        )
    ).json()
    second = (
        await app_client.post(
            f"/api/v1/dev/sessions/{session['id']}/situations/{situation_id}/progress", json={}
        )
    ).json()

    assert first["changed"] is True
    assert second["changed"] is False
    # A pass that decided nothing does not move the revision.
    assert second["state_revision"] is None

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["state_revision"] == first["state_revision"]


async def test_progressing_an_unknown_situation_is_a_404(app_client: AsyncClient) -> None:
    world = await _world(app_client)
    session = await _session(app_client, world["id"])
    response = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/situations/{uuid.uuid4()}/progress", json={}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# What the surface deliberately does not offer
# ---------------------------------------------------------------------------


def test_there_is_no_write_endpoint_for_situations_outside_dev() -> None:
    """Situations move through typed mutations inside an event and a transaction.

    A `PATCH /situations/{id}` would have no event explaining it, would not move the
    state revision and would bypass the transition rules. This asserts the shape of the
    surface rather than any one handler.
    """
    from app.config import ImageProvider, Settings, StoryProvider

    app = create_app(
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            story_provider=StoryProvider.MOCK,
            image_provider=ImageProvider.DISABLED,
            app_env="production",  # dev endpoints off
        )
    )
    paths = app.openapi()["paths"]
    situation_paths = {path: set(methods) for path, methods in paths.items() if "situation" in path}
    assert situation_paths == {
        "/api/v1/sessions/{session_id}/situations": {"get"},
        "/api/v1/sessions/{session_id}/situations/{situation_id}": {"get"},
    }


def test_the_progression_endpoint_only_exists_in_development() -> None:
    from app.config import ImageProvider, Settings, StoryProvider

    def paths_for(app_env: str) -> set[str]:
        app = create_app(
            Settings(
                database_url="sqlite+aiosqlite:///:memory:",
                story_provider=StoryProvider.MOCK,
                image_provider=ImageProvider.DISABLED,
                app_env=app_env,
            )
        )
        return set(app.openapi()["paths"])

    endpoint = "/api/v1/dev/sessions/{session_id}/situations/{situation_id}/progress"
    assert endpoint in paths_for("development")
    assert endpoint not in paths_for("production")


async def test_the_story_director_cannot_move_a_situation_over_http(
    app_client: AsyncClient,
) -> None:
    """Even through the debug mutation endpoint. Being development-only does not lift
    the authority model -- the authority model is what the endpoint exists to test."""
    world = await _world(app_client, initial_situations=[_siege()])
    session = await _session(app_client, world["id"])
    base = f"/api/v1/sessions/{session['id']}/situations"
    situation_id = (await app_client.get(base)).json()["situations"][0]["situation"]["id"]

    response = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/world-state/changes",
        json={
            "batch": {
                "authority": "story_director",
                "mutations": [
                    {
                        "op": "update_situation",
                        "situation_id": situation_id,
                        "intensity_delta": 50,
                    }
                ],
            }
        },
    )
    assert response.status_code == 422
    assert "may not change a situation" in response.text

    unchanged = (await app_client.get(f"{base}/{situation_id}")).json()["situation"]
    assert unchanged["intensity"] == 50


async def test_a_batch_that_moves_a_situation_and_a_place_lands_as_one_revision(
    app_client: AsyncClient,
) -> None:
    world = await _world(app_client, initial_situations=[_siege()])
    location = await app_client.post(
        f"/api/v1/worlds/{world['id']}/locations",
        json={
            "name": "The eastern gate",
            "category": "structure",
            "scale": "site",
        },
    )
    assert location.status_code == 201, location.text
    gate_id = location.json()["id"]

    session = await _session(app_client, world["id"])
    base = f"/api/v1/sessions/{session['id']}/situations"
    situation_id = (await app_client.get(base)).json()["situations"][0]["situation"]["id"]
    before = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()["state_revision"]

    response = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/world-state/changes",
        json={
            "batch": {
                "authority": "simulation",
                "mutations": [
                    {"op": "update_situation", "situation_id": situation_id, "intensity_delta": 12},
                    {
                        "op": "update_location_state",
                        "location_id": gate_id,
                        "condition": "destroyed",
                        "accessibility": "blocked",
                    },
                    {
                        "op": "start_situation",
                        "category": "economic",
                        "subtype": "famine",
                        "title": "Food crisis in Asterfall",
                        "parent_situation_id": situation_id,
                    },
                ],
            },
            "event": {"type": "EAST_GATE_BREACHED", "description": "The eastern gate fell."},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == before + 1
    assert len(body["applied"]) == 3

    started = [entry for entry in body["applied"] if entry["op"] == "start_situation"]
    assert len(started) == 1
    child_id = started[0]["entity_id"]
    assert child_id is not None

    child = (await app_client.get(f"{base}/{child_id}")).json()["situation"]
    assert child["parent_situation_id"] == situation_id

    place = (await app_client.get(f"/api/v1/sessions/{session['id']}/locations/{gate_id}")).json()
    assert place["state"]["condition"] == "destroyed"


# ---------------------------------------------------------------------------
# The AI contract
# ---------------------------------------------------------------------------


def test_a_situation_proposal_cannot_carry_numbers_or_an_id() -> None:
    """The strongest statement of "the model may not alter intensity": there is no
    field for it, and `extra="ignore"` means one sent anyway is dropped."""
    for absent in ("id", "situation_id", "intensity", "threat", "momentum", "importance", "status"):
        assert absent not in SituationProposal.model_fields

    proposal = SituationProposal.model_validate(
        {"category": "hazard", "title": "Fire", "intensity": 100, "id": str(uuid.uuid4())}
    )
    assert not hasattr(proposal, "intensity")


def test_an_unusable_situation_proposal_is_dropped_not_fatal() -> None:
    generation = TurnGeneration.model_validate(
        {
            "narration": "Smoke rises from the kitchens.",
            "suggested_actions": [],
            "situation_proposals": [
                {"category": "riot", "title": "Not a category"},
                {"category": "hazard", "title": "Fire at the Crown"},
            ],
        }
    )
    assert [p.title for p in generation.situation_proposals] == ["Fire at the Crown"]


def test_situation_proposals_are_capped() -> None:
    generation = TurnGeneration.model_validate(
        {
            "narration": "Everything happens at once.",
            "suggested_actions": [],
            "situation_proposals": [
                {"category": "hazard", "title": f"Thing {n}"} for n in range(10)
            ],
        }
    )
    assert len(generation.situation_proposals) == MAX_SITUATION_PROPOSALS


@pytest.mark.parametrize("raw", ["not a list", {"category": "hazard"}, 7])
def test_a_malformed_situation_proposals_field_is_ignored(raw: object) -> None:
    generation = TurnGeneration.model_validate(
        {"narration": "n", "suggested_actions": [], "situation_proposals": raw}
    )
    assert generation.situation_proposals == []
