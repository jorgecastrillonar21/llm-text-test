"""Reviewing the places the Story Director says the story just found.

The sibling of `fact_proposals`, and the same shape of gauntlet:

    a parent the session can actually see
        -> the creation policy allows something this large
        -> nothing by that name is already there
        -> the application mints the id
        -> persisted as canon local to this session
        -> given a starting LocationState

A rejection is not an error. The turn continues, the player sees nothing, and the
narration that mentioned the bookshop still stands -- it just does not become a place
the game will remember.

# Stochastic once, then canon

This is the boundary the whole design turns on. Generating "Starfall Books, east side
of Riverwood" may be a roll of the dice the first time. Once it is validated and
persisted it is *deterministic canon* for that session: the next prompt carries it as
established geography, and the model is not asked to imagine it again. A world where
the bookshop moves every time it is mentioned is a world with no places in it.

# Session-local, always

Everything created here carries `origin_session_id`. Another save of the same world
will never see it. Promoting generated content into the reusable template is a future
editing tool and deliberately has no code path today -- an automatic promotion would
let one player's improvisation rewrite the world everyone else starts from.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from app.application.contracts import LocationProposal
from app.application.persistence import NewLocation, SpatialPort
from app.application.spatial_service import create_location, load_spatial_graph
from app.domain.errors import DomainError

logger = logging.getLogger(__name__)

NARRATED_IMPORTANCE = 2
"""What a place the story mentioned in passing is worth.

Fixed here rather than proposed, for the reason `FactKind` is: importance steers what
future prompts spend their budget on, and a model asked to rate its own invention
rates it highly. A place that deserves more gets it from an authored edit or a game
system, both of which have a reason beyond having just thought of it.
"""


class LocationOutcome(StrEnum):
    CREATED = "created"
    REJECTED = "rejected"


class ReviewedLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    outcome: LocationOutcome
    reason: str
    location_id: uuid.UUID | None = None
    """The id the application minted, for an accepted proposal. Never one the model
    supplied, because it has no field to supply one in."""


class LocationReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    created: list[uuid.UUID] = []
    reviewed: list[ReviewedLocation] = []


async def review_location_proposals(
    store: SpatialPort,
    *,
    session_id: uuid.UUID,
    world_id: uuid.UUID,
    proposals: list[LocationProposal],
) -> LocationReview:
    """Turn a model's claims about geography into the subset the world will accept.

    Writes are staged, not committed: the caller's transaction owns them, so a turn
    that fails afterwards takes the new place with it.
    """
    if not proposals:
        return LocationReview()

    graph = await load_spatial_graph(store, session_id=session_id, world_id=world_id)
    existing_names = {definition.name.strip().casefold() for definition in graph.index}

    created: list[uuid.UUID] = []
    reviewed: list[ReviewedLocation] = []

    for proposal in proposals:
        name = proposal.name.strip()
        if name.casefold() in existing_names:
            # Almost always the model re-establishing somewhere it can already see,
            # which is the failure mode this whole subsystem exists to end. Refusing
            # keeps the original, with its state and its history.
            reviewed.append(
                _rejected(name, "A place with this name already exists in this session.")
            )
            continue

        try:
            location_id = await create_location(
                store,
                session_id=session_id,
                location=NewLocation(
                    world_id=world_id,
                    # Canon for this save only. Never None here: narration does not
                    # write the reusable template.
                    origin_session_id=session_id,
                    name=name,
                    description=proposal.description,
                    category=proposal.category,
                    subtype=proposal.subtype,
                    scale=proposal.scale,
                    parent_location_id=proposal.parent_location_id,
                    importance=NARRATED_IMPORTANCE,
                ),
                narrated=True,
            )
        # `DomainError` covers both refusals a proposal can hit: `ValidationError` from
        # the creation policy or the containment rules, and `NotFoundError` for a parent
        # this session cannot see.
        except (DomainError, PydanticValidationError) as exc:
            reviewed.append(_rejected(name, _first_line(exc)))
            continue

        existing_names.add(name.casefold())
        created.append(location_id)
        reviewed.append(
            ReviewedLocation(
                name=name,
                outcome=LocationOutcome.CREATED,
                reason=proposal.reason or "Established by this turn.",
                location_id=location_id,
            )
        )

    if rejections := [entry for entry in reviewed if entry.outcome is LocationOutcome.REJECTED]:
        logger.info(
            "Rejected %d of %d location proposal(s): %s",
            len(rejections),
            len(proposals),
            "; ".join(f"{entry.name}: {entry.reason}" for entry in rejections),
        )

    return LocationReview(created=created, reviewed=reviewed)


def _rejected(name: str, reason: str) -> ReviewedLocation:
    return ReviewedLocation(name=name, outcome=LocationOutcome.REJECTED, reason=reason)


def _first_line(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else exc.__class__.__name__
