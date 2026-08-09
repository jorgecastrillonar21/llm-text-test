"""Containment: the tree, and the rules that keep it a tree.

Every operation here works over a `LocationIndex` -- an in-memory view of the
definitions that matter -- rather than over a database. That keeps the invariants
testable without a connection and keeps the domain free of storage, and it is
affordable because containment fans out narrowly: a world has a handful of ancestors
above any node and the adapter only loads the subgraph it needs.

# The invariants

    a parent must exist
    a location may not be its own parent
    a location has at most one direct parent      (a column, not a table)
    containment may not cycle
    a parent must be in scope for its child       (world, and session origin)

The cycle rule is the one that needs a graph. `A.parent = B` and `B.parent = A` are
each individually fine; only together are they a world where the tavern contains the
town that contains the tavern, and every ancestor walk after that never terminates.

# Containment is not connectivity, and neither is proximity

Nothing in this module produces an edge. Being inside Riverwood says nothing about
being able to walk out of it -- see `connections.py` -- and two characters standing in
the same city are not thereby near each other. Those are three separate questions and
this one answers only the first.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator

from app.domain.errors import NotFoundError, ValidationError
from app.domain.world_locations.definitions import LocationDefinition

MAX_CONTAINMENT_DEPTH = 32
"""How deep a containment chain may go before it is treated as broken.

A guard, not a design limit -- `World > Continent > Kingdom > Region > City > District
> Building > Room > Cellar` is nine, and nothing plausible approaches this. It exists
so that a chain corrupted by a hand-edited row terminates loudly instead of spinning.
"""


class LocationIndex:
    """A read-only view of some locations, keyed for hierarchy walks.

    Holds whatever subset the caller loaded. Every operation states what it does when
    a link points outside that subset, because "the parent is not in this index" and
    "the parent does not exist" are different situations and only the database knows
    which.
    """

    def __init__(self, definitions: Iterable[LocationDefinition]) -> None:
        self._by_id: dict[uuid.UUID, LocationDefinition] = {}
        self._children: dict[uuid.UUID, list[LocationDefinition]] = {}
        for definition in definitions:
            self._by_id[definition.id] = definition
        # Built once, in a second pass, so children are grouped regardless of the order
        # definitions arrived in -- a parent may well be loaded after its child.
        for definition in self._by_id.values():
            if definition.parent_location_id is not None:
                self._children.setdefault(definition.parent_location_id, []).append(definition)
        for siblings in self._children.values():
            siblings.sort(key=lambda item: (item.name.casefold(), str(item.id)))

    def __contains__(self, location_id: object) -> bool:
        return location_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[LocationDefinition]:
        return iter(self._by_id.values())

    def get(self, location_id: uuid.UUID) -> LocationDefinition | None:
        return self._by_id.get(location_id)

    def children_of(self, location_id: uuid.UUID) -> list[LocationDefinition]:
        """Direct children as loaded, without checking that the parent is present.
        `get_children` is the checked form and the one callers should reach for."""
        return list(self._children.get(location_id, ()))

    def require(self, location_id: uuid.UUID) -> LocationDefinition:
        found = self._by_id.get(location_id)
        if found is None:
            raise NotFoundError("Location", location_id)
        return found

    def roots(self) -> list[LocationDefinition]:
        """Definitions with no parent *within this index*.

        A partially loaded index reports more roots than the world has. That is
        honest rather than wrong: from where the caller is standing, those nodes have
        no visible container.
        """
        return sorted(
            (
                definition
                for definition in self._by_id.values()
                if definition.parent_location_id is None
                or definition.parent_location_id not in self._by_id
            ),
            key=lambda item: (item.name.casefold(), str(item.id)),
        )


def get_parent(index: LocationIndex, location_id: uuid.UUID) -> LocationDefinition | None:
    """The direct container, or None at a root or at the edge of the index."""
    definition = index.require(location_id)
    if definition.parent_location_id is None:
        return None
    return index.get(definition.parent_location_id)


def get_children(index: LocationIndex, location_id: uuid.UUID) -> list[LocationDefinition]:
    """Direct children, by name. Sorted so two reads of one unchanged world agree --
    this feeds a prompt, and a set that reshuffles is a prompt that will not cache."""
    index.require(location_id)
    return index.children_of(location_id)


def get_ancestors(index: LocationIndex, location_id: uuid.UUID) -> list[LocationDefinition]:
    """Containers from the immediate parent outwards, nearest first.

    Stops at the edge of the index rather than failing: a caller that loaded a city
    and its rooms gets the ancestors it has. Raises if the chain exceeds
    `MAX_CONTAINMENT_DEPTH`, which can only happen on data that already violates the
    cycle rule.
    """
    definition = index.require(location_id)
    ancestors: list[LocationDefinition] = []
    seen = {definition.id}
    current = definition
    while current.parent_location_id is not None:
        if current.parent_location_id in seen:
            raise ValidationError(
                f"Containment cycle reached from location {location_id}: "
                f"{current.parent_location_id} is already one of its own containers."
            )
        parent = index.get(current.parent_location_id)
        if parent is None:
            break
        ancestors.append(parent)
        seen.add(parent.id)
        if len(ancestors) > MAX_CONTAINMENT_DEPTH:
            raise ValidationError(
                f"Containment from location {location_id} is deeper than "
                f"{MAX_CONTAINMENT_DEPTH}; the hierarchy is corrupt."
            )
        current = parent
    return ancestors


def get_descendants(index: LocationIndex, location_id: uuid.UUID) -> list[LocationDefinition]:
    """Everything contained, at any depth, breadth-first.

    Breadth-first so a truncating caller keeps the near ones, which are the ones a
    scene cares about.
    """
    index.require(location_id)
    found: list[LocationDefinition] = []
    seen = {location_id}
    queue = list(get_children(index, location_id))
    while queue:
        node = queue.pop(0)
        if node.id in seen:  # A cycle in stored data; stop rather than loop forever.
            continue
        seen.add(node.id)
        found.append(node)
        queue.extend(get_children(index, node.id))
    return found


def is_within(index: LocationIndex, location_id: uuid.UUID, ancestor_id: uuid.UUID) -> bool:
    """Whether `location_id` sits anywhere inside `ancestor_id`.

    A place is not within itself: `is_within(x, x)` is False. "Inside" is a
    relationship between two different places, and a caller wanting "here or below"
    can say so in one obvious line rather than having it assumed for them.
    """
    if location_id == ancestor_id:
        return False
    return any(parent.id == ancestor_id for parent in get_ancestors(index, location_id))


def check_parent(
    index: LocationIndex,
    *,
    child: LocationDefinition,
    parent_id: uuid.UUID | None,
) -> None:
    """Validate a proposed parent for `child`, or raise.

    Called before every write that sets or changes containment. Everything it checks
    is something that cannot be expressed as a column constraint, which is why it is
    a function rather than a database rule -- the database enforces "the parent row
    exists" and this enforces what that alone would let through.
    """
    if parent_id is None:
        return

    if parent_id == child.id:
        raise ValidationError(f"Location {child.name!r} cannot be its own parent.")

    parent = index.get(parent_id)
    if parent is None:
        raise NotFoundError("Location", parent_id)

    if parent.world_id != child.world_id:
        raise ValidationError(
            f"{child.name!r} belongs to world {child.world_id} but its proposed parent "
            f"{parent.name!r} belongs to {parent.world_id}. Containment does not cross worlds."
        )

    # Session-local canon may sit inside template geography -- a generated bookshop in
    # an authored town is the ordinary case. The reverse cannot: a template location
    # whose container exists in one save would be broken in every other save, where
    # that container is not there at all.
    if parent.origin_session_id is not None and parent.origin_session_id != child.origin_session_id:
        raise ValidationError(
            f"{parent.name!r} exists only inside one session, so {child.name!r} cannot be "
            "placed in it. Session-local geography may sit inside template geography, "
            "never the other way around, and never inside another session's."
        )

    # The cycle check, walking up from the proposed parent. If the child is already
    # one of its own would-be ancestors, this link closes a loop.
    if parent.id == child.id or any(
        ancestor.id == child.id for ancestor in get_ancestors(index, parent.id)
    ):
        raise ValidationError(
            f"Placing {child.name!r} inside {parent.name!r} would create a containment "
            "cycle: it is already one of that location's own containers."
        )
