"""The closed vocabularies of the resolution boundary.

Four enums, and the reason each one is closed is the same: the engine branches on it.
Everything open-ended -- what kind of event this was, why a request was refused, what a
resolver is called -- is a string, because no enum survives contact with fiction.
"""

from __future__ import annotations

from enum import StrEnum


class ResolutionSourceType(StrEnum):
    """What asked for this resolution.

    PLAYER_ACTION         something the player submitted
    SCHEDULED_EVENT       a ScheduledEvent came due
    SITUATION_PROGRESSION an ongoing process was evaluated
    NPC_ACTION            a character acting on its own goals
    FACTION_ACTION        a faction acting on its own goals
    WORLD_SIMULATION      an autonomous world system
    SYSTEM                the engine's own housekeeping
    ADMIN                 developer tooling

    Most of these have no producer yet, and they are listed anyway. The whole point of
    this boundary is that a player's sword swing and a besieging army's next move go
    through the same mechanics -- so the vocabulary that distinguishes them exists
    before the systems do, rather than being retrofitted around whichever one shipped
    first.
    """

    PLAYER_ACTION = "player_action"
    SCHEDULED_EVENT = "scheduled_event"
    SITUATION_PROGRESSION = "situation_progression"
    NPC_ACTION = "npc_action"
    FACTION_ACTION = "faction_action"
    WORLD_SIMULATION = "world_simulation"
    SYSTEM = "system"
    ADMIN = "admin"


class ResolutionDisposition(StrEnum):
    """What became of the request -- not whether the actor succeeded.

    APPLIED    the request was legitimate and was resolved
    REJECTED   the request could not legitimately be attempted at all
    NO_EFFECT  the request was legitimate and changed nothing meaningful

    The distinction this enum exists to protect is APPLIED vs REJECTED, and it is not
    success vs failure. A player who attempts a dangerous jump and falls has an APPLIED
    resolution whose mechanical outcome was failure: the jump happened, the world
    changed, there are events. A player who tries to teleport with no such capability
    has a REJECTED resolution: no teleport was attempted, nothing happened, and there is
    nothing to narrate as an event.

    Collapsing those into `success`/`failure` is what makes an engine narrate a failed
    teleport, and then a world where teleportation is apparently a thing people try.

    `success`, `partial_success` and `critical_success` are deliberately absent. They
    are meaningful *within* a domain that has degrees of success -- a skill check, an
    attack roll -- and meaningless for "the fire spread" or "the shop closed". When
    those domains arrive they get their own outcome vocabulary, carried inside the
    resolution rather than replacing this one.
    """

    APPLIED = "applied"
    REJECTED = "rejected"
    NO_EFFECT = "no_effect"


class EventCategory(StrEnum):
    """The coarse kind of a historical event.

    Twelve entries, chosen so retrieval can filter and a prompt has a word to lead
    with. The specific noun lives in the open `subtype`: `spatial/bridge_collapsed`,
    `character/character_died`, `situation/siege_broken`.

    A hard-coded enum of every event type the game will ever have is the thing this
    split exists to avoid -- it would need a new deploy to record that a bridge fell.
    """

    ACTION = "action"
    COMBAT = "combat"
    MOVEMENT = "movement"
    SOCIAL = "social"
    WORLD = "world"
    SITUATION = "situation"
    SPATIAL = "spatial"
    CHARACTER = "character"
    POLITICAL = "political"
    ECONOMIC = "economic"
    SYSTEM = "system"
    OTHER = "other"


class EventPersistence(StrEnum):
    """Whether something is worth keeping as world history, and how heavily.

    NONE      not a GameEvent. It may still have a ResolutionRecord and mutations.
    HISTORY   significant world history: keep it, retrieve it when relevant.
    LANDMARK  history that keeps mattering: retrieve it long after it happened.

    There is deliberately no AUDIT tier. Mechanical audit is what `resolutions` is
    for, and an audit tier here would immediately become the place every state change
    is logged -- which is precisely how a history table stops being history.
    """

    NONE = "none"
    HISTORY = "history"
    LANDMARK = "landmark"
