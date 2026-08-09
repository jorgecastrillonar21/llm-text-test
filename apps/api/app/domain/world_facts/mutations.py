"""Fact changes: what is being asked of the world, before anything is written.

Two operations, deliberately:

    SetFact      this property now has this value
    RemoveFact   this property is no longer structurally defined

The envelope that batches them -- with the spatial mutations they commit alongside --
is `app.domain.state_mutations.StateMutationBatch`.

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

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.world_facts.facts import FactKind, FactSubject, Importance
from app.domain.world_facts.properties import parse_property
from app.domain.world_facts.values import FactValue, check_fact_value


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
"""The fact half of `state_mutations.StateMutation`.

Kept as its own name because plenty of code only ever deals in facts -- the proposal
reviewer, the seed materialiser -- and saying so in the signature is more honest than
accepting the wider union and narrowing at runtime.

The batch that carries these lives in `app.domain.state_mutations`, with the spatial
mutations it also carries. It is not here because `world_facts` must not import
`world_locations`.
"""
