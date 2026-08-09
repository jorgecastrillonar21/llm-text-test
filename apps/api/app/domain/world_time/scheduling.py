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
    PROCESSED  the clock passed it and it was resolved
    CANCELLED  it will not happen after all
    """

    PENDING = "pending"
    PROCESSED = "processed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({ScheduledEventStatus.PROCESSED, ScheduledEventStatus.CANCELLED})
"""Once resolved, an event stays resolved. Nothing returns to pending."""


def require_transition(current: ScheduledEventStatus, target: ScheduledEventStatus) -> None:
    """Raise unless `current -> target` is a move a scheduled event may make.

    Only a pending event can be resolved, and only into a terminal status. Re-opening
    a processed event would let the same fictional moment happen twice.
    """
    if current is not ScheduledEventStatus.PENDING:
        raise ValidationError(
            f"A {current} scheduled event cannot become {target}: it is already resolved."
        )
    if target not in TERMINAL_STATUSES:
        raise ValidationError(f"{target} is not a resolution; a pending event stays pending.")
