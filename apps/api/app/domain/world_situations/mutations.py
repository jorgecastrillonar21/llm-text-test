"""Typed changes to ongoing processes.

    StartSituation     a new process now exists
    UpdateSituation    an existing one moved
    ResolveSituation   one reached an end

These sit alongside `SetFact`, `UpdateLocationState` and the rest in one
`StateMutationBatch` -- see `app.domain.state_mutations` -- because a siege progressing
raises its intensity, breaches a gate, blocks a crossing and starts a food crisis, and
a world where the gate fell but the crisis never began is worse than one where the turn
simply failed.

# Why these are not facts

`SetFact(world.siege_status = "ongoing")` would be a string in a JSON column. A
situation has a lifecycle, three bounded numbers, participants, a parent, a location
and two timestamps, and every one of those is something a query will need. Expressing
lifecycle changes as generic facts would put all of it in prose and leave the engine
with nothing to filter on.

# Deltas, not values

`UpdateSituation` carries `intensity_delta`, never `intensity`. A caller that supplied
an absolute value would be supplying one it read before the batch began -- and the most
likely such caller is a resolver that ran against state a player action has since
changed. The authoritative value is read inside the transaction and the delta applied
to it. See `state_service`.

# The application mints ids

`StartSituation` has no `id` field, exactly as `LocationProposal` has none. A caller
that could name a uuid could overwrite a live situation, and the caller most likely to
try is a language model reproducing an id it saw in a prompt.
"""

from __future__ import annotations

import uuid
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.vocabulary import MetadataValue, check_flat_metadata, clean_tags, parse_subtype
from app.domain.world_situations.enums import (
    ParticipantEntityType,
    SituationCategory,
    SituationScope,
    SituationStatus,
)
from app.domain.world_situations.situations import (
    MAX_DESCRIPTION_LENGTH,
    MAX_ROLE_LENGTH,
    MAX_TITLE_LENGTH,
    Importance,
    Intensity,
    Momentum,
    Threat,
)

MAX_PARTICIPANTS_PER_SITUATION = 12
"""Enough for two sides and their notable individuals. A process with more than a dozen
named participants is being modelled at the wrong grain -- that is a faction's
membership, and factions do not exist yet."""


class ParticipantSpec(BaseModel):
    """A participant to attach when a situation is created.

    Not the persisted `SituationParticipant`: no id, no situation id, because neither
    exists until the application writes the row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_type: ParticipantEntityType
    entity_id: uuid.UUID
    role: str = Field(min_length=1, max_length=MAX_ROLE_LENGTH)

    @field_validator("role")
    @classmethod
    def _canonical_role(cls, value: str) -> str:
        role = parse_subtype(value)
        if role is None:
            raise ValueError("A participant's role must not be blank.")
        return role


class _SituationMutation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = ""
    """One line for the audit trail. The GameEvent carries the real explanation; this
    is useful when one batch moves six situations for six different reasons."""


class StartSituation(_SituationMutation):
    """A process the world has just begun running."""

    op: Literal["start_situation"] = "start_situation"

    category: SituationCategory
    subtype: str | None = Field(default=None, max_length=60)
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)

    status: SituationStatus = SituationStatus.ACTIVE
    """`PLANNED` for something prepared but not begun -- a summit on the calendar, a
    conspiracy still assembling. Terminal statuses are refused: a situation that starts
    already over is a GameEvent, and this is not how to write one."""

    intensity: Intensity = 30
    threat: Threat = 0
    momentum: Momentum = 0
    importance: Importance = 3

    scope: SituationScope = SituationScope.LOCAL
    primary_location_id: uuid.UUID | None = None
    parent_situation_id: uuid.UUID | None = None

    participants: tuple[ParticipantSpec, ...] = ()
    situation_metadata: dict[str, MetadataValue] = Field(default_factory=dict)
    tags: tuple[str, ...] = ()

    @field_validator("subtype")
    @classmethod
    def _canonical_subtype(cls, value: str | None) -> str | None:
        return parse_subtype(value)

    @field_validator("situation_metadata", mode="before")
    @classmethod
    def _small_flat_metadata(cls, value: object) -> dict[str, MetadataValue]:
        return check_flat_metadata(value, field="situation_metadata")

    @field_validator("tags")
    @classmethod
    def _sane_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return clean_tags(value)

    @model_validator(mode="after")
    def _starts_in_a_live_status(self) -> Self:
        if self.status in {SituationStatus.RESOLVED, SituationStatus.CANCELLED}:
            raise ValueError(
                f"A situation cannot be created {self.status.value}. Something that has "
                "already finished is a GameEvent, not an ongoing process."
            )
        return self

    @model_validator(mode="after")
    def _participants_are_distinct(self) -> Self:
        """One entity, one role, once.

        The same character may well be both `investigator` and `target` -- that is a
        good story -- so the key is the pair. Two identical rows are a caller sending
        the same thing twice.
        """
        if len(self.participants) > MAX_PARTICIPANTS_PER_SITUATION:
            raise ValueError(
                f"At most {MAX_PARTICIPANTS_PER_SITUATION} participants may be attached at "
                f"creation; got {len(self.participants)}."
            )
        seen: set[tuple[ParticipantEntityType, uuid.UUID, str]] = set()
        for participant in self.participants:
            key = (participant.entity_type, participant.entity_id, participant.role)
            if key in seen:
                raise ValueError(
                    f"{participant.entity_type.value} {participant.entity_id} is listed twice "
                    f"as {participant.role!r}."
                )
            seen.add(key)
        return self

    def target(self) -> tuple[str, str]:
        """What this mutation competes for, for the one-mutation-per-target rule.

        Keyed by title, because there is no id yet. Two starts of "Fire at the Broken
        Crown" in one batch is a caller that has lost track of what it is doing; two
        genuinely different fires deserve two different names, and a reader of the
        situation list would need them anyway.
        """
        return ("situation_start", self.title.strip().casefold())


class UpdateSituation(_SituationMutation):
    """Move an existing process. Deltas are applied to the value read at apply time."""

    op: Literal["update_situation"] = "update_situation"

    situation_id: uuid.UUID

    intensity_delta: int = Field(default=0, ge=-100, le=100)
    threat_delta: int = Field(default=0, ge=-100, le=100)
    momentum_delta: int = Field(default=0, ge=-200, le=200)
    """Wider than the others because momentum spans -100..100, so reversing a process
    outright is a 200-point move: a fire at `+100` that the brigade gets on top of goes
    to `-100` in one resolution, and that is a real outcome rather than an overflow."""

    importance: Importance | None = None
    """Absolute, not a delta. Importance is a curation decision -- how much prompt
    budget this deserves -- and nothing accumulates nudges to it."""

    resulting_status: SituationStatus | None = None
    """The status this update leaves the situation in. Validated against the current
    one by `lifecycle.require_transition` at apply time; terminal statuses are refused
    here, because ending a process is `ResolveSituation` and needs a `resolved_at`."""

    situation_metadata: dict[str, MetadataValue] | None = None
    """Replaces the whole bag when present, rather than merging. Merging would make
    "remove this key" unexpressible, and the bag is small enough that a caller
    rewriting it wholesale is reasonable."""

    @field_validator("situation_metadata", mode="before")
    @classmethod
    def _small_flat_metadata(cls, value: object) -> dict[str, MetadataValue] | None:
        if value is None:
            return None
        return check_flat_metadata(value, field="situation_metadata")

    @model_validator(mode="after")
    def _ending_goes_through_resolve(self) -> Self:
        if self.resulting_status in {SituationStatus.RESOLVED, SituationStatus.CANCELLED}:
            raise ValueError(
                f"Use ResolveSituation to make a situation {self.resulting_status.value}. "
                "An ending has to record when it happened, and this mutation has nowhere "
                "to put that."
            )
        return self

    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if not (
            self.intensity_delta
            or self.threat_delta
            or self.momentum_delta
            or self.importance is not None
            or self.resulting_status is not None
            or self.situation_metadata is not None
        ):
            raise ValueError(
                "An update must change at least one thing. An empty one would move the "
                "state revision without moving the world."
            )
        return self

    def target(self) -> tuple[str, str]:
        return ("situation", str(self.situation_id))


class ResolveSituation(_SituationMutation):
    """End a process, and record when.

    Shares a target key with `UpdateSituation`, so one batch cannot both nudge a siege
    and lift it -- which is a caller that has not decided what happened.
    """

    op: Literal["resolve_situation"] = "resolve_situation"

    situation_id: uuid.UUID
    resolution_status: Literal[SituationStatus.RESOLVED, SituationStatus.CANCELLED] = (
        SituationStatus.RESOLVED
    )
    """RESOLVED reached a conclusion. CANCELLED was prevented or abandoned before it
    could. The distinction is worth keeping: a siege that was lifted and a siege that
    was called off read the same in a status column and completely differently in a
    story."""

    @model_validator(mode="after")
    def _resolution_is_explained(self) -> Self:
        """The one place `reason` is required.

        Everything else in a batch is explained by its GameEvent. An ending is the one
        change a future reader is guaranteed to ask about, and "why did the war stop?"
        deserves an answer that does not depend on a join surviving.
        """
        if not self.reason.strip():
            raise ValueError(
                "Resolving a situation requires a reason. It is the one thing anyone "
                "reading this session's history later will ask about."
            )
        return self

    def target(self) -> tuple[str, str]:
        return ("situation", str(self.situation_id))


SituationMutation = StartSituation | UpdateSituation | ResolveSituation
