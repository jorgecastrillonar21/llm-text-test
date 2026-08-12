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

Idempotency: a turn carrying a `client_action_id` records a `ResolutionRecord` under
that key, and `(session_id, idempotency_key)` is unique in the database. A retry of the
same submission finds that record and returns the turn's stored transcript -- the
provider is not called again, no second event is written, no second fact is
established. That is the whole reason the record exists here; see
`app.application.resolution_service` for the same mechanism around Commands.

# The turn is not a Command, and its record says so

There is no Intent Interpreter in this build, so a free-text action is not parsed into
a `Command` and no mechanical resolver runs. What happens instead is that the Story
Director *proposes* -- facts, places, situations, events -- and the application reviews
each proposal against policy. The `ResolutionRecord` a turn writes describes that
review, under `resolver_name="turn"`. It is deliberately not a claim that the prose
resolved anything mechanically: the model narrates, and the reviewers decide.

This module knows nothing about SQLAlchemy or the database schema: it depends on
app.application.persistence, which app.infrastructure implements.
"""

from __future__ import annotations

import logging
import time
import uuid

from pydantic import BaseModel

from app.application.context_builder import CHARACTER_LIMIT, build_story_context
from app.application.contracts import DialogueLine, TurnGeneration
from app.application.event_service import ReviewedEvent, select_events, write_events
from app.application.fact_proposals import ProposalReview, review_fact_proposals
from app.application.llm_metrics import (
    GenerationPurpose,
    TurnPerformanceMetrics,
    failed_generation_metrics,
)
from app.application.location_proposals import review_location_proposals
from app.application.observability import record_generation_safely, record_turn_safely
from app.application.persistence import (
    NewMemory,
    NewMessage,
    NewResolution,
    SessionSnapshot,
    TurnGatewayPort,
    WorldSnapshot,
)
from app.application.ports import LlmMetricsRecorderPort, StoryGeneratorPort
from app.application.situation_proposals import review_situation_proposals
from app.application.state_service import ChangeCause, stage_state_change
from app.domain.enums import MessageRole
from app.domain.errors import NotFoundError, StoryGenerationError, ValidationError
from app.domain.relationships import RelationshipVector, clamp_delta
from app.domain.resolution import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    EventCandidate,
    Resolution,
    ResolutionDisposition,
    ResolutionSourceType,
)
from app.domain.state_mutations import StateMutationBatch
from app.domain.world_facts import FactAuthority

logger = logging.getLogger(__name__)

MAX_ACTION_LENGTH = 2000

MAX_CLIENT_ACTION_ID_LENGTH = MAX_IDEMPOTENCY_KEY_LENGTH - len("turn:")

TURN_RESOLVER_NAME = "turn"
TURN_RESOLVER_VERSION = "1"
"""What the `ResolutionRecord` of a turn is attributed to.

Not the model, and not the prompt version. What decided the mechanical outcome of a
turn is the proposal review pipeline -- `fact_proposals`, `location_proposals`,
`situation_proposals` and the event policy -- and that is the thing whose behaviour a
future reader would need to know the version of. The prose is narration; it is recorded
as messages, which is where prose belongs.
"""

UNKNOWN_SPEAKER = "Unknown"
"""Used only when replaying a character line whose character has since been deleted.
The speaker label a model chose is not stored -- the character id is -- so a replayed
line is labelled from the character record, and a missing record has no name to give."""


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
    situations_started: int
    """Ongoing processes the turn set in motion. Zero on almost every turn, and a turn
    where it is not is one worth being able to see without reading the log."""
    situations_rejected: int
    visual_cue_generated: bool

    resolution_id: uuid.UUID
    """The record of what this turn's proposals established.

    Always present. A turn submitted without a `client_action_id` is recorded too, under
    a key derived from its turn index -- it is simply not *replayable*, which is a
    different thing from unrecorded. See `_record_turn_resolution`."""

    replayed: bool = False
    """True when this response is a previously played turn, returned because the same
    `client_action_id` arrived twice. The transcript is the original one, re-read.

    Suggestions and the per-stage counts come back empty on a replay, because they are
    not stored: a suggestion is an affordance the player can ignore, and persisting
    model output that nothing else reads in order to make a retry byte-identical would
    be storing it for the benefit of the retry alone. `events_created` and
    `facts_established` survive, because the `ResolutionRecord` counted them.
    """


async def execute_turn(
    gateway: TurnGatewayPort,
    *,
    session_id: uuid.UUID,
    action: str,
    generator: StoryGeneratorPort,
    client_action_id: str | None = None,
    recorder: LlmMetricsRecorderPort | None = None,
) -> TurnResult:
    """Play one turn, or return the one this submission already played.

    `client_action_id` is the client's stable name for *this submission* -- not for the
    request that carries it. A transport retry must send the same id, and a genuinely
    new action must send a new one; the server has no way to tell those apart and must
    not guess. Omitting it is allowed and means the turn is not replayable: the record
    is still written, under a key derived from the turn index, but a retry with no id
    plays a second turn.

    `recorder` is optional and observational. Nothing below branches on it, nothing it
    returns reaches the result, and a turn played without one is identical in every
    respect except that nobody wrote down how long it took.
    """
    turn_started = time.perf_counter()
    cleaned = action.strip()
    if not cleaned:
        raise ValidationError("Action must not be empty.")
    if len(cleaned) > MAX_ACTION_LENGTH:
        raise ValidationError(f"Action must be at most {MAX_ACTION_LENGTH} characters.")
    if client_action_id is not None and len(client_action_id) > MAX_CLIENT_ACTION_ID_LENGTH:
        raise ValidationError(
            f"client_action_id must be at most {MAX_CLIENT_ACTION_ID_LENGTH} characters."
        )

    session = await gateway.get_session(session_id)
    if session is None:
        raise NotFoundError("GameSession", session_id)

    world = await gateway.get_world(session.world_id)
    if world is None:  # FK guarantees this, but the type checker does not.
        raise NotFoundError("World", session.world_id)

    if client_action_id:
        # Before anything: before the player's message is staged, and long before the
        # provider is called. A replay that got as far as generating would have paid for
        # the model run it exists to avoid.
        played = await gateway.find_resolution_by_key(session_id, _turn_key(client_action_id))
        if played is not None:
            logger.info(
                "Turn for client_action_id %r on session %s was already played "
                "(resolution %s); returning it.",
                client_action_id,
                session_id,
                played.id,
            )
            return await _replay_turn(gateway, session=session, world=world, resolution=played)

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
    # Measured from here rather than inside the adapter, because this is the span the
    # player waits through: the request, the queue, the model, and the decode.
    generation_started = time.perf_counter()
    try:
        result = await generator.generate_turn(context)
    except StoryGenerationError as exc:
        # A failure is a measurement too, and the one most worth having: a provider that
        # times out after ninety seconds and one that refuses in twelve milliseconds are
        # the same exception and completely different problems. Recorded, then re-raised
        # unchanged -- the turn still fails and still rolls back.
        record_generation_safely(
            recorder,
            failed_generation_metrics(
                exc,
                purpose=GenerationPurpose.STORY_TURN,
                session_id=session.id,
                fallback_elapsed_ms=_elapsed_ms(generation_started),
            ),
        )
        record_turn_safely(
            recorder,
            TurnPerformanceMetrics(
                session_id=session.id,
                turn_index=turn_index,
                total_turn_ms=_elapsed_ms(turn_started),
                story_generation_ms=_elapsed_ms(generation_started),
                llm_call_count=1,
            ),
        )
        raise
    story_generation_ms = _elapsed_ms(generation_started)
    generation = result.generation
    # The session is attached here, not in the adapter: `narrate_outcome` has no session
    # to attach, and a provider should not have to know which of its callers happens to
    # know one.
    record_generation_safely(
        recorder,
        result.metrics.for_session(session.id) if result.metrics is not None else None,
    )

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

    # Reviewed here, written after the resolution row exists: events carry its foreign
    # key, and the row has to state how many events it produced. Deciding first and
    # writing second is what lets `event_count` be the truth rather than a count of what
    # was offered before policy dropped half of it.
    selected_events = await select_events(
        gateway,
        session_id=session.id,
        # A turn does not move the clock, so everything it records happens at the
        # session's current fictional minute. Turn index and fictional time are stamped
        # separately because they answer different questions -- and because a later
        # action that costs eight hours will make them diverge sharply.
        occurred_at=session.elapsed_minutes,
        candidates=_event_candidates(generation),
    )

    # Places, then situations, then facts. Each stage may name something the previous
    # one established: a situation can be centred on a location this turn created, and
    # a fact can be about either. Reversing the order would make a correct proposal fail
    # for referring to something that does not exist yet.
    places = await review_location_proposals(
        gateway,
        session_id=session.id,
        world_id=world.id,
        proposals=generation.location_proposals,
    )
    situations = await review_situation_proposals(
        gateway,
        session_id=session.id,
        proposals=generation.situation_proposals,
        # A turn does not move the clock, so anything it starts starts now.
        started_at=session.elapsed_minutes,
    )
    review = await review_fact_proposals(
        gateway,
        session_id=session.id,
        world=world,
        proposals=generation.fact_proposals,
        known_character_ids=known_character_ids,
    )

    # Everything the three reviews let through, counted together. A turn is one
    # resolution, and one resolution moves the state revision exactly once -- so the
    # count that decides whether it moves has to include the places and the situations,
    # not just the facts. Counting only facts is how a turn that founded a bookshop and
    # started a siege used to leave the revision sitting still, telling every client
    # polling it that nothing had happened.
    mutation_count = len(review.accepted) + len(places.created) + len(situations.created)

    resolution_id = await _record_turn_resolution(
        gateway,
        session=session,
        turn_index=turn_index,
        client_action_id=client_action_id,
        events=selected_events,
        mutation_count=mutation_count,
    )
    event_ids = await write_events(
        gateway,
        session_id=session.id,
        turn_index=turn_index,
        occurred_at=session.elapsed_minutes,
        reviewed=selected_events,
        resolution_id=resolution_id,
    )
    await _establish_proposed_facts(
        gateway,
        session=session,
        review=review,
        cause=ChangeCause(
            resolution_id=resolution_id, event_id=event_ids[0] if event_ids else None
        ),
    )

    if mutation_count:
        # The turn's single bump, once everything it established is staged. Here rather
        # than inside `_establish_proposed_facts` because places and situations are
        # written by their own services and never reach that batch: a bump living with
        # the facts would only ever have counted a third of what changed.
        await gateway.bump_state_revision(session.id)

    await gateway.set_turn_index(session.id, turn_index)

    # The turn is the transactional boundary. Committing here -- rather than in a
    # dependency teardown that runs after the response is sent -- means a client
    # that refetches the transcript on success always sees this turn.
    await gateway.commit()

    # After the commit, so the number includes the write the player is actually waiting
    # for. This is the record that answers the only question worth asking about a slow
    # turn: was it the model, or was it us? One LLM call per turn today -- see
    # `docs/llm-performance-baseline.md`.
    record_turn_safely(
        recorder,
        TurnPerformanceMetrics(
            session_id=session.id,
            turn_index=turn_index,
            total_turn_ms=_elapsed_ms(turn_started),
            story_generation_ms=story_generation_ms,
            llm_call_count=1,
        ),
    )

    return TurnResult(
        session_id=session.id,
        turn_index=turn_index,
        messages=messages,
        suggested_actions=generation.suggested_actions,
        relationships=relationships,
        memories_created=memories_created,
        events_created=len(event_ids),
        facts_established=len(review.accepted),
        facts_rejected=len(review.reviewed) - len(review.accepted),
        locations_created=len(places.created),
        locations_rejected=len(places.reviewed) - len(places.created),
        situations_started=len(situations.created),
        situations_rejected=len(situations.reviewed) - len(situations.created),
        visual_cue_generated=generation.visual_cue.generate,
        resolution_id=resolution_id,
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _turn_key(client_action_id: str) -> str:
    """The idempotency key a turn is recorded under.

    Namespaced, so a client cannot pick a string that collides with a key the engine
    mints for something else -- `dev:progress:...`, and whatever a future command
    scheduler uses.
    """
    return f"turn:{client_action_id}"


def _event_candidates(generation: TurnGeneration) -> list[EventCandidate]:
    """The model's proposed events, in the vocabulary the policy speaks.

    A straight translation, deliberately. Nothing here decides whether an event is worth
    keeping or what it is worth -- `select_events` does both, against a registry the
    model cannot see. See `app.application.event_service`.
    """
    return [
        EventCandidate(
            category=proposed.category,
            subtype=proposed.subtype,
            summary=proposed.summary,
            importance=proposed.importance,
        )
        for proposed in generation.world_events
    ]


async def _record_turn_resolution(
    gateway: TurnGatewayPort,
    *,
    session: SessionSnapshot,
    turn_index: int,
    client_action_id: str | None,
    events: list[ReviewedEvent],
    mutation_count: int,
) -> uuid.UUID:
    """Write the one record that says what this turn's proposals established.

    `applied` when something survived review, `no_effect` when nothing did -- which is
    most turns, and is the honest answer for an exchange that was entirely conversation.
    Not `rejected`: nothing about the turn was refused by the world's rules. A refused
    individual proposal is a review outcome, not a resolution disposition, and inflating
    one into the other would fill the mechanical trail with rows claiming the world said
    no to things nobody attempted.

    The revision is projected rather than read back. A turn bumps it exactly once, and
    only when something survived review, so `+1` is knowable here -- and the record has
    to exist before the events that point at it. `mutation_count` is everything the turn
    established, across facts, places and situations.
    """
    changed = bool(events) or bool(mutation_count)
    revision_before = session.state_revision
    return await gateway.add_resolution(
        NewResolution(
            session_id=session.id,
            source_type=ResolutionSourceType.PLAYER_ACTION,
            # A turn without a client id is still recorded, under a key that is unique
            # per session because turn indexes are. It is simply not replayable: a retry
            # arrives with no id, matches nothing, and plays a second turn.
            idempotency_key=(
                _turn_key(client_action_id) if client_action_id else f"turn-index:{turn_index}"
            ),
            disposition=(
                ResolutionDisposition.APPLIED if changed else ResolutionDisposition.NO_EFFECT
            ),
            resolver_name=TURN_RESOLVER_NAME,
            resolver_version=TURN_RESOLVER_VERSION,
            state_revision_before=revision_before,
            state_revision_after=revision_before + 1 if mutation_count else revision_before,
            occurred_at=session.elapsed_minutes,
            turn_index=turn_index,
            event_count=len(events),
            mutation_count=mutation_count,
        )
    )


async def _establish_proposed_facts(
    gateway: TurnGatewayPort,
    *,
    session: SessionSnapshot,
    review: ProposalReview,
    cause: ChangeCause,
) -> None:
    """Store the fact proposals that survived review.

    Staged, not committed: these facts are part of the turn, and a turn is atomic. No
    GameEvent is minted for them either -- the turn is already the event that produced
    them, and a `FACT_CREATED` row for "Elena dislikes olives" would bury the history
    that matters under the history that does not. `story_director` is exempt from the
    source-event requirement for exactly this case; see `world_facts.authority`.

    The cause is still recorded: the turn's own resolution, and the first event it wrote
    when it wrote one. That is what makes "why is this true?" answerable without a
    GameEvent per fact.

    The revision is not moved here. `execute_turn` owns the turn's single bump, because
    the turn may also have created places and started situations and all of it is one
    resolution.
    """
    if not review.accepted:
        return
    await stage_state_change(
        gateway,
        session_id=session.id,
        batch=StateMutationBatch(
            authority=FactAuthority.STORY_DIRECTOR, mutations=list(review.accepted)
        ),
        cause=cause,
        moves_revision=False,
    )


async def _replay_turn(
    gateway: TurnGatewayPort,
    *,
    session: SessionSnapshot,
    world: WorldSnapshot,
    resolution: Resolution,
) -> TurnResult:
    """Rebuild an already-played turn from what was stored, without re-running anything.

    The transcript is real -- these are the rows the original turn wrote. What is not
    reconstructed is anything that was never persisted: suggestions, and the per-stage
    review counts. Those come back empty rather than being guessed at, because a
    plausible-looking count is worse than an obviously absent one.

    Character lines are labelled from the character record rather than from the label
    the model used, which is not stored. For the overwhelmingly common case those are
    the same string.
    """
    turn_index = resolution.turn_index if resolution.turn_index is not None else session.turn_index
    stored = await gateway.load_turn_messages(session.id, turn_index)
    names = {
        character.id: character.name
        for character in await gateway.load_characters(world.id, limit=CHARACTER_LIMIT)
    }

    return TurnResult(
        session_id=session.id,
        turn_index=turn_index,
        messages=[
            TurnMessage(
                id=message.id,
                turn_index=message.turn_index,
                role=message.role,
                speaker=_replayed_speaker(
                    message.role, message.speaker_character_id, names, session
                ),
                speaker_character_id=message.speaker_character_id,
                content=message.content,
            )
            for message in stored
        ],
        suggested_actions=[],
        relationships=[],
        memories_created=0,
        events_created=resolution.event_count,
        facts_established=resolution.mutation_count,
        facts_rejected=0,
        locations_created=0,
        locations_rejected=0,
        situations_started=0,
        situations_rejected=0,
        visual_cue_generated=False,
        resolution_id=resolution.id,
        replayed=True,
    )


def _replayed_speaker(
    role: MessageRole,
    character_id: uuid.UUID | None,
    names: dict[uuid.UUID, str],
    session: SessionSnapshot,
) -> str:
    if role is MessageRole.PLAYER:
        return session.player_name
    if role is MessageRole.NARRATOR or character_id is None:
        return "Narrator" if role is MessageRole.NARRATOR else UNKNOWN_SPEAKER
    return names.get(character_id, UNKNOWN_SPEAKER)


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
