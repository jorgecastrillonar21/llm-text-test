"""Where things are: the persistent spatial foundation.

The third piece of the future `WorldState`, after `world_time` and `world_facts`.
Time says when, facts say what is so, and this says where -- structurally, so that
travel, scenes, perception and tactical space can all be built on one spatial model
instead of three.

    enums         the closed vocabularies: category, scale, condition, access
    definitions   LocationDefinition and LocationZone -- what exists, and inside what
    connections   LocationConnection -- declared traversals, never inferred
    states        LocationState and LocationConnectionState -- per session, mutable
    hierarchy     containment walks, and the rules that keep containment a tree
    policy        how large a place narration may bring into existence
    mutations     typed spatial changes, batched atomically with fact changes

# The three distinctions this package exists to keep

    containment    what is inside what          parent_location_id
    connectivity   what can be traversed        LocationConnection
    proximity      what is near what, in a scene   -- deliberately absent

None of the three implies another. A cellar is inside a tavern without the tavern
having a usable way down; two characters in one city are not near each other. Proximity
is a scene-level question and belongs to the future SceneState; nothing here persists
it. See docs/world-state-locations.md.
"""

from __future__ import annotations

from app.domain.world_locations.connections import (
    MAX_DISTANCE_UNIT_LENGTH,
    LocationConnection,
    PhysicalDistance,
)
from app.domain.world_locations.definitions import (
    MAX_METADATA_KEYS,
    MAX_NAME_LENGTH,
    MAX_SUBTYPE_LENGTH,
    MAX_TAGS,
    Importance,
    LocationDefinition,
    LocationZone,
    check_spatial_metadata,
    parse_subtype,
)
from app.domain.world_locations.enums import (
    ConnectionCategory,
    LocationAccessibility,
    LocationCategory,
    LocationCondition,
    LocationScale,
    SpatialTier,
    is_traversable,
    scale_rank,
)
from app.domain.world_locations.hierarchy import (
    MAX_CONTAINMENT_DEPTH,
    LocationIndex,
    check_parent,
    get_ancestors,
    get_children,
    get_descendants,
    get_parent,
    is_within,
)
from app.domain.world_locations.mutations import (
    SpatialMutation,
    UpdateConnectionState,
    UpdateLocationState,
)
from app.domain.world_locations.policy import (
    LARGEST_PERMISSIVE_SCALE,
    CreationPolicy,
    check_creation_allowed,
    creation_policy_for,
)
from app.domain.world_locations.states import (
    DangerModifier,
    LocationConnectionState,
    LocationState,
    SecurityLevel,
)

__all__ = [
    "LARGEST_PERMISSIVE_SCALE",
    "MAX_CONTAINMENT_DEPTH",
    "MAX_DISTANCE_UNIT_LENGTH",
    "MAX_METADATA_KEYS",
    "MAX_NAME_LENGTH",
    "MAX_SUBTYPE_LENGTH",
    "MAX_TAGS",
    "ConnectionCategory",
    "CreationPolicy",
    "DangerModifier",
    "Importance",
    "LocationAccessibility",
    "LocationCategory",
    "LocationCondition",
    "LocationConnection",
    "LocationConnectionState",
    "LocationDefinition",
    "LocationIndex",
    "LocationScale",
    "LocationState",
    "LocationZone",
    "PhysicalDistance",
    "SecurityLevel",
    "SpatialMutation",
    "SpatialTier",
    "UpdateConnectionState",
    "UpdateLocationState",
    "check_creation_allowed",
    "check_parent",
    "check_spatial_metadata",
    "creation_policy_for",
    "get_ancestors",
    "get_children",
    "get_descendants",
    "get_parent",
    "is_traversable",
    "is_within",
    "parse_subtype",
    "scale_rank",
]
