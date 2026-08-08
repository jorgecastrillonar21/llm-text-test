"""Relationship axis rules.

Relationship values live on a bounded scale and are only ever moved by small
deltas, so a model that emits an extreme value cannot destroy a playthrough.
Clamping happens here, never in the AI adapter.
"""

from __future__ import annotations

from dataclasses import dataclass

AXIS_MIN = -100
AXIS_MAX = 100

# Per-turn movement allowed on a single axis. Keeps relationships gradual.
DELTA_MIN = -5
DELTA_MAX = 5

AXES = ("trust", "affection", "respect", "fear")


def clamp_value(value: int) -> int:
    """Clamp an absolute relationship value into [-100, 100]."""
    return max(AXIS_MIN, min(AXIS_MAX, value))


def clamp_delta(delta: int) -> int:
    """Clamp a single-turn delta into [-5, 5]."""
    return max(DELTA_MIN, min(DELTA_MAX, delta))


@dataclass(frozen=True, slots=True)
class RelationshipVector:
    trust: int = 0
    affection: int = 0
    respect: int = 0
    fear: int = 0

    def apply(
        self,
        *,
        trust_delta: int = 0,
        affection_delta: int = 0,
        respect_delta: int = 0,
        fear_delta: int = 0,
    ) -> RelationshipVector:
        """Apply clamped deltas to clamped values, returning a new vector."""
        return RelationshipVector(
            trust=clamp_value(self.trust + clamp_delta(trust_delta)),
            affection=clamp_value(self.affection + clamp_delta(affection_delta)),
            respect=clamp_value(self.respect + clamp_delta(respect_delta)),
            fear=clamp_value(self.fear + clamp_delta(fear_delta)),
        )
