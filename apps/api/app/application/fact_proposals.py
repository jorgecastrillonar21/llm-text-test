"""Reviewing what the Story Director claims the story just established.

The model does not write facts. It proposes them, one property at a time, and each
proposal runs this gauntlet:

    subject the game can resolve
        -> canonical property name
        -> policy allows this authority
        -> nothing is already established there
        -> the world's rules permit it
        -> accepted

Anything that fails is dropped with a reason and the turn continues. A rejected
proposal is not an error: the model guessed at something it is not allowed to decide,
which is the normal case and exactly why the review exists.

# Why an existing value is always a refusal

Once `narrative.birthplace` is Arven, a proposal of Valeria is refused rather than
applied. That is the structural contradiction check: same subject, same canonical
property, different value. It is not a judgement about which is better -- the store
holds current truth and the story has already committed to one.

A proposal repeating the value that is already there is refused too, for a duller
reason: it would bump the state revision without changing anything, and a revision
that moves when nothing moved is worse than useless to whatever ends up watching it.

Changing an established fact needs a game system with the authority to do it. There is
no path here by which narration edits what narration already fixed.

# Locations and factions

The director may only speak about the world and about characters, because those are
the only ids this application can check. A proposal about `location:<uuid>` would name
an entity nothing can confirm exists, and an unverifiable subject is a fact that may
be about nothing at all. When locations become real rows, this restriction is one line
to relax.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from app.application.contracts import FactProposal
from app.application.persistence import WorldSnapshot, WorldStatePort
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.world_facts import (
    FactAuthority,
    FactKind,
    FactSubject,
    FactSubjectType,
    SetFact,
    check_rules_compatibility,
    parse_property,
    require_permitted,
    resolve_policy,
)

logger = logging.getLogger(__name__)

DIRECTOR_SUBJECT_TYPES = frozenset({FactSubjectType.WORLD, FactSubjectType.CHARACTER})
"""The only subjects with ids this application can resolve. See the module docstring."""


class ProposalOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ReviewedProposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    property: str
    """As proposed, before canonicalisation -- a rejection for a malformed name has to
    show the name that was actually sent."""

    outcome: ProposalOutcome
    reason: str


class ProposalReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: list[SetFact] = []
    reviewed: list[ReviewedProposal] = []


async def review_fact_proposals(
    store: WorldStatePort,
    *,
    session_id: uuid.UUID,
    world: WorldSnapshot,
    proposals: list[FactProposal],
    known_character_ids: set[uuid.UUID],
) -> ProposalReview:
    """Turn a model's claims into the subset of them the world will accept."""
    accepted: list[SetFact] = []
    reviewed: list[ReviewedProposal] = []
    claimed: set[tuple[str, str]] = set()

    for proposal in proposals:
        try:
            mutation = _to_mutation(proposal, known_character_ids)
        except (DomainValidationError, PydanticValidationError) as exc:
            reviewed.append(_rejected(proposal, _first_line(exc)))
            continue

        target = mutation.target()
        if target in claimed:
            # Two proposals for one property in a single turn. The batch would refuse
            # the pair anyway; catching it here costs the second one instead of the
            # whole turn's state change.
            reviewed.append(_rejected(proposal, "Already proposed earlier in this turn."))
            continue

        current = await store.get_fact(session_id, mutation.subject, mutation.property)
        if current is not None:
            reviewed.append(_rejected(proposal, _why_already_established(current.value, mutation)))
            continue

        try:
            check_rules_compatibility(world.rules, mutation=mutation, current=None)
        except DomainValidationError as exc:
            reviewed.append(_rejected(proposal, _first_line(exc)))
            continue

        claimed.add(target)
        accepted.append(mutation)
        reviewed.append(
            ReviewedProposal(
                property=proposal.property,
                outcome=ProposalOutcome.ACCEPTED,
                reason=proposal.reason or "Established by this turn.",
            )
        )

    if rejections := [entry for entry in reviewed if entry.outcome is ProposalOutcome.REJECTED]:
        # Visible rather than swallowed: a model that keeps proposing mechanical state
        # is a prompt that needs work, and this is the only place that would show it.
        logger.info(
            "Rejected %d of %d fact proposal(s): %s",
            len(rejections),
            len(proposals),
            "; ".join(f"{entry.property}: {entry.reason}" for entry in rejections),
        )

    return ProposalReview(accepted=accepted, reviewed=reviewed)


def _to_mutation(proposal: FactProposal, known_character_ids: set[uuid.UUID]) -> SetFact:
    """Build the mutation a proposal is asking for, raising if it may not exist."""
    if proposal.subject_type not in DIRECTOR_SUBJECT_TYPES:
        raise DomainValidationError(
            f"The Story Director may only establish facts about the world or a character, "
            f"not about a {proposal.subject_type.value}."
        )

    subject = FactSubject(type=proposal.subject_type, id=proposal.subject_id)
    if subject.type is FactSubjectType.CHARACTER and subject.id not in known_character_ids:
        raise DomainValidationError(
            f"No character {proposal.subject_id} in this world. Facts reference the "
            "character ids given in the context, never names."
        )

    canonical = parse_property(proposal.property)
    require_permitted(
        FactAuthority.STORY_DIRECTOR, resolve_policy(canonical), canonical_property=canonical
    )

    return SetFact(
        subject=subject,
        property=canonical,
        value=proposal.value,
        importance=proposal.importance,
        # Narration establishes diegetic truth, never a progression flag. The kind is
        # decided here rather than proposed, so a model cannot set gameplay state by
        # relabelling what it is doing.
        kind=FactKind.WORLD_TRUTH,
    )


def _why_already_established(current_value: object, mutation: SetFact) -> str:
    if current_value == mutation.value:
        return f"Already established as {current_value!r}."
    return (
        f"Contradicts established truth: {mutation.property} is already {current_value!r}, "
        f"and this proposes {mutation.value!r}. Changing it is not narration's to do."
    )


def _rejected(proposal: FactProposal, reason: str) -> ReviewedProposal:
    return ReviewedProposal(
        property=proposal.property, outcome=ProposalOutcome.REJECTED, reason=reason
    )


def _first_line(exc: Exception) -> str:
    """Pydantic errors are several lines of report; a reason should be one sentence."""
    text = str(exc).strip()
    return text.splitlines()[0] if text else exc.__class__.__name__
