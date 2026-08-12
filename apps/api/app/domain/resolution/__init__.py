"""How things actually happen: the authoritative resolution boundary.

Everything before this package described the world. This one describes the door
through which the world changes.

    commands    a validated structured request to attempt something
    outcomes    what a resolver decided, as a value, before anything is written
    events      what is worth remembering as history, and how loudly
    history     GameEvent -- immutable, ordered by fictional time
    records     Resolution -- the mechanical trail, one row per verdict

# The chain, and the words for each link

    player's sentence   what someone typed
    Intent              what they were trying to accomplish        (not built)
    Command             a validated request to attempt it
    Resolution          evaluating that request against the world
    Outcome             what the resolver calculated
    GameEvent           something that objectively happened
    StateMutation       an authoritative change to current state
    ScheduledEvent      something that has not happened yet
    Narration           prose describing outcomes already committed

None of those is a synonym for another. The distinction that costs the most to lose is
the last one: narration comes *after* the mechanics, describing them. A system that
infers mechanics from prose has made a language model the arbiter of who lives.

# This is not event sourcing

Current state lives in current tables -- `world_facts`, `location_states`,
`situations`. `game_events` is significant history alongside them, not a log the state
is rebuilt from. Nothing replays the stream, nothing may come to require replaying it,
and the day something does, every event ever written becomes load-bearing forever.
See docs/event-resolution.md.
"""

from __future__ import annotations

from app.domain.resolution.commands import (
    AdvanceTimeCommand,
    Command,
    ContextRequest,
    ProgressSituationCommand,
)
from app.domain.resolution.context import ResolutionContext
from app.domain.resolution.enums import (
    EventCategory,
    EventPersistence,
    ResolutionDisposition,
    ResolutionSourceType,
)
from app.domain.resolution.events import (
    DEFAULT_POLICY,
    LANDMARK_IMPORTANCE,
    MAX_IMPORTANCE,
    MIN_IMPORTANCE,
    EventPolicy,
    is_registered,
    known_policies,
    policy_for,
    register_event_policy,
)
from app.domain.resolution.history import GameEvent
from app.domain.resolution.outcomes import (
    MAX_EVENT_CANDIDATES,
    MAX_SCHEDULED_REQUESTS,
    EventCandidate,
    ResolutionOutcome,
    ScheduledEventRequest,
    no_effect,
    rejected,
)
from app.domain.resolution.records import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_RESOLVER_NAME_LENGTH,
    MAX_RESOLVER_VERSION_LENGTH,
    Resolution,
)

__all__ = [
    "DEFAULT_POLICY",
    "LANDMARK_IMPORTANCE",
    "MAX_EVENT_CANDIDATES",
    "MAX_IDEMPOTENCY_KEY_LENGTH",
    "MAX_IMPORTANCE",
    "MAX_RESOLVER_NAME_LENGTH",
    "MAX_RESOLVER_VERSION_LENGTH",
    "MAX_SCHEDULED_REQUESTS",
    "MIN_IMPORTANCE",
    "AdvanceTimeCommand",
    "Command",
    "ContextRequest",
    "EventCandidate",
    "EventCategory",
    "EventPersistence",
    "EventPolicy",
    "GameEvent",
    "ProgressSituationCommand",
    "Resolution",
    "ResolutionContext",
    "ResolutionDisposition",
    "ResolutionOutcome",
    "ResolutionSourceType",
    "ScheduledEventRequest",
    "is_registered",
    "known_policies",
    "no_effect",
    "policy_for",
    "register_event_policy",
    "rejected",
]
