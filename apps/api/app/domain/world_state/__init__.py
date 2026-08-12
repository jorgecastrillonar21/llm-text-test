"""WorldState: everything objectively true about one GameSession's mutable world.

This package holds the *root* of that state and nothing else. The state itself lives
in the domains that own it:

    world_time        what time it is                    (temporal truth)
    world_facts       what is declaratively true         (declarative truth)
    world_locations   what places are like, and reachable (spatial truth)
    world_situations  what is currently under way        (ongoing process)
    resolution        what happened, and why             (history and audit)

Those are separate on purpose -- separate indexes, separate lifecycles, separate
invariants -- and they belong to the same session's WorldState conceptually rather
than by being nested inside one document. `docs/world-state.md` is the canonical
description of that decomposition; this package is only the small root it hangs from.
"""

from __future__ import annotations

from app.domain.world_state.root import (
    SUPPORTED_WORLD_STATE_VERSIONS,
    WORLD_STATE_VERSION,
    StateRevision,
    WorldStateV1,
    read_world_state,
)

__all__ = [
    "SUPPORTED_WORLD_STATE_VERSIONS",
    "WORLD_STATE_VERSION",
    "StateRevision",
    "WorldStateV1",
    "read_world_state",
]
