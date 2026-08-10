"""What a scene needs to know about where it is happening.

The spatial graph is a graph. A prompt is a page. This is the deterministic function
between them, and the whole of its job is deciding what to leave out.

# Tiers, not distance

    CURRENT     where the scene is
    ADJACENT    one connection away, and what is directly inside
    LOCAL       the containing place and its notable contents
    REGIONAL    ancestors, so the scene knows what world it is in
    GLOBAL      deliberately not sent

`ADJACENT` means "one edge away", not "nearby". That is the whole reason proximity is
absent from this system: two characters in one city are not near each other, and a
model handed a list labelled "nearby" would write as though they were. See
docs/world-state-locations.md.

# Deterministic, and no language model anywhere

Selection is ordering and set membership. Given a session and a location the answer is
the same every time, which is what makes it testable, cacheable and safe to put in a
prompt that a model will then be held to.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.application.persistence import SpatialReaderPort
from app.application.spatial_service import SpatialGraph, load_spatial_graph
from app.application.story_context import (
    ConnectedPlaceContext,
    PlaceContext,
    SpatialContext,
)
from app.domain.errors import NotFoundError
from app.domain.world_locations import (
    LocationConnection,
    LocationDefinition,
    LocationState,
    get_ancestors,
    get_children,
)

MAX_ADJACENT = 8
"""How many exits reach the prompt. A crossroads has four; a city square with eight
named streets is already more than a scene can use."""

MAX_CHILDREN = 8
MAX_ZONES = 6
MAX_ANCESTORS = 4
"""Enough for `World > Region > City > District`. Beyond that the outer containers stop
shaping a scene and start being trivia."""


def resolve_scene_location(graph: SpatialGraph, current_location: str) -> LocationDefinition | None:
    """Match a session's free-text location to a place in the graph, or give up.

    # This is a bridge, and it is meant to be removed

    There is no canonical position in this system yet. `GameSession.current_location`
    is a string the player typed when the session began, and `CharacterState` --
    which will own where anyone actually is -- does not exist. Until it does, this is
    the only way the scene knows which place it is in.

    Matching is exact, case-insensitive, whitespace-trimmed, and **refuses ambiguity**:
    two locations named "Market Street" resolve to nothing rather than to whichever the
    query returned first. No fuzzy matching, no substring search, no "closest name" --
    those turn a missing match into a *wrong* one, and a wrong match hands the director
    the exits of somewhere else entirely.

    Names are not identity anywhere else in this system, and they are not identity
    here either: nothing is stored from this, nothing is written by it, and a failed
    match costs the prompt one optional section. When `CharacterPosition` arrives it
    supplies a real id and this function is deleted.
    """
    wanted = current_location.strip().casefold()
    if not wanted:
        return None

    matches = [
        definition for definition in graph.index if definition.name.strip().casefold() == wanted
    ]
    if len(matches) != 1:
        return None
    return matches[0]


class ScenePlacement(BaseModel):
    """Where a turn is happening, and the graph it was resolved against.

    Both, because two callers want different halves and neither should load the graph
    twice: the spatial block needs the graph to walk, and situation relevance needs the
    current place and its containers to decide what is happening *here*. Four queries
    per turn, not eight.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    graph: SpatialGraph
    current: LocationDefinition | None


async def resolve_scene(
    reader: SpatialReaderPort,
    *,
    session_id: uuid.UUID,
    world_id: uuid.UUID,
    current_location: str,
) -> ScenePlacement:
    """Load the session's geography and work out which place the scene is in.

    `current` is None when the world has no geography or the session's location string
    matches nothing -- which is most sessions today. The graph is still returned: a
    caller may have use for it even when the scene is nowhere in particular.
    """
    graph = await load_spatial_graph(reader, session_id=session_id, world_id=world_id)
    return ScenePlacement(graph=graph, current=resolve_scene_location(graph, current_location))


async def build_scene_spatial_context(
    reader: SpatialReaderPort,
    *,
    session_id: uuid.UUID,
    world_id: uuid.UUID,
    current_location: str,
) -> SpatialContext | None:
    """The spatial block for a turn, or None when the scene has no known place.

    None rather than an empty context: a world with no geography, or a session whose
    location string matches nothing, should produce *no* spatial section rather than an
    empty one. An empty heading tells a model the game tracks places and has none,
    which reads worse than silence.
    """
    placement = await resolve_scene(
        reader, session_id=session_id, world_id=world_id, current_location=current_location
    )
    return await assemble_scene_context(reader, placement)


async def assemble_scene_context(
    reader: SpatialReaderPort, placement: ScenePlacement
) -> SpatialContext | None:
    """The spatial block for an already-resolved placement, or None if it is nowhere."""
    if placement.current is None:
        return None
    return await _assemble(reader, placement.graph, placement.current)


async def get_spatial_context(
    reader: SpatialReaderPort,
    *,
    session_id: uuid.UUID,
    world_id: uuid.UUID,
    location_id: uuid.UUID,
) -> SpatialContext:
    """The same view, for a caller that named a place and expects it to exist.

    The endpoint form. Raises rather than returning None: a client that asked about a
    specific location deserves to know whether the place is missing or merely empty --
    and "missing" here also covers "belongs to another session", which from this
    session is the same thing.
    """
    graph = await load_spatial_graph(reader, session_id=session_id, world_id=world_id)
    current = graph.index.get(location_id)
    if current is None:
        raise NotFoundError("Location", location_id)
    return await _assemble(reader, graph, current)


async def _assemble(
    reader: SpatialReaderPort, graph: SpatialGraph, current: LocationDefinition
) -> SpatialContext:
    """Five tiers, each capped. The caps are the point; see the module docstring."""
    zones = await reader.load_zones(current.id)
    return SpatialContext(
        current=_place(current, graph.location_states.get(current.id)),
        zones=[zone.name for zone in zones[:MAX_ZONES]],
        contains=[
            _place(child, graph.location_states.get(child.id))
            for child in get_children(graph.index, current.id)[:MAX_CHILDREN]
        ],
        exits=_exits(graph, current.id),
        within=[
            _place(ancestor, graph.location_states.get(ancestor.id))
            for ancestor in get_ancestors(graph.index, current.id)[:MAX_ANCESTORS]
        ],
    )


def _place(definition: LocationDefinition, state: LocationState | None) -> PlaceContext:
    """One place as prose sees it: a name, what kind of thing it is, and how it is.

    The id is deliberately absent. The director reads geography, it does not address
    it -- and the one thing a model must never do is invent a uuid, which it cannot do
    for an id it was never shown.

    Condition and accessibility are only included when they are *not* the defaults.
    "The Broken Crown (intact, open)" on every place in every prompt is tokens spent
    saying nothing; "The east bridge (destroyed, blocked)" is the whole point.
    """
    detail = definition.subtype or definition.category.value
    return PlaceContext(
        name=definition.name,
        kind=detail.replace("_", " "),
        condition=None if state is None or state.condition.value == "intact" else state.condition,
        accessibility=(
            None if state is None or state.accessibility.value == "open" else state.accessibility
        ),
    )


def _exits(graph: SpatialGraph, location_id: uuid.UUID) -> list[ConnectedPlaceContext]:
    """Where you can go from here, blocked ones included and marked.

    Blocked exits are sent on purpose. A barred gate is something the scene has to
    respect, and a model that cannot see it will write the player straight through --
    which is exactly the failure the spatial model exists to stop.

    Ordered by the destination's importance then its name, so the list is stable
    between two reads of an unchanged world.
    """
    entries: list[tuple[int, str, ConnectedPlaceContext]] = []
    for connection, destination_id in graph.exits_from(location_id):
        destination = graph.index.get(destination_id)
        if destination is None:
            # An edge to somewhere this session cannot see. Skipped rather than
            # reported: from here it is not a broken link, it is another save's canon.
            continue
        entries.append(
            (
                -destination.importance,
                destination.name.casefold(),
                ConnectedPlaceContext(
                    name=destination.name,
                    via=_connection_label(connection),
                    passable=graph.is_passable(connection.id),
                    travel_minutes=connection.base_travel_minutes,
                ),
            )
        )
    entries.sort(key=lambda item: (item[0], item[1]))
    return [entry for _, __, entry in entries][:MAX_ADJACENT]


def _connection_label(connection: LocationConnection) -> str:
    label = connection.subtype or connection.category.value
    return label.replace("_", " ")
