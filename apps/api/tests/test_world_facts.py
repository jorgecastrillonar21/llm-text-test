"""The world_facts domain, with no database and no application services.

Everything here is a decision the domain makes on its own: what a fact may hold, who
may write one, and what a world's own rules refuse to allow regardless of who asks.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import (
    FactPolicyError,
    IncompatibleFactError,
    ValidationError,
)
from app.domain.world_facts import (
    WORLD_SUBJECT,
    FactAuthority,
    FactKind,
    FactPolicy,
    FactSubject,
    FactSubjectType,
    RemoveFact,
    SetFact,
    StateMutationBatch,
    WorldFact,
    check_fact_value,
    check_rules_compatibility,
    is_permitted,
    parse_property,
    require_no_conflicts,
    require_permitted,
    requires_source_event,
    resolve_policy,
)
from app.domain.world_rules import default_world_rules
from app.domain.world_rules.enums import DeathFinality, Rarity

SESSION_ID = uuid.uuid4()
CHARACTER_ID = uuid.uuid4()
CHARACTER = FactSubject(type=FactSubjectType.CHARACTER, id=CHARACTER_ID)


def _fact(**overrides: object) -> WorldFact:
    now = dt.datetime.now(dt.UTC)
    data: dict[str, object] = {
        "id": uuid.uuid4(),
        "session_id": SESSION_ID,
        "kind": FactKind.WORLD_TRUTH,
        "subject": CHARACTER,
        "property": "narrative.birthplace",
        "value": "Arven",
        "importance": 2,
        "current_value_since": 0,
        "authority": FactAuthority.SEED,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return WorldFact.model_validate(data)


# -- values -------------------------------------------------------------------


@pytest.mark.parametrize("value", [True, False])
def test_a_fact_can_hold_a_boolean(value: bool) -> None:
    assert _fact(property="system.alive", value=value).value is value


@pytest.mark.parametrize("value", [0, -3, 12.5, "a string", None, ["a", "b"], {"k": 1}])
def test_a_fact_can_hold_numbers_strings_null_and_flat_containers(value: object) -> None:
    assert _fact(value=value).value == value


@pytest.mark.parametrize("value", [{"nested": {"deeper": 1}}, [[1, 2]], object()])
def test_a_fact_refuses_a_value_it_could_not_round_trip_as_json(value: object) -> None:
    """The store holds properties, not aggregates. A nested document is the beginning
    of a WorldState object living inside one row."""
    with pytest.raises(ValidationError):
        check_fact_value(value)


def test_a_null_value_is_a_value(_unused: None = None) -> None:
    """`None` is a legitimate fact value -- "we established this has no answer" -- and
    is not the same as the property being absent. Absence is expressed by there being
    no fact at all; see the persistence tests."""
    assert check_fact_value(None) is None
    assert _fact(value=None).value is None


@pytest.mark.parametrize("importance", [1, 3, 5])
def test_importance_accepts_the_whole_scale(importance: int) -> None:
    assert _fact(importance=importance).importance == importance


@pytest.mark.parametrize("importance", [0, 6, -1])
def test_importance_outside_one_to_five_is_refused(importance: int) -> None:
    with pytest.raises(PydanticValidationError):
        _fact(importance=importance)


# -- subjects -----------------------------------------------------------------


def test_the_world_subject_carries_no_id() -> None:
    assert WORLD_SUBJECT.type is FactSubjectType.WORLD
    assert WORLD_SUBJECT.id is None
    assert WORLD_SUBJECT.key == "world"


def test_the_world_subject_refuses_an_id() -> None:
    """There is one world per session; an id would imply a choice of which."""
    with pytest.raises(PydanticValidationError):
        FactSubject(type=FactSubjectType.WORLD, id=uuid.uuid4())


@pytest.mark.parametrize(
    "subject_type",
    [
        FactSubjectType.CHARACTER,
        FactSubjectType.LOCATION,
        FactSubjectType.FACTION,
        FactSubjectType.OTHER,
    ],
)
def test_every_other_subject_requires_an_id(subject_type: FactSubjectType) -> None:
    with pytest.raises(PydanticValidationError):
        FactSubject(type=subject_type)


def test_a_subject_key_is_identity_not_a_display_name() -> None:
    assert CHARACTER.key == f"character:{CHARACTER_ID}"


# -- properties ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("narrative.birthplace", "narrative.birthplace"),
        ("  system.alive  ", "system.alive"),
        ("narrative.favourite_food", "narrative.favorite_food"),
        ("narrative.place_of_birth", "narrative.birthplace"),
        ("system.is_alive", "system.alive"),
    ],
)
def test_property_names_are_normalised_to_one_canonical_form(raw: str, canonical: str) -> None:
    assert parse_property(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "birthplace",  # no namespace
        "narrative.Birthplace",  # refused, not lowercased -- see properties.py
        "narrative.birthPlace",
        "narrative.birth place",
        "",
        "narrative.",
        "physics.gravity",  # a namespace nobody declared
    ],
)
def test_a_property_that_is_not_namespace_snake_case_is_refused(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_property(raw)


# -- policy -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("canonical", "policy"),
    [
        ("narrative.birthplace", FactPolicy.OPEN),
        ("narrative.something_nobody_registered", FactPolicy.OPEN),
        ("world.political_status", FactPolicy.GUARDED),
        ("world.something_nobody_registered", FactPolicy.GUARDED),
        ("system.alive", FactPolicy.SYSTEM),
        ("gameplay.anything_at_all", FactPolicy.SYSTEM),
        ("derived.is_night", FactPolicy.DERIVED),
    ],
)
def test_policy_falls_back_to_the_namespace_for_unregistered_properties(
    canonical: str, policy: FactPolicy
) -> None:
    """An unknown property is never OPEN by accident. `world.*` lands on GUARDED, and
    `system.*`/`gameplay.*` on SYSTEM, so inventing a name grants nothing."""
    assert resolve_policy(canonical) is policy


@pytest.mark.parametrize(
    ("authority", "policy", "allowed"),
    [
        (FactAuthority.STORY_DIRECTOR, FactPolicy.OPEN, True),
        (FactAuthority.STORY_DIRECTOR, FactPolicy.GUARDED, False),
        (FactAuthority.STORY_DIRECTOR, FactPolicy.SYSTEM, False),
        (FactAuthority.STORY_DIRECTOR, FactPolicy.DERIVED, False),
        (FactAuthority.ENGINE, FactPolicy.SYSTEM, True),
        (FactAuthority.SIMULATION, FactPolicy.GUARDED, True),
        (FactAuthority.PLAYER_RESOLUTION, FactPolicy.SYSTEM, True),
        (FactAuthority.SEED, FactPolicy.GUARDED, True),
        (FactAuthority.ADMIN, FactPolicy.SYSTEM, True),
        (FactAuthority.ADMIN, FactPolicy.DERIVED, False),
        (FactAuthority.ENGINE, FactPolicy.DERIVED, False),
    ],
)
def test_who_may_write_what(authority: FactAuthority, policy: FactPolicy, allowed: bool) -> None:
    """The Story Director reaches OPEN and nothing else, and nobody at all writes a
    DERIVED property -- those are computed, and a stored copy could only disagree."""
    assert is_permitted(authority, policy) is allowed


def test_the_story_director_cannot_write_a_system_fact() -> None:
    with pytest.raises(FactPolicyError) as caught:
        require_permitted(
            FactAuthority.STORY_DIRECTOR, FactPolicy.SYSTEM, canonical_property="system.alive"
        )
    assert "system.alive" in str(caught.value)


def test_a_mechanical_authority_must_name_the_event_that_caused_the_change() -> None:
    assert requires_source_event(FactAuthority.ENGINE)
    assert requires_source_event(FactAuthority.SIMULATION)
    assert requires_source_event(FactAuthority.PLAYER_RESOLUTION)
    # Seeding, admin repair and narration are the documented exceptions: the first two
    # are not consequences of anything in the story, and a turn is already its own event.
    assert not requires_source_event(FactAuthority.SEED)
    assert not requires_source_event(FactAuthority.ADMIN)
    assert not requires_source_event(FactAuthority.STORY_DIRECTOR)


# -- mutations ----------------------------------------------------------------


def test_a_batch_refuses_two_mutations_touching_one_fact() -> None:
    """Which one wins would be an ordering accident, so neither does."""
    with pytest.raises(PydanticValidationError):
        StateMutationBatch(
            authority=FactAuthority.ADMIN,
            mutations=[
                SetFact(subject=CHARACTER, property="narrative.birthplace", value="Arven"),
                SetFact(subject=CHARACTER, property="narrative.birthplace", value="Valeria"),
            ],
        )


def test_the_conflict_check_is_available_to_objects_that_skipped_validation() -> None:
    batch = StateMutationBatch.model_construct(
        authority=FactAuthority.ADMIN,
        mutations=[
            SetFact(subject=CHARACTER, property="narrative.birthplace", value="Arven"),
            RemoveFact(subject=CHARACTER, property="narrative.birthplace"),
        ],
        expected_revision=None,
    )
    with pytest.raises(ValidationError):
        require_no_conflicts(batch)


def test_a_mutation_has_no_previous_value_field() -> None:
    """Caller-supplied "what it used to be" would be a claim the store has to either
    trust or re-check, and re-checking makes it redundant. The store reads."""
    assert "previous_value" not in SetFact.model_fields
    assert "previous_value" not in RemoveFact.model_fields


def test_a_mutations_property_is_canonicalised_on_the_way_in() -> None:
    mutation = SetFact(subject=CHARACTER, property=" narrative.place_of_birth ", value="Arven")
    assert mutation.property == "narrative.birthplace"
    assert mutation.target() == (CHARACTER.key, "narrative.birthplace")


# -- world rules compatibility ------------------------------------------------


def test_the_supernatural_is_refused_in_a_world_that_has_none() -> None:
    base = default_world_rules()
    # Every sub-flag too: the rules document refuses to describe a world with no
    # supernatural that still has supernatural creatures in it.
    rules = base.model_copy(
        update={
            "supernatural": base.supernatural.model_copy(
                update={
                    "enabled": False,
                    "innate_powers_exist": False,
                    "learnable_powers_exist": False,
                    "supernatural_items_exist": False,
                    "supernatural_creatures_exist": False,
                }
            )
        }
    )

    with pytest.raises(IncompatibleFactError):
        check_rules_compatibility(
            rules,
            mutation=SetFact(
                subject=CHARACTER, property="narrative.supernatural_nature", value="revenant"
            ),
            current=None,
        )


def test_resurrection_is_refused_where_death_is_permanent() -> None:
    rules = default_world_rules()
    dead = _fact(property="system.alive", value=False)

    with pytest.raises(IncompatibleFactError):
        check_rules_compatibility(
            rules,
            mutation=SetFact(subject=CHARACTER, property="system.alive", value=True),
            current=dead,
        )


def test_resurrection_is_allowed_where_the_world_says_it_exists() -> None:
    rules = default_world_rules().model_copy(
        update={
            "mortality": default_world_rules().mortality.model_copy(
                update={
                    "death_finality": DeathFinality.RESURRECTION_POSSIBLE,
                    "resurrection_enabled": True,
                    "resurrection_rarity": Rarity.RARE,
                }
            )
        }
    )
    dead = _fact(property="system.alive", value=False)

    check_rules_compatibility(
        rules,
        mutation=SetFact(subject=CHARACTER, property="system.alive", value=True),
        current=dead,
    )


def test_killing_an_npc_is_refused_where_npcs_cannot_die() -> None:
    base = default_world_rules()
    rules = base.model_copy(
        update={"mortality": base.mortality.model_copy(update={"npc_death": False})}
    )
    alive = _fact(property="system.alive", value=True)

    with pytest.raises(IncompatibleFactError):
        check_rules_compatibility(
            rules,
            mutation=SetFact(subject=CHARACTER, property="system.alive", value=False),
            current=alive,
        )


def test_withdrawing_a_fact_claims_nothing_and_so_cannot_be_incompatible() -> None:
    """RemoveFact says "this is no longer established", not "the opposite is true",
    so there is no truth for the rules to object to."""
    rules = default_world_rules()
    check_rules_compatibility(
        rules,
        mutation=RemoveFact(subject=CHARACTER, property="narrative.supernatural_nature"),
        current=_fact(property="narrative.supernatural_nature", value="revenant"),
    )
