"""Situations that contain situations, and the rule that keeps it a tree.

    War
    └── Siege of Asterfall
        └── Food Crisis
            └── Disease Outbreak

The link is causal and organisational: it says the food crisis exists *because of* the
siege and should be read alongside it. That is all it says.

# Lifecycle is not inherited, and that is the point

Resolving the war does not resolve the siege, and lifting the siege does not end the
hunger. A city does not stop starving because a treaty was signed somewhere else --
the grain has to arrive, and whether it does is a separate process with its own
resolution. Any code that cascades a parent's status onto its children would end a
dozen live stories the moment one of them concluded.

Application logic may of course decide, explicitly, that resolving one thing resolves
another. That decision belongs to whatever knows why -- not to this link.

# Everything here is session-local

Unlike containment for locations there is no template tier: a situation belongs to one
session, so the scope check is simply that parent and child share it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator

from app.domain.errors import NotFoundError, ValidationError
from app.domain.world_situations.situations import Situation

MAX_SITUATION_DEPTH = 16
"""How deep a causal chain may go before it is treated as broken.

`War > Siege > Food Crisis > Disease Outbreak` is four. This is a guard against a
corrupted row producing an endless walk, not a design limit anyone will meet.
"""


class SituationIndex:
    """A read-only view of some situations, keyed for hierarchy walks.

    Holds whatever subset the caller loaded, the same contract as `LocationIndex`: a
    link pointing outside the loaded set is a boundary, not an error, because only the
    database can tell "not loaded" from "not there".
    """

    def __init__(self, situations: Iterable[Situation]) -> None:
        self._by_id: dict[uuid.UUID, Situation] = {}
        self._children: dict[uuid.UUID, list[Situation]] = {}
        for situation in situations:
            self._by_id[situation.id] = situation
        # Second pass, so a child loaded before its parent is still grouped correctly.
        for situation in self._by_id.values():
            if situation.parent_situation_id is not None:
                self._children.setdefault(situation.parent_situation_id, []).append(situation)
        for children in self._children.values():
            children.sort(key=lambda item: (item.title.casefold(), str(item.id)))

    def __contains__(self, situation_id: object) -> bool:
        return situation_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Situation]:
        return iter(self._by_id.values())

    def get(self, situation_id: uuid.UUID) -> Situation | None:
        return self._by_id.get(situation_id)

    def require(self, situation_id: uuid.UUID) -> Situation:
        found = self._by_id.get(situation_id)
        if found is None:
            raise NotFoundError("Situation", situation_id)
        return found

    def children_of(self, situation_id: uuid.UUID) -> list[Situation]:
        return list(self._children.get(situation_id, ()))


def get_children(index: SituationIndex, situation_id: uuid.UUID) -> list[Situation]:
    """Direct children, by title. Sorted so two reads of one unchanged session agree."""
    index.require(situation_id)
    return index.children_of(situation_id)


def get_ancestors(index: SituationIndex, situation_id: uuid.UUID) -> list[Situation]:
    """Causes from the immediate parent outwards, nearest first.

    Stops at the edge of the index rather than failing. Raises on a chain longer than
    `MAX_SITUATION_DEPTH`, which can only happen to data that already violates the
    cycle rule below.
    """
    situation = index.require(situation_id)
    ancestors: list[Situation] = []
    seen = {situation.id}
    current = situation
    while current.parent_situation_id is not None:
        if current.parent_situation_id in seen:
            raise ValidationError(
                f"Situation cycle reached from {situation_id}: "
                f"{current.parent_situation_id} is already one of its own causes."
            )
        parent = index.get(current.parent_situation_id)
        if parent is None:
            break
        ancestors.append(parent)
        seen.add(parent.id)
        if len(ancestors) > MAX_SITUATION_DEPTH:
            raise ValidationError(
                f"Situation chain from {situation_id} is deeper than {MAX_SITUATION_DEPTH}; "
                "the hierarchy is corrupt."
            )
        current = parent
    return ancestors


def get_descendants(index: SituationIndex, situation_id: uuid.UUID) -> list[Situation]:
    """Everything caused by this, at any depth, breadth-first.

    Reading only. Nothing in this module changes a descendant, and no caller should
    use this list to cascade a status -- see the module docstring.
    """
    index.require(situation_id)
    found: list[Situation] = []
    seen = {situation_id}
    queue = list(get_children(index, situation_id))
    while queue:
        node = queue.pop(0)
        if node.id in seen:  # A cycle in stored data; stop rather than loop forever.
            continue
        seen.add(node.id)
        found.append(node)
        queue.extend(get_children(index, node.id))
    return found


def check_parent_situation(
    index: SituationIndex,
    *,
    child: Situation,
    parent_id: uuid.UUID | None,
) -> None:
    """Validate a proposed parent for `child`, or raise.

    Called before every write that sets the link. Two of these -- self-parenting and
    the session match -- could almost be column constraints; the cycle rule cannot be
    one in SQLite or PostgreSQL, which is why all three live together here where a
    reader can see the whole invariant at once.
    """
    if parent_id is None:
        return

    if parent_id == child.id:
        raise ValidationError(f"Situation {child.title!r} cannot be its own cause.")

    parent = index.get(parent_id)
    if parent is None:
        raise NotFoundError("Situation", parent_id)

    if parent.session_id != child.session_id:
        raise ValidationError(
            f"{child.title!r} belongs to session {child.session_id} but its proposed parent "
            f"{parent.title!r} belongs to {parent.session_id}. A situation in one save cannot "
            "be caused by one in another."
        )

    if parent.id == child.id or any(
        ancestor.id == child.id for ancestor in get_ancestors(index, parent.id)
    ):
        raise ValidationError(
            f"Making {parent.title!r} the cause of {child.title!r} would create a cycle: "
            "it is already one of that situation's own causes."
        )
