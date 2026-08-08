"""Relationship clamping: the guard that stops a model wrecking a save."""

from __future__ import annotations

import pytest

from app.domain.relationships import (
    AXIS_MAX,
    AXIS_MIN,
    DELTA_MAX,
    DELTA_MIN,
    RelationshipVector,
    clamp_delta,
    clamp_value,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, 0), (50, 50), (100, 100), (101, AXIS_MAX), (10_000, AXIS_MAX), (-101, AXIS_MIN)],
)
def test_clamp_value_bounds_axis(raw: int, expected: int) -> None:
    assert clamp_value(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [(0, 0), (3, 3), (5, 5), (6, DELTA_MAX), (99, DELTA_MAX), (-99, DELTA_MIN)]
)
def test_clamp_delta_bounds_single_turn_movement(raw: int, expected: int) -> None:
    assert clamp_delta(raw) == expected


def test_apply_clamps_delta_before_adding() -> None:
    vector = RelationshipVector(trust=0)
    # A +50 request may only move the axis by DELTA_MAX.
    assert vector.apply(trust_delta=50).trust == DELTA_MAX


def test_apply_cannot_exceed_axis_ceiling() -> None:
    vector = RelationshipVector(trust=98, affection=-98)
    result = vector.apply(trust_delta=5, affection_delta=-5)
    assert result.trust == AXIS_MAX
    assert result.affection == AXIS_MIN


def test_apply_is_immutable() -> None:
    original = RelationshipVector(trust=10)
    updated = original.apply(trust_delta=3)
    assert original.trust == 10
    assert updated.trust == 13


def test_apply_moves_each_axis_independently() -> None:
    result = RelationshipVector().apply(
        trust_delta=2, affection_delta=-1, respect_delta=4, fear_delta=0
    )
    assert (result.trust, result.affection, result.respect, result.fear) == (2, -1, 4, 0)
