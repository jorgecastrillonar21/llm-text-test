"""Which of a session's ongoing processes a scene actually needs to know about.

A session accumulates situations. A prompt does not grow. This is the deterministic
function between them, and the whole of its job is deciding what to leave out.

# The priority order

    1  here          centred on the scene's location, or on a place containing it
    2  involving     a character in the scene is a participant
    3  regional      important enough that the region would be talking about it
    4  global        world-scale and critical

Within each band: importance, then threat, then most recently progressed, then title.
Total, so two reads of an unchanged session agree -- this feeds a prompt, and a list
that reshuffles between turns is a prompt that will not cache and a diff nobody can
read.

# Concluded processes do not reach the prompt

A resolved siege is history, and history is what the transcript and the memories are
for. Sending every situation a session ever ran would spend the budget on things that
have stopped happening. `include_resolved` exists for developer tooling and is never
true on the turn path.

# Hidden situations, and the gap that is not closed

A conspiracy exists objectively whether or not anyone has noticed it. There is no
`KnowledgeState` yet, so this system cannot ask "does the player know?" -- which means
the honest options were to send every live situation and hope, or to hold back the ones
a world has explicitly marked secret. This does the second: a `secret` tag keeps a
situation out of the player-facing context.

That is a *convention*, not a mechanism, and it should be read as one. It protects
against the obvious leak -- an assassination plot narrated into the open the turn it
begins -- and protects against nothing else. Real perception, real discovery and real
per-character knowledge are `KnowledgeState`'s, and until it exists this file is where
the limitation lives. See docs/world-state-situations.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from app.application.persistence import SituationReaderPort
from app.application.situation_service import MAX_CONTEXT_SITUATIONS
from app.application.story_context import SituationContext, SituationsContext
from app.domain.world_locations import LocationDefinition, LocationIndex, get_ancestors
from app.domain.world_situations import (
    LIVE_STATUSES,
    Situation,
    SituationScope,
    SituationStatus,
)

MAX_SITUATIONS_IN_CONTEXT = 6
"""How many ongoing processes reach the prompt.

Six is already a lot for one scene to hold. A session with more than six live processes
is not a session where the director should be told about all of them -- it is one where
the four that matter here should crowd out the rest, which is what the ordering does.
"""

SECRET_TAG = "secret"
"""The tag that keeps a situation out of player-facing context. See the module
docstring for why this is a convention rather than a knowledge model."""

REGIONAL_IMPORTANCE = 3
"""How important a regional process must be before a scene hears about it. Below this
it is happening somewhere else to someone else."""

GLOBAL_IMPORTANCE = 4
"""And how important a world-scale one must be. Higher, because "somewhere in the
world" is further away than "in this region"."""


async def build_situations_context(
    reader: SituationReaderPort,
    *,
    session_id: uuid.UUID,
    elapsed_minutes: int,
    location_index: LocationIndex | None = None,
    current_location: LocationDefinition | None = None,
    present_character_ids: Iterable[uuid.UUID] = (),
) -> SituationsContext | None:
    """The situations block for a turn, or None when nothing is under way.

    None rather than an empty block, for the reason `SpatialContext` is: an empty
    heading tells a model the game tracks ongoing processes and has none, which reads
    worse than saying nothing at all.

    `location_index` and `current_location` are optional because a session may have no
    geography. Without them the location band is empty and selection falls through to
    participants, region and world -- which is correct, not degraded: a scene that does
    not know where it is cannot know what is happening there.
    """
    live = await reader.load_situations(
        session_id, statuses=LIVE_STATUSES, limit=MAX_CONTEXT_SITUATIONS
    )
    if not live:
        return None

    here = _place_ids(location_index, current_location)
    participating = await _participant_situation_ids(
        reader, session_id=session_id, character_ids=present_character_ids
    )

    selected = select_relevant(
        live,
        here=here,
        involving=participating,
        limit=MAX_SITUATIONS_IN_CONTEXT,
    )
    if not selected:
        return None

    return SituationsContext(
        ongoing=[_to_context(situation, elapsed_minutes=elapsed_minutes) for situation in selected]
    )


def select_relevant(
    situations: list[Situation],
    *,
    here: frozenset[uuid.UUID],
    involving: frozenset[uuid.UUID],
    limit: int,
    include_resolved: bool = False,
) -> list[Situation]:
    """Rank and truncate. Pure, so the whole policy is testable without a database.

    `here` is the scene's location *and everything containing it*: a siege of the city
    is happening in the tavern, and a model told only about the tavern would write a
    quiet evening inside a starving city.
    """
    ranked: list[tuple[int, int, int, int, str, Situation]] = []
    for situation in situations:
        if not include_resolved and situation.status in {
            SituationStatus.RESOLVED,
            SituationStatus.CANCELLED,
        }:
            continue
        if SECRET_TAG in situation.tags:
            continue

        band = _band(situation, here=here, involving=involving)
        if band is None:
            continue

        ranked.append(
            (
                band,
                -situation.importance,
                -situation.threat,
                -situation.last_progressed_at,
                situation.title.casefold(),
                situation,
            )
        )

    ranked.sort(key=lambda entry: entry[:5])
    return [entry[5] for entry in ranked][:limit]


def _band(
    situation: Situation, *, here: frozenset[uuid.UUID], involving: frozenset[uuid.UUID]
) -> int | None:
    """Which priority band a situation falls into, or None to leave it out entirely.

    Returning None is the important half. A minor local process happening three regions
    away is not low priority -- it is irrelevant, and giving it a rank would let it
    displace something that matters on a quiet turn.
    """
    if situation.primary_location_id is not None and situation.primary_location_id in here:
        return 0
    if situation.id in involving:
        return 1
    if situation.scope is SituationScope.REGIONAL and situation.importance >= REGIONAL_IMPORTANCE:
        return 2
    if situation.scope is SituationScope.GLOBAL and situation.importance >= GLOBAL_IMPORTANCE:
        return 3
    if situation.scope is SituationScope.ENTITY_SPECIFIC and situation.id in involving:
        # Already covered by band 1; stated so a reader does not assume entity-specific
        # situations reach a scene on scope alone. A manhunt for someone who is not here
        # is not happening here.
        return 1
    return None


def _place_ids(
    index: LocationIndex | None, current: LocationDefinition | None
) -> frozenset[uuid.UUID]:
    """The scene's location and every place that contains it.

    Containment only. A situation in the *next* room is not happening here, and
    conflating adjacency with location is how a fire in the cellar becomes a fire in
    the bar.
    """
    if index is None or current is None:
        return frozenset()
    return frozenset({current.id} | {ancestor.id for ancestor in get_ancestors(index, current.id)})


async def _participant_situation_ids(
    reader: SituationReaderPort, *, session_id: uuid.UUID, character_ids: Iterable[uuid.UUID]
) -> frozenset[uuid.UUID]:
    """Ids of live situations any of these characters is taking part in.

    One query per character, which is bounded by `CHARACTER_LIMIT` in the context
    builder and is why that cap exists. A batched port method would be the fix if the
    character limit ever grew.
    """
    found: set[uuid.UUID] = set()
    for character_id in character_ids:
        involved = await reader.load_situations_for_entity(
            session_id,
            entity_id=character_id,
            statuses=LIVE_STATUSES,
            limit=MAX_CONTEXT_SITUATIONS,
        )
        found.update(situation.id for situation in involved)
    return frozenset(found)


def _to_context(situation: Situation, *, elapsed_minutes: int) -> SituationContext:
    """One process as prose sees it.

    No id, like `PlaceContext` and `FactContext`: the director reads what is going on,
    it does not address it. The numbers *are* sent -- unlike a location's default
    condition, there is no uninteresting value for intensity, and a director that knows
    a siege is at 78 writes a different scene from one that knows it is at 20.

    `duration` is rendered here and stored nowhere. "3 days" is what shapes a sentence;
    the authoritative minute counts stay in the database, where arithmetic belongs.
    """
    return SituationContext(
        title=situation.title,
        kind=(situation.subtype or situation.category.value).replace("_", " "),
        status=situation.status,
        intensity=situation.intensity,
        threat=situation.threat,
        momentum=situation.momentum,
        scope=situation.scope,
        duration=_humanise(situation.duration_at(elapsed_minutes)),
    )


def _humanise(minutes: int) -> str:
    """A duration a sentence could use. Authoritative minutes stay in the database."""
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 24 * 60:
        hours = minutes // 60
        return f"{hours} hour{'' if hours == 1 else 's'}"
    days = minutes // (24 * 60)
    return f"{days} day{'' if days == 1 else 's'}"
