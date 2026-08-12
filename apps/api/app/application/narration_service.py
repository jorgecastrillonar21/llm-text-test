"""Prose for an outcome that has already happened.

This runs *after* the resolution's transaction has closed, and everything about it
follows from that one fact:

    resolve()  -- BEGIN ... COMMIT --  the world has changed, permanently
    narrate()  -- may fail, may be retried, may be regenerated, changes nothing

A failed narration is therefore not a failed turn. The lock was picked, the guard is
dead, the clock has moved; what is missing is a paragraph describing it. The right
response is to say so and offer to try again, never to undo the outcome -- and this
module could not undo it anyway, because `NarrationStorePort` cannot write a fact, an
event, a mutation or the clock.

# Retry and regeneration are different, and only one of them is safe

`narrate_resolution` without `regenerate` is idempotent: an outcome that already has
narration returns the stored prose and the provider is never called. That is what makes
a client's retry free -- of latency, of a second model invocation, and of the risk of
the player seeing two different accounts of one moment.

`regenerate=True` calls the provider again and *replaces* the prose in place. It is
allowed because the paragraph is a description and a better description of the same
outcome is still the same outcome. What is not allowed anywhere is re-running the
mechanical resolution: that would be a second verdict on a settled question. There is
no code path here that can reach a resolver.

# The provider is given a verdict, not a decision

`OutcomeContext` carries the disposition, the reason, what history recorded and the
resolver's own detail -- and no rules, no geography, no relationships, no secrets.
`OutcomeNarration` has exactly one field, and it holds prose. Between them there is
nowhere for a model to write a consequence, so the worst a bad narration can do is read
badly.
"""

from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel, ConfigDict

from app.application.context_builder import build_outcome_context
from app.application.llm_metrics import GenerationPurpose, failed_generation_metrics
from app.application.observability import record_generation_safely
from app.application.persistence import (
    NarrationStorePort,
    NewMessage,
    SessionSnapshot,
    WorldSnapshot,
)
from app.application.ports import LlmMetricsRecorderPort, StoryGeneratorPort
from app.domain.enums import MessageRole
from app.domain.errors import NotFoundError, StoryGenerationError
from app.domain.resolution import ResolutionOutcome

logger = logging.getLogger(__name__)


class NarrationResult(BaseModel):
    """One paragraph, and where it came from."""

    model_config = ConfigDict(frozen=True)

    resolution_id: uuid.UUID
    message_id: uuid.UUID
    narration: str

    generated: bool
    """False when stored prose was returned untouched -- a retry that cost nothing.
    True when the provider actually ran, whether for the first narration or a
    regeneration."""

    provider: str


async def narrate_resolution(
    store: NarrationStorePort,
    generator: StoryGeneratorPort,
    *,
    session_id: uuid.UUID,
    resolution_id: uuid.UUID,
    outcome: ResolutionOutcome | None = None,
    regenerate: bool = False,
    recorder: LlmMetricsRecorderPort | None = None,
) -> NarrationResult:
    """Describe a committed resolution, storing the prose against it.

    Raises `NotFoundError` when the session or the resolution does not exist, and
    `StoryGenerationError` -- from the provider, unswallowed -- when generation fails.
    Neither leaves a mark: the outcome was committed by an earlier transaction and this
    one has written nothing at the point either can be raised.
    """
    session, world = await _load_session(store, session_id)
    resolution = await store.get_resolution(session_id, resolution_id)
    if resolution is None:
        # Session-scoped, so this also covers "that id belongs to another save".
        raise NotFoundError("Resolution", resolution_id)

    existing = await store.get_resolution_narration(session_id, resolution_id)
    if existing is not None and not regenerate:
        # The whole point of the idempotent path: no provider call, no second version of
        # a moment the player has already read.
        return NarrationResult(
            resolution_id=resolution_id,
            message_id=existing.id,
            narration=existing.content,
            generated=False,
            provider=generator.name,
        )

    context = build_outcome_context(
        session=session,
        world=world,
        resolution=resolution,
        events=await store.load_events_for_resolution(resolution_id),
        outcome=outcome,
    )
    try:
        result = await generator.narrate_outcome(context)
    except StoryGenerationError as exc:
        # Recorded before re-raising, for the reason the turn path does it: a narration
        # that reliably fails is a performance fact, and the record that says so is the
        # one nobody writes if the exception simply propagates.
        record_generation_safely(
            recorder,
            failed_generation_metrics(
                exc,
                purpose=GenerationPurpose.OUTCOME_NARRATION,
                session_id=session_id,
            ),
        )
        raise
    generation = result.narration
    # `OutcomeContext` carries no session id -- the provider had no way to know one -- so
    # it is attached here, where one is in scope.
    record_generation_safely(
        recorder,
        result.metrics.for_session(session_id) if result.metrics is not None else None,
    )

    if existing is not None:
        await store.replace_message_content(existing.id, generation.narration)
        message_id = existing.id
        logger.info("Narration for resolution %s regenerated by %s.", resolution_id, generator.name)
    else:
        message_id = await store.add_message(
            NewMessage(
                session_id=session_id,
                # The resolution's own exchange when it had one. A resolution nobody was
                # present for -- an offscreen pass -- is filed against the session's
                # current turn, which is where a player who reads it would meet it.
                turn_index=resolution.turn_index
                if resolution.turn_index is not None
                else session.turn_index,
                role=MessageRole.NARRATOR,
                content=generation.narration,
                resolution_id=resolution_id,
            )
        )

    await store.commit()
    return NarrationResult(
        resolution_id=resolution_id,
        message_id=message_id,
        narration=generation.narration,
        generated=True,
        provider=generator.name,
    )


async def narration_for(
    store: NarrationStorePort, *, session_id: uuid.UUID, resolution_id: uuid.UUID
) -> str | None:
    """The stored prose for a resolution, if it has any. No provider, ever.

    For readers -- an API response listing resolutions, a client checking whether a
    retry is worth making. Separate from `narrate_resolution` because "show me what is
    there" must not be a call that can invoke a language model by accident.
    """
    message = await store.get_resolution_narration(session_id, resolution_id)
    return None if message is None else message.content


async def _load_session(
    store: NarrationStorePort, session_id: uuid.UUID
) -> tuple[SessionSnapshot, WorldSnapshot]:
    session = await store.get_session(session_id)
    if session is None:
        raise NotFoundError("Session", session_id)
    world = await store.get_world(session.world_id)
    if world is None:
        raise NotFoundError("World", session.world_id)
    return session, world
