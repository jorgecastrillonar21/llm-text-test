"""Which status a situation may move to, from where.

    planned ──> active ──> resolved
       │          ↕
       │       dormant ──> resolved
       │
       └──> cancelled          (and active/dormant may also be cancelled)

A table, not a workflow engine. There is no state machine framework here, no guards,
no hooks, no per-transition handlers -- just the set of moves that make sense and a
function that refuses the rest. The spec asks to prevent nonsense like
`resolved -> planned`; the smallest thing that does that is this.

# Terminal means terminal

Resolved and cancelled have no outgoing edges. A siege that ended did not un-end, and
a world where it can is one where the transcript and the state disagree about the same
week. Admin tooling that genuinely needs to undo one should say so in its own words --
`reopen_situation` with its own authority and its own audit line -- rather than
quietly reusing the gameplay path. There is deliberately no such function today.

# Dormant is not a pause button

`active <-> dormant` goes both ways, because a cold war warms and a stalled inquiry
gets a new witness. That round trip is the whole reason `dormant` exists rather than
being spelled `active` with `momentum = 0`: a process that is going nowhere is
different from one that is going nowhere *right now*, and only the second one wakes up
when something happens.
"""

from __future__ import annotations

from app.domain.errors import ValidationError
from app.domain.world_situations.enums import TERMINAL_STATUSES, SituationStatus

_ALLOWED: dict[SituationStatus, frozenset[SituationStatus]] = {
    SituationStatus.PLANNED: frozenset(
        {SituationStatus.ACTIVE, SituationStatus.DORMANT, SituationStatus.CANCELLED}
    ),
    SituationStatus.ACTIVE: frozenset(
        {SituationStatus.DORMANT, SituationStatus.RESOLVED, SituationStatus.CANCELLED}
    ),
    SituationStatus.DORMANT: frozenset(
        {SituationStatus.ACTIVE, SituationStatus.RESOLVED, SituationStatus.CANCELLED}
    ),
    SituationStatus.RESOLVED: frozenset(),
    SituationStatus.CANCELLED: frozenset(),
}
"""Every move a situation may make during normal play.

`planned -> dormant` is in the list and looks odd until you have a conspiracy that was
prepared and then shelved: it began existing without ever becoming active. Leaving it
out would force that through `active`, writing a moment of activity that never happened.
"""


def can_transition(current: SituationStatus, target: SituationStatus) -> bool:
    """Whether `current -> target` is a legal move. Staying put always is."""
    if current is target:
        return True
    return target in _ALLOWED[current]


def require_transition(current: SituationStatus, target: SituationStatus) -> None:
    """Raise unless `current -> target` is a move a situation may make."""
    if can_transition(current, target):
        return
    if current in TERMINAL_STATUSES:
        raise ValidationError(
            f"A {current.value} situation cannot become {target.value}: it has already "
            "concluded, and re-opening it would make the same fictional period happen "
            "twice. Start a new situation instead, with this one as its parent."
        )
    raise ValidationError(
        f"A situation cannot move from {current.value} to {target.value}. "
        f"Allowed from {current.value}: "
        f"{', '.join(sorted(status.value for status in _ALLOWED[current])) or 'nothing'}."
    )


def is_terminal(status: SituationStatus) -> bool:
    return status in TERMINAL_STATUSES
