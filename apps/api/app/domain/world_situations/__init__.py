"""What the world is currently doing: ongoing processes.

The fourth piece of `WorldState`, after `world_time`, `world_facts` and
`world_locations`. Time says when, facts say what is so, locations say where, and this
says what is *underway* -- a siege, a fire, a festival, an investigation, a
reconstruction.

    enums         category, status, scope, participant type, progression trigger
    lifecycle     which status may follow which, and why terminal is terminal
    situations    Situation and SituationParticipant -- the process and who is in it
    hierarchy     causal parentage, and the rule that keeps it acyclic
    progression   the interval question, and the arithmetic of moving three numbers
    mutations     typed lifecycle changes, batched atomically with facts and space

# The distinction this package exists to keep

    WorldFact       what is objectively true now
    Situation       what ongoing process exists now
    GameEvent       what happened
    ScheduledEvent  what is expected to be processed later

`Eastern gate = destroyed` is a fact about a place. `Eastern gate breached` is an event.
`Siege of Asterfall` is the process that produced both, and `evaluate the siege again
in six hours` is a note for later. Four models, four questions, no collapsing.

# What a Situation is not

Not player knowledge, not rumour, not memory, not prose. A conspiracy exists here
whether anyone has noticed it. Who knows about it is `KnowledgeState`'s question and
`KnowledgeState` does not exist yet -- see docs/world-state-situations.md for what that
currently costs and how the prompt boundary works around it.

Not a simulation, either. This package models the *representation* and *lifecycle* of
ongoing processes and provides one generic progression boundary. Fire spread, warfare,
epidemiology, economics and politics are all deliberately absent.
"""

from __future__ import annotations

from app.domain.world_situations.enums import (
    LIVE_STATUSES,
    PROGRESSING_STATUSES,
    TERMINAL_STATUSES,
    ParticipantEntityType,
    ProgressionTrigger,
    SituationCategory,
    SituationScope,
    SituationStatus,
)
from app.domain.world_situations.hierarchy import (
    MAX_SITUATION_DEPTH,
    SituationIndex,
    check_parent_situation,
    get_ancestors,
    get_children,
    get_descendants,
)
from app.domain.world_situations.lifecycle import can_transition, is_terminal, require_transition
from app.domain.world_situations.mutations import (
    MAX_PARTICIPANTS_PER_SITUATION,
    ParticipantSpec,
    ResolveSituation,
    SituationMutation,
    StartSituation,
    UpdateSituation,
)
from app.domain.world_situations.progression import (
    SituationDeltas,
    SituationProgressionRequest,
    apply_deltas,
    momentum_drift,
    next_status_after,
)
from app.domain.world_situations.situations import (
    INTENSITY_MAX,
    INTENSITY_MIN,
    MAX_DESCRIPTION_LENGTH,
    MAX_ROLE_LENGTH,
    MAX_TITLE_LENGTH,
    MOMENTUM_MAX,
    MOMENTUM_MIN,
    THREAT_MAX,
    THREAT_MIN,
    Importance,
    Intensity,
    Momentum,
    Situation,
    SituationParticipant,
    Threat,
    clamp_intensity,
    clamp_momentum,
    clamp_threat,
)

__all__ = [
    "INTENSITY_MAX",
    "INTENSITY_MIN",
    "LIVE_STATUSES",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_PARTICIPANTS_PER_SITUATION",
    "MAX_ROLE_LENGTH",
    "MAX_SITUATION_DEPTH",
    "MAX_TITLE_LENGTH",
    "MOMENTUM_MAX",
    "MOMENTUM_MIN",
    "PROGRESSING_STATUSES",
    "TERMINAL_STATUSES",
    "THREAT_MAX",
    "THREAT_MIN",
    "Importance",
    "Intensity",
    "Momentum",
    "ParticipantEntityType",
    "ParticipantSpec",
    "ProgressionTrigger",
    "ResolveSituation",
    "Situation",
    "SituationCategory",
    "SituationDeltas",
    "SituationIndex",
    "SituationMutation",
    "SituationParticipant",
    "SituationProgressionRequest",
    "SituationScope",
    "SituationStatus",
    "StartSituation",
    "Threat",
    "UpdateSituation",
    "apply_deltas",
    "can_transition",
    "check_parent_situation",
    "clamp_intensity",
    "clamp_momentum",
    "clamp_threat",
    "get_ancestors",
    "get_children",
    "get_descendants",
    "is_terminal",
    "momentum_drift",
    "next_status_after",
    "require_transition",
]
