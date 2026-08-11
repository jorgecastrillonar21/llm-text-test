"""The authoritative root of one session's mutable reality.

# What this is, and what it deliberately is not

`WorldStateV1` is the *root* of a GameSession's WorldState, not the whole of it. It
holds the three things that are true of the session as a whole -- which version of the
state model it is, how many times its reality has changed, and what time it is -- and
nothing that has a cardinality.

    version      which shape this state is stored in
    session_id   whose reality this is
    revision     the logical version of that reality
    time         where the session sits on its own clock

Facts, locations, connections, situations, scheduled events, history and audit are
*not* fields here. They are separate tables with their own indexes, their own
lifecycles and their own foreign keys, and they belong to this root conceptually
rather than structurally. A root that carried them would be one JSON document holding
an entire world: unqueryable, unindexable, and rewritten in full every time a single
lamp went out. See docs/world-state.md.

# Why there is no clock in here

`time` is a `world_time.TimeState`, imported rather than redefined. The clock has one
authority and this is not it -- a second `elapsed_minutes` on this model would be a
second answer to what time it is, which is the exact failure the whole state
decomposition exists to prevent.

# Versioning

`version` is stored, not assumed. A future `WorldStateV2` will change what the root
means, and a build that reads a version it does not know must say so rather than
guess: `read_world_state` refuses, loudly, instead of best-efforting a shape nobody
wrote. There is deliberately no migration framework here -- one integer and one
refusal is the whole mechanism until a second version actually exists.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import UnsupportedWorldStateVersionError
from app.domain.world_time import TimeState

WORLD_STATE_VERSION: Literal[1] = 1
"""The version this build writes. Stamped on every session at creation.

Typed as the literal rather than as `int` so that `WorldStateV1.version` can default to
it: the day a `WorldStateV2` arrives, this line stops type-checking against the V1 model
and the compiler asks which one each caller meant. That is the intended way to find out.
"""

SUPPORTED_WORLD_STATE_VERSIONS: tuple[int, ...] = (1,)
"""Every version this build can read. Not a range: a version is supported when
somebody wrote the code to read it, and an unbounded upper end would let a session
written by a newer build be silently misread by an older one."""

StateRevision = Annotated[int, Field(ge=0)]
"""A logical version of one session's runtime reality.

Monotonic and never reused. Zero is a session that has been initialised and has not
changed since -- the world exactly as its template declared it.

Not a turn count and not a clock reading. A turn of pure conversation leaves it
alone; a resolution that changes six things moves it by one. See `docs/world-state.md`
for why all three counters are independent.
"""


class WorldStateV1(BaseModel):
    """The persisted root, as the application reads it.

    Intentionally four fields. Anything that would make this model grow with the size
    of a session belongs in its own table, and `CurrentWorldSnapshot` is where a
    composed view gets assembled for reading -- never here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = WORLD_STATE_VERSION
    session_id: uuid.UUID
    revision: StateRevision
    time: TimeState


def read_world_state(
    *, version: int, session_id: uuid.UUID, revision: int, elapsed_minutes: int
) -> WorldStateV1:
    """Build the root from stored columns, refusing a version this build cannot read.

    The refusal is the point. A session stamped `version=2` by a later build holds a
    root that means something this code does not know; carrying on with it would run a
    story against a state model nobody wrote, and the failure would surface later as
    nonsense rather than here as an error.
    """
    if version not in SUPPORTED_WORLD_STATE_VERSIONS:
        raise UnsupportedWorldStateVersionError(version, SUPPORTED_WORLD_STATE_VERSIONS)
    return WorldStateV1(
        version=WORLD_STATE_VERSION,
        session_id=session_id,
        revision=revision,
        time=TimeState(elapsed_minutes=elapsed_minutes),
    )
