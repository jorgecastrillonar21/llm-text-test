"""The resolution and history surface, over HTTP.

Two read-only views and one idempotent write. The write is the turn endpoint: a client
that mints a stable `client_action_id` and re-sends it after a timeout gets the turn it
already played, not a second one. The reads are the two trails that turn leaves --
`resolutions` for what the engine decided, `events` for what the story will remember --
and neither has a write counterpart anywhere in the API.

`test_neither_trail_can_be_written_over_http` is the one to keep. An endpoint that could
POST a resolution would let a client assert the world changed without anything having
decided that it did, and one that could PATCH an event would rewrite history.
"""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient

from .test_turns import bootstrap

# Contains "steal", which is one of the tokens the mock provider treats as consequential,
# so the turn proposes a `player_acted_consequentially` world event. A bland action
# proposes none -- see `test_a_quiet_turn_leaves_a_record_and_no_history`.
CONSEQUENTIAL = "I steal the ledger from the archive."
QUIET = "I look around the room."


def _paths(client: AsyncClient) -> dict:
    transport = client._transport
    assert isinstance(transport, ASGITransport)
    return transport.app.openapi()["paths"]  # type: ignore[union-attr]


async def _turn(
    client: AsyncClient, session_id: str, action: str, *, client_action_id: str | None = None
) -> dict:
    body: dict[str, object] = {"action": action}
    if client_action_id is not None:
        body["client_action_id"] = client_action_id
    response = await client.post(f"/api/v1/sessions/{session_id}/turns", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Idempotency, from the side the client sees
# ---------------------------------------------------------------------------


async def test_the_same_client_action_id_twice_plays_one_turn(app_client: AsyncClient) -> None:
    """The retry case: a request that timed out but was in fact received.

    The second call returns the turn that was played -- marked `replayed` so the client
    can tell -- and nothing about the session moved.
    """
    _, session = await bootstrap(app_client)
    first = await _turn(app_client, session["id"], CONSEQUENTIAL, client_action_id="tap-1")
    second = await _turn(app_client, session["id"], CONSEQUENTIAL, client_action_id="tap-1")

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["turn_index"] == first["turn_index"] == 1
    assert second["resolution_id"] == first["resolution_id"]
    assert [m["content"] for m in second["messages"]] == [m["content"] for m in first["messages"]]

    detail = (await app_client.get(f"/api/v1/sessions/{session['id']}")).json()
    assert detail["turn_index"] == 1
    messages = (await app_client.get(f"/api/v1/sessions/{session['id']}/messages")).json()
    assert len(messages) == len(first["messages"])
    resolutions = (await app_client.get(f"/api/v1/sessions/{session['id']}/resolutions")).json()[
        "resolutions"
    ]
    assert len(resolutions) == 1
    events = (await app_client.get(f"/api/v1/sessions/{session['id']}/events")).json()["events"]
    assert len(events) == 1


async def test_a_different_client_action_id_is_a_different_action(app_client: AsyncClient) -> None:
    """The player genuinely acted twice. Same words, new id, two turns -- which is why
    the id is the client's to mint and not something the server infers from the text."""
    _, session = await bootstrap(app_client)
    first = await _turn(app_client, session["id"], CONSEQUENTIAL, client_action_id="tap-1")
    second = await _turn(app_client, session["id"], CONSEQUENTIAL, client_action_id="tap-2")

    assert second["replayed"] is False
    assert second["turn_index"] == 2
    assert second["resolution_id"] != first["resolution_id"]
    resolutions = (await app_client.get(f"/api/v1/sessions/{session['id']}/resolutions")).json()[
        "resolutions"
    ]
    assert len(resolutions) == 2


async def test_a_turn_without_an_id_is_recorded_but_not_replayable(
    app_client: AsyncClient,
) -> None:
    """Documented honestly rather than papered over: a client that sends no id has no
    protection. The turn is still recorded -- under a key derived from the turn index --
    so the trail is complete either way. Unrecorded and unreplayable are different
    things, and only the second one is true here."""
    _, session = await bootstrap(app_client)
    first = await _turn(app_client, session["id"], CONSEQUENTIAL)
    second = await _turn(app_client, session["id"], CONSEQUENTIAL)

    assert first["resolution_id"] is not None
    assert second["resolution_id"] != first["resolution_id"]
    assert second["replayed"] is False
    assert second["turn_index"] == 2
    resolutions = (await app_client.get(f"/api/v1/sessions/{session['id']}/resolutions")).json()[
        "resolutions"
    ]
    assert len(resolutions) == 2
    assert {r["idempotency_key"] for r in resolutions} == {"turn-index:1", "turn-index:2"}


# ---------------------------------------------------------------------------
# The mechanical trail
# ---------------------------------------------------------------------------


async def test_the_trail_records_the_verdict_and_the_revision(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    await _turn(app_client, session["id"], CONSEQUENTIAL, client_action_id="tap-1")

    listed = (await app_client.get(f"/api/v1/sessions/{session['id']}/resolutions")).json()
    record = listed["resolutions"][0]

    assert record["source_type"] == "player_action"
    assert record["disposition"] in {"applied", "no_effect"}
    assert record["resolver_name"] == "turn"
    assert record["resolver_version"]
    assert record["idempotency_key"] == "turn:tap-1"
    assert record["state_revision_after"] >= record["state_revision_before"]
    assert record["turn_index"] == 1


async def test_the_trail_can_be_filtered_by_what_triggered_it(app_client: AsyncClient) -> None:
    """`source_type` is a real filter, not decoration: once simulation and scheduled work
    share the table, "what did the player do?" has to be answerable separately."""
    _, session = await bootstrap(app_client)
    await _turn(app_client, session["id"], CONSEQUENTIAL, client_action_id="tap-1")

    mine = (
        await app_client.get(
            f"/api/v1/sessions/{session['id']}/resolutions", params={"source_type": "player_action"}
        )
    ).json()["resolutions"]
    assert len(mine) == 1

    simulated = (
        await app_client.get(
            f"/api/v1/sessions/{session['id']}/resolutions",
            params={"source_type": "world_simulation"},
        )
    ).json()["resolutions"]
    assert simulated == []


async def test_an_unknown_source_type_is_the_callers_problem(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    response = await app_client.get(
        f"/api/v1/sessions/{session['id']}/resolutions", params={"source_type": "vibes"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


async def test_history_points_back_at_the_resolution_that_wrote_it(
    app_client: AsyncClient,
) -> None:
    _, session = await bootstrap(app_client)
    turn = await _turn(app_client, session["id"], CONSEQUENTIAL, client_action_id="tap-1")

    events = (await app_client.get(f"/api/v1/sessions/{session['id']}/events")).json()["events"]
    assert len(events) == 1
    event = events[0]

    assert event["resolution_id"] == turn["resolution_id"]
    assert event["subtype"] == "player_acted_consequentially"
    assert event["category"] == "action"
    assert event["turn_index"] == 1
    assert event["sequence"] >= 1
    # The mock offered no importance, so this is the unregistered-subtype default.
    assert event["importance"] == 2


async def test_a_quiet_turn_leaves_a_record_and_no_history(app_client: AsyncClient) -> None:
    """Most turns are this. The engine still recorded what it decided; the story has
    nothing to remember about someone looking around a room."""
    _, session = await bootstrap(app_client)
    await _turn(app_client, session["id"], QUIET, client_action_id="tap-1")

    resolutions = (await app_client.get(f"/api/v1/sessions/{session['id']}/resolutions")).json()[
        "resolutions"
    ]
    assert len(resolutions) == 1
    assert resolutions[0]["event_count"] == 0

    events = (await app_client.get(f"/api/v1/sessions/{session['id']}/events")).json()["events"]
    assert events == []


async def test_history_can_be_filtered_by_weight_and_by_kind(app_client: AsyncClient) -> None:
    _, session = await bootstrap(app_client)
    await _turn(app_client, session["id"], CONSEQUENTIAL, client_action_id="tap-1")
    base = f"/api/v1/sessions/{session['id']}/events"

    assert len((await app_client.get(base, params={"min_importance": 2})).json()["events"]) == 1
    assert (await app_client.get(base, params={"min_importance": 3})).json()["events"] == []
    assert len((await app_client.get(base, params={"category": "action"})).json()["events"]) == 1
    assert (await app_client.get(base, params={"category": "combat"})).json()["events"] == []


async def test_the_limit_is_a_ceiling_the_caller_cannot_raise(app_client: AsyncClient) -> None:
    """Bounded by `limit` rather than pageable, on purpose: an endpoint that could stream
    every event of a long session is the one a client would use to build a prompt out of
    all of them."""
    _, session = await bootstrap(app_client)

    assert (
        await app_client.get(f"/api/v1/sessions/{session['id']}/events", params={"limit": 10_000})
    ).status_code == 422
    assert (
        await app_client.get(
            f"/api/v1/sessions/{session['id']}/resolutions", params={"limit": 10_000}
        )
    ).status_code == 422
    assert (
        await app_client.get(f"/api/v1/sessions/{session['id']}/events", params={"limit": 0})
    ).status_code == 422


async def test_a_session_that_does_not_exist_has_no_trail_and_no_history(
    app_client: AsyncClient,
) -> None:
    missing = uuid.uuid4()
    assert (await app_client.get(f"/api/v1/sessions/{missing}/resolutions")).status_code == 404
    assert (await app_client.get(f"/api/v1/sessions/{missing}/events")).status_code == 404


# ---------------------------------------------------------------------------
# The surface itself
# ---------------------------------------------------------------------------


async def test_neither_trail_can_be_written_over_http(app_client: AsyncClient) -> None:
    """Three operations, and only one of them writes -- narration, which writes prose.

    Asserted against the OpenAPI document rather than by trying verbs, so a write
    endpoint added here fails the build instead of waiting to be noticed.
    """
    surface = {
        f"{method.upper()} {path}"
        for path, operations in _paths(app_client).items()
        for method in operations
        if "/resolutions" in path or path.endswith("/events")
    }

    assert surface == {
        "GET /api/v1/sessions/{session_id}/resolutions",
        "GET /api/v1/sessions/{session_id}/events",
        "POST /api/v1/sessions/{session_id}/resolutions/{resolution_id}/narration",
    }
