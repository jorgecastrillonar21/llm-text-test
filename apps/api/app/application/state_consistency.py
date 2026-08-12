"""Checking that one session's world still hangs together.

# Why this exists

WorldState is deliberately not one document. It is a root on `game_sessions` and six
tables that reference it, each with its own lifecycle -- which is what makes it
queryable, and also what makes it possible for one part to end up pointing at
something another part no longer has. A foreign key catches most of that. It does not
catch a `LocationState` for a place this session cannot see, a fact about a character
id nobody ever wrote, or a situation centred on a location from another world.

So this is a **diagnostic**, not a gate. Nothing in the turn loop calls it, and
nothing waits on its answer: state changes are already validated on the way in, by the
domain that owns them. This is for the case where something got in anyway -- a bad
migration, a hand-edited row, a bug in a mutation handler -- and a person needs to know
which part of the world is lying before they can decide what to do about it.

# What it checks

The eight referential invariants that hold across the whole decomposition today:

    root                    the session exists, at a version this build reads, with a
                            revision and a clock reading that are not negative
    fact_ownership          every fact belongs to this session
    fact_subjects           location and character subjects point at things that exist
    location_states         every state is for a location this session can see
    connection_states       every state is for a connection this session can see
    situation_locations     centres and parents point at real places and real siblings
    situation_participants  character participants are characters that exist
    scheduled_events        every commitment belongs to this session

# What it deliberately does not check

Faction and "other" references, because there is no faction table yet and inventing
one check that always passes would be worse than none. Anything about characters
beyond existence, because CharacterState does not exist. Anything about economies,
knowledge, reputation or inventories, for the same reason. Cross-domain *semantic*
invariants -- whether a siege makes sense in a world at peace -- are not referential
integrity and are not this function's business.

The report says which checks ran, so a caller can tell "clean" from "not looked at".

# Bounded, like everything else

Every read has a ceiling and `truncated` says when one was hit. A partial check that
found nothing is not a clean bill of health, and the report refuses to be read as one.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, computed_field

from app.application.persistence import WorldStateReaderPort
from app.application.spatial_service import MAX_GRAPH_SIZE, load_spatial_graph
from app.domain.errors import NotFoundError
from app.domain.world_facts import FactSubjectType
from app.domain.world_situations import ParticipantEntityType
from app.domain.world_state import SUPPORTED_WORLD_STATE_VERSIONS

logger = logging.getLogger(__name__)

CHECK_FACT_LIMIT = 2000
"""How many facts one check will read. Higher than a snapshot's ceiling on purpose:
this is the pass that is supposed to look at everything, and a session with two
thousand established truths is already the interesting case."""

CHECK_SITUATION_LIMIT = 500
"""How many situations one check will read, concluded ones included -- a dangling
reference on a finished siege is still a dangling reference."""

CHECK_SCHEDULED_EVENT_LIMIT = 1000
"""How many scheduled events one check will read, every status included."""


class ConsistencyCheck(StrEnum):
    """The invariants this validator knows how to test.

    Named rather than numbered so a report stays readable when the list grows, and so a
    caller can tell which check produced an issue without parsing its message.
    """

    ROOT = "root"
    FACT_OWNERSHIP = "fact_ownership"
    FACT_SUBJECTS = "fact_subjects"
    LOCATION_STATES = "location_states"
    CONNECTION_STATES = "connection_states"
    SITUATION_LOCATIONS = "situation_locations"
    SITUATION_PARTICIPANTS = "situation_participants"
    SCHEDULED_EVENTS = "scheduled_events"


class ConsistencyIssue(BaseModel):
    """One thing that does not add up.

    Carries the check that found it and the row that is wrong, because the useful
    question after "is it consistent?" is always "which row do I look at".
    """

    model_config = ConfigDict(frozen=True)

    check: ConsistencyCheck
    entity: str
    """What kind of row this is: `WorldFact`, `LocationState`, `Situation`. A string
    rather than an enum because it names a table, and this module is not the place that
    should have to enumerate them."""

    entity_id: uuid.UUID | None
    detail: str


class ConsistencyReport(BaseModel):
    """What the check found, and what it looked at.

    `checks` is as load-bearing as `issues`. An empty issue list from a run that
    skipped half the world means nothing, and the only way to tell the difference is to
    say what ran.
    """

    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    revision: int
    checks: list[ConsistencyCheck]
    issues: list[ConsistencyIssue]

    truncated: bool = False
    """True when a read hit its ceiling. The part beyond it was not examined, so a
    clean report with this set says "nothing wrong in what I saw", not "nothing wrong"."""

    @computed_field  # type: ignore[prop-decorator]  # pydantic needs the property last
    @property
    def consistent(self) -> bool:
        """No issues found. Read together with `truncated`, always."""
        return not self.issues


async def check_state_consistency(
    reader: WorldStateReaderPort, *, session_id: uuid.UUID
) -> ConsistencyReport:
    """Run every referential check over one session's world.

    Reads only. Raises `NotFoundError` when there is no such session -- a report about
    a world that does not exist would be a report of nothing wrong, which is the one
    answer this must never give by accident.

    A session stamped with an unreadable version is reported as an issue rather than
    raised, unlike everywhere else. This is the diagnostic: refusing to describe the
    thing somebody is trying to diagnose would defeat the purpose.
    """
    session = await reader.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    issues: list[ConsistencyIssue] = []
    truncated = False

    # --- root -------------------------------------------------------------
    if session.world_state_version not in SUPPORTED_WORLD_STATE_VERSIONS:
        issues.append(
            ConsistencyIssue(
                check=ConsistencyCheck.ROOT,
                entity="GameSession",
                entity_id=session.id,
                detail=(
                    f"world_state_version {session.world_state_version} is not one this "
                    f"build can read {SUPPORTED_WORLD_STATE_VERSIONS}."
                ),
            )
        )
    if session.state_revision < 0:
        issues.append(
            ConsistencyIssue(
                check=ConsistencyCheck.ROOT,
                entity="GameSession",
                entity_id=session.id,
                detail=f"state_revision {session.state_revision} is negative.",
            )
        )
    if session.elapsed_minutes < 0:
        issues.append(
            ConsistencyIssue(
                check=ConsistencyCheck.ROOT,
                entity="GameSession",
                entity_id=session.id,
                detail=f"elapsed_minutes {session.elapsed_minutes} is negative.",
            )
        )

    world = await reader.get_world(session.world_id)
    if world is None:  # pragma: no cover - the FK makes this unreachable
        issues.append(
            ConsistencyIssue(
                check=ConsistencyCheck.ROOT,
                entity="GameSession",
                entity_id=session.id,
                detail=f"world {session.world_id} does not exist.",
            )
        )
        return ConsistencyReport(
            session_id=session_id,
            revision=session.state_revision,
            checks=[ConsistencyCheck.ROOT],
            issues=issues,
        )

    graph = await load_spatial_graph(reader, session_id=session_id, world_id=world.id)
    truncated = truncated or len(graph.index) == MAX_GRAPH_SIZE
    characters = await reader.known_character_ids(world.id)

    # --- facts ------------------------------------------------------------
    facts = await reader.load_facts(session_id, limit=CHECK_FACT_LIMIT)
    truncated = truncated or len(facts) == CHECK_FACT_LIMIT
    for fact in facts:
        if fact.session_id != session_id:
            issues.append(
                ConsistencyIssue(
                    check=ConsistencyCheck.FACT_OWNERSHIP,
                    entity="WorldFact",
                    entity_id=fact.id,
                    detail=f"belongs to session {fact.session_id}, not {session_id}.",
                )
            )
        subject = fact.subject
        if subject.type is FactSubjectType.LOCATION and subject.id not in graph.index:
            issues.append(
                ConsistencyIssue(
                    check=ConsistencyCheck.FACT_SUBJECTS,
                    entity="WorldFact",
                    entity_id=fact.id,
                    detail=(
                        f"{fact.property!r} is about location {subject.id}, which this "
                        "session cannot see."
                    ),
                )
            )
        elif subject.type is FactSubjectType.CHARACTER and subject.id not in characters:
            issues.append(
                ConsistencyIssue(
                    check=ConsistencyCheck.FACT_SUBJECTS,
                    entity="WorldFact",
                    entity_id=fact.id,
                    detail=(
                        f"{fact.property!r} is about character {subject.id}, who does not "
                        "exist in this world."
                    ),
                )
            )

    # --- spatial state ----------------------------------------------------
    for location_id in graph.location_states:
        if location_id not in graph.index:
            issues.append(
                ConsistencyIssue(
                    check=ConsistencyCheck.LOCATION_STATES,
                    entity="LocationState",
                    entity_id=graph.location_states[location_id].id,
                    detail=(f"state for location {location_id}, which this session cannot see."),
                )
            )
    connection_ids = {connection.id for connection in graph.connections}
    for connection_id in graph.connection_states:
        if connection_id not in connection_ids:
            issues.append(
                ConsistencyIssue(
                    check=ConsistencyCheck.CONNECTION_STATES,
                    entity="LocationConnectionState",
                    entity_id=graph.connection_states[connection_id].id,
                    detail=(
                        f"state for connection {connection_id}, which this session cannot see."
                    ),
                )
            )

    # --- situations -------------------------------------------------------
    situations = await reader.load_situations(session_id, limit=CHECK_SITUATION_LIMIT)
    truncated = truncated or len(situations) == CHECK_SITUATION_LIMIT
    situation_ids = {situation.id for situation in situations}
    for situation in situations:
        if (
            situation.primary_location_id is not None
            and situation.primary_location_id not in graph.index
        ):
            issues.append(
                ConsistencyIssue(
                    check=ConsistencyCheck.SITUATION_LOCATIONS,
                    entity="Situation",
                    entity_id=situation.id,
                    detail=(
                        f"{situation.title!r} is centred on location "
                        f"{situation.primary_location_id}, which this session cannot see."
                    ),
                )
            )
        if (
            situation.parent_situation_id is not None
            and situation.parent_situation_id not in situation_ids
        ):
            issues.append(
                ConsistencyIssue(
                    check=ConsistencyCheck.SITUATION_LOCATIONS,
                    entity="Situation",
                    entity_id=situation.id,
                    detail=(
                        f"{situation.title!r} hangs off situation "
                        f"{situation.parent_situation_id}, which is not in this session."
                    ),
                )
            )

    for participant in await reader.load_participants(sorted(situation_ids, key=str)):
        if (
            participant.entity_type is ParticipantEntityType.CHARACTER
            and participant.entity_id not in characters
        ):
            issues.append(
                ConsistencyIssue(
                    check=ConsistencyCheck.SITUATION_PARTICIPANTS,
                    entity="SituationParticipant",
                    entity_id=participant.id,
                    detail=(
                        f"character {participant.entity_id} takes part as "
                        f"{participant.role!r} but does not exist in this world."
                    ),
                )
            )

    # --- scheduled events -------------------------------------------------
    scheduled = await reader.load_scheduled_events(session_id, limit=CHECK_SCHEDULED_EVENT_LIMIT)
    truncated = truncated or len(scheduled) == CHECK_SCHEDULED_EVENT_LIMIT
    for event in scheduled:
        if event.session_id != session_id:
            issues.append(
                ConsistencyIssue(
                    check=ConsistencyCheck.SCHEDULED_EVENTS,
                    entity="ScheduledEvent",
                    entity_id=event.id,
                    detail=f"belongs to session {event.session_id}, not {session_id}.",
                )
            )

    if issues:
        # Warned once with a count rather than once per issue: the report is the detail,
        # and a corrupt session should not be able to fill a log by being very corrupt.
        logger.warning(
            "Session %s failed %d world-state consistency check(s).", session_id, len(issues)
        )

    return ConsistencyReport(
        session_id=session_id,
        revision=session.state_revision,
        checks=list(ConsistencyCheck),
        issues=issues,
        truncated=truncated,
    )
