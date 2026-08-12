"""The vertical slice: POST /sessions/{id}/turns end to end, and its transaction."""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from app.application.contracts import DialogueLine, TurnGeneration
from app.application.ports import TurnGenerationResult
from app.domain.errors import StoryGenerationError
from tests.conftest import FailingStoryGenerator


async def bootstrap(client: AsyncClient, language: str = "en") -> tuple[dict, dict]:
    world = (
        await client.post(
            "/api/v1/worlds",
            json={"name": "W", "genre": "fantasy", "language": language},
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


def set_generator(client: AsyncClient, generator: object) -> None:
    transport = client._transport
    assert isinstance(transport, ASGITransport)
    transport.app.state.story_generator = generator


async def test_turn_returns_narration_dialogue_and_suggestions(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    response = await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={"action": "I walk toward Elena and ask why she was looking for me."},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["turn_index"] == 1
    roles = [m["role"] for m in body["messages"]]
    assert roles[0] == "player"
    assert "narrator" in roles
    assert "character" in roles
    assert 3 <= len(body["suggested_actions"]) <= 4


async def test_turn_persists_messages_across_requests(app_client: AsyncClient) -> None:
    """Reload the transcript: this is the 'save game survives' guarantee."""
    _, session = await bootstrap(app_client)
    await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns", json={"action": "I thank Elena."}
    )

    messages = (await app_client.get(f"/api/v1/sessions/{session['id']}/messages")).json()
    assert len(messages) >= 3
    assert messages[0]["role"] == "player"
    assert messages[0]["content"] == "I thank Elena."

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["turn_index"] == 1


async def test_turn_is_visible_to_an_immediate_reread(app_client: AsyncClient) -> None:
    """Regression: the commit must land before the turn response is returned.

    Committing in a `yield` dependency's teardown runs after the response is sent,
    so the client's very next read could miss its own write.
    """
    _, session = await bootstrap(app_client)
    for expected_messages in (3, 6, 9):
        await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns", json={"action": "I look around."}
        )
        messages = (await app_client.get(f"/api/v1/sessions/{session['id']}/messages")).json()
        assert len(messages) == expected_messages


async def test_multiple_turns_increment_and_keep_order(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    for action in ("I look around.", "I ask Elena about the wards.", "I leave."):
        response = await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns", json={"action": action}
        )
        assert response.status_code == 200

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["turn_index"] == 3

    messages = (await app_client.get(f"/api/v1/sessions/{session['id']}/messages")).json()
    turn_indexes = [m["turn_index"] for m in messages]
    assert turn_indexes == sorted(turn_indexes)


async def test_relationship_is_created_and_clamped_within_bounds(
    app_client: AsyncClient,
) -> None:
    _, session = await bootstrap(app_client)
    for _ in range(4):
        await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns", json={"action": "I thank Elena."}
        )

    relationships = (await app_client.get(f"/api/v1/sessions/{session['id']}/relationships")).json()
    assert len(relationships) == 1
    trust = relationships[0]["trust"]
    assert 0 < trust <= 100
    # 4 friendly turns at +2 each; proves deltas accumulate rather than being overwritten.
    assert trust == 8


async def test_durable_memory_is_persisted(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={"action": "I promise Elena I will find the traitor."},
    )
    memories = (await app_client.get(f"/api/v1/sessions/{session['id']}/memories")).json()
    assert len(memories) == 1
    assert 1 <= memories[0]["importance"] <= 5


async def test_spanish_world_produces_spanish_narration(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client, language="es")
    body = (
        await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns",
            json={"action": "Miro a mi alrededor."},
        )
    ).json()
    narrator = next(m for m in body["messages"] if m["role"] == "narrator")
    assert "El aire" in narrator["content"]
    assert any("Preguntar" in a for a in body["suggested_actions"])


async def test_empty_action_is_rejected(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    response = await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns", json={"action": "   "}
    )
    assert response.status_code == 422


async def test_turn_on_missing_session_returns_404(app_client: AsyncClient) -> None:
    response = await app_client.post(
        f"/api/v1/sessions/{uuid.uuid4()}/turns", json={"action": "hello"}
    )
    assert response.status_code == 404


async def test_failed_generation_rolls_back_the_entire_turn(app_client: AsyncClient) -> None:
    """A provider failure must leave no trace: no player message, no turn increment.

    This is the core transaction guarantee documented in turn_service.
    """
    _, session = await bootstrap(app_client)
    await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns", json={"action": "I look around."}
    )

    before = (await app_client.get(f"/api/v1/sessions/{session['id']}/messages")).json()
    set_generator(
        app_client,
        FailingStoryGenerator(
            StoryGenerationError("model exploded", provider="ollama", retryable=True)
        ),
    )

    response = await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={"action": "This action must never be stored."},
    )
    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "story_generation_failed"
    assert body["provider"] == "ollama"
    assert body["retryable"] is True

    after = (await app_client.get(f"/api/v1/sessions/{session['id']}/messages")).json()
    assert len(after) == len(before)
    assert all("must never be stored" not in m["content"] for m in after)

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["turn_index"] == 1


async def test_session_is_usable_again_after_a_failed_turn(app_client: AsyncClient) -> None:
    """A failed turn is a no-op, so retrying must simply work."""
    _, session = await bootstrap(app_client)
    set_generator(
        app_client, FailingStoryGenerator(StoryGenerationError("boom", provider="ollama"))
    )
    failed = await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns", json={"action": "I try something."}
    )
    assert failed.status_code == 502

    from app.infrastructure.story.mock import MockStoryGenerator

    set_generator(app_client, MockStoryGenerator())
    retried = await app_client.post(
        f"/api/v1/sessions/{session['id']}/turns", json={"action": "I try something."}
    )
    assert retried.status_code == 200
    assert retried.json()["turn_index"] == 1


class ScriptedStoryGenerator:
    """Returns one fixed generation, for exercising what the turn service refuses."""

    name = "scripted"

    def __init__(self, generation: TurnGeneration) -> None:
        self._generation = generation

    async def generate_turn(self, context: object) -> TurnGenerationResult:
        return TurnGenerationResult(generation=self._generation)

    async def status(self) -> object:  # pragma: no cover - not exercised
        raise NotImplementedError


async def test_dialogue_attributed_to_the_player_is_dropped(app_client: AsyncClient) -> None:
    """Observed with mistral:7b: it wrote a line of the player's own dialogue.

    The player character belongs to the player. Persisting an invented quote is
    worse than losing it, because the transcript is replayed to the model next turn
    as established fact -- so the invention becomes canon.
    """
    _, session = await bootstrap(app_client)
    set_generator(
        app_client,
        ScriptedStoryGenerator(
            TurnGeneration(
                narration="The alley is quiet.",
                dialogue=[
                    DialogueLine(speaker="Rin", text="I never trust coincidences."),
                    DialogueLine(speaker="Elena", text="Neither do I."),
                ],
                suggested_actions=["Leave", "Stay", "Listen"],
            )
        ),
    )

    body = (
        await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns", json={"action": "I ask her."}
        )
    ).json()

    spoken = [m["content"] for m in body["messages"] if m["role"] == "character"]
    assert spoken == ["Neither do I."]

    # And it must not reappear once the transcript is reloaded.
    messages = (await app_client.get(f"/api/v1/sessions/{session['id']}/messages")).json()
    assert all("never trust coincidences" not in m["content"] for m in messages)


async def test_a_character_sharing_the_players_name_still_speaks(
    app_client: AsyncClient,
) -> None:
    """The guard keys on attribution, not on the name alone."""
    world, session = await bootstrap(app_client)
    twin = (
        await app_client.post(f"/api/v1/worlds/{world['id']}/characters", json={"name": "Rin"})
    ).json()
    set_generator(
        app_client,
        ScriptedStoryGenerator(
            TurnGeneration(
                narration="Two people answer at once.",
                dialogue=[
                    DialogueLine(
                        character_id=uuid.UUID(twin["id"]),
                        speaker="Rin",
                        text="That is my name too.",
                    )
                ],
                suggested_actions=["Ask which"],
            )
        ),
    )

    body = (
        await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns", json={"action": "I call out a name."}
        )
    ).json()

    assert [m["content"] for m in body["messages"] if m["role"] == "character"] == [
        "That is my name too."
    ]
