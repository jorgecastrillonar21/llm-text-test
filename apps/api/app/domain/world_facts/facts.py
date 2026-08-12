"""The WorldFact: one statement of current objective truth in one session.

# What a fact is not

    GameEvent      "King Aldren was assassinated."   history, append-only
    StateMutation  "Aldren.alive: true -> false"     the transition itself
    WorldFact      "Aldren.alive = false"            what is true right now
    Memory         "Elena remembers the body."       what someone recalls
    Belief         "Kael thinks he survived."        what someone thinks

Only the third one lives here. A fact carries no `known_by`, no `believed_by` and no
`confidence`, and none of those may be added: the moment this table starts holding
opinions, "is it true?" and "does anyone know?" become the same query, and every
future knowledge system inherits the confusion. Those belong to KnowledgeState and
BeliefState, which do not exist yet.

The store is objective, and objectivity is not omniscience. A fact may be true with
nobody aware of it, and a fact *about* a belief -- `world.church_doctrine` -- is a
true statement about what a church teaches, not an endorsement of the doctrine.

# History lives in events, not here

There is exactly one current row per subject and property. A change replaces the
value in place. Reconstructing what was true last Tuesday is what GameEvents are for;
keeping superseded facts alongside current ones would mean every read had to know
which was which, and the first read that forgot would hand a language model a
resurrected king.
"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.world_facts.authority import FactAuthority
from app.domain.world_facts.properties import parse_property
from app.domain.world_facts.values import FactValue, check_fact_value

Importance = Annotated[int, Field(ge=1, le=5)]
"""How much a fact matters for context selection and summarisation. Never a permission
-- see `world_facts.policy`."""

MAX_TAGS = 8
MAX_TAG_LENGTH = 40


class FactKind(StrEnum):
    WORLD_TRUTH = "world_truth"
    """Diegetic reality. `east_bridge.condition = destroyed` is something a character
    could walk up to and see."""

    GAMEPLAY_FLAG = "gameplay_flag"
    """Internal progression state. `palace_secret_discovered = true` need not
    correspond to any sentence anyone in the world would say."""


class FactSubjectType(StrEnum):
    WORLD = "world"
    CHARACTER = "character"
    LOCATION = "location"
    FACTION = "faction"
    OTHER = "other"


class FactSubject(BaseModel):
    """What a fact is about: a type and, for everything but the world itself, an id.

    Bundled into one value object rather than two loose fields so the invariant below
    cannot be sidestepped by constructing the halves separately. Identity is always a
    canonical id -- never a display name, because names are not unique, change, and
    are exactly what a language model will produce if allowed to.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: FactSubjectType
    id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _identity_matches_type(self) -> Self:
        if self.type is FactSubjectType.WORLD:
            if self.id is not None:
                raise ValueError(
                    "The world subject is the session's world itself and takes no id; "
                    f"got {self.id}."
                )
            return self
        if self.id is None:
            raise ValueError(f"A {self.type.value!r} fact subject requires an id.")
        return self

    @property
    def key(self) -> str:
        """A stable string form, for log lines and error messages."""
        return self.type.value if self.id is None else f"{self.type.value}:{self.id}"


WORLD_SUBJECT = FactSubject(type=FactSubjectType.WORLD)
"""The session's world as a whole. Shared because it is a constant, not a lookup."""


class WorldFact(BaseModel):
    """One current, authoritative truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID
    session_id: uuid.UUID
    """Facts are per session. Two sessions of the same world diverge from the first
    turn and never see each other's state."""

    kind: FactKind
    subject: FactSubject
    property: str
    value: FactValue
    importance: Importance

    current_value_since: int = Field(ge=0)
    """The simulation minute at which *this* value became authoritative -- session
    elapsed minutes, from Time V1. Not a wall-clock date and not a duplicate of
    `updated_at`: one says when it became true in the story, the other when the row
    was written."""

    authority: FactAuthority
    source_event_id: uuid.UUID | None = None
    tags: tuple[str, ...] = ()

    created_at: dt.datetime
    updated_at: dt.datetime

    @field_validator("property")
    @classmethod
    def _canonical_property(cls, value: str) -> str:
        return parse_property(value)

    @field_validator("value", mode="before")
    @classmethod
    def _storable_value(cls, value: object) -> FactValue:
        """Runs before the union check so bad input gets this module's error message
        rather than a seven-branch pydantic union report."""
        return check_fact_value(value)

    @field_validator("tags")
    @classmethod
    def _sane_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_TAGS:
            raise ValueError(f"A fact may carry at most {MAX_TAGS} tags; got {len(value)}.")
        cleaned: list[str] = []
        for tag in value:
            text = tag.strip().casefold()
            if not text:
                continue
            if len(text) > MAX_TAG_LENGTH:
                raise ValueError(f"Tag {tag!r} is longer than {MAX_TAG_LENGTH} characters.")
            if text not in cleaned:
                cleaned.append(text)
        return tuple(cleaned)

    def describe(self) -> str:
        """`character:<id> system.alive = False`. For logs and audit descriptions."""
        return f"{self.subject.key} {self.property} = {self.value!r}"
