"""Rules crossing the HTTP boundary, the database, and into a turn.

The three things worth proving here: a world always ends up with valid rules
whichever way it was created, those rules survive a reload byte for byte, and the
rules that come back out are the ones the Story Director is handed.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.contracts import TurnGeneration
from app.application.story_context import StoryContext
from app.domain.errors import InvalidWorldRulesError
from app.domain.world_rules import (
    WorldRulesPreset,
    build_preset,
    default_world_rules,
    parse_world_rules,
)
from app.infrastructure.db.turn_gateway import SqlAlchemyTurnGateway
from app.infrastructure.story.mock import MockStoryGenerator
from app.infrastructure.story.rendering import render_context
from tests.conftest import override_story_generator


class RecordingStoryGenerator:
    """Mock provider that keeps the context it was called with."""

    name = "recording"

    def __init__(self) -> None:
        self._inner = MockStoryGenerator()
        self.contexts: list[StoryContext] = []

    async def generate_turn(self, context: StoryContext) -> TurnGeneration:
        self.contexts.append(context)
        return await self._inner.generate_turn(context)

    async def status(self) -> object:  # pragma: no cover - not exercised
        return await self._inner.status()


async def create_world(client: AsyncClient, **extra: object) -> dict:
    response = await client.post("/api/v1/worlds", json={"name": "W", "genre": "fantasy", **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def get_rules(client: AsyncClient, world_id: str) -> dict:
    response = await client.get(f"/api/v1/worlds/{world_id}/rules")
    assert response.status_code == 200, response.text
    return response.json()


# -- creation ----------------------------------------------------------------


async def test_a_world_created_without_rules_gets_valid_defaults(
    app_client: AsyncClient,
) -> None:
    world = await create_world(app_client)
    assert parse_world_rules(await get_rules(app_client, world["id"])) == default_world_rules()


@pytest.mark.parametrize("preset", list(WorldRulesPreset))
async def test_every_preset_can_create_a_world_and_reads_back_intact(
    app_client: AsyncClient, preset: WorldRulesPreset
) -> None:
    world = await create_world(app_client, rules_preset=preset.value)
    assert parse_world_rules(await get_rules(app_client, world["id"])) == build_preset(preset)


async def test_a_world_can_be_created_from_an_explicit_document(
    app_client: AsyncClient,
) -> None:
    """The round trip that matters: what a client sends is what it gets back."""
    wanted = build_preset(WorldRulesPreset.DARK_FANTASY)
    world = await create_world(app_client, rules=wanted.model_dump(mode="json"))

    assert await get_rules(app_client, world["id"]) == wanted.model_dump(mode="json")


async def test_supplying_both_a_preset_and_a_document_is_rejected(
    app_client: AsyncClient,
) -> None:
    """Ambiguity is a 422, never a silent precedence rule."""
    response = await app_client.post(
        "/api/v1/worlds",
        json={
            "name": "W",
            "rules_preset": WorldRulesPreset.SHONEN.value,
            "rules": default_world_rules().model_dump(mode="json"),
        },
    )
    assert response.status_code == 422, response.text
    assert "not both" in response.text


async def test_an_unknown_preset_is_rejected(app_client: AsyncClient) -> None:
    response = await app_client.post(
        "/api/v1/worlds", json={"name": "W", "rules_preset": "grimdark_isekai"}
    )
    assert response.status_code == 422


async def test_an_out_of_range_setting_is_rejected_at_the_api(app_client: AsyncClient) -> None:
    raw = default_world_rules().model_dump(mode="json")
    raw["danger"]["lethality"] = 140
    response = await app_client.post("/api/v1/worlds", json={"name": "W", "rules": raw})
    assert response.status_code == 422


async def test_a_document_from_another_version_is_rejected_at_the_api(
    app_client: AsyncClient,
) -> None:
    raw = default_world_rules().model_dump(mode="json") | {"version": 2}
    response = await app_client.post("/api/v1/worlds", json={"name": "W", "rules": raw})
    assert response.status_code == 422


# -- reading -----------------------------------------------------------------


async def test_the_world_list_does_not_carry_rules(app_client: AsyncClient) -> None:
    """~3 KB per row for a screen that does not show them."""
    await create_world(app_client)
    worlds = (await app_client.get("/api/v1/worlds")).json()
    assert worlds
    assert "rules" not in worlds[0]
    assert "rules_json" not in worlds[0]


async def test_rules_for_an_unknown_world_are_a_404(app_client: AsyncClient) -> None:
    response = await app_client.get(f"/api/v1/worlds/{uuid.uuid4()}/rules")
    assert response.status_code == 404


async def test_rules_survive_a_reload(app_client: AsyncClient) -> None:
    world = await create_world(app_client, rules_preset=WorldRulesPreset.COZY_FANTASY.value)
    first = await get_rules(app_client, world["id"])
    second = await get_rules(app_client, world["id"])
    assert first == second == build_preset(WorldRulesPreset.COZY_FANTASY).model_dump(mode="json")


# -- reaching the Story Director ---------------------------------------------


async def test_the_world_rules_reach_the_story_context(app_client: AsyncClient) -> None:
    world = await create_world(app_client, rules_preset=WorldRulesPreset.DARK_FANTASY.value)
    session = (
        await app_client.post(
            "/api/v1/sessions",
            json={"world_id": world["id"], "title": "Run", "player_name": "Rin"},
        )
    ).json()

    recorder = RecordingStoryGenerator()
    override_story_generator(app_client, recorder)
    response = await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns", json={"action": "I look around."}
    )
    assert response.status_code == 200, response.text

    (context,) = recorder.contexts
    expected = build_preset(WorldRulesPreset.DARK_FANTASY)
    assert context.world_rules.lethality == expected.danger.lethality
    assert context.world_rules.plot_armor.player == expected.narrative.plot_armor.player
    assert context.world_rules.darkness == expected.narrative.darkness
    assert context.world_rules.enforcement is expected.rules.enforcement


async def test_a_corrupt_rules_row_fails_loudly_instead_of_defaulting(
    db_session: AsyncSession, make_world
) -> None:
    """A half-migrated or hand-edited row must not quietly become 'balanced'."""
    world = make_world(rules_json=default_world_rules().model_dump(mode="json"))
    db_session.add(world)
    await db_session.flush()

    world.rules_json = {"version": 1, "danger": {"lethality": "very"}}
    await db_session.flush()

    with pytest.raises(InvalidWorldRulesError):
        await SqlAlchemyTurnGateway(db_session).get_world(world.id)


async def test_the_rendered_prompt_states_the_rules(app_client: AsyncClient, make_story_context):
    """A rules block the model can actually read, not a JSON dump."""
    rendered = render_context(make_story_context())

    assert "# World rules" in rendered
    assert "Plot armor:" in rendered
    assert "Lethality" in rendered or "lethality" in rendered
    assert '"narrative"' not in rendered  # never raw JSON
