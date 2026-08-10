"""Reviewing the processes the Story Director says the story just set in motion.

The third of these reviewers, and the strictest:

    nothing by that title is already running
        -> the location, if named, is one this session can see
        -> the application chooses every number
        -> becomes a StartSituation the application submits
        -> persisted as canon for this session

A rejection is not an error. The turn continues, the player sees nothing, and the
narration that mentioned the riot still stands -- it just does not become a process the
game will simulate.

# The application decides the numbers, all of them

`SituationProposal` carries no `intensity`, no `threat`, no `momentum`, no
`importance`, and no `status`. This module supplies them, from constants below, and a
narrated process always begins small: something just started, in the scene, and it has
not had time to become anything yet.

The alternative was to let the model propose numbers and clamp them. That reads as a
compromise and is really a surrender -- clamping `intensity: 100` to 40 still lets a
sentence decide that a fire is severe, and "the model may not alter intensity" stops
meaning anything the moment it may suggest one.

If a process deserves to start large, a game system starts it large. That system has a
reason beyond having just written the word "inferno".

# It writes through the service, not through a batch

An accepted proposal becomes a `StartSituation` -- the same typed mutation a resolver
produces -- handed to `start_situation`, which validates the location, the parent and
the participants and mints the id. It does *not* go through `StateMutationBatch`, for
the reason an accepted location proposal does not: the turn is already the unit of
work, and wrapping one narrated process in its own batch would mint a `state_revision`
bump and a GameEvent for "a scene mentioned a scuffle".

Progression takes the batch path, because a progression genuinely is a resolved
outcome that changes several domains at once. Narration is not.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from app.application.contracts import SituationProposal
from app.application.persistence import SituationPort
from app.application.situation_service import MAX_SITUATIONS, start_situation
from app.domain.errors import DomainError
from app.domain.world_situations import (
    LIVE_STATUSES,
    SituationStatus,
    StartSituation,
)

logger = logging.getLogger(__name__)

NARRATED_INTENSITY = 20
"""What a process the story just mentioned is manifesting at.

Low, and fixed. Something that began this turn has been going for one scene. A siege
that deserves to open at 80 is one a game system declared, not one a sentence did.
"""

NARRATED_THREAT = 0
"""How dangerous a narrated process starts. Zero, because danger is a judgement about
consequences and narration has not established any yet. A resolver or a game system
raises it once something is actually at risk."""

NARRATED_MOMENTUM = 20
"""Which way it is going. Mildly growing: something that just started is, by default,
still starting. Small enough that an unattended narrated process drifts slowly and
then stalls, rather than escalating on its own into a crisis nobody wrote."""

NARRATED_IMPORTANCE = 2
"""How much prompt budget it earns. The same value narrated locations get, for the same
reason: a model asked to rate its own invention rates it highly."""


class SituationOutcome(StrEnum):
    CREATED = "created"
    REJECTED = "rejected"


class ReviewedSituation(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    outcome: SituationOutcome
    reason: str
    situation_id: uuid.UUID | None = None
    """The id the application minted, for an accepted proposal. Never one the model
    supplied, because it has no field to supply one in."""


class SituationReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    created: list[uuid.UUID] = []
    reviewed: list[ReviewedSituation] = []


async def review_situation_proposals(
    store: SituationPort,
    *,
    session_id: uuid.UUID,
    proposals: list[SituationProposal],
    started_at: int,
) -> SituationReview:
    """Turn a model's claims about what began into the subset the world will accept.

    Writes are staged, not committed: the caller's transaction owns them, so a turn
    that fails afterwards takes the new process with it.

    `started_at` is the session's fictional clock, passed in rather than read here --
    the caller already has it, and a second read could disagree with the one the rest
    of the turn used.
    """
    if not proposals:
        return SituationReview()

    # Live titles only. A resolved "Tavern Fire" from three days ago should not stop a
    # second fire in the same tavern -- that is a new process, and refusing it would be
    # the history preventing the story.
    existing = await store.load_situations(session_id, statuses=LIVE_STATUSES, limit=MAX_SITUATIONS)
    running = {situation.title.strip().casefold() for situation in existing}

    created: list[uuid.UUID] = []
    reviewed: list[ReviewedSituation] = []

    for proposal in proposals:
        title = proposal.title.strip()
        if title.casefold() in running:
            # Almost always the model re-establishing something it can already see in
            # the context, which is the failure mode this whole subsystem exists to end.
            # Refusing keeps the original, with its numbers and its history.
            reviewed.append(
                _rejected(title, "A situation with this title is already running in this session.")
            )
            continue

        try:
            situation_id = await start_situation(
                store,
                session_id=session_id,
                mutation=StartSituation(
                    category=proposal.category,
                    subtype=proposal.subtype,
                    title=title,
                    description=proposal.description or None,
                    # Everything below is the application's decision, not the model's.
                    status=SituationStatus.ACTIVE,
                    intensity=NARRATED_INTENSITY,
                    threat=NARRATED_THREAT,
                    momentum=NARRATED_MOMENTUM,
                    importance=NARRATED_IMPORTANCE,
                    scope=proposal.scope,
                    primary_location_id=proposal.primary_location_id,
                    # No participants. Attaching them means resolving who the story
                    # meant, and a model naming character ids for roles it invented is
                    # a different review than this one. Participants are attached by
                    # game systems and by authoring.
                    participants=(),
                    reason=proposal.reason or "Established by this turn.",
                ),
                started_at=started_at,
            )
        # `DomainError` covers both refusals a proposal can hit: `NotFoundError` for a
        # location this session cannot see, and `ValidationError` from the model's own
        # invariants.
        except (DomainError, PydanticValidationError) as exc:
            reviewed.append(_rejected(title, _first_line(exc)))
            continue

        running.add(title.casefold())
        created.append(situation_id)
        reviewed.append(
            ReviewedSituation(
                title=title,
                outcome=SituationOutcome.CREATED,
                reason=proposal.reason or "Established by this turn.",
                situation_id=situation_id,
            )
        )

    if rejections := [entry for entry in reviewed if entry.outcome is SituationOutcome.REJECTED]:
        logger.info(
            "Rejected %d of %d situation proposal(s): %s",
            len(rejections),
            len(proposals),
            "; ".join(f"{entry.title}: {entry.reason}" for entry in rejections),
        )

    return SituationReview(created=created, reviewed=reviewed)


def _rejected(title: str, reason: str) -> ReviewedSituation:
    return ReviewedSituation(title=title, outcome=SituationOutcome.REJECTED, reason=reason)


def _first_line(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else exc.__class__.__name__
