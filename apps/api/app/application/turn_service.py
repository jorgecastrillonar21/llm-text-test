"""Executes one game turn.

Transaction strategy: a turn is atomic. Everything -- the player's message, the
narration, dialogue, memories, relationship updates, events and the turn counter
increment -- is written inside one transaction that the caller commits only after
the story provider has returned a valid TurnGeneration.

If generation fails, the whole transaction rolls back, including the player
message. The alternative (committing the player message first) leaves a session
whose transcript ends with an unanswered action and whose turn counter disagrees
with its messages. A failed turn is therefore a no-op the player can simply retry.
"""

from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.context_builder import build_story_context
from app.application.contracts import TurnGeneration
from app.application.ports import StoryGeneratorPort
from app.domain.enums import MessageRole
from app.domain.errors import NotFoundError, ValidationError
from app.domain.relationships import RelationshipVector, clamp_delta
from app.infrastructure.db import models

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
    visual_cue_generated: bool


async def execute_turn(
    db: AsyncSession,
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

    session = await db.get(models.GameSession, session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    world = await db.get(models.World, session.world_id)
    if world is None:  # FK guarantees this, but the type checker does not.
        raise NotFoundError("World", session.world_id)

    turn_index = session.turn_index + 1

    player_message = models.Message(
        session_id=session.id,
        turn_index=turn_index,
        role=MessageRole.PLAYER,
        content=cleaned,
    )
    db.add(player_message)
    # Flush so the action is part of the context the provider sees, without committing.
    await db.flush()

    context = await build_story_context(db, session=session, world=world, player_action=cleaned)

    # Raises StoryGenerationError on provider failure -> caller rolls back the turn.
    generation = await generator.generate_turn(context)

    known_character_ids = set(
        (
            await db.execute(
                select(models.Character.id).where(models.Character.world_id == world.id)
            )
        ).scalars()
    )

    messages = [
        TurnMessage(
            id=player_message.id,
            turn_index=turn_index,
            role=MessageRole.PLAYER,
            speaker=session.player_name,
            speaker_character_id=None,
            content=cleaned,
        )
    ]

    narrator_message = models.Message(
        session_id=session.id,
        turn_index=turn_index,
        role=MessageRole.NARRATOR,
        content=generation.narration,
    )
    db.add(narrator_message)
    await db.flush()
    messages.append(
        TurnMessage(
            id=narrator_message.id,
            turn_index=turn_index,
            role=MessageRole.NARRATOR,
            speaker="Narrator",
            speaker_character_id=None,
            content=generation.narration,
        )
    )

    for line in generation.dialogue:
        character_id = line.character_id if line.character_id in known_character_ids else None
        if line.character_id is not None and character_id is None:
            logger.warning(
                "Story provider referenced unknown character %s; storing line unattributed.",
                line.character_id,
            )
        row = models.Message(
            session_id=session.id,
            turn_index=turn_index,
            role=MessageRole.CHARACTER,
            speaker_character_id=character_id,
            content=line.text,
        )
        db.add(row)
        await db.flush()
        messages.append(
            TurnMessage(
                id=row.id,
                turn_index=turn_index,
                role=MessageRole.CHARACTER,
                speaker=line.speaker,
                speaker_character_id=character_id,
                content=line.text,
            )
        )

    memories_created = _persist_memories(db, session, generation, known_character_ids)
    relationships = await _apply_relationships(db, session, generation, known_character_ids)
    events_created = _persist_events(db, session, generation, turn_index)

    session.turn_index = turn_index

    # The turn is the transactional boundary. Committing here -- rather than in a
    # dependency teardown that runs after the response is sent -- means a client
    # that refetches the transcript on success always sees this turn.
    await db.commit()

    return TurnResult(
        session_id=session.id,
        turn_index=turn_index,
        messages=messages,
        suggested_actions=generation.suggested_actions,
        relationships=relationships,
        memories_created=memories_created,
        events_created=events_created,
        visual_cue_generated=generation.visual_cue.generate,
    )


def _persist_memories(
    db: AsyncSession,
    session: models.GameSession,
    generation: TurnGeneration,
    known_character_ids: set[uuid.UUID],
) -> int:
    count = 0
    for candidate in generation.memory_candidates:
        character_id = (
            candidate.character_id if candidate.character_id in known_character_ids else None
        )
        db.add(
            models.Memory(
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
    db: AsyncSession,
    session: models.GameSession,
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

        row = (
            await db.execute(
                select(models.Relationship).where(
                    models.Relationship.session_id == session.id,
                    models.Relationship.character_id == change.character_id,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            # Axes are set explicitly: column defaults are not materialised on the
            # instance until flush, and they are read below before that happens.
            row = models.Relationship(
                session_id=session.id,
                character_id=change.character_id,
                trust=0,
                affection=0,
                respect=0,
                fear=0,
            )
            db.add(row)

        # Clamping is the application layer's job; the model only proposes.
        updated = RelationshipVector(
            trust=row.trust, affection=row.affection, respect=row.respect, fear=row.fear
        ).apply(
            trust_delta=clamp_delta(change.trust_delta),
            affection_delta=clamp_delta(change.affection_delta),
            respect_delta=clamp_delta(change.respect_delta),
            fear_delta=clamp_delta(change.fear_delta),
        )
        row.trust = updated.trust
        row.affection = updated.affection
        row.respect = updated.respect
        row.fear = updated.fear
        await db.flush()

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


def _persist_events(
    db: AsyncSession,
    session: models.GameSession,
    generation: TurnGeneration,
    turn_index: int,
) -> int:
    for event in generation.world_events:
        db.add(
            models.GameEvent(
                session_id=session.id,
                turn_index=turn_index,
                type=event.type,
                description=event.description,
            )
        )
    return len(generation.world_events)
