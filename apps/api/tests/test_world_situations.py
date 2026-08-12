"""The situation domain: bounds, lifecycle, hierarchy, mutations and the arithmetic.

Pure domain. No database, no application layer, no ports -- every invariant here has
to hold for a `Situation` built in memory, which is what makes them invariants rather
than habits of one code path.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import ValidationError
from app.domain.situation_progression import GeneratedEvent, SituationProgressionResult
from app.domain.world_situations import (
    LIVE_STATUSES,
    MAX_PARTICIPANTS_PER_SITUATION,
    TERMINAL_STATUSES,
    ParticipantEntityType,
    ParticipantSpec,
    ProgressionTrigger,
    ResolveSituation,
    Situation,
    SituationCategory,
    SituationDeltas,
    SituationIndex,
    SituationParticipant,
    SituationProgressionRequest,
    SituationScope,
    SituationStatus,
    StartSituation,
    UpdateSituation,
    apply_deltas,
    can_transition,
    check_parent_situation,
    clamp_intensity,
    clamp_momentum,
    clamp_threat,
    get_ancestors,
    get_children,
    get_descendants,
    is_terminal,
    momentum_drift,
    next_status_after,
    require_transition,
)

SESSION = uuid.uuid4()
OTHER_SESSION = uuid.uuid4()
NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def make_situation(**overrides: object) -> Situation:
    data: dict[str, object] = {
        "id": uuid.uuid4(),
        "session_id": SESSION,
        "category": SituationCategory.CONFLICT,
        "subtype": "siege",
        "title": "Siege of Asterfall",
        "status": SituationStatus.ACTIVE,
        "intensity": 50,
        "threat": 60,
        "momentum": 10,
        "importance": 4,
        "scope": SituationScope.REGIONAL,
        "started_at": 0,
        "last_progressed_at": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return Situation(**data)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [-1, 101])
def test_intensity_outside_its_range_is_refused(value: int) -> None:
    with pytest.raises(PydanticValidationError):
        make_situation(intensity=value)


@pytest.mark.parametrize("value", [-1, 101])
def test_threat_outside_its_range_is_refused(value: int) -> None:
    with pytest.raises(PydanticValidationError):
        make_situation(threat=value)


@pytest.mark.parametrize("value", [-101, 101])
def test_momentum_outside_its_range_is_refused(value: int) -> None:
    with pytest.raises(PydanticValidationError):
        make_situation(momentum=value)


@pytest.mark.parametrize("value", [0, 6])
def test_importance_outside_its_range_is_refused(value: int) -> None:
    with pytest.raises(PydanticValidationError):
        make_situation(importance=value)


def test_momentum_may_be_negative_and_intensity_may_not() -> None:
    """The asymmetry is the model, not an oversight.

    Intensity is a magnitude: nothing manifests less than not at all. Momentum is a
    direction, and half of its range is a process winding down -- which is the half that
    lets a fire brigade exist.
    """
    assert make_situation(momentum=-100).momentum == -100
    with pytest.raises(PydanticValidationError):
        make_situation(intensity=-1)


def test_an_invalid_category_is_refused() -> None:
    with pytest.raises(PydanticValidationError):
        make_situation(category="riot")


def test_an_invalid_status_is_refused() -> None:
    with pytest.raises(PydanticValidationError):
        make_situation(status="smouldering")


def test_a_subtype_is_normalised_to_an_identifier() -> None:
    assert make_situation(subtype="Murder Investigation").subtype == "murder_investigation"
    assert make_situation(subtype="bridge-reconstruction").subtype == "bridge_reconstruction"


def test_a_subtype_that_is_not_an_identifier_is_refused() -> None:
    """The vocabulary is open; the shape is not. `siege!` would be a fourth spelling of
    a thing the engine groups by."""
    with pytest.raises(ValidationError):
        make_situation(subtype="siege!")


def test_progress_before_the_start_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="does not run backwards"):
        make_situation(started_at=100, last_progressed_at=50)


def test_resolution_before_the_start_is_refused() -> None:
    with pytest.raises(PydanticValidationError):
        make_situation(
            started_at=100,
            last_progressed_at=100,
            status=SituationStatus.RESOLVED,
            resolved_at=50,
        )


def test_a_terminal_status_without_an_ending_time_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="has to say when it concluded"):
        make_situation(status=SituationStatus.RESOLVED, resolved_at=None)


def test_a_live_status_with_an_ending_time_is_refused() -> None:
    """The worse half of the pair: a row that reads as over to anything filtering on the
    timestamp and as ongoing to anything filtering on the status."""
    with pytest.raises(PydanticValidationError, match="Only resolved and cancelled"):
        make_situation(status=SituationStatus.ACTIVE, resolved_at=10)


def test_a_situation_cannot_be_its_own_cause() -> None:
    identity = uuid.uuid4()
    with pytest.raises(PydanticValidationError, match="cannot be its own parent"):
        make_situation(id=identity, parent_situation_id=identity)


def test_metadata_must_stay_flat_and_scalar() -> None:
    with pytest.raises(ValidationError, match="own model"):
        make_situation(situation_metadata={"stage": {"name": "three"}})


def test_a_tag_never_replaces_a_field() -> None:
    """Not enforceable in code, so this documents the shape instead: tags are strings
    with no ordering, which is why `dangerous` cannot stand in for `threat`."""
    situation = make_situation(tags=("Military", "urgent", "military"))
    assert situation.tags == ("military", "urgent")


def test_duration_stops_growing_once_a_situation_ends() -> None:
    ongoing = make_situation(started_at=100, last_progressed_at=100)
    assert ongoing.duration_at(1_000) == 900

    ended = make_situation(
        started_at=100,
        last_progressed_at=400,
        status=SituationStatus.RESOLVED,
        resolved_at=400,
    )
    assert ended.duration_at(1_000) == 300
    assert ended.duration_at(50_000) == 300


def test_a_participant_role_is_normalised() -> None:
    participant = SituationParticipant(
        id=uuid.uuid4(),
        situation_id=uuid.uuid4(),
        entity_type=ParticipantEntityType.CHARACTER,
        entity_id=uuid.uuid4(),
        role="Chief Investigator",
        created_at=NOW,
    )
    assert participant.role == "chief_investigator"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SituationStatus.PLANNED, SituationStatus.ACTIVE),
        (SituationStatus.PLANNED, SituationStatus.DORMANT),
        (SituationStatus.PLANNED, SituationStatus.CANCELLED),
        (SituationStatus.ACTIVE, SituationStatus.DORMANT),
        (SituationStatus.ACTIVE, SituationStatus.RESOLVED),
        (SituationStatus.ACTIVE, SituationStatus.CANCELLED),
        (SituationStatus.DORMANT, SituationStatus.ACTIVE),
        (SituationStatus.DORMANT, SituationStatus.RESOLVED),
    ],
)
def test_valid_transitions_are_allowed(current: SituationStatus, target: SituationStatus) -> None:
    assert can_transition(current, target)
    require_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SituationStatus.RESOLVED, SituationStatus.PLANNED),
        (SituationStatus.RESOLVED, SituationStatus.ACTIVE),
        (SituationStatus.CANCELLED, SituationStatus.ACTIVE),
        (SituationStatus.ACTIVE, SituationStatus.PLANNED),
        (SituationStatus.DORMANT, SituationStatus.PLANNED),
    ],
)
def test_invalid_transitions_are_refused(current: SituationStatus, target: SituationStatus) -> None:
    assert not can_transition(current, target)
    with pytest.raises(ValidationError):
        require_transition(current, target)


def test_re_opening_a_concluded_situation_says_what_to_do_instead() -> None:
    with pytest.raises(ValidationError, match="Start a new situation instead"):
        require_transition(SituationStatus.RESOLVED, SituationStatus.ACTIVE)


def test_staying_put_is_always_allowed() -> None:
    """An update that changes only intensity does not change status, and should not have
    to prove that `active -> active` is legal."""
    for status in SituationStatus:
        assert can_transition(status, status)


def test_dormant_is_live_and_resolved_is_not() -> None:
    assert SituationStatus.DORMANT in LIVE_STATUSES
    assert SituationStatus.RESOLVED in TERMINAL_STATUSES
    assert not is_terminal(SituationStatus.DORMANT)
    assert make_situation(status=SituationStatus.DORMANT).is_live


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


def test_children_and_ancestors_walk_the_causal_chain() -> None:
    war = make_situation(title="The war")
    siege = make_situation(title="Siege of Asterfall", parent_situation_id=war.id)
    hunger = make_situation(title="Food crisis", parent_situation_id=siege.id)
    index = SituationIndex([hunger, war, siege])

    assert [s.id for s in get_children(index, war.id)] == [siege.id]
    assert [s.id for s in get_ancestors(index, hunger.id)] == [siege.id, war.id]
    assert [s.id for s in get_descendants(index, war.id)] == [siege.id, hunger.id]


def test_a_two_node_cycle_is_refused() -> None:
    first = make_situation(title="A")
    second = make_situation(title="B", parent_situation_id=first.id)
    index = SituationIndex([first, second])

    with pytest.raises(ValidationError, match="would create a cycle"):
        check_parent_situation(index, child=first, parent_id=second.id)


def test_a_longer_cycle_is_refused() -> None:
    a = make_situation(title="A")
    b = make_situation(title="B", parent_situation_id=a.id)
    c = make_situation(title="C", parent_situation_id=b.id)
    index = SituationIndex([a, b, c])

    with pytest.raises(ValidationError, match="would create a cycle"):
        check_parent_situation(index, child=a, parent_id=c.id)


def test_self_parenting_is_refused_by_the_checker_too() -> None:
    situation = make_situation()
    index = SituationIndex([situation])
    with pytest.raises(ValidationError, match="cannot be its own cause"):
        check_parent_situation(index, child=situation, parent_id=situation.id)


def test_a_parent_in_another_session_is_refused() -> None:
    mine = make_situation(title="Mine")
    theirs = make_situation(title="Theirs", session_id=OTHER_SESSION)
    index = SituationIndex([mine, theirs])

    with pytest.raises(ValidationError, match="cannot be caused by one in another"):
        check_parent_situation(index, child=mine, parent_id=theirs.id)


def test_a_missing_parent_is_refused() -> None:
    situation = make_situation()
    index = SituationIndex([situation])
    with pytest.raises(Exception, match="Situation"):
        check_parent_situation(index, child=situation, parent_id=uuid.uuid4())


def test_no_parent_is_always_fine() -> None:
    situation = make_situation()
    check_parent_situation(SituationIndex([situation]), child=situation, parent_id=None)


def test_resolving_a_parent_does_not_touch_its_children() -> None:
    """The lifecycle-inheritance rule, stated as behaviour: there is no function that
    cascades a status, and the child is a separate object nothing here reaches."""
    war = make_situation(title="The war")
    siege = make_situation(title="Siege", parent_situation_id=war.id, status=SituationStatus.ACTIVE)
    index = SituationIndex([war, siege])

    ended = war.model_copy(update={"status": SituationStatus.RESOLVED, "resolved_at": 500})
    assert ended.status is SituationStatus.RESOLVED
    assert get_children(index, war.id)[0].status is SituationStatus.ACTIVE


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def test_start_situation_has_no_id_field() -> None:
    """The whole reason a model cannot overwrite a live process."""
    assert "id" not in StartSituation.model_fields
    assert "situation_id" not in StartSituation.model_fields


def test_a_situation_cannot_be_created_already_over() -> None:
    for status in (SituationStatus.RESOLVED, SituationStatus.CANCELLED):
        with pytest.raises(PydanticValidationError, match="already finished is a GameEvent"):
            StartSituation(category=SituationCategory.HAZARD, title="Done", status=status)


def test_the_same_entity_may_hold_two_roles_but_not_one_role_twice() -> None:
    entity = uuid.uuid4()
    StartSituation(
        category=SituationCategory.INVESTIGATION,
        title="The inquiry",
        participants=(
            ParticipantSpec(
                entity_type=ParticipantEntityType.CHARACTER, entity_id=entity, role="investigator"
            ),
            ParticipantSpec(
                entity_type=ParticipantEntityType.CHARACTER, entity_id=entity, role="target"
            ),
        ),
    )
    with pytest.raises(PydanticValidationError, match="listed twice"):
        StartSituation(
            category=SituationCategory.INVESTIGATION,
            title="The inquiry",
            participants=(
                ParticipantSpec(
                    entity_type=ParticipantEntityType.CHARACTER,
                    entity_id=entity,
                    role="investigator",
                ),
                ParticipantSpec(
                    entity_type=ParticipantEntityType.CHARACTER,
                    entity_id=entity,
                    role="Investigator",
                ),
            ),
        )


def test_too_many_participants_at_creation_is_refused() -> None:
    many = tuple(
        ParticipantSpec(
            entity_type=ParticipantEntityType.OTHER, entity_id=uuid.uuid4(), role="participant"
        )
        for _ in range(MAX_PARTICIPANTS_PER_SITUATION + 1)
    )
    with pytest.raises(PydanticValidationError, match="At most 12 participants"):
        StartSituation(category=SituationCategory.SOCIAL, title="Crowd", participants=many)


def test_an_update_that_changes_nothing_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="at least one thing"):
        UpdateSituation(situation_id=uuid.uuid4())


def test_an_update_cannot_end_a_situation() -> None:
    with pytest.raises(PydanticValidationError, match="Use ResolveSituation"):
        UpdateSituation(situation_id=uuid.uuid4(), resulting_status=SituationStatus.RESOLVED)


def test_resolving_requires_a_reason() -> None:
    with pytest.raises(PydanticValidationError, match="requires a reason"):
        ResolveSituation(situation_id=uuid.uuid4(), reason="   ")


def test_update_and_resolve_compete_for_the_same_target() -> None:
    """So one batch cannot both nudge a siege and lift it."""
    identity = uuid.uuid4()
    update = UpdateSituation(situation_id=identity, intensity_delta=5)
    resolve = ResolveSituation(situation_id=identity, reason="The walls held.")
    assert update.target() == resolve.target()


def test_two_starts_of_one_title_collide_and_two_titles_do_not() -> None:
    first = StartSituation(category=SituationCategory.HAZARD, title="Fire at the Crown")
    same = StartSituation(category=SituationCategory.HAZARD, title="  fire at the crown  ")
    other = StartSituation(category=SituationCategory.HAZARD, title="Fire on Market Street")
    assert first.target() == same.target()
    assert first.target() != other.target()


def test_situation_targets_cannot_collide_with_fact_or_spatial_targets() -> None:
    start = StartSituation(category=SituationCategory.EVENT, title="Tournament")
    update = UpdateSituation(situation_id=uuid.uuid4(), intensity_delta=1)
    assert start.target()[0] == "situation_start"
    assert update.target()[0] == "situation"


# ---------------------------------------------------------------------------
# Progression arithmetic
# ---------------------------------------------------------------------------


def test_a_backward_interval_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="cannot run backwards"):
        SituationProgressionRequest(
            situation_id=uuid.uuid4(),
            from_time=500,
            to_time=100,
            trigger=ProgressionTrigger.SCHEDULED,
        )


def test_a_zero_length_interval_is_allowed() -> None:
    """An event-triggered evaluation happens at an instant."""
    request = SituationProgressionRequest(
        situation_id=uuid.uuid4(), from_time=100, to_time=100, trigger=ProgressionTrigger.EVENT
    )
    assert request.elapsed_minutes == 0


@pytest.mark.parametrize(
    ("value", "clamped"),
    [(-40, 0), (0, 0), (100, 100), (250, 100)],
)
def test_intensity_and_threat_clamp_to_their_range(value: int, clamped: int) -> None:
    assert clamp_intensity(value) == clamped
    assert clamp_threat(value) == clamped


@pytest.mark.parametrize(("value", "clamped"), [(-250, -100), (-30, -30), (250, 100)])
def test_momentum_clamps_to_its_signed_range(value: int, clamped: int) -> None:
    assert clamp_momentum(value) == clamped


def test_deltas_are_applied_to_current_values_and_clamped() -> None:
    situation = make_situation(intensity=95, threat=5, momentum=90)
    intensity, threat, momentum = apply_deltas(
        situation,
        SituationDeltas(intensity_delta=40, threat_delta=-20, momentum_delta=50),
    )
    assert (intensity, threat, momentum) == (100, 0, 100)


def test_momentum_drift_is_symmetric() -> None:
    """A fire being contained and a festival winding down are the same arithmetic."""
    assert momentum_drift(50, 360, per_hour=10) == -momentum_drift(-50, 360, per_hour=10)


def test_a_process_with_no_momentum_never_drifts() -> None:
    assert momentum_drift(0, 60 * 24 * 365, per_hour=100) == 0


def test_drift_scales_with_elapsed_time() -> None:
    short = momentum_drift(60, 60, per_hour=20)
    long = momentum_drift(60, 600, per_hour=20)
    assert long > short > 0


def test_an_exhausted_active_process_goes_dormant_not_resolved() -> None:
    """Burning out is not concluding. Only something that knows what the process was
    for can say it is over."""
    assert next_status_after(SituationStatus.ACTIVE, 0) is SituationStatus.DORMANT
    assert next_status_after(SituationStatus.ACTIVE, 5) is None
    assert next_status_after(SituationStatus.DORMANT, 0) is None


# ---------------------------------------------------------------------------
# Progression result
# ---------------------------------------------------------------------------


def test_an_empty_result_is_a_noop() -> None:
    assert SituationProgressionResult(situation_id=uuid.uuid4()).is_noop()


def test_a_result_with_only_a_scheduled_time_is_still_a_noop() -> None:
    """Deciding when to look again is not a change to the world, and should not mint an
    event or move the revision."""
    result = SituationProgressionResult(situation_id=uuid.uuid4(), next_progression_at=500)
    assert result.is_noop()


def test_a_result_with_an_event_is_not_a_noop() -> None:
    result = SituationProgressionResult(
        situation_id=uuid.uuid4(),
        generated_events=(GeneratedEvent(type="EAST_GATE_BREACHED", description="It fell."),),
    )
    assert not result.is_noop()


def test_a_result_that_ends_a_situation_must_explain_itself() -> None:
    with pytest.raises(PydanticValidationError, match="must say why"):
        SituationProgressionResult(
            situation_id=uuid.uuid4(), status_change=SituationStatus.RESOLVED
        )


def test_a_result_may_not_start_an_unbounded_number_of_situations() -> None:
    starts = tuple(
        StartSituation(category=SituationCategory.OTHER, title=f"Thing {n}") for n in range(9)
    )
    with pytest.raises(PydanticValidationError, match="at most"):
        SituationProgressionResult(situation_id=uuid.uuid4(), new_situations=starts)
