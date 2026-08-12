"""Closed vocabularies for WorldRules.

Every one of these is a *setting*, never a measurement of current state. They say
how the universe works, not what is happening in it right now.

Ordered enums declare their order explicitly through `ORDER` tuples rather than
relying on definition order, so comparisons stay meaningful if a member is ever
inserted in the middle.
"""

from __future__ import annotations

from enum import StrEnum


class Rarity(StrEnum):
    """How exceptional something is within this universe.

    Deliberately not a percentage. Turning rarity into a probability is a
    rules-resolution decision that depends on context, and baking one mapping in
    here would quietly make it authoritative everywhere.
    """

    IMPOSSIBLE = "impossible"
    LEGENDARY = "legendary"
    VERY_RARE = "very_rare"
    RARE = "rare"
    UNCOMMON = "uncommon"
    COMMON = "common"
    VERY_COMMON = "very_common"


class DeathFinality(StrEnum):
    PERMANENT = "permanent"
    RESURRECTION_POSSIBLE = "resurrection_possible"
    RESPAWN = "respawn"
    NARRATIVE_CHECKPOINT = "narrative_checkpoint"


class IncapacitationPolicy(StrEnum):
    """How often a fight stops at 'down' rather than continuing to 'dead'."""

    ALWAYS = "always"
    LIKELY = "likely"
    CONTEXTUAL = "contextual"
    RARE = "rare"
    NEVER = "never"


class PowerScale(StrEnum):
    MUNDANE = "mundane"
    ENHANCED = "enhanced"
    SUPERHUMAN = "superhuman"
    LEGENDARY = "legendary"
    COSMIC = "cosmic"


class PowerGapSignificance(StrEnum):
    """How much a difference in power decides an outcome.

    LOW      a large gap is often overcome by circumstance or creativity
    MEDIUM   gaps matter substantially but remain surmountable
    HIGH     closing a large gap needs strong tactical justification
    ABSOLUTE some gaps make direct confrontation effectively impossible
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ABSOLUTE = "absolute"


class PublicAwareness(StrEnum):
    UNKNOWN = "unknown"
    MYTH = "myth"
    HIDDEN = "hidden"
    LIMITED = "limited"
    COMMON = "common"
    UNIVERSAL = "universal"


class Regulation(StrEnum):
    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    STRICT = "strict"
    TOTALITARIAN = "totalitarian"


class Discoverability(StrEnum):
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ProgressionPace(StrEnum):
    VERY_SLOW = "very_slow"
    SLOW = "slow"
    MEDIUM = "medium"
    FAST = "fast"
    ANIME_FAST = "anime_fast"


class ProgressionCeiling(StrEnum):
    NONE = "none"
    SOFT = "soft"
    HARD = "hard"


class TrainingRequirement(StrEnum):
    NEVER = "never"
    OPTIONAL = "optional"
    CONTEXTUAL = "contextual"
    USUALLY = "usually"
    ALWAYS = "always"


class InformationSpread(StrEnum):
    """How fast news travels. Not how much anyone believes it."""

    VERY_SLOW = "very_slow"
    SLOW = "slow"
    MEDIUM = "medium"
    FAST = "fast"
    INSTANT = "instant"


class RecoverySpeed(StrEnum):
    VERY_SLOW = "very_slow"
    SLOW = "slow"
    MEDIUM = "medium"
    FAST = "fast"
    VERY_FAST = "very_fast"


class SimulationScope(StrEnum):
    """How wide a net offscreen simulation casts."""

    MINIMAL = "minimal"
    RELEVANT = "relevant"
    REGIONAL = "regional"
    GLOBAL = "global"


class TimeProgression(StrEnum):
    """PAUSED        time moves only through explicit scenario actions
    ACTION_BASED  player actions advance time by their own duration
    ACTIVE        the world is expected to move on its own
    """

    PAUSED = "paused"
    ACTION_BASED = "action_based"
    ACTIVE = "active"


class ChanceModel(StrEnum):
    """How mechanical uncertainty is produced -- never by the language model.

    SEEDED         reproducible from a recorded seed
    TRUE_RANDOM    unseeded
    DETERMINISTIC  no randomness; outcomes follow from state alone
    """

    SEEDED = "seeded"
    TRUE_RANDOM = "true_random"
    DETERMINISTIC = "deterministic"


class ChanceTransparency(StrEnum):
    HIDDEN = "hidden"
    PARTIAL = "partial"
    VISIBLE = "visible"


class RulesEnforcement(StrEnum):
    """Which authority wins when narrative appeal and established rules disagree.

    CINEMATIC      genre logic and dramatic plausibility dominate
    FLEXIBLE       rules matter, but clever plausible exceptions are common
    STRICT         established world rules dominate outcomes
    SIMULATIONIST  physical and contextual detail is expected to matter heavily

    This is not a difficulty setting and not a tone setting.
    """

    CINEMATIC = "cinematic"
    FLEXIBLE = "flexible"
    STRICT = "strict"
    SIMULATIONIST = "simulationist"


class ContentIntensity(StrEnum):
    """How graphic *description* gets. Independent of how dangerous the world is."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"


class RomancePolicy(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"


class SexualContentPolicy(StrEnum):
    NONE = "none"
    FADE_TO_BLACK = "fade_to_black"
    EXPLICIT = "explicit"


class SubstanceUsePolicy(StrEnum):
    NONE = "none"
    CONTEXTUAL = "contextual"
    FREQUENT = "frequent"


# Explicit orderings for the enums that have a natural low-to-high axis. Tests and
# the AI-facing summary compare against these instead of assuming member order.
RARITY_ORDER: tuple[Rarity, ...] = (
    Rarity.IMPOSSIBLE,
    Rarity.LEGENDARY,
    Rarity.VERY_RARE,
    Rarity.RARE,
    Rarity.UNCOMMON,
    Rarity.COMMON,
    Rarity.VERY_COMMON,
)

POWER_GAP_ORDER: tuple[PowerGapSignificance, ...] = (
    PowerGapSignificance.LOW,
    PowerGapSignificance.MEDIUM,
    PowerGapSignificance.HIGH,
    PowerGapSignificance.ABSOLUTE,
)

PROGRESSION_PACE_ORDER: tuple[ProgressionPace, ...] = (
    ProgressionPace.VERY_SLOW,
    ProgressionPace.SLOW,
    ProgressionPace.MEDIUM,
    ProgressionPace.FAST,
    ProgressionPace.ANIME_FAST,
)

CONTENT_INTENSITY_ORDER: tuple[ContentIntensity, ...] = (
    ContentIntensity.NONE,
    ContentIntensity.LOW,
    ContentIntensity.MEDIUM,
    ContentIntensity.HIGH,
    ContentIntensity.EXTREME,
)


def rank(value: object, order: tuple[object, ...]) -> int:
    """Position of `value` within `order`, for comparing two settings.

    Raises rather than returning a sentinel: a missing member means the ordering
    tuple fell out of sync with its enum, which is a bug worth failing on.
    """
    try:
        return order.index(value)
    except ValueError as exc:  # pragma: no cover - guards a coding error
        raise ValueError(f"{value!r} is missing from its ordering tuple") from exc
