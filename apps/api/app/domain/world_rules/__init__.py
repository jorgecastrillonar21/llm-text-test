"""WorldRules: how a universe works, as opposed to what is happening in it.

WorldRules is configuration, not state. A future `WorldState` holds what is currently
true -- the king is dead, the capital is besieged, it is winter -- and CharacterState
holds what is currently true of one character. Nothing dynamic belongs here.

    WorldRules      resurrection is impossible; violence is highly lethal
    WorldState      the king is dead; the capital is under siege
    CharacterState  Elena is injured; Kael is in the capital

Read `rules` for the document and the 0..100 scale, `presets` for the named starting
points, and `parsing` for the versioned loading boundary. See docs/world-rules.md.
"""

from __future__ import annotations

from app.domain.world_rules.parsing import (
    SUPPORTED_VERSIONS,
    WorldRules,
    default_world_rules,
    parse_world_rules,
)
from app.domain.world_rules.presets import WorldRulesPreset, build_preset
from app.domain.world_rules.rules import WORLD_RULES_VERSION, Setting, WorldRulesV1

__all__ = [
    "SUPPORTED_VERSIONS",
    "WORLD_RULES_VERSION",
    "Setting",
    "WorldRules",
    "WorldRulesPreset",
    "WorldRulesV1",
    "build_preset",
    "default_world_rules",
    "parse_world_rules",
]
