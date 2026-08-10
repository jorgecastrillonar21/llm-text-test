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
    FactContext,
    MemoryContext,
    MessageContext,
    PlayerContext,
    RelationshipContext,
    SessionContext,
    StoryContext,
    TimeContext,
    WorldContext,
    WorldFactsContext,
)
from app.domain.enums import MessageRole
from app.domain.world_facts import FactSubjectType, WorldFact
from app.domain.world_time import project_time

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

    # Derived here, every turn, from the one number that is stored. There is no
    # cached "current date" anywhere for this to disagree with.
    now = project_time(session.elapsed_minutes, initial=world.initial_datetime)

    return StoryContext(
        world=WorldContext(
            id=world.id,
            name=world.name,
            description=world.description,
            genre=world.genre,
            setting=world.setting,
            language=world.language,
        ),
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
        time=TimeContext(
            calendar_date=now.calendar_date,
            clock=now.clock,
            period=now.period,
            elapsed_since_start=now.elapsed_since_start,
        ),
        space=space,
        situations=situations,
        world_facts=_to_facts_context(facts, names, world.name),
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
