"""The closed vocabularies of an ongoing process.

Four small enums, and every one of them stays small. The open axis is `subtype`, a
free-form identifier -- `siege`, `bridge_reconstruction`, `murder_investigation` --
because no enum survives contact with fiction. What is closed is the handful of
distinctions the engine actually branches on.
"""

from __future__ import annotations

from enum import StrEnum


class SituationCategory(StrEnum):
    """What *kind* of process this is, at the coarsest useful grain.

    Ten entries, chosen so that a future resolver registry has somewhere to hang a
    default and a prompt has a word to lead with. The specific noun lives in `subtype`:
    `conflict/siege`, `hazard/fire`, `project/bridge_reconstruction`.

    CONFLICT       war, siege, raid, feud, blockade
    HAZARD         fire, flood, structural collapse, magical anomaly, epidemic
    SOCIAL         festival, funeral, migration, public celebration, panic
    POLITICAL      succession crisis, election, coup, treaty talks
    ECONOMIC       boom, famine, trade collapse, strike
    ENVIRONMENTAL  drought, storm season, blight, a changing landscape
    INVESTIGATION  manhunt, inquiry, murder investigation, search
    PROJECT        reconstruction, research, a campaign someone is running
    EVENT          a bounded scheduled happening -- tournament, summit, market day
    OTHER          the escape hatch, so a world is never blocked on this list
    """

    CONFLICT = "conflict"
    HAZARD = "hazard"
    SOCIAL = "social"
    POLITICAL = "political"
    ECONOMIC = "economic"
    ENVIRONMENTAL = "environmental"
    INVESTIGATION = "investigation"
    PROJECT = "project"
    EVENT = "event"
    OTHER = "other"


class SituationStatus(StrEnum):
    """Where a process is in its life.

    PLANNED    prepared or expected, not yet begun
    ACTIVE     progressing, or able to
    DORMANT    still real, currently going nowhere -- a cold war, a stalled inquiry,
               a contained anomaly. Not the same as resolved: dormant can wake.
    RESOLVED   reached a conclusion
    CANCELLED  prevented or abandoned before it could conclude

    Resolved and cancelled rows stay in the table. See `lifecycle.py`.
    """

    PLANNED = "planned"
    ACTIVE = "active"
    DORMANT = "dormant"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


LIVE_STATUSES = frozenset(
    {SituationStatus.PLANNED, SituationStatus.ACTIVE, SituationStatus.DORMANT}
)
"""Statuses a situation can still move on from.

The reason the entity is called `Situation` and not `ActiveSituation`: "active
situations" is a *query*, and this is the set it filters on. A table of only live rows
would have nowhere to put the siege that ended last week, and the history of a session
is most of what makes it a session.
"""

TERMINAL_STATUSES = frozenset({SituationStatus.RESOLVED, SituationStatus.CANCELLED})
"""Reached an end. A terminal situation carries `resolved_at` and stays queryable."""

PROGRESSING_STATUSES = frozenset({SituationStatus.ACTIVE})
"""What a progression pass will actually move.

Narrower than `LIVE_STATUSES` on purpose. A planned situation has not started and a
dormant one is defined by not progressing; both are still real, both still reach
context, and neither has its numbers moved by the passage of time alone. Waking a
dormant situation is a status change some system decides, not something a clock does.
"""


class SituationScope(StrEnum):
    """How wide the process reaches -- geographically, or around whoever it follows.

    LOCAL            one site or immediate area: a tavern fire, a street blockade
    REGIONAL         a settlement, region or kingdom: a siege, a provincial famine
    GLOBAL           world-scale: a continental war, a spreading plague
    ENTITY_SPECIFIC  centred on people rather than a map -- a manhunt for the player,
                     a feud between two houses. Deliberately not a fourth size: it is
                     a different *kind* of extent, and forcing it onto the size axis is
                     what makes a manhunt look like a local event happening in one room.
    """

    LOCAL = "local"
    REGIONAL = "regional"
    GLOBAL = "global"
    ENTITY_SPECIFIC = "entity_specific"


class ParticipantEntityType(StrEnum):
    """What sort of thing is taking part.

    CHARACTER  an existing character in this world
    FACTION    a group. No faction table exists yet; ids are accepted on trust and
               that is recorded here rather than quietly tolerated.
    OTHER      anything the game later learns to model -- a creature, an institution,
               a ship. The escape hatch, so a participant is never impossible to record.
    """

    CHARACTER = "character"
    FACTION = "faction"
    OTHER = "other"


class ProgressionTrigger(StrEnum):
    """Why a progression pass is being asked for.

    SCHEDULED   a ScheduledEvent came due
    EVENT       something else happened that this process reacts to
    EXPLICIT    a developer or admin asked directly
    SIMULATION  a future simulation pass swept it up

    Recorded on the request rather than inferred, because the same interval means
    different things depending on why it is being evaluated, and a resolver that has to
    guess would guess wrong on the case that matters.
    """

    SCHEDULED = "scheduled"
    EVENT = "event"
    EXPLICIT = "explicit"
    SIMULATION = "simulation"
