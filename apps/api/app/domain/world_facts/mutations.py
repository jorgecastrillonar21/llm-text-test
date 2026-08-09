"""State changes: what is being asked of the world, before anything is written.

Two operations, deliberately:

    SetFact      this property now has this value
    RemoveFact   this property is no longer structurally defined

and one envelope, `StateMutationBatch`, because one resolved action usually changes
several things at once and either all of them happen or none do.

# RemoveFact is not "false"

Removing `system.locked` does not mean the door is unlocked. It means the game no
longer makes any structured claim about whether that door has a lock. A door that
gets unlocked is `system.locked: true -> false` -- a SetFact.

The distinction matters because absence is a real answer downstream: a future
knowledge system asking "what does the world say about this?" gets *nothing* from an
absent property and *a definite no* from a false one. Collapsing the two loses the
difference between "not established" and "established as not so", permanently.

RemoveFact is therefore rare. Almost every change is a SetFact.

# There is no `previous_value`

A caller cannot tell the application what a fact used to be. It reads the current
value itself, from storage, inside the same transaction. Trusting a caller-supplied
previous value would mean trusting a stale read -- and the one caller whose reads are
least trustworthy is the language model, which is reading a prompt assembled before
the turn began.

Optimistic concurrency, where it is wanted, is `expected_revision` on the batch: one
number covering the whole session's state, checked once.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.errors import ValidationError
from app.domain.world_facts.authority import FactAuthority
from app.domain.world_facts.facts import FactKind, FactSubject, Importance
from app.domain.world_facts.properties import parse_property
from app.domain.world_facts.values import FactValue, check_fact_value

MAX_MUTATIONS_PER_BATCH = 50


class _Mutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: FactSubject
    property: str

    @field_validator("property")
    @classmethod
    def _canonical_property(cls, value: str) -> str:
        return parse_property(value)

    def target(self) -> tuple[str, str]:
        """The logical fact this touches. Two mutations sharing one are a conflict.

        A method rather than a `@property`, because this class has a field called
        `property` and the decorator would resolve to it.
        """
        return (self.subject.key, self.property)


class SetFact(_Mutation):
    """Establish or replace the current value of a property."""

    op: Literal["set_fact"] = "set_fact"

    value: FactValue
    """Required, with no default. `SetFact(...)` without a value is an error rather
    than an accidental null, because a null value is itself a meaningful statement --
    see `world_facts.values`."""

    importance: Importance = 3
    kind: FactKind = FactKind.WORLD_TRUTH
    tags: tuple[str, ...] = ()

    @field_validator("value", mode="before")
    @classmethod
    def _storable_value(cls, value: object) -> FactValue:
        return check_fact_value(value)


class RemoveFact(_Mutation):
    """Withdraw a property, leaving the world making no claim about it."""

    op: Literal["remove_fact"] = "remove_fact"


FactMutation = Annotated[SetFact | RemoveFact, Field(discriminator="op")]


class StateMutationBatch(BaseModel):
    """Everything one resolved event changes, as a single unit of work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority: FactAuthority
    """Who is asking. Applies to every mutation in the batch -- a batch that mixed
    authorities would have to be validated per mutation and audited per mutation, at
    which point it is not one unit of work."""

    mutations: list[FactMutation] = Field(min_length=1, max_length=MAX_MUTATIONS_PER_BATCH)

    expected_revision: int | None = Field(default=None, ge=0)
    """Refuse to apply if the session's state has moved on since this was decided.

    Optional because most callers run inside a single request that read the state a
    moment earlier. It exists now, rather than later, because the callers that will
    need it -- a simulation tick resolving against state a player action just changed
    -- are the ones this whole boundary is being built for.
    """

    @model_validator(mode="after")
    def _no_two_mutations_touch_one_fact(self) -> Self:
        """A batch that sets and then removes the same property is not an intent.

        Applying both in order would make the outcome depend on list position, and a
        caller that sends `alive = true` and `alive = false` together does not have a
        view of the world -- it has a bug. Refusing is the only reading that cannot be
        wrong.
        """
        seen: set[tuple[str, str]] = set()
        for mutation in self.mutations:
            target = mutation.target()
            if target in seen:
                subject, prop = target
                raise ValueError(
                    f"Batch contains more than one mutation for {subject} {prop}. "
                    "Decide the final value before submitting the batch."
                )
            seen.add(target)
        return self


def require_no_conflicts(batch: StateMutationBatch) -> None:
    """Re-check the batch invariant on an object that skipped validation.

    `model_copy(update=...)` and `model_construct` both bypass validators, and tests
    use them. Cheap enough to call again at the door of the application service.
    """
    seen: set[tuple[str, str]] = set()
    for mutation in batch.mutations:
        target = mutation.target()
        if target in seen:
            subject, prop = target
            raise ValidationError(f"Batch contains more than one mutation for {subject} {prop}.")
        seen.add(target)
