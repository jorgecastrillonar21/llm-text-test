"""One batch, every kind of state change.

    StateMutation
    ├── SetFact                 a property of something is now this
    ├── RemoveFact              the world no longer claims anything about it
    ├── UpdateLocationState     what is currently true about a place
    └── UpdateConnectionState   whether a traversal can be made

A resolved event rarely changes one thing. `BRIDGE_COLLAPSED` destroys the bridge,
blocks the crossing and raises the local danger, and a world where the first landed
and the third did not is worse than one where none did. So they travel together and
commit together.

# Why this module exists at all

The batch used to live in `world_facts`, which was right while facts were the only
kind of change. Spatial mutations are not facts and `world_facts` must not import
`world_locations` to hold them -- so the envelope moved here, to a module that
composes both and belongs to neither. Anything that adds a third kind of state adds
it here too, which is the point: there is one place to look for what a batch may
contain.

# Facts and dedicated state do not overlap

`UpdateLocationState` exists so that a location's condition is *not* a `SetFact`. See
`world_locations.mutations` for why, and `world_facts.policy` for the reserved
property names that stop it happening by the back door.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.errors import FactPolicyError, ValidationError
from app.domain.world_facts.authority import FactAuthority
from app.domain.world_facts.mutations import RemoveFact, SetFact
from app.domain.world_locations.mutations import UpdateConnectionState, UpdateLocationState

MAX_MUTATIONS_PER_BATCH = 50

StateMutation = Annotated[
    SetFact | RemoveFact | UpdateLocationState | UpdateConnectionState,
    Field(discriminator="op"),
]
"""Discriminated on `op`, so a malformed mutation names its own problem instead of
producing a four-branch union report."""

_SPATIAL_OPS = frozenset({"update_location_state", "update_connection_state"})

_SPATIAL_AUTHORITIES = frozenset(
    {
        FactAuthority.ENGINE,
        FactAuthority.SIMULATION,
        FactAuthority.PLAYER_RESOLUTION,
        FactAuthority.SEED,
        FactAuthority.ADMIN,
    }
)
"""Everyone except the Story Director.

Spatial state is mechanical without exception. Whether a gate is barred, whether a
bridge still stands, who holds a fort -- each is something a resolution system decides
and hands to narration as an outcome. There is no open tier here the way
`narrative.birthplace` is open for facts, because there is no spatial field whose value
is mere colour: every one of them changes what a character can physically do next.
"""


def require_spatial_authority(authority: FactAuthority) -> None:
    """Refuse a spatial mutation from an authority that may not make one."""
    if authority not in _SPATIAL_AUTHORITIES:
        raise FactPolicyError(
            f"Authority {authority.value!r} may not change spatial state. Whether a place "
            "is damaged, sealed, guarded or held is decided by game systems and narrated "
            "afterwards -- prose does not open a barred gate."
        )


class StateMutationBatch(BaseModel):
    """Everything one resolved event changes, as a single unit of work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority: FactAuthority
    """Who is asking. Applies to every mutation in the batch -- a batch that mixed
    authorities would have to be validated per mutation and audited per mutation, at
    which point it is not one unit of work."""

    mutations: list[StateMutation] = Field(min_length=1, max_length=MAX_MUTATIONS_PER_BATCH)

    expected_revision: int | None = Field(default=None, ge=0)
    """Refuse to apply if the session's state has moved on since this was decided.

    Optional because most callers run inside a single request that read the state a
    moment earlier. It exists now, rather than later, because the callers that will
    need it -- a simulation tick resolving against state a player action just changed
    -- are the ones this whole boundary is being built for.
    """

    @model_validator(mode="after")
    def _no_two_mutations_touch_one_thing(self) -> Self:
        """A batch that sets and then removes the same target is not an intent.

        Applying both in order would make the outcome depend on list position, and a
        caller that sends `alive = true` and `alive = false` together does not have a
        view of the world -- it has a bug. Refusing is the only reading that cannot be
        wrong.

        Targets from different kinds of mutation cannot collide: a fact's target starts
        with its subject key (`world`, `character:<id>`) and a spatial one with
        `location_state` or `connection_state`.
        """
        seen: set[tuple[str, str]] = set()
        for mutation in self.mutations:
            target = mutation.target()
            if target in seen:
                kind, name = target
                raise ValueError(
                    f"Batch contains more than one mutation for {kind} {name}. "
                    "Decide the final value before submitting the batch."
                )
            seen.add(target)
        return self

    @model_validator(mode="after")
    def _spatial_mutations_need_mechanical_authority(self) -> Self:
        """Checked on the batch rather than only in the service, so a
        `story_director` batch carrying a spatial change cannot be constructed at all.
        """
        if any(mutation.op in _SPATIAL_OPS for mutation in self.mutations):
            try:
                require_spatial_authority(self.authority)
            except FactPolicyError as exc:
                raise ValueError(str(exc)) from exc
        return self

    def touches_space(self) -> bool:
        return any(mutation.op in _SPATIAL_OPS for mutation in self.mutations)


def require_no_conflicts(batch: StateMutationBatch) -> None:
    """Re-check the batch invariants on an object that skipped validation.

    `model_copy(update=...)` and `model_construct` both bypass validators, and tests
    use them. Cheap enough to call again at the door of the application service.
    """
    seen: set[tuple[str, str]] = set()
    for mutation in batch.mutations:
        target = mutation.target()
        if target in seen:
            kind, name = target
            raise ValidationError(f"Batch contains more than one mutation for {kind} {name}.")
        seen.add(target)
    if batch.touches_space():
        require_spatial_authority(batch.authority)
