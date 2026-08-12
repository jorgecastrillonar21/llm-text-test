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

import logging
import uuid

from pydantic import BaseModel, ConfigDict

from app.application.persistence import SpatialReaderPort
from app.application.position_service import player_position
from app.application.spatial_service import SpatialGraph, load_spatial_graph
from app.application.story_context import (
    ConnectedPlaceContext,
    PlaceContext,
    SpatialContext,
)
from app.domain.character_position import scene_location_id
from app.domain.errors import NotFoundError
from app.domain.world_locations import (
    LocationConnection,
    LocationDefinition,
    LocationState,
    get_ancestors,
    get_children,
)

logger = logging.getLogger(__name__)

MAX_ADJACENT = 8
"""How many exits reach the prompt. A crossroads has four; a city square with eight
named streets is already more than a scene can use."""

MAX_CHILDREN = 8
MAX_ZONES = 6
MAX_ANCESTORS = 4
"""Enough for `World > Region > City > District`. Beyond that the outer containers stop
shaping a scene and start being trivia."""


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
    reader: SpatialReaderPort, *, session_id: uuid.UUID, world_id: uuid.UUID
) -> ScenePlacement:
    """Load the session's geography and work out which place the scene is in.

    The place comes from the player's canonical position -- by id, through
    `position_service` -- and nothing here matches a name against anything. That is the
    correction: a name match could resolve to the wrong room and hand a director its
    exits as canon, whereas an id either names a place this session can see or does not.

    `current` is None when the world has no geography, when nobody has written a
    position, or when the player is `in_transit` or `offstage` -- see
    `scene_location_id`, which returns None for those on purpose. The graph is still
    returned: a caller may have use for it even when the scene is nowhere in particular.

    A position pointing at a place this session cannot see is *reported*, not silently
    dropped. Writes go through `position_service.set_position`, which validates every
    id, so reaching this branch means something bypassed it.
    """
    graph = await load_spatial_graph(reader, session_id=session_id, world_id=world_id)
    location_id = scene_location_id(await player_position(reader, session_id=session_id))
    if location_id is None:
        return ScenePlacement(graph=graph, current=None)

    current = graph.index.get(location_id)
    if current is None:
        logger.warning(
            "Session %s has a canonical position at location %s, which is not in its "
            "visible geography. Building the scene without a place.",
            session_id,
            location_id,
        )
    return ScenePlacement(graph=graph, current=current)


async def build_scene_spatial_context(
    reader: SpatialReaderPort, *, session_id: uuid.UUID, world_id: uuid.UUID
) -> SpatialContext | None:
    """The spatial block for a turn, or None when the scene has no known place.

    None rather than an empty context: a world with no geography, or a player nobody has
    placed, should produce *no* spatial section rather than an empty one. An empty
    heading tells a model the game tracks places and has none, which reads worse than
    silence.
    """
    placement = await resolve_scene(reader, session_id=session_id, world_id=world_id)
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
