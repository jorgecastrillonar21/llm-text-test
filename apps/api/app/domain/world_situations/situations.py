"""A process the world is currently running.

    WorldFact       what is objectively true now
    Situation       what ongoing process exists now
    GameEvent       what happened
    ScheduledEvent  what is expected to be processed later

    Situation:       Siege of Asterfall
    GameEvent:       Eastern gate breached
    LocationState:   eastern gate = destroyed
    ScheduledEvent:  evaluate the siege again in six hours

Four models, four questions, and collapsing any pair of them loses one of the answers.
A siege is not the breach; the breach is not the ruined gate; the ruined gate is not
the next evaluation. This module owns the first one.

# Objective, not known

A `Situation` is world state. It is not player knowledge, NPC knowledge, rumour,
memory, or something the narrator said. A conspiracy exists as a `Situation` whether
or not anyone has noticed it -- which is exactly why there is no `known_by_player`
field here and must not be one. Who knows what is `KnowledgeState`'s question, and
`KnowledgeState` does not exist yet; until it does, see the hidden-situation note in
docs/world-state-situations.md for what that costs.

# Three numbers, because one would be a lie

`intensity`, `threat` and `momentum` are independent, and the alternative -- a single
`severity` -- fails on the first festival. A city-wide celebration is `intensity 90,
threat 5`. A siege is `intensity 80, threat 90`. An investigation closing in is
`intensity 70, threat 20` unless it is closing in on the player. Positive processes are
not a special case of this model; they are half of what it is for.

# Situation does not own the world it changes

A reconstruction project finishing does not mean the bridge's condition lives in
`situation_metadata`. It means the project produces a `BRIDGE_REPAIRED` event and an
`UpdateConnectionState`, and the spatial domain stays the one place that knows whether
the bridge stands. Same for participants: this records that a faction is the attacker,
never how many soldiers it has.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.vocabulary import MetadataValue, check_flat_metadata, clean_tags, parse_subtype
from app.domain.world_situations.enums import (
    ParticipantEntityType,
    SituationCategory,
    SituationScope,
    SituationStatus,
)
from app.domain.world_situations.lifecycle import is_terminal

MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 4000
MAX_ROLE_LENGTH = 60

INTENSITY_MIN, INTENSITY_MAX = 0, 100
THREAT_MIN, THREAT_MAX = 0, 100
MOMENTUM_MIN, MOMENTUM_MAX = -100, 100

Intensity = Annotated[int, Field(ge=INTENSITY_MIN, le=INTENSITY_MAX)]
"""How strongly this process is currently manifesting. Deliberately neutral: 90 is a
raging fire and also a city-wide festival."""

Threat = Annotated[int, Field(ge=THREAT_MIN, le=THREAT_MAX)]
"""How dangerous it currently is. Independent of intensity, and *not* a probability --
`threat = 80` does not mean an 80% chance of anything. It is a domain measure that
steers priority and context; outcomes belong to resolution logic."""

Momentum = Annotated[int, Field(ge=MOMENTUM_MIN, le=MOMENTUM_MAX)]
"""Which way it is going, and how fast.

Negative is shrinking, zero is stable, positive is growing. Growing is *not* the same
as worsening: `+50` on a fire is spreading and `+50` on a reconstruction is work
accelerating. Any code that reads positive momentum as bad news has imported a
tone this model does not have."""

Importance = Annotated[int, Field(ge=1, le=5)]
"""How much this deserves a place in a prompt or a simulation pass. Independent of the
other three: a vast distant storm can be `intensity 100, importance 1`, and a small
investigation pointed at the player `intensity 30, importance 5`."""


def clamp_intensity(value: int) -> int:
    return max(INTENSITY_MIN, min(INTENSITY_MAX, value))


def clamp_threat(value: int) -> int:
    return max(THREAT_MIN, min(THREAT_MAX, value))


def clamp_momentum(value: int) -> int:
    return max(MOMENTUM_MIN, min(MOMENTUM_MAX, value))


class SituationParticipant(BaseModel):
    """Someone or something taking part, and in what capacity.

    A reference and a role, nothing more. What the participant can *do* -- its
    soldiers, its money, its influence, its health -- belongs to whatever domain owns
    that entity, and duplicating any of it here would create a second answer that
    drifts from the first.

    `role` is an open string on purpose: `attacker`, `defender`, `investigator`,
    `target`, `organizer`, `beneficiary`, `opponent`, `participant`, and whatever a
    world needs that this list did not anticipate. Normalised like a subtype so
    `Attacker` and `attacker` are one role.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    situation_id: uuid.UUID

    entity_type: ParticipantEntityType
    entity_id: uuid.UUID
    role: str = Field(min_length=1, max_length=MAX_ROLE_LENGTH)

    created_at: dt.datetime

    @field_validator("role")
    @classmethod
    def _canonical_role(cls, value: str) -> str:
        role = parse_subtype(value)
        if role is None:
            raise ValueError("A participant's role must not be blank.")
        return role


class Situation(BaseModel):
    """One ongoing process in one session.

    Session-scoped without exception. Unlike a `LocationDefinition`, there is no
    template form: geography is a property of the world and is shared between saves,
    while a war is something that happened in one of them. A world can *seed* a
    starting situation, but what gets written is a row belonging to that session.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    session_id: uuid.UUID

    category: SituationCategory
    subtype: str | None = None
    """The specific noun: `siege`, `fire`, `festival`, `bridge_reconstruction`. Open,
    because `conflict` is not a thing that happens to anyone."""

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)

    status: SituationStatus = SituationStatus.ACTIVE

    intensity: Intensity = 50
    threat: Threat = 0
    momentum: Momentum = 0
    importance: Importance = 3

    scope: SituationScope = SituationScope.LOCAL

    primary_location_id: uuid.UUID | None = None
    """Where it is centred, when it is centred anywhere. Null for a manhunt, which
    follows a person rather than a place -- and null is the honest answer there, not a
    reason to invent a location for it."""

    parent_situation_id: uuid.UUID | None = None
    """Causal and organisational only. A war contains a siege contains a food crisis.
    Resolving the parent does **not** resolve the children: whether a city's hunger
    ends when the war does is a question about that city, and answering it
    automatically would end a dozen stories the moment a treaty was signed."""

    started_at: int = Field(ge=0)
    """Session `elapsed_minutes`, from Time V1. Fictional time throughout -- there is
    no wall-clock value anywhere in this model that carries simulation meaning, and
    `created_at` below is a row's birthday rather than a story's."""

    last_progressed_at: int = Field(ge=0)
    resolved_at: int | None = Field(default=None, ge=0)

    source_event_id: uuid.UUID | None = None
    """The GameEvent that started this. `KING_ASSASSINATED -> Succession Crisis`.

    Optional because seeded and authored situations have no event to point at -- the
    world simply began that way. Everything created during gameplay should have one,
    and the application layer is where that expectation is enforced rather than here,
    since only it knows which of the two it is doing.
    """

    situation_metadata: dict[str, MetadataValue] = Field(default_factory=dict)
    """A small flat bag for subtype-specific detail that has not earned a model yet --
    an investigation's stage, a ritual's completion. Bounded and scalar-only, and never
    a place to keep state another domain owns. See `app.domain.vocabulary`."""

    tags: tuple[str, ...] = ()
    """`military`, `political`, `urgent`, `public`, `secret`. For retrieval and
    filtering. A tag never replaces a field: `dangerous` is not a substitute for
    `threat`, because nothing can compare two tags."""

    created_at: dt.datetime
    updated_at: dt.datetime

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
    def _time_runs_forward(self) -> Self:
        """A process cannot have progressed or ended before it began."""
        if self.last_progressed_at < self.started_at:
            raise ValueError(
                f"Situation {self.title!r} was last progressed at {self.last_progressed_at} "
                f"but started at {self.started_at}. Fictional time does not run backwards."
            )
        if self.resolved_at is not None and self.resolved_at < self.started_at:
            raise ValueError(
                f"Situation {self.title!r} resolved at {self.resolved_at} but started at "
                f"{self.started_at}."
            )
        return self

    @model_validator(mode="after")
    def _ending_is_recorded(self) -> Self:
        """`resolved_at` and a terminal status imply each other, in both directions.

        A resolved situation with no end time cannot answer "when did the siege lift?",
        which is the question the field exists for. An active situation *with* one is
        worse: it is a row that has already been read as over by anything filtering on
        the timestamp, and as ongoing by anything filtering on the status.
        """
        if is_terminal(self.status) and self.resolved_at is None:
            raise ValueError(
                f"Situation {self.title!r} is {self.status.value} but has no resolved_at. "
                "A concluded process has to say when it concluded."
            )
        if not is_terminal(self.status) and self.resolved_at is not None:
            raise ValueError(
                f"Situation {self.title!r} is {self.status.value} but carries "
                f"resolved_at={self.resolved_at}. Only resolved and cancelled situations "
                "have an ending."
            )
        return self

    @model_validator(mode="after")
    def _cannot_contain_itself(self) -> Self:
        """The one hierarchy rule visible from a single node; cycles need the graph."""
        if self.parent_situation_id == self.id:
            raise ValueError(f"Situation {self.id} cannot be its own parent.")
        return self

    @property
    def is_live(self) -> bool:
        """Still capable of moving. Planned, active or dormant."""
        return not is_terminal(self.status)

    def duration_at(self, elapsed_minutes: int) -> int:
        """How long this has been running, in fictional minutes.

        Measured to `resolved_at` once it has one, so a concluded siege reports the
        three days it lasted rather than growing forever afterwards.
        """
        end = self.resolved_at if self.resolved_at is not None else elapsed_minutes
        return max(0, end - self.started_at)

    def describe(self) -> str:
        detail = f"{self.category.value}/{self.subtype}" if self.subtype else self.category.value
        return f"{self.title} ({detail}, {self.status.value})"
