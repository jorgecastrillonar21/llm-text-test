"""What is worth remembering, and how loudly.

A `GameEvent` is significant persistent world history. It is not a log. The difference
is not a matter of taste -- it decides whether "what has happened in this story?" is
answerable in two years of play, or is a million rows of `PLAYER_MOVED_2_METERS` with
the coronation somewhere inside it.

# The rule

Persist a GameEvent only when knowing that it happened can plausibly affect future
simulation, explanation, narrative continuity, causal reasoning or long-term context.

    Did something durable change?
    Can another entity react to it later?
    Does it explain why the world is currently like this?
    Is it needed for causal history?
    Could it plausibly matter much later?

If every answer is no, there is no event. There is still a `ResolutionRecord` -- the
mechanical trail is complete whether or not the world's history records anything.

# Why a policy object rather than a judgement call

Because two of the three writers are not people. The Story Director proposes events and
will happily mark a spilled drink importance 5; a future simulation pass will produce
them in bulk. A registry lets the application clamp both against something written
down, and lets a test assert that it does.

# The registry is a seed, not a taxonomy

Registering every subtype the game will ever have is the enum mistake wearing a
dictionary. Unknown subtypes get `DEFAULT_POLICY`, which keeps them as history and
caps their importance below landmark: an unregistered event may be remembered, but it
may not declare itself one of the pillars of the campaign.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.resolution.enums import EventPersistence

MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 5

LANDMARK_IMPORTANCE = 5
"""What a LANDMARK event is worth. Not a floor a policy may raise -- landmarks are the
top of the scale by definition, and a landmark at importance 3 would sort below an
ordinary death."""


class EventPolicy(BaseModel):
    """How one kind of event is to be treated before it reaches the history table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    persistence: EventPersistence

    default_importance: int = Field(default=2, ge=MIN_IMPORTANCE, le=MAX_IMPORTANCE)
    """Used when the proposer offered no opinion, which is the common case for the
    engine's own events."""

    minimum_importance: int = Field(default=MIN_IMPORTANCE, ge=MIN_IMPORTANCE, le=MAX_IMPORTANCE)
    maximum_importance: int = Field(default=MAX_IMPORTANCE, ge=MIN_IMPORTANCE, le=MAX_IMPORTANCE)
    """The band a proposed importance is clamped into. Both ends matter: the ceiling
    stops a model inflating a bar brawl to world-historic, and the floor stops one
    filing a king's death at importance 1 and burying it."""

    dedupe_window_minutes: int | None = Field(default=None, ge=1)
    """Collapse repeats of this subtype within this many fictional minutes.

    Opt-in per subtype, never global. A universal deduplicator would eventually eat two
    genuinely different deaths that shared a minute; this only ever applies where
    somebody decided repetition is noise. Nothing in the engine sets it yet -- the
    mechanism is here because the first system that emits `guard_alerted` three times
    in one scuffle should not have to invent it.
    """

    @model_validator(mode="after")
    def _band_is_coherent(self) -> Self:
        if self.minimum_importance > self.maximum_importance:
            raise ValueError(
                f"minimum_importance ({self.minimum_importance}) cannot exceed "
                f"maximum_importance ({self.maximum_importance})."
            )
        if not self.minimum_importance <= self.default_importance <= self.maximum_importance:
            raise ValueError(
                f"default_importance ({self.default_importance}) must fall within "
                f"{self.minimum_importance}..{self.maximum_importance}."
            )
        if self.persistence is EventPersistence.LANDMARK and self.default_importance != (
            LANDMARK_IMPORTANCE
        ):
            raise ValueError(
                "A landmark policy must default to importance "
                f"{LANDMARK_IMPORTANCE}. Landmarks are the top of the scale; one filed "
                "lower would sort below ordinary history and stop being a landmark."
            )
        return self

    def importance_for(self, proposed: int | None) -> int:
        """The importance this event will actually be stored with.

        Clamped rather than refused. A proposal outside the band is a proposer with an
        inflated sense of the moment, not a malformed request, and refusing the whole
        event would lose the fact that something happened over a disagreement about how
        much it mattered.
        """
        if proposed is None:
            return self.default_importance
        return max(self.minimum_importance, min(self.maximum_importance, proposed))

    @property
    def persists(self) -> bool:
        return self.persistence is not EventPersistence.NONE


DEFAULT_POLICY = EventPolicy(
    persistence=EventPersistence.HISTORY,
    default_importance=2,
    minimum_importance=MIN_IMPORTANCE,
    maximum_importance=3,
)
"""What an unregistered subtype gets.

Conservative in both directions. It keeps the event -- losing history because nobody
wrote a policy line would make the registry a tax on recording anything new -- and it
caps importance at 3, below the band where selection treats an event as defining the
story. Declaring something a landmark is a decision somebody makes on purpose.
"""


_POLICIES: dict[str, EventPolicy] = {
    # -- the engine's own -------------------------------------------------------
    #
    # `time_advanced` and `situation_progressed` are NONE, and that is the whole
    # argument of this module in two lines. Both used to be written as GameEvents
    # because there was nowhere else to record them. There is now: a ResolutionRecord
    # carries the source, the resolver, the revision either side and the fictional
    # instant, which is everything "why did the clock jump?" ever needed. Keeping them
    # here would mean a session's history is mostly the engine talking to itself.
    "time_advanced": EventPolicy(persistence=EventPersistence.NONE),
    "situation_progressed": EventPolicy(persistence=EventPersistence.NONE),
    # One row per session, and it answers a question nothing else can: why these
    # things were already true before anyone played. Importance 1 keeps it out of
    # every retrieval that matters.
    "world_state_seeded": EventPolicy(
        persistence=EventPersistence.HISTORY,
        default_importance=1,
        maximum_importance=1,
    ),
    # -- the shapes worth stating up front --------------------------------------
    #
    # A short list on purpose. These are the ones whose *policy* is the interesting
    # part: a death must not be filed as trivia, a landmark death must not be filed as
    # a death, and an opened door must not be filed at all.
    "character_died": EventPolicy(
        persistence=EventPersistence.HISTORY,
        default_importance=4,
        minimum_importance=3,
    ),
    "major_character_died": EventPolicy(
        persistence=EventPersistence.LANDMARK,
        default_importance=LANDMARK_IMPORTANCE,
        minimum_importance=LANDMARK_IMPORTANCE,
    ),
    "secret_discovered": EventPolicy(
        persistence=EventPersistence.HISTORY,
        default_importance=3,
    ),
    "door_opened": EventPolicy(persistence=EventPersistence.NONE),
}


def policy_for(subtype: str) -> EventPolicy:
    """The policy governing a subtype, or the conservative default."""
    return _POLICIES.get(subtype, DEFAULT_POLICY)


def is_registered(subtype: str) -> bool:
    """Whether somebody wrote a policy for this subtype, as opposed to inheriting one.

    Exposed so callers and tests can tell "policy says importance 3" from "nobody has
    thought about this yet and the default said 3".
    """
    return subtype in _POLICIES


def register_event_policy(subtype: str, policy: EventPolicy) -> None:
    """Declare how a subtype is to be treated.

    A plain dict write, called at import time by whichever module owns the subtype --
    the same shape as the situation resolver registry, and for the same reason. A
    plugin framework here would buy discovery nobody needs and cost the ability to read
    the whole policy set by opening one file.
    """
    _POLICIES[subtype] = policy


def known_policies() -> dict[str, EventPolicy]:
    """A copy, for diagnostics and tests. Never the registry itself."""
    return dict(_POLICIES)
