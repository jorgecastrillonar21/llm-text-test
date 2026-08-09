"""World state over HTTP: what a client can read, what it cannot write, and seeding.

The read endpoint is the only non-development fact endpoint that exists, on purpose.
Several tests below exist to keep it that way.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.domain.world_facts import WORLD_SUBJECT, FactSubject, FactSubjectType, SetFact


def _seed(character_id: uuid.UUID | None = None) -> list[dict]:
    facts = [
        SetFact(
            subject=WORLD_SUBJECT,
            property="world.political_status",
            value="contested",
            importance=4,
        ).model_dump(mode="json"),
    ]
    if character_id is not None:
        facts.append(
            SetFact(
                subject=FactSubject(type=FactSubjectType.CHARACTER, id=character_id),
                property="narrative.birthplace",
                value="Arven",
                importance=1,
            ).model_dump(mode="json")
        )
    return facts


async def _world_with_character(client: AsyncClient) -> tuple[dict, dict]:
    world = (await client.post("/api/v1/worlds", json={"name": "W", "genre": "fantasy"})).json()
    character = (
        await client.post(f"/api/v1/worlds/{world['id']}/characters", json={"name": "Elena"})
    ).json()
    return world, character


# -- world templates ----------------------------------------------------------


async def test_a_world_can_be_created_with_starting_facts(app_client: AsyncClient) -> None:
    response = await app_client.post(
        "/api/v1/worlds", json={"name": "W", "genre": "fantasy", "initial_facts": _seed()}
    )
    assert response.status_code == 201, response.text

    template = await app_client.get(f"/api/v1/worlds/{response.json()['id']}/initial-facts")
    assert template.status_code == 200
    assert [fact["property"] for fact in template.json()] == ["world.political_status"]


async def test_a_world_with_a_malformed_starting_fact_is_refused_at_creation(
    app_client: AsyncClient,
) -> None:
    """Better a 422 here than a surprise the first time someone starts a session."""
    response = await app_client.post(
        "/api/v1/worlds",
        json={
            "name": "W",
            "initial_facts": [
                {
                    "op": "set_fact",
                    "subject": {"type": "world"},
                    "property": "birthplace",
                    "value": 1,
                }
            ],
        },
    )
    assert response.status_code == 422, response.text


async def test_a_world_created_without_starting_facts_has_none(app_client: AsyncClient) -> None:
    world = (await app_client.post("/api/v1/worlds", json={"name": "W"})).json()
    template = await app_client.get(f"/api/v1/worlds/{world['id']}/initial-facts")
    assert template.json() == []


# -- session creation ---------------------------------------------------------


async def test_a_new_session_materialises_the_worlds_starting_facts(
    app_client: AsyncClient,
) -> None:
    # Created fresh with the template: a world's facts are fixed at creation.
    world = (
        await app_client.post(
            "/api/v1/worlds",
            json={"name": "W2", "initial_facts": _seed()},
        )
    ).json()

    session = (
        await app_client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()
    assert session["state_revision"] == 1, "seeding is a state change like any other"

    state = (await app_client.get(f"/api/v1/sessions/{session['id']}/world-state/facts")).json()
    assert [fact["property"] for fact in state["facts"]] == ["world.political_status"]
    assert state["state_revision"] == 1
    assert state["facts"][0]["authority"] == "seed"
    assert state["truncated"] is False


async def test_two_sessions_of_one_world_get_their_own_copies(app_client: AsyncClient) -> None:
    world = (
        await app_client.post("/api/v1/worlds", json={"name": "W", "initial_facts": _seed()})
    ).json()
    first = (
        await app_client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "A", "player_name": "Rin"},
        )
    ).json()
    second = (
        await app_client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "B", "player_name": "Kai"},
        )
    ).json()

    assert first["id"] != second["id"]
    for session in (first, second):
        state = (await app_client.get(f"/api/v1/sessions/{session['id']}/world-state/facts")).json()
        assert [fact["value"] for fact in state["facts"]] == ["contested"]


async def test_a_session_of_a_world_with_no_template_starts_at_revision_zero(
    app_client: AsyncClient,
) -> None:
    world = (await app_client.post("/api/v1/worlds", json={"name": "W"})).json()
    session = (
        await app_client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()

    assert session["state_revision"] == 0
    state = (await app_client.get(f"/api/v1/sessions/{session['id']}/world-state/facts")).json()
    assert state["facts"] == []


# -- reading ------------------------------------------------------------------


async def test_facts_can_be_filtered_by_subject(app_client: AsyncClient) -> None:
    session, character = await _session_with_character(app_client)
    for mutation in (
        {
            "op": "set_fact",
            "subject": {"type": "world"},
            "property": "world.condition",
            "value": "x",
        },
        {
            "op": "set_fact",
            "subject": {"type": "character", "id": character["id"]},
            "property": "narrative.birthplace",
            "value": "Arven",
        },
    ):
        response = await app_client.post(
            f"/api/v1/dev/sessions/{session['id']}/world-state/changes",
            json={
                "batch": {"authority": "admin", "mutations": [mutation]},
                "event": {"type": "debug", "description": "Set up."},
            },
        )
        assert response.status_code == 200, response.text

    everything = (
        await app_client.get(f"/api/v1/sessions/{session['id']}/world-state/facts")
    ).json()
    assert len(everything["facts"]) == 2

    world_only = (
        await app_client.get(
            f"/api/v1/sessions/{session['id']}/world-state/facts",
            params={"subject_type": "world"},
        )
    ).json()
    assert [fact["property"] for fact in world_only["facts"]] == ["world.condition"]

    character_only = (
        await app_client.get(
            f"/api/v1/sessions/{session['id']}/world-state/facts",
            params={"subject_type": "character", "subject_id": character["id"]},
        )
    ).json()
    assert [fact["property"] for fact in character_only["facts"]] == ["narrative.birthplace"]


async def test_a_template_fact_about_a_character_of_another_world_fails_session_creation(
    app_client: AsyncClient,
) -> None:
    """The materialisation runs the same entity check every mutation does.

    This is also the shape of a real limitation: `POST /worlds` runs before the world
    has any characters, so a template written through the API can only address the
    world itself. Character-scoped templates are reachable from the seed script, which
    creates both in one transaction. See docs/world-state-facts.md.
    """
    _, character = await _world_with_character(app_client)
    other = (
        await app_client.post(
            "/api/v1/worlds",
            json={"name": "Elsewhere", "initial_facts": _seed(uuid.UUID(character["id"]))},
        )
    ).json()

    response = await app_client.post(
        "/api/v1/sessions",
        json={"world_id": other["id"], "title": "Run", "player_name": "Rin"},
    )
    assert response.status_code == 404, response.text


async def test_facts_can_be_filtered_by_importance(app_client: AsyncClient) -> None:
    world = (
        await app_client.post("/api/v1/worlds", json={"name": "W", "initial_facts": _seed()})
    ).json()
    session = (
        await app_client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()

    critical = (
        await app_client.get(
            f"/api/v1/sessions/{session['id']}/world-state/facts",
            params={"min_importance": 5},
        )
    ).json()
    assert critical["facts"] == []


async def test_a_subject_id_without_a_subject_type_is_a_422(app_client: AsyncClient) -> None:
    world = (await app_client.post("/api/v1/worlds", json={"name": "W"})).json()
    session = (
        await app_client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()

    response = await app_client.get(
        f"/api/v1/sessions/{session['id']}/world-state/facts",
        params={"subject_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422, response.text


async def test_asking_for_the_world_by_id_is_a_422_not_an_empty_page(
    app_client: AsyncClient,
) -> None:
    """There is one world per session; an id would imply a choice of which."""
    world = (await app_client.post("/api/v1/worlds", json={"name": "W"})).json()
    session = (
        await app_client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()

    response = await app_client.get(
        f"/api/v1/sessions/{session['id']}/world-state/facts",
        params={"subject_type": "world", "subject_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422, response.text


async def test_reading_the_state_of_a_session_that_does_not_exist_is_a_404(
    app_client: AsyncClient,
) -> None:
    response = await app_client.get(f"/api/v1/sessions/{uuid.uuid4()}/world-state/facts")
    assert response.status_code == 404


# -- no gameplay write path ---------------------------------------------------


def _fact_surface(app_client: AsyncClient) -> dict[str, list[str]]:
    """Every documented path that touches world state, and the verbs it accepts.

    Read from the OpenAPI document rather than by trying URLs, so a route added later
    fails these tests instead of quietly existing.
    """
    transport = app_client._transport
    app = transport.app  # type: ignore[union-attr]
    return {
        path: sorted(operations)
        for path, operations in app.openapi()["paths"].items()
        if "world-state" in path or "initial-facts" in path
    }


async def test_the_only_way_to_write_a_fact_is_the_development_endpoint(
    app_client: AsyncClient,
) -> None:
    """The authority model would be decoration if a client could POST `system.alive`.

    The test settings use `app_env="test"`, which mounts `/dev`. What is asserted here
    is that the write path is *only* ever there -- an environment where dev routes are
    off has no way to change a fact over HTTP at all.
    """
    assert _fact_surface(app_client) == {
        "/api/v1/sessions/{session_id}/world-state/facts": ["get"],
        "/api/v1/worlds/{world_id}/initial-facts": ["get"],
        "/api/v1/dev/sessions/{session_id}/world-state/changes": ["post"],
    }


async def test_dev_routes_are_absent_when_the_environment_does_not_allow_them() -> None:
    """An unrecognised APP_ENV switches developer tooling off, never on."""
    from app.config import ImageProvider, Settings, StoryProvider
    from app.main import create_app

    app = create_app(
        Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            story_provider=StoryProvider.MOCK,
            image_provider=ImageProvider.DISABLED,
            app_env="production",
        )
    )
    assert not any("world-state/changes" in path for path in app.openapi()["paths"])


# -- the debug mutation endpoint ----------------------------------------------


async def _session_with_character(client: AsyncClient) -> tuple[dict, dict]:
    world = (await client.post("/api/v1/worlds", json={"name": "W"})).json()
    character = (
        await client.post(f"/api/v1/worlds/{world['id']}/characters", json={"name": "Elena"})
    ).json()
    session = (
        await client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()
    return session, character


async def test_a_debug_mutation_goes_through_the_state_service(app_client: AsyncClient) -> None:
    session, character = await _session_with_character(app_client)

    response = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/world-state/changes",
        json={
            "batch": {
                "authority": "engine",
                "mutations": [
                    {
                        "op": "set_fact",
                        "subject": {"type": "character", "id": character["id"]},
                        "property": "system.alive",
                        "value": False,
                        "importance": 5,
                    }
                ],
            },
            "event": {"type": "death", "description": "Elena did not make it."},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 1
    assert body["event_id"] is not None

    state = (await app_client.get(f"/api/v1/sessions/{session['id']}/world-state/facts")).json()
    assert state["facts"][0]["value"] is False
    assert state["facts"][0]["source_event_id"] == body["event_id"]


async def test_the_debug_endpoint_still_refuses_what_the_story_director_may_not_write(
    app_client: AsyncClient,
) -> None:
    """Development-only does not mean authority-free."""
    session, character = await _session_with_character(app_client)

    response = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/world-state/changes",
        json={
            "batch": {
                "authority": "story_director",
                "mutations": [
                    {
                        "op": "set_fact",
                        "subject": {"type": "character", "id": character["id"]},
                        "property": "system.alive",
                        "value": False,
                    }
                ],
            }
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_request"


async def test_a_stale_expected_revision_is_a_409(app_client: AsyncClient) -> None:
    session, character = await _session_with_character(app_client)
    body = {
        "batch": {
            "authority": "admin",
            "expected_revision": 0,
            "mutations": [
                {
                    "op": "set_fact",
                    "subject": {"type": "character", "id": character["id"]},
                    "property": "narrative.birthplace",
                    "value": "Arven",
                }
            ],
        },
        "event": {"type": "debug", "description": "Set once."},
    }
    first = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/world-state/changes", json=body
    )
    assert first.status_code == 200, first.text

    # The same batch again, still claiming revision 0.
    second = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/world-state/changes", json=body
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"] == "stale_state"


async def test_a_debug_mutation_naming_an_unknown_character_is_a_404(
    app_client: AsyncClient,
) -> None:
    session, _ = await _session_with_character(app_client)

    response = await app_client.post(
        f"/api/v1/dev/sessions/{session['id']}/world-state/changes",
        json={
            "batch": {
                "authority": "admin",
                "mutations": [
                    {
                        "op": "set_fact",
                        "subject": {"type": "character", "id": str(uuid.uuid4())},
                        "property": "narrative.birthplace",
                        "value": "Arven",
                    }
                ],
            },
            "event": {"type": "debug", "description": "Nobody is there."},
        },
    )
    assert response.status_code == 404, response.text


# -- turns --------------------------------------------------------------------


async def test_a_turn_reports_how_its_fact_proposals_were_judged(
    app_client: AsyncClient,
) -> None:
    session, _ = await _session_with_character(app_client)

    response = await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns", json={"action": "I ask Elena about herself."}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # The mock provider proposes nothing; what matters is that the counters exist and
    # a turn with no proposals is not an error.
    assert body["facts_established"] == 0
    assert body["facts_rejected"] == 0


async def test_a_turn_does_not_move_the_state_revision_on_its_own(
    app_client: AsyncClient,
) -> None:
    """Turns, minutes and state changes are three counters that move for three
    different reasons. A conversation moves only the first."""
    session, _ = await _session_with_character(app_client)
    await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns", json={"action": "I greet Elena."}
    )

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["turn_index"] == 1
    assert detail["state_revision"] == 0
