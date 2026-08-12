"""What a resolver decided, before anything has been written down.

    Command + ResolutionContext  ->  ResolutionOutcome

That arrow is pure. A resolver reads, calculates and returns; it does not open a
transaction, write a row, mint an id or advance a clock. Everything it wants the world
to do is described here and handed to the application, which decides whether it is
allowed and makes it happen atomically or not at all.

The separation is what makes resolvers testable -- a siege resolver is a function from
numbers to numbers -- and it is also the only thing standing between "the fire spread"
and a half-applied world where the fire spread but the building it destroyed is fine.

# Nothing here is authoritative

An `EventCandidate` is a candidate. A `state_mutation` is a proposal. A
`ScheduledEventRequest` is a request. The application clamps importance against policy,
validates every mutation against the world's rules and the session's revision, and may
persist none of it. A resolver that assumed otherwise would be deciding policy from
inside the calculation, which is where "the model said importance 5" comes from.
"""

from __future__ import annotations

import uuid
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.errors import ValidationError
from app.domain.resolution.enums import EventCategory, ResolutionDisposition
from app.domain.resolution.events import MAX_IMPORTANCE, MIN_IMPORTANCE
from app.domain.state_mutations import MAX_MUTATIONS_PER_BATCH, StateMutation
from app.domain.vocabulary import MetadataValue, check_flat_metadata, parse_subtype
from app.domain.world_time.advancement import TimeAdvanceRequest

MAX_EVENT_CANDIDATES = 8
"""Per resolution. A pass that believes eight distinct things worth remembering just
happened has stopped modelling an outcome and started narrating."""

MAX_SCHEDULED_REQUESTS = 8
MAX_SUMMARY_LENGTH = 500
MAX_REASON_CODE_LENGTH = 60


class EventCandidate(BaseModel):
    """Something a resolver believes is worth remembering.

    Whether it actually is, is not the resolver's call: `EventPolicy` decides, and may
    drop it entirely. The candidate says what happened and how much the resolver thinks
    it mattered; the application says what gets stored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: EventCategory
    subtype: str = Field(min_length=1)
    """The open half. `bridge_collapsed`, `character_died`, `siege_broken` -- and the
    key the policy registry is looked up by."""

    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    """One human-and-model-readable sentence: "The eastern bridge collapsed."

    Presentation, never mechanics. Nothing parses this string to decide anything, and
    the day something does is the day a typo changes the world.
    """

    importance: int | None = Field(default=None, ge=MIN_IMPORTANCE, le=MAX_IMPORTANCE)
    """What the resolver thinks it is worth, or None to accept the policy's default.
    Clamped into the policy's band either way -- see `EventPolicy.importance_for`."""

    primary_location_id: uuid.UUID | None = None
    caused_by_event_id: uuid.UUID | None = None
    """An already-persisted event this one follows from: FIRE_STARTED -> FIRE_SPREAD.

    Only ever an event that already exists. Siblings produced by the same resolution
    have no ids yet, and they do not need one -- they share a `resolution_id`, which is
    what "these happened together" means. This field is for causality *across*
    resolutions, which is the kind that cannot be inferred later.
    """

    payload: dict[str, MetadataValue] = Field(default_factory=dict)
    """Compact structured detail: `{"connection_id": "...", "cause": "structural"}`.

    Flat, bounded and non-executable, like every other metadata bag in the domain. When
    a subtype's payload starts wanting a shape, it gets a typed model -- not a deeper
    dict.
    """

    @field_validator("subtype", mode="before")
    @classmethod
    def _normalise_subtype(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        parsed = parse_subtype(value)
        if parsed is None:
            raise ValueError("An event subtype cannot be blank.")
        return parsed

    @field_validator("payload", mode="before")
    @classmethod
    def _payload_is_flat(cls, value: object) -> object:
        return check_flat_metadata(value, field="payload")


class ScheduledEventRequest(BaseModel):
    """Something the resolver wants looked at later.

    Absolute session time, never a delay. The resolver has the clock in its context and
    converts before it gets here, so the row does not mean something different every
    time it is read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    due_at: int = Field(ge=0)
    type: str = Field(min_length=1, max_length=80)
    payload: dict[str, MetadataValue] = Field(default_factory=dict)
    interrupt_player_action: bool = False

    @field_validator("payload", mode="before")
    @classmethod
    def _payload_is_flat(cls, value: object) -> object:
        return check_flat_metadata(value, field="payload")


class ResolutionOutcome(BaseModel):
    """The complete result of one resolution, as a value.

    Everything the world is about to do is in this object. That is what makes the
    validation pipeline possible: the application can check the whole projected change
    -- rules, authority, invariants, revision -- before the first write, so an illegal
    outcome is a refusal rather than a rollback.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    disposition: ResolutionDisposition

    reason_code: str | None = Field(default=None, max_length=MAX_REASON_CODE_LENGTH)
    """Why, in a word a machine can group by: `capability_missing`, `invalid_target`,
    `already_open`. Required for a rejection -- a refusal nobody can classify is a
    refusal nobody can fix -- and optional otherwise."""

    events: tuple[EventCandidate, ...] = Field(default=(), max_length=MAX_EVENT_CANDIDATES)

    state_mutations: tuple[StateMutation, ...] = Field(
        default=(), max_length=MAX_MUTATIONS_PER_BATCH
    )
    """Bare mutations, not a `StateMutationBatch`. The batch carries an authority and an
    expected revision, and neither is the resolver's to declare: a resolver does not
    know who invoked it and must not be able to claim it was the engine."""

    time_advance: TimeAdvanceRequest | None = None
    """A *request*, honoured by the time service, which is the only thing that may move
    the clock. Routed rather than applied because the interval may contain scheduled
    events, and a resolution that added minutes directly would skip them."""

    scheduled_events: tuple[ScheduledEventRequest, ...] = Field(
        default=(), max_length=MAX_SCHEDULED_REQUESTS
    )

    narrative_context: dict[str, MetadataValue] = Field(default_factory=dict)
    """Authoritative detail for narration, in a form prose cannot argue with.

    This is what the Story Director is handed *after* the mechanics have committed:
    what happened, and what is now true. It narrates this. It does not get to change
    it, and there is no field anywhere in its response that reaches back here.
    """

    @field_validator("reason_code", mode="before")
    @classmethod
    def _normalise_reason_code(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return parse_subtype(value)

    @field_validator("narrative_context", mode="before")
    @classmethod
    def _context_is_flat(cls, value: object) -> object:
        return check_flat_metadata(value, field="narrative_context")

    @model_validator(mode="after")
    def _disposition_matches_the_effects(self) -> Self:
        """A refusal that changes the world is a contradiction, not an edge case.

        Enforced on the model rather than trusted to each resolver, because this is the
        invariant the audit trail rests on: a reader who sees `rejected` must be able to
        conclude that nothing happened, without opening the mutation list.
        """
        if self.disposition is ResolutionDisposition.APPLIED:
            return self

        if self.has_effects:
            raise ValueError(
                f"A {self.disposition.value!r} resolution cannot carry effects. "
                "Nothing was attempted, so there is nothing for the world to record. "
                "Use 'applied' if the attempt happened and merely failed."
            )
        if self.disposition is ResolutionDisposition.REJECTED and not self.reason_code:
            raise ValueError(
                "A rejected resolution must say why: 'capability_missing', "
                "'invalid_target', 'actor_dead'. A refusal nobody can classify is a "
                "refusal nobody can act on."
            )
        return self

    @property
    def has_effects(self) -> bool:
        """Whether anything about the world would change if this were committed."""
        return bool(
            self.events
            or self.state_mutations
            or self.scheduled_events
            or self.time_advance is not None
        )

    @property
    def changes_state(self) -> bool:
        """Whether this touches authoritative WorldState, as opposed to history or the
        schedule. This is what decides whether the session's state revision moves."""
        return bool(self.state_mutations)


def rejected(reason_code: str, *, detail: str = "") -> ResolutionOutcome:
    """The refusal shorthand, since every resolver needs it and the shape is fixed."""
    if not reason_code.strip():
        raise ValidationError("A rejection needs a reason code.")
    context: dict[str, MetadataValue] = {"detail": detail} if detail else {}
    return ResolutionOutcome(
        disposition=ResolutionDisposition.REJECTED,
        reason_code=reason_code,
        narrative_context=context,
    )


def no_effect(reason_code: str | None = None) -> ResolutionOutcome:
    """Legitimate, and nothing happened. Opening a door that is already open."""
    return ResolutionOutcome(
        disposition=ResolutionDisposition.NO_EFFECT,
        reason_code=reason_code,
    )
