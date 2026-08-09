"""Renders a StoryContext into the user message sent to a language model."""

from __future__ import annotations

from app.application.story_context import StoryContext, WorldRulesContext
from app.domain.enums import LANGUAGE_NAMES


def _words(value: object) -> str:
    """`fade_to_black` -> `fade to black`. Enum values are written for code, not prose."""
    return str(value).replace("_", " ")


def _either(flag: bool, yes: str, no: str) -> str:
    return yes if flag else no


def _render_world_rules(rules: WorldRulesContext) -> list[str]:
    """One line per topic, values only.

    The *principles* -- that plot armor acts before an outcome, that sampling is not
    randomness -- live in the system prompt, which is sent once per request and does
    not repeat per section. Restating them here would pay for the same tokens twice
    on every turn, and this block is already the only part of the prompt that grows
    for every world regardless of how much has happened in it.
    """
    armor = rules.plot_armor
    mortality = ", ".join(
        [
            _either(rules.player_death, "the player can die", "the player cannot die"),
            _either(rules.npc_death, "NPCs can die", "NPCs cannot die"),
            f"death is {_words(rules.death_finality)}",
            _either(rules.permanent_injury, "injuries can be lasting", "injuries always heal"),
            f"incapacitation before death is {_words(rules.incapacitation_before_death)}",
            _either(
                rules.resurrection_enabled,
                f"resurrection exists but is {_words(rules.resurrection_rarity)}",
                "resurrection does not exist",
            ),
        ]
    )
    consequences = ", ".join(
        [
            f"severity {rules.consequence_severity}/100",
            f"persistence {rules.consequence_persistence}/100",
            f"people remember {rules.social_memory}/100",
            _either(
                rules.actions_can_close_content,
                "destroyed opportunities stay destroyed",
                "lost opportunities tend to reappear in another form",
            ),
            _either(
                rules.irreversible_outcomes,
                "outcomes can be permanent",
                "outcomes are rarely permanent",
            ),
        ]
    )
    simulation = ", ".join(
        [
            _either(
                rules.world_continues_without_player,
                "the world continues without the player",
                "the world waits for the player",
            ),
            f"NPC autonomy {rules.npc_autonomy}/100",
            f"faction autonomy {rules.faction_autonomy}/100",
            _either(rules.offscreen_events, "offscreen events happen", "nothing happens offscreen"),
            _either(
                rules.missed_opportunities,
                "opportunities can be missed",
                "opportunities wait for the player",
            ),
            f"time is {_words(rules.time_progression)}",
        ]
    )

    return [
        "\n# World rules  (authoritative: this is how the universe works)",
        f"Enforcement: {_words(rules.enforcement)}",
        f"Tone: optimism {rules.optimism}/100, darkness {rules.darkness}/100",
        f"Protagonist centrality: {rules.protagonist_centrality}/100"
        f" | deus ex machina: {rules.deus_ex_machina}/100"
        f" | coincidence: {rules.coincidence_frequency}/100",
        f"Plot armor: player {armor.player}/100, important NPCs {armor.important_npcs}/100, "
        f"ordinary NPCs {armor.ordinary_npcs}/100",
        f"Mortality: {mortality}",
        f"Danger: baseline {rules.danger_baseline}/100, "
        f"encounter frequency {rules.encounter_frequency}/100, "
        f"encounter severity {rules.encounter_severity}/100, "
        f"escalation {rules.escalation_rate}/100, lethality {rules.lethality}/100, "
        + _either(rules.safe_zones_exist, "safe places exist", "nowhere is safe"),
        f"Consequences: {consequences}",
        f"Power: {_words(rules.power_scale)} scale, "
        f"power gaps matter {_words(rules.power_gap_significance)}, "
        f"rule-breaking feats are {_words(rules.rule_breaking_allowed)}"
        + _either(rules.rule_breaking_requires_explanation, " and must be explained", ""),
        _either(
            rules.supernatural_enabled,
            f"Supernatural: present, {_words(rules.supernatural_prevalence)}, "
            f"public awareness {_words(rules.supernatural_public_awareness)}",
            "Supernatural: nothing supernatural exists in this world",
        ),
        _either(
            rules.progression_enabled,
            f"Progression: characters grow at a {_words(rules.progression_pace)} pace",
            "Progression: characters do not grow stronger",
        ),
        f"Simulation: {simulation}",
        f"Chance: {_words(rules.chance_model)} and mechanical, "
        + _either(rules.narrative_rerolls, "rerolls allowed", "never rerolled"),
        f"Content (description intensity, not danger): violence {_words(rules.violence)}, "
        f"gore {_words(rules.gore)}, horror {_words(rules.horror)}, "
        f"romance {_words(rules.romance)}, sexual content {_words(rules.sexual_content)}, "
        f"substance use {_words(rules.substance_use)}, profanity {_words(rules.profanity)}",
    ]


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

    lines.extend(_render_world_rules(context.world_rules))

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
