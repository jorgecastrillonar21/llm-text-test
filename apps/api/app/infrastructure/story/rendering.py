"""Renders a StoryContext into the user message sent to a language model."""

from __future__ import annotations

from app.application.story_context import StoryContext
from app.domain.enums import LANGUAGE_NAMES


def render_context(context: StoryContext) -> str:
    lines: list[str] = []

    language = LANGUAGE_NAMES[context.world.language]
    lines.append(
        f"# Output language\n"
        f"Write every player-visible string in {language}: narration, dialogue text, "
        f"speaker names as spoken, suggested actions, memory summaries, world event "
        f"descriptions. Keep JSON keys and enum values exactly as the schema defines them, "
        f"in English.\n"
    )

    lines.append("# World")
    lines.append(f"{context.world.name} — {context.world.genre}")
    if context.world.setting:
        lines.append(f"Setting: {context.world.setting}")
    if context.world.description:
        lines.append(context.world.description)

    lines.append("\n# Player character")
    lines.append(f"Name: {context.player.name}")
    if context.player.description:
        lines.append(context.player.description)

    lines.append("\n# Session")
    lines.append(f"Turn: {context.session.turn_index}")
    lines.append(f"Location: {context.session.current_location or 'unspecified'}")
    if context.session.summary:
        lines.append(f"Story so far: {context.session.summary}")

    if context.relevant_characters:
        lines.append("\n# Characters present in this world")
        for character in context.relevant_characters:
            lines.append(f"\n## {character.name}  (character_id: {character.id})")
            if character.description:
                lines.append(f"Description: {character.description}")
            if character.appearance:
                lines.append(f"Appearance: {character.appearance}")
            if character.personality:
                lines.append(f"Personality: {character.personality}")
            if character.speech_style:
                lines.append(f"Speech: {character.speech_style}")
            if character.backstory:
                lines.append(f"Backstory: {character.backstory}")
            if character.goals:
                lines.append(f"Goals: {'; '.join(character.goals)}")
            if character.secrets:
                lines.append(
                    f"Secrets (play these, never state them outright): "
                    f"{'; '.join(character.secrets)}"
                )

    if context.relationships:
        lines.append("\n# How they currently regard the player  (scale -100..100)")
        for rel in context.relationships:
            lines.append(
                f"- {rel.character_name}: trust {rel.trust}, affection {rel.affection}, "
                f"respect {rel.respect}, fear {rel.fear}"
            )

    if context.relevant_memories:
        lines.append("\n# Established memories  (treat as fact)")
        for memory in context.relevant_memories:
            lines.append(f"- [{memory.kind}/{memory.importance}] {memory.summary}")

    if context.recent_messages:
        lines.append("\n# Recent transcript")
        for message in context.recent_messages:
            lines.append(f"[{message.role}] {message.speaker}: {message.content}")

    lines.append("\n# The player's action this turn")
    lines.append(context.player_action)
    lines.append(
        "\nNarrate what happens next. Respond only with an object matching the JSON schema."
    )

    return "\n".join(lines)
