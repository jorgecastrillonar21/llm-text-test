"""The closed vocabularies of space.

Every enum here is deliberately small. A world is written in whatever genre its author
likes, and the way that variety is absorbed is `subtype` -- a free string -- not a
larger enum. `tavern`, `orbital_station` and `enchanted_forest` are subtypes; the
enums below are the handful of structural distinctions the engine itself reasons
about, and they have to stay stable across every genre this ever runs.
"""

from __future__ import annotations

from enum import StrEnum


class LocationCategory(StrEnum):
    """What *kind* of place this is, structurally.

    Not what it is in the fiction -- that is `subtype`. A tavern, a laboratory and a
    shrine are all `structure`, and the engine treats them alike because nothing it
    does depends on the difference.
    """

    WORLD = "world"
    REGION = "region"
    SETTLEMENT = "settlement"
    AREA = "area"
    STRUCTURE = "structure"
    INTERIOR = "interior"
    TRANSIT = "transit"
    OTHER = "other"


class LocationScale(StrEnum):
    """How *big* a place is, qualitatively.

    A separate axis from category, and the two are not derivable from one another: a
    royal palace is `structure`/`building` while its gardens are `area`/`site`, and a
    cupboard and a cathedral are both `structure`.

    Qualitative on purpose. This application is narrative-first: "building" is what a
    scene needs, and inventing `width = 87.4m` for a tavern would be precision nobody
    asked for and no system consumes. Real measurements, where a world genuinely has
    them, go in `spatial_metadata` and are never required.
    """

    WORLD = "world"
    CONTINENTAL = "continental"
    REGIONAL = "regional"
    SETTLEMENT = "settlement"
    DISTRICT = "district"
    SITE = "site"
    BUILDING = "building"
    ROOM = "room"
    POINT = "point"


_SCALE_ORDER: tuple[LocationScale, ...] = (
    LocationScale.POINT,
    LocationScale.ROOM,
    LocationScale.BUILDING,
    LocationScale.SITE,
    LocationScale.DISTRICT,
    LocationScale.SETTLEMENT,
    LocationScale.REGIONAL,
    LocationScale.CONTINENTAL,
    LocationScale.WORLD,
)
"""Smallest to largest. Used to rank scales for the creation policy, and nothing else.

Explicitly *not* used to validate containment. A cellar under a city wall, a district
that contains a whole abandoned settlement, a pocket dimension behind a door -- worlds
are full of places that do not nest by size, and rejecting them would be the engine
overruling the author about their own geography.
"""


def scale_rank(scale: LocationScale) -> int:
    """Position in `_SCALE_ORDER`. Larger means bigger."""
    return _SCALE_ORDER.index(scale)


class LocationCondition(StrEnum):
    """The physical state of a place.

    Independent of accessibility: a pristine vault can be sealed, and a destroyed
    tower can be wide open. Both axes exist because collapsing them would make
    "ruined but walkable" unrepresentable.
    """

    PRISTINE = "pristine"
    INTACT = "intact"
    WORN = "worn"
    DAMAGED = "damaged"
    HEAVILY_DAMAGED = "heavily_damaged"
    RUINED = "ruined"
    DESTROYED = "destroyed"


class LocationAccessibility(StrEnum):
    """Whether a place can currently be entered, and how hard that is.

    `destroyed` + `open` is a legitimate pair: the ruins of the Broken Crown are still
    somewhere a character can stand. A definition is never deleted because its
    condition reached `destroyed`; see `states.py`.
    """

    OPEN = "open"
    RESTRICTED = "restricted"
    """Entry is possible for those entitled to it. What "entitled" means is a future
    access-requirement system; nothing here evaluates it."""

    CLOSED = "closed"
    BLOCKED = "blocked"
    SEALED = "sealed"
    INACCESSIBLE = "inaccessible"


_TRAVERSABLE = frozenset({LocationAccessibility.OPEN, LocationAccessibility.RESTRICTED})


def is_traversable(accessibility: LocationAccessibility) -> bool:
    """Whether this accessibility permits passage at all, before requirements.

    `restricted` counts: a guarded gate is a gate, and whether *this* character may
    pass is a question for the future requirement system rather than for topology.
    Everything else is a hard no, and no authority narrates its way past one.
    """
    return accessibility in _TRAVERSABLE


class ConnectionCategory(StrEnum):
    """The structural kind of a traversal between two places.

    As with `LocationCategory`, specifics live in `subtype`: `door`, `stairs`,
    `secret_passage`, `imperial_highway`, `teleport_gate`, `railway`.
    """

    PASSAGE = "passage"
    ROAD = "road"
    PATH = "path"
    VERTICAL = "vertical"
    PORTAL = "portal"
    WATER = "water"
    AIR = "air"
    TRANSIT = "transit"
    OTHER = "other"


class SpatialTier(StrEnum):
    """How far from a viewpoint a place sits, for context selection.

    A retrieval-budget concept, not a distance. `ADJACENT` means "one connection
    away", not "nearby" -- proximity is a scene-level question this deliberately does
    not answer. See the note on proximity in `docs/world-state-locations.md`.
    """

    CURRENT = "current"
    ADJACENT = "adjacent"
    LOCAL = "local"
    REGIONAL = "regional"
    GLOBAL = "global"
