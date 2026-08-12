"""Geography over HTTP: authoring, reading, and what the Story Director may add.

The paths are the design here. Template geography lives under `/worlds`, and anything
that depends on what has happened lives under `/sessions` -- because a read with no
session cannot apply the visibility rule, and a read that cannot apply it either leaks
another save's canon or hides half of this one.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.application.contracts import TurnGeneration
from app.application.ports import TurnGenerationResult


async def _world(client: AsyncClient, name: str = "W") -> dict:
    return (await client.post("/api/v1/worlds", json={"name": name, "genre": "fantasy"})).json()


async def _place(client: AsyncClient, world_id: str, **payload: object) -> dict:
    body: dict[str, object] = {
        "name": "Riverwood",
        "category": "settlement",
        "scale": "settlement",
    }
    body.update(payload)
    response = await client.post(f"/api/v1/worlds/{world_id}/locations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _session(client: AsyncClient, world_id: str, **payload: object) -> dict:
    body: dict[str, object] = {"world_id": world_id, "title": "Run", "player_name": "Rin"}
    body.update(payload)
    response = await client.post("/api/v1/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _set_generator(client: AsyncClient, generator: object) -> None:
    transport = client._transport
    assert isinstance(transport, ASGITransport)
    transport.app.state.story_generator = generator  # type: ignore[union-attr]


class _Generator:
    """Returns a fixed generation, so a turn's proposal handling is assertable."""

    name = "fixed"

    def __init__(self, generation: TurnGeneration) -> None:
        self._generation = generation

    async def generate_turn(self, context: object) -> TurnGenerationResult:
        return TurnGenerationResult(generation=self._generation)

    async def status(self) -> object:  # pragma: no cover - not exercised
        raise NotImplementedError


def _generation(**overrides: object) -> TurnGeneration:
    data: dict[str, object] = {"narration": "A door opens.", "suggested_actions": ["Go in"]}
    data.update(overrides)
    return TurnGeneration.model_validate(data)


# -- template authoring -------------------------------------------------------


async def test_a_world_can_be_given_geography(app_client: AsyncClient) -> None:
    world = await _world(app_client)
    town = await _place(app_client, world["id"])
    await _place(
        app_client,
        world["id"],
        name="Broken Crown",
        category="structure",
        subtype="tavern",
        scale="building",
        parent_location_id=town["id"],
    )

    listed = (await app_client.get(f"/api/v1/worlds/{world['id']}/locations")).json()
    assert {place["name"] for place in listed} == {"Riverwood", "Broken Crown"}
    assert all(place["origin_session_id"] is None for place in listed)


async def test_an_author_may_write_geography_narration_could_not(
    app_client: AsyncClient,
) -> None:
    """The creation policy gates narration, not authoring."""
    world = await _world(app_client)
    response = await app_client.post(
        f"/api/v1/worlds/{world['id']}/locations",
        json={"name": "The Northern Reach", "category": "region", "scale": "continental"},
    )
    assert response.status_code == 201, response.text


async def test_a_parent_in_another_world_is_refused(app_client: AsyncClient) -> None:
    first = await _world(app_client, "First")
    second = await _world(app_client, "Second")
    town = await _place(app_client, first["id"])

    response = await app_client.post(
        f"/api/v1/worlds/{second['id']}/locations",
        json={
            "name": "Orphan",
            "category": "structure",
            "scale": "building",
            "parent_location_id": town["id"],
        },
    )
    assert response.status_code in (404, 422), response.text


async def test_containment_over_http_can_only_ever_deepen(app_client: AsyncClient) -> None:
    """A cycle is unreachable through this API, and that is structural rather than
    checked: creation always mints a fresh id, so a new place cannot already be one of
    its own ancestors, and there is no endpoint that re-parents an existing one. The
    cycle rules themselves are exercised directly in `test_world_locations.py`."""
    world = await _world(app_client)
    town = await _place(app_client, world["id"])
    inner = await _place(
        app_client,
        world["id"],
        name="Inner",
        category="area",
        scale="district",
        parent_location_id=town["id"],
    )

    response = await app_client.post(
        f"/api/v1/worlds/{world['id']}/locations",
        json={
            "name": "Deeper",
            "category": "area",
            "scale": "site",
            "parent_location_id": inner["id"],
        },
    )
    assert response.status_code == 201
    assert response.json()["parent_location_id"] == inner["id"]

    assert not any(
        "patch" in operations or "put" in operations
        for path, operations in _paths(app_client).items()
        if "locations" in path
    ), "no re-parenting endpoint, so no way to close a loop"


def _paths(client: AsyncClient) -> dict:
    transport = client._transport
    return transport.app.openapi()["paths"]  # type: ignore[union-attr]


async def test_a_connection_needs_two_real_endpoints(app_client: AsyncClient) -> None:
    world = await _world(app_client)
    town = await _place(app_client, world["id"])

    response = await app_client.post(
        f"/api/v1/worlds/{world['id']}/connections",
        json={
            "from_location_id": town["id"],
            "to_location_id": str(uuid.uuid4()),
            "category": "road",
        },
    )
    assert response.status_code == 404, response.text


async def test_a_connection_records_distance_and_duration_separately(
    app_client: AsyncClient,
) -> None:
    world = await _world(app_client)
    town = await _place(app_client, world["id"])
    far = await _place(app_client, world["id"], name="Capital")

    created = (
        await app_client.post(
            f"/api/v1/worlds/{world['id']}/connections",
            json={
                "from_location_id": town["id"],
                "to_location_id": far["id"],
                "category": "portal",
                "physical_distance": {"value": 4000, "unit": "km"},
                "base_travel_minutes": 1,
            },
        )
    ).json()

    assert created["physical_distance"] == {"value": 4000.0, "unit": "km"}
    assert created["base_travel_minutes"] == 1


async def test_zones_hang_off_a_location(app_client: AsyncClient) -> None:
    world = await _world(app_client)
    tavern = await _place(
        app_client, world["id"], name="Broken Crown", category="structure", scale="building"
    )

    created = (
        await app_client.post(
            f"/api/v1/worlds/{world['id']}/locations/{tavern['id']}/zones",
            json={"name": "the fireplace", "category": "seating"},
        )
    ).json()
    assert created["location_id"] == tavern["id"]
    assert created["name"] == "the fireplace"


# -- session-scoped reads -----------------------------------------------------


async def test_a_session_sees_the_template_with_its_own_state(app_client: AsyncClient) -> None:
    world = await _world(app_client)
    await _place(app_client, world["id"])
    session = await _session(app_client, world["id"])

    listed = (await app_client.get(f"/api/v1/sessions/{session['id']}/locations")).json()
    assert len(listed) == 1
    assert listed[0]["definition"]["name"] == "Riverwood"
    assert listed[0]["state"]["condition"] == "intact"
    assert listed[0]["state"]["accessibility"] == "open"


async def test_a_session_created_before_the_geography_still_reads_it(
    app_client: AsyncClient,
) -> None:
    """Template rows are shared, not copied, so a place authored later is simply there.
    Its state row is created lazily on first change."""
    world = await _world(app_client)
    session = await _session(app_client, world["id"])
    await _place(app_client, world["id"])

    listed = (await app_client.get(f"/api/v1/sessions/{session['id']}/locations")).json()
    assert [entry["definition"]["name"] for entry in listed] == ["Riverwood"]
    assert listed[0]["state"] is None


async def test_a_location_detail_carries_zones_children_and_exits(
    app_client: AsyncClient,
) -> None:
    world = await _world(app_client)
    town = await _place(app_client, world["id"])
    tavern = await _place(
        app_client,
        world["id"],
        name="Broken Crown",
        category="structure",
        subtype="tavern",
        scale="building",
        parent_location_id=town["id"],
    )
    await app_client.post(
        f"/api/v1/worlds/{world['id']}/locations/{tavern['id']}/zones", json={"name": "the bar"}
    )
    await app_client.post(
        f"/api/v1/worlds/{world['id']}/connections",
        json={
            "from_location_id": town["id"],
            "to_location_id": tavern["id"],
            "category": "passage",
            "subtype": "door",
        },
    )
    session = await _session(app_client, world["id"])

    detail = (
        await app_client.get(f"/api/v1/sessions/{session['id']}/locations/{tavern['id']}")
    ).json()
    assert [zone["name"] for zone in detail["zones"]] == ["the bar"]
    assert detail["children"] == []
    assert [c["connection"]["subtype"] for c in detail["connections"]] == ["door"]


async def test_spatial_context_is_scene_sized(app_client: AsyncClient) -> None:
    world = await _world(app_client)
    town = await _place(app_client, world["id"])
    tavern = await _place(
        app_client,
        world["id"],
        name="Broken Crown",
        category="structure",
        subtype="tavern",
        scale="building",
        parent_location_id=town["id"],
    )
    await _place(app_client, world["id"], name="The Capital")
    session = await _session(app_client, world["id"])

    context = (
        await app_client.get(f"/api/v1/sessions/{session['id']}/spatial-context/{tavern['id']}")
    ).json()
    assert context["current"]["name"] == "Broken Crown"
    assert [place["name"] for place in context["within"]] == ["Riverwood"]
    # Unrelated geography does not appear at any tier.
    rendered = str(context)
    assert "The Capital" not in rendered


async def test_reading_a_place_another_session_invented_is_a_404(
    app_client: AsyncClient,
) -> None:
    """Invisible, not forbidden -- and from here the two are the same thing."""
    world = await _world(app_client)
    town = await _place(app_client, world["id"])
    first = await _session(app_client, world["id"], title="A")
    second = await _session(app_client, world["id"], title="B", player_name="Kai")

    _set_generator(
        app_client,
        _Generator(
            _generation(
                location_proposals=[
                    {
                        "name": "Starfall Books",
                        "category": "structure",
                        "subtype": "bookstore",
                        "scale": "building",
                        "parent_location_id": town["id"],
                    }
                ]
            )
        ),
    )
    turn = (
        await app_client.post(
            f"/api/v1/sessions/{first['id']}/turns", json={"action": "I look around."}
        )
    ).json()
    assert turn["locations_created"] == 1

    mine = (await app_client.get(f"/api/v1/sessions/{first['id']}/locations")).json()
    theirs = (await app_client.get(f"/api/v1/sessions/{second['id']}/locations")).json()
    shop = next(e["definition"] for e in mine if e["definition"]["name"] == "Starfall Books")
    assert "Starfall Books" not in {e["definition"]["name"] for e in theirs}

    response = await app_client.get(f"/api/v1/sessions/{second['id']}/locations/{shop['id']}")
    assert response.status_code == 404


async def test_reading_geography_for_a_session_that_does_not_exist_is_a_404(
    app_client: AsyncClient,
) -> None:
    for path in (
        f"/api/v1/sessions/{uuid.uuid4()}/locations",
        f"/api/v1/sessions/{uuid.uuid4()}/locations/{uuid.uuid4()}",
        f"/api/v1/sessions/{uuid.uuid4()}/spatial-context/{uuid.uuid4()}",
    ):
        assert (await app_client.get(path)).status_code == 404, path


# -- the Story Director -------------------------------------------------------


async def test_a_narrated_place_becomes_canon_with_an_id_the_game_chose(
    app_client: AsyncClient,
) -> None:
    world = await _world(app_client)
    town = await _place(app_client, world["id"])
    session = await _session(app_client, world["id"])
    _set_generator(
        app_client,
        _Generator(
            _generation(
                location_proposals=[
                    {
                        "name": "Starfall Books",
                        "category": "structure",
                        "subtype": "bookstore",
                        "scale": "building",
                        "parent_location_id": town["id"],
                        "reason": "The player pushed the door open.",
                    }
                ]
            )
        ),
    )

    turn = (
        await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns", json={"action": "I try the door."}
        )
    ).json()
    assert turn["locations_created"] == 1
    assert turn["locations_rejected"] == 0

    listed = (await app_client.get(f"/api/v1/sessions/{session['id']}/locations")).json()
    shop = next(e for e in listed if e["definition"]["name"] == "Starfall Books")
    # Local to this save, importance fixed by the reviewer, and with a starting state.
    assert shop["definition"]["origin_session_id"] == session["id"]
    assert shop["definition"]["importance"] == 2
    assert shop["state"]["condition"] == "intact"


async def test_narration_may_not_establish_a_country(app_client: AsyncClient) -> None:
    world = await _world(app_client)
    session = await _session(app_client, world["id"])
    _set_generator(
        app_client,
        _Generator(
            _generation(
                location_proposals=[
                    {"name": "The Northern Reach", "category": "region", "scale": "continental"}
                ]
            )
        ),
    )

    turn = (
        await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns", json={"action": "I look north."}
        )
    ).json()
    assert turn["locations_created"] == 0
    assert turn["locations_rejected"] == 1
    # The turn itself is unharmed: a refused proposal never costs the prose.
    assert turn["turn_index"] == 1
    assert turn["messages"][0]["content"] == "I look north."


async def test_re_proposing_an_existing_place_is_refused(app_client: AsyncClient) -> None:
    """The failure this subsystem exists to end: a bookshop that moves every time it is
    mentioned."""
    world = await _world(app_client)
    await _place(app_client, world["id"])
    session = await _session(app_client, world["id"])
    _set_generator(
        app_client,
        _Generator(
            _generation(
                location_proposals=[
                    {"name": "Riverwood", "category": "settlement", "scale": "settlement"}
                ]
            )
        ),
    )

    turn = (
        await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns", json={"action": "I look around."}
        )
    ).json()
    assert turn["locations_created"] == 0
    assert turn["locations_rejected"] == 1


async def test_a_proposal_naming_a_parent_that_does_not_exist_is_refused(
    app_client: AsyncClient,
) -> None:
    world = await _world(app_client)
    session = await _session(app_client, world["id"])
    _set_generator(
        app_client,
        _Generator(
            _generation(
                location_proposals=[
                    {
                        "name": "Starfall Books",
                        "category": "structure",
                        "scale": "building",
                        "parent_location_id": str(uuid.uuid4()),
                    }
                ]
            )
        ),
    )

    turn = (
        await app_client.post(
            f"/api/v1/sessions/{session['id']}/turns", json={"action": "I look around."}
        )
    ).json()
    assert turn["locations_created"] == 0
    assert turn["locations_rejected"] == 1


async def test_a_malformed_proposal_never_costs_the_turn_its_prose(
    app_client: AsyncClient,
) -> None:
    generation = TurnGeneration.model_validate(
        {
            "narration": "The street is quiet.",
            "suggested_actions": ["Wait"],
            "location_proposals": [
                {"name": "Nowhere", "category": "not_a_category", "scale": "building"},
                {"name": "Somewhere", "category": "structure", "scale": "building"},
            ],
        }
    )
    assert [p.name for p in generation.location_proposals] == ["Somewhere"]
    assert generation.narration == "The street is quiet."


async def test_a_turn_with_no_proposals_creates_no_geography(app_client: AsyncClient) -> None:
    world = await _world(app_client)
    await _place(app_client, world["id"])
    session = await _session(app_client, world["id"])

    turn = (
        await app_client.post(f"/api/v1/sessions/{session['id']}/turns", json={"action": "I wait."})
    ).json()
    assert turn["locations_created"] == 0
    assert turn["locations_rejected"] == 0


# -- no gameplay write path ---------------------------------------------------


async def test_the_only_spatial_writes_are_authoring_and_the_dev_endpoint(
    app_client: AsyncClient,
) -> None:
    """A client that could PATCH a location's accessibility could open a barred gate,
    and the whole spatial model would be advisory."""
    transport = app_client._transport
    app = transport.app  # type: ignore[union-attr]
    writable = {
        f"{method} {path}"
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if ("location" in path or "spatial" in path or "connection" in path) and method != "get"
    }
    # Three authoring endpoints and nothing else. Changing what has *happened* to a
    # place goes through the development state-change endpoint, which is asserted to be
    # the only such door in `test_world_state_api.py`.
    assert writable == {
        "post /api/v1/worlds/{world_id}/locations",
        "post /api/v1/worlds/{world_id}/connections",
        "post /api/v1/worlds/{world_id}/locations/{location_id}/zones",
    }


@pytest.mark.parametrize("verb", ["patch", "put", "delete"])
async def test_there_is_no_way_to_edit_a_location_s_state_over_rest(
    app_client: AsyncClient, verb: str
) -> None:
    world = await _world(app_client)
    town = await _place(app_client, world["id"])
    session = await _session(app_client, world["id"])

    response = await app_client.request(
        verb.upper(),
        f"/api/v1/sessions/{session['id']}/locations/{town['id']}",
        json={"condition": "destroyed"},
    )
    assert response.status_code == 405
