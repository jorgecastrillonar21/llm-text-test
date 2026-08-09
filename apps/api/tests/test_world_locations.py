"""The world_locations domain, with no database and no application services.

Containment, connectivity and the rules that keep them apart -- all decisions the
domain makes on its own, and all testable against an in-memory index.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import NotFoundError, ValidationError
from app.domain.world_locations import (
    ConnectionCategory,
    CreationPolicy,
    LocationAccessibility,
    LocationCategory,
    LocationCondition,
    LocationConnection,
    LocationConnectionState,
    LocationDefinition,
    LocationIndex,
    LocationScale,
    LocationState,
    LocationZone,
    PhysicalDistance,
    UpdateConnectionState,
    UpdateLocationState,
    check_creation_allowed,
    check_parent,
    creation_policy_for,
    get_ancestors,
    get_children,
    get_descendants,
    get_parent,
    is_traversable,
    is_within,
    parse_subtype,
)

WORLD_ID = uuid.uuid4()
OTHER_WORLD_ID = uuid.uuid4()
SESSION_ID = uuid.uuid4()
OTHER_SESSION_ID = uuid.uuid4()

NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _place(name: str, **overrides: object) -> LocationDefinition:
    data: dict[str, object] = {
        "id": uuid.uuid4(),
        "world_id": WORLD_ID,
        "name": name,
        "category": LocationCategory.STRUCTURE,
        "scale": LocationScale.BUILDING,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return LocationDefinition.model_validate(data)


def _connection(source: uuid.UUID, target: uuid.UUID, **overrides: object) -> LocationConnection:
    data: dict[str, object] = {
        "id": uuid.uuid4(),
        "world_id": WORLD_ID,
        "from_location_id": source,
        "to_location_id": target,
        "category": ConnectionCategory.PASSAGE,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return LocationConnection.model_validate(data)


def _tree() -> tuple[LocationIndex, dict[str, LocationDefinition]]:
    """`Riverwood > Broken Crown > Cellar`, plus an unrelated Capital."""
    town = _place("Riverwood", category=LocationCategory.SETTLEMENT, scale=LocationScale.SETTLEMENT)
    tavern = _place("Broken Crown", parent_location_id=town.id)
    cellar = _place(
        "Cellar",
        category=LocationCategory.INTERIOR,
        scale=LocationScale.ROOM,
        parent_location_id=tavern.id,
    )
    capital = _place(
        "Capital", category=LocationCategory.SETTLEMENT, scale=LocationScale.SETTLEMENT
    )
    places = {"town": town, "tavern": tavern, "cellar": cellar, "capital": capital}
    return LocationIndex(places.values()), places


# -- category and scale -------------------------------------------------------


def test_category_and_scale_are_independent_axes() -> None:
    """A palace and its gardens: different categories, different scales, no rule
    connecting the two."""
    palace = _place(
        "Royal Palace",
        category=LocationCategory.STRUCTURE,
        subtype="royal_palace",
        scale=LocationScale.BUILDING,
    )
    gardens = _place(
        "Palace Gardens",
        category=LocationCategory.AREA,
        subtype="palace_gardens",
        scale=LocationScale.SITE,
    )
    assert palace.category is not gardens.category
    assert palace.scale is not gardens.scale


def test_one_category_carries_every_genre_through_its_subtype() -> None:
    """The enum does not grow for each fictional noun; `subtype` absorbs them."""
    for subtype in ("tavern", "orbital_station", "shrine", "server_farm"):
        place = _place("X", subtype=subtype)
        assert place.category is LocationCategory.STRUCTURE
        assert place.subtype == subtype


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Tavern", "tavern"),
        ("  orbital station ", "orbital_station"),
        ("enchanted-forest", "enchanted_forest"),
    ],
)
def test_subtypes_are_normalised_to_one_spelling(raw: str, expected: str) -> None:
    assert parse_subtype(raw) == expected


@pytest.mark.parametrize("raw", ["a tavern!", "café", "tavern/inn"])
def test_a_subtype_that_is_not_an_identifier_is_refused(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_subtype(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [("category", "metropolis"), ("scale", "enormous")],
)
def test_a_value_outside_the_enum_is_refused(field: str, value: str) -> None:
    with pytest.raises(PydanticValidationError):
        _place("X", **{field: value})


def test_metric_dimensions_are_never_required() -> None:
    """Narrative-first: "building" is what a scene needs, and no world has to invent
    87.4 metres to fill a column."""
    place = _place("X")
    assert place.spatial_metadata == {}
    measured = _place("Y", spatial_metadata={"length_m": 40, "note": "surveyed"})
    assert measured.spatial_metadata == {"length_m": 40, "note": "surveyed"}


def test_spatial_metadata_refuses_a_nested_document() -> None:
    with pytest.raises(ValidationError):
        _place("X", spatial_metadata={"bounds": {"w": 1, "h": 2}})


# -- containment --------------------------------------------------------------


def test_a_root_location_has_no_parent() -> None:
    index, places = _tree()
    assert get_parent(index, places["town"].id) is None
    assert places["town"] in index.roots()


def test_parent_and_children_resolve() -> None:
    index, places = _tree()
    parent = get_parent(index, places["tavern"].id)
    assert parent is not None
    assert parent.name == "Riverwood"
    assert [child.name for child in get_children(index, places["town"].id)] == ["Broken Crown"]


def test_ancestors_come_back_nearest_first() -> None:
    index, places = _tree()
    assert [a.name for a in get_ancestors(index, places["cellar"].id)] == [
        "Broken Crown",
        "Riverwood",
    ]


def test_descendants_come_back_breadth_first() -> None:
    index, places = _tree()
    assert [d.name for d in get_descendants(index, places["town"].id)] == [
        "Broken Crown",
        "Cellar",
    ]


def test_is_within_reaches_through_every_level() -> None:
    index, places = _tree()
    assert is_within(index, places["cellar"].id, places["tavern"].id)
    assert is_within(index, places["cellar"].id, places["town"].id)
    assert not is_within(index, places["cellar"].id, places["capital"].id)


def test_a_place_is_not_within_itself() -> None:
    """ "Inside" is a relationship between two different places. A caller wanting
    "here or below" says so in one obvious line."""
    index, places = _tree()
    assert not is_within(index, places["town"].id, places["town"].id)


def test_a_location_cannot_be_its_own_parent() -> None:
    place = _place("X")
    with pytest.raises(PydanticValidationError):
        LocationDefinition.model_validate({**place.model_dump(), "parent_location_id": place.id})


def test_check_parent_refuses_self_parenting() -> None:
    index, places = _tree()
    with pytest.raises(ValidationError):
        check_parent(index, child=places["town"], parent_id=places["town"].id)


def test_a_two_node_cycle_is_refused() -> None:
    """A.parent = B and B.parent = A: each link is fine alone, and together they are a
    town inside the tavern inside the town."""
    first = _place("A")
    second = _place("B", parent_location_id=first.id)
    index = LocationIndex([first, second])

    with pytest.raises(ValidationError, match="cycle"):
        check_parent(index, child=first, parent_id=second.id)


def test_a_deep_cycle_is_refused() -> None:
    first = _place("A")
    second = _place("B", parent_location_id=first.id)
    third = _place("C", parent_location_id=second.id)
    fourth = _place("D", parent_location_id=third.id)
    index = LocationIndex([first, second, third, fourth])

    with pytest.raises(ValidationError, match="cycle"):
        check_parent(index, child=first, parent_id=fourth.id)


def test_a_parent_in_another_world_is_refused() -> None:
    index, places = _tree()
    stranger = _place("Elsewhere", world_id=OTHER_WORLD_ID)
    index = LocationIndex([*places.values(), stranger])

    with pytest.raises(ValidationError, match="does not cross worlds"):
        check_parent(index, child=places["tavern"], parent_id=stranger.id)


def test_a_parent_that_does_not_exist_is_refused() -> None:
    index, places = _tree()
    with pytest.raises(NotFoundError):
        check_parent(index, child=places["tavern"], parent_id=uuid.uuid4())


def test_session_local_geography_may_sit_inside_template_geography() -> None:
    """The ordinary case: a generated bookshop in an authored town."""
    town = _place("Riverwood", scale=LocationScale.SETTLEMENT)
    shop = _place("Starfall Books", origin_session_id=SESSION_ID)
    index = LocationIndex([town, shop])

    check_parent(index, child=shop, parent_id=town.id)


def test_template_geography_may_not_sit_inside_one_session_s_canon() -> None:
    """It would be broken in every other save, where the container is not there."""
    generated = _place("Starfall Books", origin_session_id=SESSION_ID)
    authored = _place("The Guild Hall")
    index = LocationIndex([generated, authored])

    with pytest.raises(ValidationError, match="only inside one session"):
        check_parent(index, child=authored, parent_id=generated.id)


def test_one_session_s_canon_may_not_sit_inside_another_s() -> None:
    mine = _place("My Shop", origin_session_id=SESSION_ID)
    theirs = _place("Their Alley", origin_session_id=OTHER_SESSION_ID)
    index = LocationIndex([mine, theirs])

    with pytest.raises(ValidationError):
        check_parent(index, child=mine, parent_id=theirs.id)


def test_visibility_is_template_plus_mine() -> None:
    template = _place("Riverwood")
    mine = _place("My Shop", origin_session_id=SESSION_ID)
    theirs = _place("Their Alley", origin_session_id=OTHER_SESSION_ID)

    assert template.visible_to(SESSION_ID)
    assert mine.visible_to(SESSION_ID)
    assert not theirs.visible_to(SESSION_ID)


# -- connectivity -------------------------------------------------------------


def test_containment_does_not_create_a_connection() -> None:
    """The distinction the whole module exists for. A cellar is inside a tavern; that
    says nothing about whether there are stairs."""
    index, places = _tree()
    # Nothing in the hierarchy API produces an edge, and nothing constructs one from
    # a parent link -- the only way to have a connection is to have written one.
    assert not hasattr(index, "connections")
    assert get_children(index, places["tavern"].id)[0].name == "Cellar"


def test_a_bidirectional_connection_is_an_exit_from_both_ends() -> None:
    _, places = _tree()
    edge = _connection(places["town"].id, places["tavern"].id)

    assert edge.leads_from(places["town"].id) == places["tavern"].id
    assert edge.leads_from(places["tavern"].id) == places["town"].id


def test_a_one_way_connection_is_not_an_exit_back() -> None:
    """A drop shaft, a waterfall, a one-way portal. Nothing supplies the return edge."""
    _, places = _tree()
    chute = _connection(places["town"].id, places["cellar"].id, bidirectional=False)

    assert chute.leads_from(places["town"].id) == places["cellar"].id
    assert chute.leads_from(places["cellar"].id) is None


def test_a_connection_touching_neither_end_leads_nowhere() -> None:
    edge = _connection(uuid.uuid4(), uuid.uuid4())
    assert edge.leads_from(uuid.uuid4()) is None


def test_a_connection_must_join_two_different_places() -> None:
    same = uuid.uuid4()
    with pytest.raises(PydanticValidationError):
        _connection(same, same)


def test_distance_and_travel_time_are_stored_separately() -> None:
    """A portal is four thousand kilometres and one minute; neither is derived."""
    portal = _connection(
        uuid.uuid4(),
        uuid.uuid4(),
        category=ConnectionCategory.PORTAL,
        physical_distance=PhysicalDistance(value=4000, unit="km"),
        base_travel_minutes=1,
    )
    assert portal.physical_distance is not None
    assert portal.physical_distance.describe() == "4000 km"
    assert portal.base_travel_minutes == 1


def test_a_doorway_has_no_distance_and_no_cost() -> None:
    door = _connection(uuid.uuid4(), uuid.uuid4(), subtype="door", base_travel_minutes=0)
    assert door.physical_distance is None
    # Zero is meaningful and different from None: no time, versus nobody measured.
    assert door.base_travel_minutes == 0


def test_a_distance_needs_both_a_number_and_a_unit() -> None:
    with pytest.raises(PydanticValidationError):
        PhysicalDistance(value=20)  # type: ignore[call-arg]


# -- state --------------------------------------------------------------------


def _state(**overrides: object) -> LocationState:
    data: dict[str, object] = {
        "id": uuid.uuid4(),
        "session_id": SESSION_ID,
        "location_id": uuid.uuid4(),
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return LocationState.model_validate(data)


def test_a_new_state_is_intact_and_open_and_unowned() -> None:
    state = _state()
    assert state.condition is LocationCondition.INTACT
    assert state.accessibility is LocationAccessibility.OPEN
    assert state.security_level == 0
    assert state.local_danger_modifier == 0
    assert state.owner_entity_id is None
    assert state.controller_entity_id is None


@pytest.mark.parametrize(
    ("condition", "accessibility"),
    [
        (LocationCondition.PRISTINE, LocationAccessibility.RESTRICTED),
        (LocationCondition.DAMAGED, LocationAccessibility.OPEN),
        (LocationCondition.INTACT, LocationAccessibility.SEALED),
        (LocationCondition.DESTROYED, LocationAccessibility.OPEN),
    ],
)
def test_condition_and_accessibility_are_independent(
    condition: LocationCondition, accessibility: LocationAccessibility
) -> None:
    """Ruins somebody can walk into, and a pristine vault nobody can. Both are real."""
    state = _state(condition=condition, accessibility=accessibility)
    assert state.condition is condition
    assert state.accessibility is accessibility


@pytest.mark.parametrize("value", [-1, 101])
def test_security_outside_zero_to_one_hundred_is_refused(value: int) -> None:
    with pytest.raises(PydanticValidationError):
        _state(security_level=value)


@pytest.mark.parametrize("value", [-101, 101])
def test_a_danger_modifier_outside_minus_one_hundred_to_one_hundred_is_refused(
    value: int,
) -> None:
    with pytest.raises(PydanticValidationError):
        _state(local_danger_modifier=value)


def test_ownership_and_control_are_different_fields() -> None:
    """Owned by the Kingdom of Aster, held by the Northern Rebellion."""
    owner, controller = uuid.uuid4(), uuid.uuid4()
    state = _state(owner_entity_id=owner, controller_entity_id=controller)
    assert state.owner_entity_id == owner
    assert state.controller_entity_id == controller


@pytest.mark.parametrize(
    ("accessibility", "passable"),
    [
        (LocationAccessibility.OPEN, True),
        # Restricted is a gate with a guard, not a wall: whether *this* character may
        # pass is the future requirement system's question.
        (LocationAccessibility.RESTRICTED, True),
        (LocationAccessibility.CLOSED, False),
        (LocationAccessibility.BLOCKED, False),
        (LocationAccessibility.SEALED, False),
        (LocationAccessibility.INACCESSIBLE, False),
    ],
)
def test_what_counts_as_passable(accessibility: LocationAccessibility, passable: bool) -> None:
    assert is_traversable(accessibility) is passable
    state = LocationConnectionState(
        id=uuid.uuid4(),
        session_id=SESSION_ID,
        connection_id=uuid.uuid4(),
        accessibility=accessibility,
        created_at=NOW,
        updated_at=NOW,
    )
    assert state.is_passable is passable


# -- zones --------------------------------------------------------------------


def test_a_zone_belongs_to_a_location_and_carries_no_state() -> None:
    location_id = uuid.uuid4()
    zone = LocationZone(
        id=uuid.uuid4(),
        location_id=location_id,
        name="the fireplace",
        category="seating",
        created_at=NOW,
        updated_at=NOW,
    )
    assert zone.location_id == location_id
    # Deliberately absent: a zone with condition and accessibility would be a location
    # with extra steps, and every session would need a row for the hearth.
    for absent in ("condition", "accessibility", "parent_location_id", "scale"):
        assert absent not in LocationZone.model_fields


# -- creation policy ----------------------------------------------------------


@pytest.mark.parametrize(
    ("scale", "policy"),
    [
        (LocationScale.POINT, CreationPolicy.PERMISSIVE),
        (LocationScale.ROOM, CreationPolicy.PERMISSIVE),
        (LocationScale.BUILDING, CreationPolicy.PERMISSIVE),
        (LocationScale.SITE, CreationPolicy.PERMISSIVE),
        (LocationScale.DISTRICT, CreationPolicy.GUARDED),
        (LocationScale.SETTLEMENT, CreationPolicy.GUARDED),
        (LocationScale.REGIONAL, CreationPolicy.GUARDED),
        (LocationScale.CONTINENTAL, CreationPolicy.GUARDED),
        (LocationScale.WORLD, CreationPolicy.GUARDED),
    ],
)
def test_where_the_creation_line_is_drawn(scale: LocationScale, policy: CreationPolicy) -> None:
    assert creation_policy_for(scale) is policy


@pytest.mark.parametrize(
    "scale", [LocationScale.POINT, LocationScale.ROOM, LocationScale.BUILDING, LocationScale.SITE]
)
def test_narration_may_establish_something_small(scale: LocationScale) -> None:
    check_creation_allowed(scale, importance=2, narrated=True, name="Starfall Books")


@pytest.mark.parametrize(
    "scale",
    [LocationScale.DISTRICT, LocationScale.SETTLEMENT, LocationScale.CONTINENTAL],
)
def test_narration_may_not_establish_geography(scale: LocationScale) -> None:
    with pytest.raises(ValidationError, match="narration may not create"):
        check_creation_allowed(scale, importance=2, narrated=True, name="The Northern Reach")


def test_an_author_may_write_a_continent() -> None:
    """The policy gates narration, not authoring. A world is allowed to have one."""
    check_creation_allowed(
        LocationScale.CONTINENTAL, importance=5, narrated=False, name="The Northern Reach"
    )


def test_narration_may_not_smuggle_a_landmark_in_at_a_small_scale() -> None:
    """Scale is what the model chose; importance is what it claims the place means."""
    with pytest.raises(ValidationError, match="importance"):
        check_creation_allowed(
            LocationScale.POINT, importance=5, narrated=True, name="The Tomb of the First King"
        )


# -- spatial mutations --------------------------------------------------------


def test_a_spatial_mutation_must_change_something() -> None:
    """An empty mutation would move the state revision without moving the world."""
    with pytest.raises(PydanticValidationError):
        UpdateLocationState(location_id=uuid.uuid4())
    with pytest.raises(PydanticValidationError):
        UpdateConnectionState(connection_id=uuid.uuid4())


def test_clearing_an_owner_and_setting_one_are_contradictory() -> None:
    with pytest.raises(PydanticValidationError):
        UpdateLocationState(
            location_id=uuid.uuid4(), owner_entity_id=uuid.uuid4(), clear_owner=True
        )


def test_clearing_an_owner_on_its_own_is_a_change() -> None:
    mutation = UpdateLocationState(location_id=uuid.uuid4(), clear_owner=True)
    assert mutation.clear_owner


def test_spatial_mutations_identify_what_they_touch() -> None:
    location_id, connection_id = uuid.uuid4(), uuid.uuid4()
    assert UpdateLocationState(
        location_id=location_id, condition=LocationCondition.RUINED
    ).target() == ("location_state", str(location_id))
    assert UpdateConnectionState(
        connection_id=connection_id, accessibility=LocationAccessibility.BLOCKED
    ).target() == ("connection_state", str(connection_id))
