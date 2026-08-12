"""Things the world has agreed to do later.

A scheduled event is a note that says "at minute 29400 of this session, something of
type X happens". It is fictional scheduling, not infrastructure scheduling: no queue,
no worker, no retry, no cron. Nothing fires while the application is closed, because
nothing fires except during an explicit time advance.

# Absolute, always

`due_at` is a position on the session clock, never a delay. "In three days" is
converted the moment it is written:

    due_at = current_elapsed_minutes + 4320

Storing the phrase instead would make the event's meaning depend on when someone got
around to reading it.

Nothing in this release creates one during play. The model, the storage and the
processing exist so that a shop closing, a caravan arriving or a deadline passing has
somewhere to live; the systems that would schedule those are deliberately not built.
See docs/world-state-time.md.
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.errors import ValidationError


class ScheduledEventStatus(StrEnum):
    """PENDING    waiting for the clock to reach it
    DUE        the clock reached it; the work it owns has not run
    PROCESSED  an owner executed the work and said so
    CANCELLED  it will not happen after all

    # Reaching an event is not doing it

    `DUE` exists because those used to be the same status, and the lie was expensive.
    Time knows *when*; it does not know what a `situation.progress` or a `caravan.arrives`
    means, and it has no way to make one happen. Marking an event PROCESSED because the
    clock walked past it recorded work as done that nothing had done -- and, worse, took
    it out of the pending query, so the work became unfindable at the same moment it
    became a lie.

    So the clock only ever moves an event to DUE. It stays there, visible to whoever
    owns that event type, until that owner executes it and acknowledges it. PROCESSED
    means one thing now: the owning work ran and finished.
    """

    PENDING = "pending"
    DUE = "due"
    PROCESSED = "processed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({ScheduledEventStatus.PROCESSED, ScheduledEventStatus.CANCELLED})
"""Once resolved, an event stays resolved. Nothing returns to pending."""

UNRESOLVED_STATUSES = frozenset({ScheduledEventStatus.PENDING, ScheduledEventStatus.DUE})
"""Still owed. The set a time advance looks at, and the set that keeps due work findable
after the advance that surfaced it."""

_PERMITTED_TRANSITIONS: dict[ScheduledEventStatus, frozenset[ScheduledEventStatus]] = {
    ScheduledEventStatus.PENDING: frozenset(
        {ScheduledEventStatus.DUE, ScheduledEventStatus.CANCELLED}
    ),
    ScheduledEventStatus.DUE: frozenset(
        {ScheduledEventStatus.PROCESSED, ScheduledEventStatus.CANCELLED}
    ),
    ScheduledEventStatus.PROCESSED: frozenset(),
    ScheduledEventStatus.CANCELLED: frozenset(),
}
"""The whole lifecycle, in one table.

    pending --clock reaches it--> due --owner ran it--> processed
       |                           |
       +------ called off ---------+--> cancelled

`pending -> processed` is absent on purpose: an event may not be completed before the
clock has reached it, so PROCESSED always implies the fictional moment arrived *and*
someone did the work. `due -> cancelled` is present because an owner is allowed to look
at due work and decide it will not happen -- the siege was lifted, the caravan turned
back -- and that is a different verdict from having carried it out.
"""


def require_transition(current: ScheduledEventStatus, target: ScheduledEventStatus) -> None:
    """Raise unless `current -> target` is a move a scheduled event may make.

    Terminal is terminal: re-opening a processed event would let the same fictional
    moment happen twice, and completing one twice is the retry bug this table exists to
    make impossible.
    """
    if target not in _PERMITTED_TRANSITIONS[current]:
        allowed = ", ".join(sorted(status.value for status in _PERMITTED_TRANSITIONS[current]))
        raise ValidationError(
            f"A {current.value} scheduled event cannot become {target.value}"
            + (f"; it may only become: {allowed}." if allowed else ": it is already resolved.")
        )
