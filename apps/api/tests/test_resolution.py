"""The resolution domain: what a verdict may claim, and what history keeps.

Pure. No database, no ports, no application layer -- every invariant here holds for a
`Resolution`, a `ResolutionOutcome` or an `EventPolicy` built in memory, which is what
makes them invariants rather than habits of one code path. The pipeline that produces
them is exercised against a real transaction in `test_resolution_service.py`.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import ValidationError
from app.domain.resolution import (
    DEFAULT_POLICY,
    LANDMARK_IMPORTANCE,
    MAX_IMPORTANCE,
    EventCandidate,
    EventCategory,
    EventPersistence,
    EventPolicy,
    GameEvent,
    Resolution,
    ResolutionDisposition,
    ResolutionOutcome,
    ResolutionSourceType,
    is_registered,
    known_policies,
    no_effect,
    policy_for,
    rejected,
)
from app.domain.world_facts import WORLD_SUBJECT, SetFact

SESSION = uuid.uuid4()
NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def make_resolution(**overrides: object) -> Resolution:
    data: dict[str, object] = {
        "id": uuid.uuid4(),
        "session_id": SESSION,
        "source_type": ResolutionSourceType.PLAYER_ACTION,
        "idempotency_key": "turn:abc",
        "disposition": ResolutionDisposition.APPLIED,
        "resolver_name": "advance_time",
        "resolver_version": "1",
        "state_revision_before": 0,
        "state_revision_after": 0,
        "occurred_at": 0,
        "event_count": 0,
        "mutation_count": 0,
        "created_at": NOW,
    }
    data.update(overrides)
    return Resolution(**data)  # type: ignore[arg-type]


def make_event(**overrides: object) -> GameEvent:
    data: dict[str, object] = {
        "id": uuid.uuid4(),
        "session_id": SESSION,
        "turn_index": 0,
        "category": EventCategory.WORLD,
        "subtype": "bridge_collapsed",
        "summary": "The bridge came down.",
        "occurred_at": 0,
        "sequence": 1,
        "importance": 3,
        "created_at": NOW,
    }
    data.update(overrides)
    return GameEvent(**data)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Dispositions
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_applied_rejected_no_effect_and_nothing_else() -> None:
    """`success` and `failure` are deliberately absent.

    A lock that was picked badly and a lock the world refuses to let anyone pick are
    different facts, and one boolean cannot hold both. The first is `applied` -- the
    attempt happened, and the world recorded what came of it. The second is `rejected`,
    which means nothing was attempted at all.
    """
    assert {member.value for member in ResolutionDisposition} == {
        "applied",
        "rejected",
        "no_effect",
    }


def test_a_failed_attempt_is_applied_and_a_refusal_is_rejected() -> None:
    """The distinction the whole audit trail rests on, stated as a test.

    Both of these are outcomes of "I pick the lock". Only one of them is the world
    saying no.
    """
    went_badly = ResolutionOutcome(
        disposition=ResolutionDisposition.APPLIED,
        events=(
            EventCandidate(
                category=EventCategory.ACTION,
                subtype="lockpick_snapped",
                summary="The pick snapped in the ward.",
            ),
        ),
    )
    refused = rejected("capability_missing", detail="Rin has never held a lockpick.")

    assert went_badly.disposition is ResolutionDisposition.APPLIED
    assert went_badly.has_effects
    assert refused.disposition is ResolutionDisposition.REJECTED
    assert not refused.has_effects


def test_no_effect_needs_no_reason_and_a_rejection_does() -> None:
    """Opening a door that is already open needs no explanation. Being told you may not
    open it does -- a refusal nobody can classify is a refusal nobody can act on."""
    assert no_effect().reason_code is None
    assert no_effect("already_open").reason_code == "already_open"
    with pytest.raises(ValidationError):
        rejected("   ")


# ---------------------------------------------------------------------------
# The record agrees with itself
# ---------------------------------------------------------------------------


def test_an_applied_resolution_may_move_the_revision_by_one() -> None:
    record = make_resolution(state_revision_before=4, state_revision_after=5, mutation_count=2)
    assert record.changed_state


@pytest.mark.parametrize(
    "disposition",
    [ResolutionDisposition.REJECTED, ResolutionDisposition.NO_EFFECT],
)
def test_a_resolution_that_applied_nothing_cannot_have_moved_the_revision(
    disposition: ResolutionDisposition,
) -> None:
    with pytest.raises(PydanticValidationError, match="nothing may have moved"):
        make_resolution(
            disposition=disposition,
            reason_code="already_open",
            state_revision_before=4,
            state_revision_after=5,
        )


@pytest.mark.parametrize(("events", "mutations"), [(1, 0), (0, 1)])
def test_a_resolution_that_applied_nothing_cannot_have_produced_anything(
    events: int, mutations: int
) -> None:
    with pytest.raises(PydanticValidationError, match="without something having been applied"):
        make_resolution(
            disposition=ResolutionDisposition.NO_EFFECT,
            event_count=events,
            mutation_count=mutations,
        )


def test_a_rejection_without_a_reason_code_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="must carry a reason code"):
        make_resolution(disposition=ResolutionDisposition.REJECTED, reason_code=None)


def test_the_state_revision_never_runs_backwards() -> None:
    with pytest.raises(PydanticValidationError, match="monotonic"):
        make_resolution(state_revision_before=5, state_revision_after=4)


def test_changed_state_reads_the_revision_rather_than_the_disposition() -> None:
    """An applied resolution that only wrote history did not change authoritative state.

    The two are separate on purpose: `game_events` is what happened, `world_facts` and
    friends are what is true, and a resolution can touch one without the other.
    """
    history_only = make_resolution(state_revision_before=7, state_revision_after=7, event_count=1)
    assert history_only.disposition is ResolutionDisposition.APPLIED
    assert not history_only.changed_state


def test_the_record_keeps_fictional_and_wall_clock_time_apart() -> None:
    """`occurred_at` is a session minute; `created_at` is when the row was written. A
    save restored on another machine has an honest `created_at` and the same story."""
    record = make_resolution(occurred_at=29022)
    assert record.occurred_at == 29022
    assert record.created_at == NOW


def test_a_resolution_names_the_formula_that_produced_it() -> None:
    """Audit, not dispatch. Nothing looks a resolver up by the recorded version -- it is
    there so an outcome recorded today survives the formula changing next year."""
    record = make_resolution(resolver_name="situation_progression", resolver_version="1")
    assert (record.resolver_name, record.resolver_version) == ("situation_progression", "1")


def test_a_resolution_can_name_the_resolution_it_belongs_to() -> None:
    """Nothing produces children yet; the field exists because compound actions are
    sub-resolutions under a parent, and retrofitting parentage means rewriting whatever
    was built assuming it did not exist."""
    parent = make_resolution()
    child = make_resolution(parent_resolution_id=parent.id)
    assert child.parent_resolution_id == parent.id


# ---------------------------------------------------------------------------
# What an outcome may carry
# ---------------------------------------------------------------------------


def test_a_refusal_cannot_carry_effects() -> None:
    with pytest.raises(PydanticValidationError, match="cannot carry effects"):
        ResolutionOutcome(
            disposition=ResolutionDisposition.REJECTED,
            reason_code="invalid_target",
            state_mutations=(SetFact(subject=WORLD_SUBJECT, property="world.war", value=True),),
        )


def test_changing_state_means_mutations_and_not_events() -> None:
    """What decides whether the session's state revision moves. A resolution that wrote
    three events and touched no fact left authoritative state exactly where it was."""
    history_only = ResolutionOutcome(
        disposition=ResolutionDisposition.APPLIED,
        events=(
            EventCandidate(
                category=EventCategory.WORLD,
                subtype="bridge_collapsed",
                summary="The bridge came down.",
            ),
        ),
    )
    assert history_only.has_effects
    assert not history_only.changes_state


def test_a_resolver_cannot_declare_who_invoked_it() -> None:
    """There is no `authority` on an outcome, and `extra="forbid"` is what enforces it.

    Authority is a property of the source that asked -- a player action, a simulation
    pass, an admin tool -- and a resolver that could claim to be the engine could write
    facts nothing else is allowed to write.
    """
    with pytest.raises(PydanticValidationError):
        ResolutionOutcome(
            disposition=ResolutionDisposition.NO_EFFECT,
            authority="engine",  # type: ignore[call-arg]
        )


def test_narrative_context_stays_flat_and_scalar() -> None:
    """It is handed to a narrator as authoritative detail. A nested object in there is a
    small schema nobody designed, and prose would start describing its shape."""
    with pytest.raises(ValidationError, match="own model"):
        ResolutionOutcome(
            disposition=ResolutionDisposition.NO_EFFECT,
            narrative_context={"siege": {"intensity": 40}},  # type: ignore[dict-item]
        )


# ---------------------------------------------------------------------------
# Event policy: what is worth remembering, and how loudly
# ---------------------------------------------------------------------------


def test_an_opened_door_is_not_history() -> None:
    assert policy_for("door_opened").persistence is EventPersistence.NONE
    assert not policy_for("door_opened").persists


def test_a_discovered_secret_is_history_without_being_a_landmark() -> None:
    policy = policy_for("secret_discovered")
    assert policy.persistence is EventPersistence.HISTORY
    assert policy.persists


def test_a_major_death_is_a_landmark_at_the_top_of_the_scale() -> None:
    policy = policy_for("major_character_died")
    assert policy.persistence is EventPersistence.LANDMARK
    assert policy.importance_for(None) == LANDMARK_IMPORTANCE
    # And it cannot be filed lower by whoever proposed it.
    assert policy.importance_for(1) == LANDMARK_IMPORTANCE


def test_an_inflated_importance_is_clamped_rather_than_refused() -> None:
    """The Story Director will mark a spilled drink importance 5. Clamping keeps the
    fact that something happened and discards only the opinion about how much it
    mattered; refusing the event would lose both."""
    assert policy_for("world_state_seeded").importance_for(MAX_IMPORTANCE) == 1
    assert DEFAULT_POLICY.importance_for(MAX_IMPORTANCE) == 3


def test_a_buried_importance_is_raised_to_the_policy_floor() -> None:
    """The other half, and the one that matters more: a proposer filing a death at
    importance 1 would bury it below every retrieval that reads history."""
    assert policy_for("character_died").importance_for(1) == 3


def test_an_unregistered_subtype_is_kept_but_cannot_declare_itself_a_landmark() -> None:
    """The registry is a seed, not a taxonomy. Losing history because nobody wrote a
    policy line would make the registry a tax on recording anything new."""
    assert not is_registered("east_gate_breached")
    policy = policy_for("east_gate_breached")
    assert policy is DEFAULT_POLICY
    assert policy.persists
    assert policy.importance_for(LANDMARK_IMPORTANCE) < LANDMARK_IMPORTANCE


def test_the_registry_holds_the_shapes_worth_stating_and_not_a_hundred_names() -> None:
    """An enum of every event the game will ever have is the mistake this replaces. The
    list is short because only the *policies* are interesting; the vocabulary is open."""
    assert len(known_policies()) < 20


def test_deduplication_is_opt_in_per_subtype_and_nothing_opts_in_yet() -> None:
    """A universal deduplicator would eventually eat two genuinely different deaths that
    shared a fictional minute."""
    assert DEFAULT_POLICY.dedupe_window_minutes is None
    assert all(policy.dedupe_window_minutes is None for policy in known_policies().values())


def test_a_landmark_policy_must_default_to_the_top_of_the_scale() -> None:
    with pytest.raises(PydanticValidationError, match="top of the scale"):
        EventPolicy(persistence=EventPersistence.LANDMARK, default_importance=3)


def test_a_policy_band_that_excludes_its_own_default_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="must fall within"):
        EventPolicy(
            persistence=EventPersistence.HISTORY,
            default_importance=5,
            maximum_importance=3,
        )


def test_a_policy_floor_above_its_ceiling_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="cannot exceed"):
        EventPolicy(
            persistence=EventPersistence.HISTORY,
            default_importance=2,
            minimum_importance=4,
            maximum_importance=2,
        )


# ---------------------------------------------------------------------------
# History: ordering and immutability
# ---------------------------------------------------------------------------


def test_events_order_by_fictional_minute_then_sequence() -> None:
    first = make_event(occurred_at=100, sequence=7)
    second = make_event(occurred_at=100, sequence=8)
    later = make_event(occurred_at=160, sequence=9)

    assert sorted([later, second, first], key=GameEvent.key) == [first, second, later]


def test_a_row_written_later_does_not_become_a_thing_that_happened_later() -> None:
    """The wall clock is not the story's clock. An event written second but placed
    earlier in fictional time sorts earlier, and `created_at` never breaks the tie."""
    earlier_minute = make_event(occurred_at=10, sequence=2, created_at=NOW + dt.timedelta(hours=1))
    later_minute = make_event(occurred_at=90, sequence=1, created_at=NOW)

    assert sorted([later_minute, earlier_minute], key=GameEvent.key) == [
        earlier_minute,
        later_minute,
    ]


def test_the_same_minute_is_the_normal_case_and_still_totally_ordered() -> None:
    """A resolution usually produces everything it produces in one fictional minute, so
    ties are the rule. The tiebreak is a counter rather than invented seconds -- a clock
    that only has minutes should not grow a seconds field to satisfy a sort."""
    events = [make_event(occurred_at=42, sequence=n) for n in (3, 1, 2)]
    assert [event.sequence for event in sorted(events, key=GameEvent.key)] == [1, 2, 3]


def test_an_event_cannot_be_edited_after_it_is_written() -> None:
    """A repair is a second event, not an edit to the first. A history you can rewrite
    is current state with extra steps."""
    event = make_event(importance=2)
    with pytest.raises(PydanticValidationError):
        event.importance = 5  # type: ignore[misc]
