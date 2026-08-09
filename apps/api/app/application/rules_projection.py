"""Projects a full WorldRules document down to what the Story Director sees.

Kept separate from `context_builder` because it answers a different question. That
module decides *how much history* to retrieve; this one decides *which rules are
worth spending prompt tokens on*.

The direction is one-way and lossy by design. Nothing reconstructs `WorldRulesV1`
from a `WorldRulesContext`, and no provider is handed the domain object -- it would
carry sections that only future deterministic systems care about, and the whole point
of a compact projection is that the prompt stays small enough to survive its context
window. See docs/world-rules.md for what is dropped and why.
"""

from __future__ import annotations

from app.application.story_context import PlotArmorContext, WorldRulesContext
from app.domain.world_rules import WorldRules


def project_world_rules(rules: WorldRules) -> WorldRulesContext:
    """Flatten the sections the director needs into one AI-facing view."""
    narrative = rules.narrative
    mortality = rules.mortality
    danger = rules.danger
    consequences = rules.consequences
    power = rules.power
    supernatural = rules.supernatural
    progression = rules.progression
    simulation = rules.simulation
    content = rules.content

    return WorldRulesContext(
        optimism=narrative.optimism,
        darkness=narrative.darkness,
        protagonist_centrality=narrative.protagonist_centrality,
        deus_ex_machina=narrative.deus_ex_machina,
        coincidence_frequency=narrative.coincidence_frequency,
        consequence_persistence=narrative.consequence_persistence,
        plot_armor=PlotArmorContext(
            player=narrative.plot_armor.player,
            important_npcs=narrative.plot_armor.important_npcs,
            ordinary_npcs=narrative.plot_armor.ordinary_npcs,
        ),
        player_death=mortality.player_death,
        npc_death=mortality.npc_death,
        death_finality=mortality.death_finality,
        permanent_injury=mortality.permanent_injury,
        incapacitation_before_death=mortality.incapacitation_before_death,
        resurrection_enabled=mortality.resurrection.enabled,
        resurrection_rarity=mortality.resurrection.rarity,
        danger_baseline=danger.baseline,
        encounter_frequency=danger.encounter_frequency,
        encounter_severity=danger.encounter_severity,
        escalation_rate=danger.escalation_rate,
        lethality=danger.lethality,
        safe_zones_exist=danger.safe_zones_exist,
        consequence_severity=consequences.severity,
        social_memory=consequences.social_memory,
        actions_can_close_content=consequences.actions_can_close_content,
        irreversible_outcomes=consequences.irreversible_outcomes,
        power_scale=power.scale,
        power_gap_significance=power.power_gap_significance,
        rule_breaking_allowed=power.rule_breaking.allowed,
        rule_breaking_requires_explanation=power.rule_breaking.requires_explanation,
        supernatural_enabled=supernatural.enabled,
        supernatural_prevalence=supernatural.prevalence,
        supernatural_public_awareness=supernatural.public_awareness,
        progression_enabled=progression.enabled,
        progression_pace=progression.pace,
        world_continues_without_player=simulation.world_continues_without_player,
        npc_autonomy=simulation.npc_autonomy,
        faction_autonomy=simulation.faction_autonomy,
        offscreen_events=simulation.offscreen_events,
        missed_opportunities=simulation.missed_opportunities,
        time_progression=simulation.time_progression,
        enforcement=rules.rules.enforcement,
        chance_model=rules.chance.model,
        narrative_rerolls=rules.chance.narrative_rerolls,
        violence=content.violence,
        gore=content.gore,
        horror=content.horror,
        romance=content.romance,
        sexual_content=content.sexual_content,
        substance_use=content.substance_use,
        profanity=content.profanity,
    )
