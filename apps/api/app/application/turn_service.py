"""Executes one game turn.

Transaction strategy: a turn is atomic. Everything -- the player's message, the
narration, dialogue, memories, relationship updates, events and the turn counter
increment -- is staged through TurnGatewayPort and made durable by a single
commit once the story provider has returned a valid TurnGeneration.

If generation fails, nothing is committed and the caller's transaction scope
rolls the staged work back, including the player message. The alternative
(committing the player message first) leaves a session whose transcript ends with
an unanswered action and whose turn counter disagrees with its messages. A failed
turn is therefore a no-op the player can simply retry.

The commit happens here, before the result is returned, rather than in a
framework teardown hook that would run after the response was already sent. A
client that re-reads the transcript on success always sees its own write.

This module knows nothing about SQLAlchemy or the database schema: it depends on
app.application.persistence, which app.infrastructure implements.
"""

from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel

from app.application.context_builder import build_story_context
from app.application.contracts import DialogueLine, TurnGeneration
from app.application.fact_proposals import ProposalReview, review_fact_proposals
from app.application.location_proposals import review_location_proposals
from app.application.persistence import (
    NewEvent,
    NewMemory,
    NewMessage,
    SessionSnapshot,
    TurnGatewayPort,
    WorldSnapshot,
)
from app.application.ports import StoryGeneratorPort
from app.application.state_service import stage_state_change
from app.domain.enums import MessageRole
from app.domain.errors import NotFoundError, ValidationError
from app.domain.relationships import RelationshipVector, clamp_delta
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import FactAuthority

logger = logging.getLogger(__name__)

MAX_ACTION_LENGTH = 2000


class TurnMessage(BaseModel):
    id: uuid.UUID
    turn_index: int
    role: MessageRole
    speaker: str
    speaker_character_id: uuid.UUID | None
    content: str


class AppliedRelationship(BaseModel):
    character_id: uuid.UUID
    trust: int
    affection: int
    respect: int
    fear: int
    reason: str


class TurnResult(BaseModel):
    session_id: uuid.UUID
    turn_index: int
    messages: list[TurnMessage]
    suggested_actions: list[str]
    relationships: list[AppliedRelationship]
    memories_created: int
    events_created: int
    facts_established: int
    """How many of the Story Director's fact proposals survived review. Usually zero,
    and a turn where it is not is worth being able to see without reading the log."""
    facts_rejected: int
    locations_created: int
    """New places the turn established. Usually zero -- most scenes happen somewhere
    that already exists."""
    locations_rejected: int
    visual_cue_generated: bool


async def execute_turn(
    gateway: TurnGatewayPort,
    *,
    session_id: uuid.UUID,
    action: str,
    generator: StoryGeneratorPort,
) -> TurnResult:
    cleaned = action.strip()
    if not cleaned:
        raise ValidationError("Action must not be empty.")
    if len(cleaned) > MAX_ACTION_LENGTH:
        raise ValidationError(f"Action must be at most {MAX_ACTION_LENGTH} characters.")

    session = await gateway.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    world = await gateway.get_world(session.world_id)
    if world is None:  # FK guarantees this, but the type checker does not.
        raise NotFoundError("World", session.world_id)

    turn_index = session.turn_index + 1

    # Staged before generation so the action is part of the transcript the
    # provider reads. Still uncommitted: a failed turn takes it down with it.
    player_message_id = await gateway.add_message(
        NewMessage(
            session_id=session.id,
            turn_index=turn_index,
            role=MessageRole.PLAYER,
            content=cleaned,
        )
    )

    context = await build_story_context(
        gateway, session=session, world=world, player_action=cleaned
    )

    # Raises StoryGenerationError on provider failure -> caller rolls back the turn.
    generation = await generator.generate_turn(context)

    known_character_ids = await gateway.known_character_ids(world.id)

    messages = [
        TurnMessage(
            id=player_message_id,
            turn_index=turn_index,
            role=MessageRole.PLAYER,
            speaker=session.player_name,
            speaker_character_id=None,
            content=cleaned,
        )
    ]

    narrator_message_id = await gateway.add_message(
        NewMessage(
            session_id=session.id,
            turn_index=turn_index,
            role=MessageRole.NARRATOR,
            content=generation.narration,
        )
    )
    messages.append(
        TurnMessage(
            id=narrator_message_id,
            turn_index=turn_index,
            role=MessageRole.NARRATOR,
            speaker="Narrator",
            speaker_character_id=None,
            content=generation.narration,
        )
    )

    for line in generation.dialogue:
        if _is_the_player_speaking(line, session):
            # The player character belongs to the player. A model that puts words in
            # their mouth is overstepping the one boundary the game has, and the line
            # is dropped rather than persisted -- an invented quote becomes canon on
            # the next turn, because the transcript is fed back as established fact.
            logger.warning(
                "Story provider wrote dialogue for the player character %r; line dropped.",
                session.player_name,
            )
            continue

        character_id = line.character_id if line.character_id in known_character_ids else None
        if line.character_id is not None and character_id is None:
            logger.warning(
                "Story provider referenced unknown character %s; storing line unattributed.",
                line.character_id,
            )
        message_id = await gateway.add_message(
            NewMessage(
                session_id=session.id,
                turn_index=turn_index,
                role=MessageRole.CHARACTER,
                speaker_character_id=character_id,
                content=line.text,
            )
        )
        messages.append(
            TurnMessage(
                id=message_id,
                turn_index=turn_index,
                role=MessageRole.CHARACTER,
                speaker=line.speaker,
                speaker_character_id=character_id,
                content=line.text,
            )
        )

    memories_created = await _persist_memories(gateway, session, generation, known_character_ids)
    relationships = await _apply_relationships(gateway, session, generation, known_character_ids)
    events_created = await _persist_events(gateway, session, generation, turn_index)

    # Places first, then facts. A proposal may be about somewhere this turn just
    # established, and the fact reviewer resolves location ids against what exists --
    # so the geography has to be there before the truths about it are judged.
    places = await review_location_proposals(
        gateway,
        session_id=session.id,
        world_id=world.id,
        proposals=generation.location_proposals,
    )
    review = await _establish_proposed_facts(
        gateway, session, world, generation, known_character_ids
    )

    await gateway.set_turn_index(session.id, turn_index)

    # The turn is the transactional boundary. Committing here -- rather than in a
    # dependency teardown that runs after the response is sent -- means a client
    # that refetches the transcript on success always sees this turn.
    await gateway.commit()

    return TurnResult(
        session_id=session.id,
        turn_index=turn_index,
        messages=messages,
        suggested_actions=generation.suggested_actions,
        relationships=relationships,
        memories_created=memories_created,
        events_created=events_created,
        facts_established=len(review.accepted),
        facts_rejected=len(review.reviewed) - len(review.accepted),
        locations_created=len(places.created),
        locations_rejected=len(places.reviewed) - len(places.created),
        visual_cue_generated=generation.visual_cue.generate,
    )


async def _establish_proposed_facts(
    gateway: TurnGatewayPort,
    session: SessionSnapshot,
    world: WorldSnapshot,
    generation: TurnGeneration,
    known_character_ids: set[uuid.UUID],
) -> ProposalReview:
    """Review what the model claims the turn established, and store what survives.

    Staged, not committed: these facts are part of the turn, and a turn is atomic. No
    GameEvent is minted for them either -- the turn is already the event that produced
    them, and a `FACT_CREATED` row for "Elena dislikes olives" would bury the history
    that matters under the history that does not. `story_director` is exempt from the
    source-event requirement for exactly this case; see `world_facts.authority`.
    """
    review = await review_fact_proposals(
        gateway,
        session_id=session.id,
        world=world,
        proposals=generation.fact_proposals,
        known_character_ids=known_character_ids,
    )
    if review.accepted:
        await stage_state_change(
            gateway,
            session_id=session.id,
            batch=StateMutationBatch(
                authority=FactAuthority.STORY_DIRECTOR, mutations=list(review.accepted)
            ),
        )
    return review


def _is_the_player_speaking(line: DialogueLine, session: SessionSnapshot) -> bool:
    """True when a dialogue line is attributed to the player rather than an NPC.

    Only unattributed lines are candidates: an NPC that genuinely shares the player's
    name carries a real character_id and is left alone.
    """
    if line.character_id is not None:
        return False
    return line.speaker.strip().casefold() == session.player_name.strip().casefold()


async def _persist_memories(
    gateway: TurnGatewayPort,
    session: SessionSnapshot,
    generation: TurnGeneration,
    known_character_ids: set[uuid.UUID],
) -> int:
    count = 0
    for candidate in generation.memory_candidates:
        character_id = (
            candidate.character_id if candidate.character_id in known_character_ids else None
        )
        await gateway.add_memory(
            NewMemory(
                session_id=session.id,
                character_id=character_id,
                kind=candidate.kind,
                summary=candidate.summary,
                importance=candidate.importance,
            )
        )
        count += 1
    return count


async def _apply_relationships(
    gateway: TurnGatewayPort,
    session: SessionSnapshot,
    generation: TurnGeneration,
    known_character_ids: set[uuid.UUID],
) -> list[AppliedRelationship]:
    applied: list[AppliedRelationship] = []

    for change in generation.relationship_changes:
        if change.character_id not in known_character_ids:
            logger.warning(
                "Story provider proposed a relationship change for unknown character %s; ignored.",
                change.character_id,
            )
            continue

        current = await gateway.get_relationship(session.id, change.character_id)
        existing = (
            RelationshipVector(
                trust=current.trust,
                affection=current.affection,
                respect=current.respect,
                fear=current.fear,
            )
            if current is not None
            else RelationshipVector()
        )

        # Clamping is the application's job; the model only proposes.
        updated = existing.apply(
            trust_delta=clamp_delta(change.trust_delta),
            affection_delta=clamp_delta(change.affection_delta),
            respect_delta=clamp_delta(change.respect_delta),
            fear_delta=clamp_delta(change.fear_delta),
        )
        await gateway.save_relationship(session.id, change.character_id, updated)

        applied.append(
            AppliedRelationship(
                character_id=change.character_id,
                trust=updated.trust,
                affection=updated.affection,
                respect=updated.respect,
                fear=updated.fear,
                reason=change.reason,
            )
        )

    return applied


async def _persist_events(
    gateway: TurnGatewayPort,
    session: SessionSnapshot,
    generation: TurnGeneration,
    turn_index: int,
) -> int:
    for event in generation.world_events:
        await gateway.add_event(
            NewEvent(
                session_id=session.id,
                turn_index=turn_index,
                # A turn does not move the clock, so everything it records happens at
                # the session's current fictional minute. Turn index and fictional
                # time are stamped separately because they answer different
                # questions -- and because a later action that costs eight hours will
                # make them diverge sharply.
                occurred_at=session.elapsed_minutes,
                type=event.type,
                description=event.description,
            )
        )
    return len(generation.world_events)
