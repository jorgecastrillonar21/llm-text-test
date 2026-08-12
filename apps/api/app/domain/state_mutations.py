"""One batch, every kind of state change.

    StateMutation
    ├── SetFact                 a property of something is now this
    ├── RemoveFact              the world no longer claims anything about it
    ├── UpdateLocationState     what is currently true about a place
    ├── UpdateConnectionState   whether a traversal can be made
    ├── StartSituation          a process the world has begun running
    ├── UpdateSituation         a process moved
    └── ResolveSituation        a process reached an end

A resolved event rarely changes one thing. `BRIDGE_COLLAPSED` destroys the bridge,
blocks the crossing and raises the local danger, and a world where the first landed
and the third did not is worse than one where none did. So they travel together and
commit together.

A siege progressing is the same shape, one step larger: intensity rises, a gate is
breached, the gate becomes destroyed, the crossing closes, and a food crisis begins.
Five changes, four domains, one outcome. Committing part of that is committing a
reality nobody decided.

# Why this module exists at all

The batch used to live in `world_facts`, which was right while facts were the only
kind of change. Spatial mutations are not facts and `world_facts` must not import
`world_locations` to hold them -- so the envelope moved here, to a module that
composes those packages and belongs to none of them. `world_situations` joined on the
same terms. That is the point of the module: there is one place to look for what a
batch may contain.

# Facts and dedicated state do not overlap

`UpdateLocationState` exists so that a location's condition is *not* a `SetFact`. See
`world_locations.mutations` for why, and `world_facts.policy` for the reserved
property names that stop it happening by the back door. The situation mutations exist
for the same reason: a siege's lifecycle expressed as `SetFact(world.siege_status)`
would be a string in a JSON column, with no bounds, no transitions and nothing to
query.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.errors import FactPolicyError, ValidationError
from app.domain.world_facts.authority import FactAuthority
from app.domain.world_facts.mutations import RemoveFact, SetFact
from app.domain.world_locations.mutations import UpdateConnectionState, UpdateLocationState
from app.domain.world_situations.mutations import (
    ResolveSituation,
    StartSituation,
    UpdateSituation,
)

MAX_MUTATIONS_PER_BATCH = 50

StateMutation = Annotated[
    SetFact
    | RemoveFact
    | UpdateLocationState
    | UpdateConnectionState
    | StartSituation
    | UpdateSituation
    | ResolveSituation,
    Field(discriminator="op"),
]
"""Discriminated on `op`, so a malformed mutation names its own problem instead of
producing a seven-branch union report."""

_SPATIAL_OPS = frozenset({"update_location_state", "update_connection_state"})

_SITUATION_OPS = frozenset({"start_situation", "update_situation", "resolve_situation"})

_MECHANICAL_AUTHORITIES = frozenset(
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

Situations are the same, and `StartSituation` is deliberately no exception even though
the Story Director is allowed to propose new *places*. A location is a noun the story
mentioned; a situation is a process with three numbers, a lifecycle and a claim on
future simulation, and a model that could open one directly could declare a war by
writing an atmospheric sentence about one. What it can do is propose -- see
`app.application.situation_proposals`, where the application reads the proposal,
chooses every number itself and submits the mutation under its own authority.
"""


def require_mechanical_authority(authority: FactAuthority, *, kind: str) -> None:
    """Refuse a spatial or situation mutation from an authority that may not make one.

    `kind` is the noun for the message -- "spatial state", "a situation" -- so a refusal
    tells the caller which boundary it hit rather than naming this function's own
    generality.
    """
    if authority not in _MECHANICAL_AUTHORITIES:
        raise FactPolicyError(
            f"Authority {authority.value!r} may not change {kind}. What is damaged, sealed, "
            "held, under way or over is decided by game systems and narrated afterwards -- "
            "prose does not open a barred gate or end a siege."
        )


def require_spatial_authority(authority: FactAuthority) -> None:
    """Refuse a spatial mutation from an authority that may not make one."""
    require_mechanical_authority(authority, kind="spatial state")


def require_situation_authority(authority: FactAuthority) -> None:
    """Refuse a situation mutation from an authority that may not make one."""
    require_mechanical_authority(authority, kind="a situation")


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
        with its subject key (`world`, `character:<id>`), a spatial one with
        `location_state` or `connection_state`, and a situation's with `situation` or
        `situation_start`.
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
    def _mechanical_mutations_need_mechanical_authority(self) -> Self:
        """Checked on the batch rather than only in the service, so a `story_director`
        batch carrying a spatial or situation change cannot be constructed at all.
        """
        try:
            if self.touches_space():
                require_spatial_authority(self.authority)
            if self.touches_situations():
                require_situation_authority(self.authority)
        except FactPolicyError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def touches_space(self) -> bool:
        return any(mutation.op in _SPATIAL_OPS for mutation in self.mutations)

    def touches_situations(self) -> bool:
        return any(mutation.op in _SITUATION_OPS for mutation in self.mutations)


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
    if batch.touches_situations():
        require_situation_authority(batch.authority)
