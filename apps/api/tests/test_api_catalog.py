"""World / character / session creation and retrieval through the HTTP API."""

from __future__ import annotations

import uuid

from httpx import AsyncClient


async def create_world(client: AsyncClient, **overrides: object) -> dict:
    payload = {"name": "The Fractured Crown", "genre": "fantasy", "setting": "a walled capital"}
    payload.update(overrides)
    response = await client.post("/api/v1/worlds", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_health_reports_schema_ready(app_client: AsyncClient) -> None:
    response = await app_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database_ready"] is True


async def test_world_round_trips_and_persists(app_client: AsyncClient) -> None:
    created = await create_world(app_client)
    fetched = await app_client.get(f"/api/v1/worlds/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "The Fractured Crown"

    listed = await app_client.get("/api/v1/worlds")
    assert [w["id"] for w in listed.json()] == [created["id"]]


async def test_world_language_defaults_to_english_and_is_stored(app_client: AsyncClient) -> None:
    default = await create_world(app_client)
    assert default["language"] == "en"

    spanish = await create_world(app_client, name="La Corona Rota", language="es")
    assert spanish["language"] == "es"

    refetched = await app_client.get(f"/api/v1/worlds/{spanish['id']}")
    assert refetched.json()["language"] == "es"


async def test_invalid_language_is_rejected(app_client: AsyncClient) -> None:
    response = await app_client.post("/api/v1/worlds", json={"name": "X", "language": "fr"})
    assert response.status_code == 422


async def test_character_creation_and_listing(app_client: AsyncClient) -> None:
    world = await create_world(app_client)
    response = await app_client.post(
        f"/api/v1/worlds/{world['id']}/characters",
        json={
            "name": "Elena",
            "personality": "sarcastic, cautious",
            "goals": ["find the truth"],
            "secrets": ["helped build the wards"],
        },
    )
    assert response.status_code == 201, response.text
    character = response.json()
    assert character["goals"] == ["find the truth"]
    assert character["world_id"] == world["id"]

    listed = await app_client.get(f"/api/v1/worlds/{world['id']}/characters")
    assert len(listed.json()) == 1

    fetched = await app_client.get(f"/api/v1/characters/{character['id']}")
    assert fetched.json()["name"] == "Elena"


async def test_missing_world_returns_consistent_404_envelope(app_client: AsyncClient) -> None:
    response = await app_client.get(f"/api/v1/worlds/{uuid.uuid4()}")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "not_found"
    assert "detail" in body


async def test_session_creation_requires_existing_world(app_client: AsyncClient) -> None:
    response = await app_client.post(
        "/api/v1/sessions",
        json={
            "world_id": str(uuid.uuid4()),
            "title": "Run 1",
            "player_name": "Rin",
        },
    )
    assert response.status_code == 404


async def test_session_round_trips_with_world(app_client: AsyncClient) -> None:
    world = await create_world(app_client, language="es")
    response = await app_client.post(
        "/api/v1/sessions",
        json={
            "world_id": world["id"],
            "title": "Partida 1",
            "player_name": "Rin",
            "current_location": "el mercado",
        },
    )
    assert response.status_code == 201, response.text
    session = response.json()
    assert session["turn_index"] == 0

    detail = await app_client.get(f"/api/v1/sessions/{session['id']}")
    assert detail.status_code == 200
    # The session carries its world, so the client knows the narrative language.
    assert detail.json()["world"]["language"] == "es"

    listed = await app_client.get("/api/v1/sessions", params={"world_id": world["id"]})
    assert len(listed.json()) == 1
