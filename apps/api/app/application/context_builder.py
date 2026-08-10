"""Builds the StoryContext handed to a story provider.

All retrieval policy lives here so it is deterministic and testable in one place:
how much history is worth sending, how many characters, and what a stored row
turns into once the provider sees it. Reads go through StoryContextReaderPort --
this module does not know what a database is.

Retrieval is currently recency and importance ordering, no embeddings. Semantic
retrieval replaces the memory read in Phase 3; see docs/ai-contract.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.application.persistence import (
    CharacterRecord,
    SessionSnapshot,
    StoryContextReaderPort,
    TranscriptMessage,
    WorldSnapshot,
)
from app.application.rules_projection import project_world_rules
from app.application.situation_context import build_situations_context
from app.application.spatial_context import assemble_scene_context, resolve_scene
from app.application.story_context import (
    CharacterContext,
    EventContext,
    FactContext,
    HistoryContext,
    MemoryContext,
    MessageContext,
    OutcomeContext,
    PlayerContext,
    RelationshipContext,
    SessionContext,
    StoryContext,
    TimeContext,
    WorldContext,
    WorldFactsContext,
)
from app.domain.enums import MessageRole
from app.domain.resolution import GameEvent, Resolution, ResolutionOutcome
from app.domain.world_facts import FactSubjectType, WorldFact
from app.domain.world_time import describe_duration, project_time

RECENT_MESSAGE_LIMIT = 20
MEMORY_LIMIT = 30
CHARACTER_LIMIT = 12

FACT_LIMIT = 40
"""How many established facts reach the prompt at all. A long session accumulates far
more than this; the prompt has a budget and importance is what spends it."""

CRITICAL_IMPORTANCE = 4
"""At or above this, a fact is presented as something the scene must not contradict.
Below it, as colour. The line is drawn here rather than in the domain because it is a
prompt-shaping decision, and the domain's 1..5 scale has no opinion about prompts."""

LANDMARK_IMPORTANCE = 4
LANDMARK_EVENT_LIMIT = 8
"""The heaviest events of the session, however long ago. Small on purpose: this band
never expires, so every entry is a permanent charge against the prompt budget for the
rest of the session."""

RECENT_EVENT_IMPORTANCE = 2
RECENT_EVENT_LIMIT = 12
"""What has happened lately. Importance 1 is deliberately excluded: those are the
events the policy kept because they might matter to a later system, not because a
narrator needs them, and they are also the most numerous."""


async def build_story_context(
    reader: StoryContextReaderPort,
    *,
    session: SessionSnapshot,
    world: WorldSnapshot,
    player_action: str,
) -> StoryContext:
    characters = await reader.load_characters(world.id, limit=CHARACTER_LIMIT)
    names = {character.id: character.name for character in characters}

    messages = await reader.load_recent_messages(session.id, limit=RECENT_MESSAGE_LIMIT)
    memories = await reader.load_memories(session.id, limit=MEMORY_LIMIT)
    relationships = await reader.load_relationships(session.id)
    facts = await reader.load_facts(session.id, limit=FACT_LIMIT)
    history = await _load_history(reader, session=session)

    # Resolved once and used twice: the spatial block walks the graph, and situation
    # relevance needs the current place and its containers to know what is happening
    # *here*. See `spatial_context.resolve_scene_location` for why a location string is
    # the input, and why that is temporary.
    placement = await resolve_scene(
        reader,
        session_id=session.id,
        world_id=world.id,
        current_location=session.current_location,
    )
    # None when the world has no geography, or when the session's location string
    # matches nothing in it -- which is most sessions today.
    space = await assemble_scene_context(reader, placement)

    # None when nothing relevant is under way, which is also most turns.
    situations = await build_situations_context(
        reader,
        session_id=session.id,
        elapsed_minutes=session.elapsed_minutes,
        location_index=placement.graph.index,
        current_location=placement.current,
        present_character_ids=[character.id for character in characters],
    )

    return StoryContext(
        world=_to_world_context(world),
        # Projected, not passed whole: the director gets the rules that shape a turn,
        # not the sections reserved for future deterministic systems.
        world_rules=project_world_rules(world.rules),
        player=PlayerContext(name=session.player_name, description=session.player_description),
        session=SessionContext(
            id=session.id,
            title=session.title,
            current_location=session.current_location,
            summary=session.summary,
            turn_index=session.turn_index,
        ),
        time=_to_time_context(session, world),
        space=space,
        situations=situations,
        world_facts=_to_facts_context(facts, names, world.name),
        history=history,
        relevant_characters=[_to_character_context(record) for record in characters],
        recent_messages=[_to_message_context(message, names) for message in messages],
        relevant_memories=[
            MemoryContext(
                kind=memory.kind,
                summary=memory.summary,
                importance=memory.importance,
                character_id=memory.character_id,
            )
            for memory in memories
        ],
        relationships=[
            RelationshipContext(
                character_id=relationship.character_id,
                character_name=names.get(relationship.character_id, "Unknown"),
                trust=relationship.trust,
                affection=relationship.affection,
                respect=relationship.respect,
                fear=relationship.fear,
            )
            for relationship in relationships
        ],
        player_action=player_action,
    )


def build_outcome_context(
    *,
    session: SessionSnapshot,
    world: WorldSnapshot,
    resolution: Resolution,
    events: Sequence[GameEvent],
    outcome: ResolutionOutcome | None = None,
) -> OutcomeContext:
    """The context for narrating something the game has already committed.

    Synchronous, and that is the shape of the job: everything here was read by the
    caller inside the transaction that committed the outcome, or read back from the
    record afterwards. There is no retrieval policy to apply because there is nothing
    left to decide -- a narrator that needed the world's facts, its geography and its
    relationships to describe one outcome would be deciding what the outcome was.

    `outcome` is the resolver's own account, present when narration follows resolution
    in the same request and absent when a stored resolution is narrated later. Absent is
    not a degraded case: the disposition, the reason and what history recorded are the
    parts that are *authoritative*, and they come from the database either way.
    """
    return OutcomeContext(
        world=_to_world_context(world),
        player=PlayerContext(name=session.player_name, description=session.player_description),
        # The clock as it stands now, not as it stood when the outcome was resolved.
        # Narration describes a moment the story has already reached.
        time=_to_time_context(session, world),
        disposition=resolution.disposition,
        reason_code=resolution.reason_code,
        resolver=resolution.resolver_name,
        events=[_to_event_context(event, session) for event in events],
        detail=dict(outcome.narrative_context) if outcome is not None else {},
    )


def _to_world_context(world: WorldSnapshot) -> WorldContext:
    return WorldContext(
        id=world.id,
        name=world.name,
        description=world.description,
        genre=world.genre,
        setting=world.setting,
        language=world.language,
    )


def _to_time_context(session: SessionSnapshot, world: WorldSnapshot) -> TimeContext:
    """Derived on every read from the one number that is stored. There is no cached
    "current date" anywhere for this to disagree with."""
    now = project_time(session.elapsed_minutes, initial=world.initial_datetime)
    return TimeContext(
        calendar_date=now.calendar_date,
        clock=now.clock,
        period=now.period,
        elapsed_since_start=now.elapsed_since_start,
    )


async def _load_history(
    reader: StoryContextReaderPort, *, session: SessionSnapshot
) -> HistoryContext:
    """The bounded slice of history the director is shown.

    Two reads, not one: "the eight heaviest things that ever happened" and "the twelve
    most recent things that mattered" are different questions, and a single ordering
    cannot answer both. Sorting by importance buries what just happened under year one;
    sorting by recency loses the war the story is about the moment a quiet evening
    produces twelve events.

    Bounded by construction -- at most `LANDMARK_EVENT_LIMIT + RECENT_EVENT_LIMIT` rows
    reach the prompt no matter how long the session runs, and no query here is unbounded
    even before the limit applies. Deliberately not implemented: any form of semantic or
    embedding-based event retrieval. That is a Phase 3 concern; this is importance and
    recency, and it is meant to stay legible.
    """
    landmarks = await reader.load_events(
        session.id, min_importance=LANDMARK_IMPORTANCE, limit=LANDMARK_EVENT_LIMIT
    )
    recent = await reader.load_events(
        session.id, min_importance=RECENT_EVENT_IMPORTANCE, limit=RECENT_EVENT_LIMIT
    )
    landmark_ids = {event.id for event in landmarks}
    return HistoryContext(
        # Reversed: the port returns most recent first because that is what retrieval
        # wants, and a prompt reads forwards because that is what a story does.
        landmarks=[_to_event_context(event, session) for event in reversed(landmarks)],
        recent=[
            _to_event_context(event, session)
            for event in reversed(recent)
            if event.id not in landmark_ids
        ],
    )


def _to_event_context(event: GameEvent, session: SessionSnapshot) -> EventContext:
    return EventContext(
        summary=event.summary,
        kind=event.subtype or event.category.value,
        when=_when(event.occurred_at, session.elapsed_minutes),
    )


def _when(occurred_at: int, now: int) -> str:
    """`"3 hours, 20 minutes ago"`. Relative, because that is what a scene needs.

    An absolute minute would be the honest number and the useless one: the director is
    given the fictional date and clock for *now* and nothing to subtract from it, and a
    model asked to do that arithmetic gets it wrong in a way that reads as a continuity
    error. Clamped at zero -- an event from the future is a bug elsewhere, and phrasing
    it as "in 40 minutes" would launder that bug into the prose.
    """
    elapsed = max(0, now - occurred_at)
    if elapsed == 0:
        return "just now"
    return f"{describe_duration(elapsed)} ago"


def _to_facts_context(
    facts: list[WorldFact], names: dict[uuid.UUID, str], world_name: str
) -> WorldFactsContext:
    """Split the loaded facts by importance and give each one a readable subject.

    Gameplay flags are included: `palace_secret_discovered` is not a sentence anyone in
    the world would say, but a director that does not know the secret is out will write
    a scene where it is still a secret.
    """
    critical: list[FactContext] = []
    relevant: list[FactContext] = []
    for fact in facts:
        entry = FactContext(
            subject=_subject_label(fact, names, world_name),
            property=fact.property,
            value=_render_value(fact.value),
        )
        bucket = critical if fact.importance >= CRITICAL_IMPORTANCE else relevant
        bucket.append(entry)
    return WorldFactsContext(critical=critical, relevant=relevant)


def _subject_label(fact: WorldFact, names: dict[uuid.UUID, str], world_name: str) -> str:
    """A name a sentence could use.

    Falls back to the subject type when an id resolves to nothing -- a character the
    context did not load, or an entity type with no table yet. Never the bare uuid: an
    id the model cannot use is an id it should not be shown.
    """
    if fact.subject.type is FactSubjectType.WORLD:
        return world_name
    if fact.subject.id is not None and fact.subject.id in names:
        return names[fact.subject.id]
    return f"an unnamed {fact.subject.type.value}"


def _render_value(value: object) -> str:
    if isinstance(value, bool):  # Before the list branch, and before str(): bool is int.
        return "yes" if value else "no"
    if value is None:
        return "none"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "nothing"
    if isinstance(value, dict):
        return ", ".join(f"{key}: {item}" for key, item in value.items())
    return str(value)


def _to_character_context(record: CharacterRecord) -> CharacterContext:
    return CharacterContext(
        id=record.id,
        name=record.name,
        description=record.description,
        appearance=record.appearance,
        personality=record.personality,
        backstory=record.backstory,
        speech_style=record.speech_style,
        goals=list(record.goals),
        secrets=list(record.secrets),
    )


def _to_message_context(message: TranscriptMessage, names: dict[uuid.UUID, str]) -> MessageContext:
    return MessageContext(
        turn_index=message.turn_index,
        role=message.role,
        speaker=_speaker_label(message, names),
        content=message.content,
    )


def _speaker_label(message: TranscriptMessage, names: dict[uuid.UUID, str]) -> str:
    if message.speaker_character_id is not None:
        return names.get(message.speaker_character_id, "Unknown")
    return {
        MessageRole.PLAYER: "Player",
        MessageRole.NARRATOR: "Narrator",
        MessageRole.SYSTEM: "System",
    }.get(message.role, "Narrator")
