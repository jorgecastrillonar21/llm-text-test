"""Named starting points for a rules document.

A preset is a *constructor*, not a mode. `build_preset` is the only place in the
codebase that reads a preset name; it runs once, when a world is created, and hands
back a plain `WorldRulesV1`. Nothing downstream stores the name or branches on it,
so there is no `if preset is SHONEN` anywhere and there never should be. Two worlds
whose resolved rules match behave identically regardless of how they were made.

The name is deliberately not persisted. It would become a lie the moment someone
edited a value, and a preset's numbers may legitimately change between releases --
existing worlds keep the rules they were created with, which is only coherent if the
label was never treated as a live reference.

The numbers below are chosen to be *coherent with each other*, not precise. What the
tests pin down is the relationships between presets (dark fantasy is deadlier than
cozy) rather than any individual value.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from app.domain.world_rules.enums import (
    ChanceTransparency,
    ContentIntensity,
    DeathFinality,
    Discoverability,
    IncapacitationPolicy,
    InformationSpread,
    PowerGapSignificance,
    PowerScale,
    ProgressionCeiling,
    ProgressionPace,
    PublicAwareness,
    Rarity,
    RecoverySpeed,
    Regulation,
    RulesEnforcement,
    SimulationScope,
    SubstanceUsePolicy,
    TimeProgression,
    TrainingRequirement,
)
from app.domain.world_rules.rules import (
    AwakeningRules,
    BreakthroughRules,
    ChanceRules,
    ConsequenceRules,
    ContentRules,
    DangerRules,
    DiscriminationRules,
    HealingRules,
    HiddenPowerRules,
    MortalityRules,
    NarrativeRules,
    PlotArmorRules,
    PowerLossRules,
    PowerRules,
    ProgressionRules,
    RecoveryRules,
    RegressionRules,
    ResourceRules,
    ResurrectionRules,
    RuleBreakingRules,
    RulesEnforcementRules,
    ScarcityRules,
    SimulationRules,
    SlaveryRules,
    SocietyRules,
    SupernaturalRules,
    WorldRulesV1,
)


class WorldRulesPreset(StrEnum):
    BALANCED = "balanced"
    COZY_FANTASY = "cozy_fantasy"
    SHONEN = "shonen"
    DARK_FANTASY = "dark_fantasy"
    SIMULATIONIST = "simulationist"
    ISEKAI_POWER_FANTASY = "isekai_power_fantasy"


def balanced() -> WorldRulesV1:
    """General-purpose defaults: every section's declared default, unmodified."""
    return WorldRulesV1()


def cozy_fantasy() -> WorldRulesV1:
    """Low stakes, warm tone, generous protection. Setbacks, not catastrophes."""
    return WorldRulesV1(
        narrative=NarrativeRules(
            optimism=85,
            darkness=15,
            protagonist_centrality=55,
            deus_ex_machina=20,
            coincidence_frequency=45,
            consequence_persistence=55,
            plot_armor=PlotArmorRules(player=70, important_npcs=55, ordinary_npcs=30),
        ),
        mortality=MortalityRules(
            player_death=False,
            npc_death=True,
            death_finality=DeathFinality.NARRATIVE_CHECKPOINT,
            permanent_injury=False,
            dismemberment=False,
            aging=True,
            incapacitation_before_death=IncapacitationPolicy.ALWAYS,
        ),
        danger=DangerRules(
            baseline=15,
            encounter_frequency=20,
            encounter_severity=20,
            escalation_rate=15,
            environmental_hazards=10,
            hostile_actor_frequency=15,
            lethality=5,
            safe_zones_exist=True,
        ),
        consequences=ConsequenceRules(
            severity=30,
            persistence=50,
            social_memory=60,
            legal_response=40,
            faction_response=35,
            economic_response=20,
            collateral_damage_matters=True,
            actions_can_close_content=False,
            irreversible_outcomes=False,
        ),
        power=PowerRules(
            scale=PowerScale.MUNDANE,
            mundane_ceiling=25,
            superhuman_ceiling=35,
            legendary_ceiling=45,
            power_gap_significance=PowerGapSignificance.LOW,
            training_matters=60,
            talent_matters=50,
            experience_matters=60,
            improvisation=70,
            rule_breaking=RuleBreakingRules(allowed=Rarity.UNCOMMON),
            awakenings=AwakeningRules(rarity=Rarity.UNCOMMON),
        ),
        supernatural=SupernaturalRules(
            prevalence=Rarity.COMMON,
            public_awareness=PublicAwareness.COMMON,
            regulation=Regulation.LIGHT,
            discoverability=Discoverability.VERY_HIGH,
        ),
        progression=ProgressionRules(
            pace=ProgressionPace.SLOW,
            ceiling=ProgressionCeiling.SOFT,
            training_required=TrainingRequirement.OPTIONAL,
            breakthroughs=BreakthroughRules(rarity=Rarity.UNCOMMON),
            power_loss=PowerLossRules(possible=False),
            regression=RegressionRules(possible=False),
        ),
        society=SocietyRules(
            law_strength=70,
            corruption=15,
            inequality=25,
            social_mobility=70,
            violence_tolerance=10,
            institutional_stability=85,
            reputation_matters=70,
            discrimination=DiscriminationRules(intensity=5),
        ),
        resources=ResourceRules(
            scarcity=20,
            food=ScarcityRules(scarcity=10),
            medicine=ScarcityRules(scarcity=15),
            weapons=ScarcityRules(scarcity=40),
            supernatural_resources=ScarcityRules(scarcity=40),
            money=ScarcityRules(scarcity=30),
            healing=HealingRules(accessibility=80),
            recovery=RecoveryRules(speed=RecoverySpeed.FAST),
        ),
        simulation=SimulationRules(
            npc_autonomy=55,
            faction_autonomy=40,
            event_frequency=25,
            time_progression=TimeProgression.ACTION_BASED,
            missed_opportunities=False,
        ),
        chance=ChanceRules(
            transparency=ChanceTransparency.PARTIAL,
            critical_failure=False,
            luck_influence=45,
        ),
        rules=RulesEnforcementRules(enforcement=RulesEnforcement.FLEXIBLE),
        content=ContentRules(
            violence=ContentIntensity.LOW,
            gore=ContentIntensity.NONE,
            horror=ContentIntensity.LOW,
            substance_use=SubstanceUsePolicy.NONE,
            profanity=ContentIntensity.LOW,
        ),
    )


def shonen() -> WorldRulesV1:
    """Constant danger, rarely fatal.

    The interesting pairing: `danger.*` is high while `lethality` is low. Fights
    happen everywhere and people lose them without dying -- which is exactly why
    those are separate dimensions.
    """
    return WorldRulesV1(
        narrative=NarrativeRules(
            optimism=80,
            darkness=40,
            protagonist_centrality=85,
            deus_ex_machina=15,
            coincidence_frequency=55,
            consequence_persistence=70,
            plot_armor=PlotArmorRules(player=60, important_npcs=45, ordinary_npcs=10),
        ),
        mortality=MortalityRules(
            incapacitation_before_death=IncapacitationPolicy.LIKELY,
        ),
        danger=DangerRules(
            baseline=75,
            encounter_frequency=75,
            encounter_severity=70,
            escalation_rate=70,
            environmental_hazards=45,
            hostile_actor_frequency=75,
            lethality=30,
            safe_zones_exist=True,
        ),
        consequences=ConsequenceRules(
            severity=55,
            persistence=70,
            social_memory=70,
            legal_response=45,
            faction_response=65,
            economic_response=30,
            actions_can_close_content=False,
        ),
        power=PowerRules(
            scale=PowerScale.LEGENDARY,
            mundane_ceiling=15,
            superhuman_ceiling=40,
            legendary_ceiling=85,
            power_gap_significance=PowerGapSignificance.MEDIUM,
            training_matters=85,
            talent_matters=70,
            experience_matters=70,
            improvisation=65,
            rule_breaking=RuleBreakingRules(allowed=Rarity.UNCOMMON),
            awakenings=AwakeningRules(rarity=Rarity.UNCOMMON),
        ),
        supernatural=SupernaturalRules(
            prevalence=Rarity.COMMON,
            public_awareness=PublicAwareness.UNIVERSAL,
            regulation=Regulation.LIGHT,
            discoverability=Discoverability.VERY_HIGH,
        ),
        progression=ProgressionRules(
            pace=ProgressionPace.ANIME_FAST,
            ceiling=ProgressionCeiling.NONE,
            training_required=TrainingRequirement.USUALLY,
            breakthroughs=BreakthroughRules(rarity=Rarity.UNCOMMON),
            power_loss=PowerLossRules(possible=False),
            regression=RegressionRules(possible=False),
        ),
        society=SocietyRules(
            law_strength=50,
            corruption=45,
            inequality=45,
            social_mobility=65,
            violence_tolerance=55,
            institutional_stability=55,
            information_spread=InformationSpread.FAST,
            reputation_matters=80,
            discrimination=DiscriminationRules(intensity=15),
        ),
        resources=ResourceRules(
            scarcity=35,
            food=ScarcityRules(scarcity=20),
            medicine=ScarcityRules(scarcity=25),
            weapons=ScarcityRules(scarcity=25),
            supernatural_resources=ScarcityRules(scarcity=45),
            money=ScarcityRules(scarcity=40),
            healing=HealingRules(accessibility=65),
            recovery=RecoveryRules(speed=RecoverySpeed.FAST),
        ),
        simulation=SimulationRules(
            npc_autonomy=65,
            faction_autonomy=65,
            event_frequency=55,
            simulation_scope=SimulationScope.REGIONAL,
        ),
        chance=ChanceRules(transparency=ChanceTransparency.PARTIAL, luck_influence=40),
        rules=RulesEnforcementRules(enforcement=RulesEnforcement.CINEMATIC),
        content=ContentRules(
            violence=ContentIntensity.HIGH,
            gore=ContentIntensity.LOW,
            horror=ContentIntensity.LOW,
            substance_use=SubstanceUsePolicy.CONTEXTUAL,
            profanity=ContentIntensity.LOW,
        ),
    )


def dark_fantasy() -> WorldRulesV1:
    """Deadly, bleak, and permanent. Almost no protection for anyone."""
    return WorldRulesV1(
        narrative=NarrativeRules(
            optimism=20,
            darkness=90,
            protagonist_centrality=35,
            deus_ex_machina=0,
            coincidence_frequency=15,
            consequence_persistence=100,
            plot_armor=PlotArmorRules(player=5, important_npcs=3, ordinary_npcs=0),
        ),
        mortality=MortalityRules(
            dismemberment=True,
            incapacitation_before_death=IncapacitationPolicy.RARE,
        ),
        danger=DangerRules(
            baseline=85,
            encounter_frequency=70,
            encounter_severity=85,
            escalation_rate=80,
            environmental_hazards=65,
            hostile_actor_frequency=80,
            lethality=90,
            safe_zones_exist=False,
        ),
        consequences=ConsequenceRules(
            severity=95,
            persistence=100,
            social_memory=90,
            legal_response=60,
            faction_response=85,
            economic_response=65,
        ),
        power=PowerRules(
            scale=PowerScale.ENHANCED,
            mundane_ceiling=20,
            superhuman_ceiling=35,
            legendary_ceiling=55,
            power_gap_significance=PowerGapSignificance.ABSOLUTE,
            training_matters=85,
            talent_matters=55,
            experience_matters=90,
            improvisation=55,
            rule_breaking=RuleBreakingRules(allowed=Rarity.VERY_RARE),
            awakenings=AwakeningRules(rarity=Rarity.VERY_RARE),
        ),
        supernatural=SupernaturalRules(
            prevalence=Rarity.RARE,
            public_awareness=PublicAwareness.MYTH,
            regulation=Regulation.STRICT,
            discoverability=Discoverability.LOW,
        ),
        progression=ProgressionRules(
            pace=ProgressionPace.SLOW,
            ceiling=ProgressionCeiling.HARD,
            training_required=TrainingRequirement.USUALLY,
            breakthroughs=BreakthroughRules(rarity=Rarity.VERY_RARE),
        ),
        society=SocietyRules(
            law_strength=35,
            corruption=80,
            inequality=85,
            social_mobility=15,
            violence_tolerance=70,
            institutional_stability=30,
            information_spread=InformationSpread.SLOW,
            reputation_matters=85,
            slavery=SlaveryRules(exists=True),
            discrimination=DiscriminationRules(intensity=70),
        ),
        resources=ResourceRules(
            scarcity=75,
            food=ScarcityRules(scarcity=70),
            medicine=ScarcityRules(scarcity=80),
            weapons=ScarcityRules(scarcity=45),
            supernatural_resources=ScarcityRules(scarcity=90),
            money=ScarcityRules(scarcity=70),
            healing=HealingRules(accessibility=20),
            recovery=RecoveryRules(speed=RecoverySpeed.VERY_SLOW),
        ),
        simulation=SimulationRules(
            npc_autonomy=85,
            faction_autonomy=85,
            event_frequency=60,
            simulation_scope=SimulationScope.REGIONAL,
        ),
        chance=ChanceRules(luck_influence=15),
        rules=RulesEnforcementRules(enforcement=RulesEnforcement.STRICT),
        content=ContentRules(
            violence=ContentIntensity.EXTREME,
            gore=ContentIntensity.HIGH,
            horror=ContentIntensity.HIGH,
            profanity=ContentIntensity.HIGH,
        ),
    )


def simulationist() -> WorldRulesV1:
    """A grounded world that does not notice the player.

    No plot armor at any tier, no centrality, no supernatural escape hatch, and the
    highest autonomy of any preset. Outcomes are supposed to follow from detail.
    """
    return WorldRulesV1(
        narrative=NarrativeRules(
            protagonist_centrality=5,
            deus_ex_machina=0,
            coincidence_frequency=5,
            consequence_persistence=100,
            plot_armor=PlotArmorRules(player=0, important_npcs=0, ordinary_npcs=0),
        ),
        mortality=MortalityRules(dismemberment=True),
        danger=DangerRules(
            baseline=55,
            encounter_frequency=40,
            encounter_severity=60,
            escalation_rate=55,
            environmental_hazards=55,
            hostile_actor_frequency=45,
            lethality=70,
        ),
        consequences=ConsequenceRules(
            severity=85,
            persistence=100,
            social_memory=95,
            legal_response=85,
            faction_response=85,
            economic_response=85,
        ),
        power=PowerRules(
            scale=PowerScale.MUNDANE,
            mundane_ceiling=30,
            superhuman_ceiling=40,
            legendary_ceiling=50,
            power_gap_significance=PowerGapSignificance.ABSOLUTE,
            training_matters=90,
            talent_matters=45,
            experience_matters=95,
            improvisation=60,
            rule_breaking=RuleBreakingRules(allowed=Rarity.IMPOSSIBLE),
            hidden_power=HiddenPowerRules(allowed=False),
            awakenings=AwakeningRules(enabled=False, rarity=Rarity.IMPOSSIBLE),
        ),
        supernatural=SupernaturalRules(
            enabled=False,
            prevalence=Rarity.IMPOSSIBLE,
            public_awareness=PublicAwareness.UNKNOWN,
            regulation=Regulation.NONE,
            discoverability=Discoverability.VERY_LOW,
            innate_powers_exist=False,
            learnable_powers_exist=False,
            supernatural_items_exist=False,
            supernatural_creatures_exist=False,
        ),
        progression=ProgressionRules(
            pace=ProgressionPace.VERY_SLOW,
            ceiling=ProgressionCeiling.HARD,
            training_required=TrainingRequirement.ALWAYS,
            breakthroughs=BreakthroughRules(enabled=False, rarity=Rarity.IMPOSSIBLE),
        ),
        society=SocietyRules(
            law_strength=65,
            corruption=45,
            inequality=60,
            social_mobility=30,
            violence_tolerance=25,
            institutional_stability=65,
            information_spread=InformationSpread.SLOW,
            reputation_matters=90,
            discrimination=DiscriminationRules(intensity=35),
        ),
        resources=ResourceRules(
            scarcity=60,
            food=ScarcityRules(scarcity=50),
            medicine=ScarcityRules(scarcity=60),
            weapons=ScarcityRules(scarcity=55),
            supernatural_resources=ScarcityRules(scarcity=100),
            money=ScarcityRules(scarcity=60),
            healing=HealingRules(accessibility=35),
            recovery=RecoveryRules(speed=RecoverySpeed.VERY_SLOW),
        ),
        simulation=SimulationRules(
            npc_autonomy=95,
            faction_autonomy=95,
            event_frequency=70,
            simulation_scope=SimulationScope.GLOBAL,
        ),
        chance=ChanceRules(transparency=ChanceTransparency.VISIBLE, luck_influence=5),
        rules=RulesEnforcementRules(enforcement=RulesEnforcement.SIMULATIONIST),
        content=ContentRules(
            violence=ContentIntensity.HIGH,
            gore=ContentIntensity.MEDIUM,
            horror=ContentIntensity.MEDIUM,
        ),
    )


def isekai_power_fantasy() -> WorldRulesV1:
    """The world revolves around the player, and it is kind to them.

    The only preset where resurrection is real, which is also the only combination
    `death_finality` permits alongside an enabled resurrection block.
    """
    return WorldRulesV1(
        narrative=NarrativeRules(
            optimism=75,
            darkness=30,
            protagonist_centrality=95,
            deus_ex_machina=35,
            coincidence_frequency=70,
            consequence_persistence=60,
            plot_armor=PlotArmorRules(player=80, important_npcs=50, ordinary_npcs=10),
        ),
        mortality=MortalityRules(
            death_finality=DeathFinality.RESURRECTION_POSSIBLE,
            permanent_injury=False,
            incapacitation_before_death=IncapacitationPolicy.LIKELY,
            resurrection=ResurrectionRules(enabled=True, rarity=Rarity.VERY_RARE),
        ),
        danger=DangerRules(
            baseline=55,
            encounter_frequency=60,
            encounter_severity=50,
            escalation_rate=40,
            environmental_hazards=30,
            hostile_actor_frequency=60,
            lethality=25,
        ),
        consequences=ConsequenceRules(
            severity=40,
            persistence=55,
            social_memory=60,
            legal_response=35,
            faction_response=55,
            economic_response=30,
            collateral_damage_matters=False,
            actions_can_close_content=False,
            irreversible_outcomes=False,
        ),
        power=PowerRules(
            scale=PowerScale.COSMIC,
            mundane_ceiling=10,
            superhuman_ceiling=35,
            legendary_ceiling=95,
            power_gap_significance=PowerGapSignificance.MEDIUM,
            training_matters=45,
            talent_matters=90,
            experience_matters=55,
            improvisation=70,
            rule_breaking=RuleBreakingRules(allowed=Rarity.COMMON),
            awakenings=AwakeningRules(rarity=Rarity.COMMON),
        ),
        supernatural=SupernaturalRules(
            prevalence=Rarity.COMMON,
            public_awareness=PublicAwareness.UNIVERSAL,
            regulation=Regulation.LIGHT,
            discoverability=Discoverability.VERY_HIGH,
        ),
        progression=ProgressionRules(
            pace=ProgressionPace.ANIME_FAST,
            ceiling=ProgressionCeiling.NONE,
            training_required=TrainingRequirement.OPTIONAL,
            breakthroughs=BreakthroughRules(rarity=Rarity.COMMON),
            power_loss=PowerLossRules(possible=False),
            regression=RegressionRules(possible=False),
        ),
        society=SocietyRules(
            law_strength=45,
            corruption=50,
            inequality=65,
            social_mobility=60,
            violence_tolerance=45,
            institutional_stability=55,
            reputation_matters=70,
            discrimination=DiscriminationRules(intensity=30),
        ),
        resources=ResourceRules(
            scarcity=30,
            food=ScarcityRules(scarcity=20),
            medicine=ScarcityRules(scarcity=30),
            weapons=ScarcityRules(scarcity=25),
            supernatural_resources=ScarcityRules(scarcity=35),
            money=ScarcityRules(scarcity=35),
            healing=HealingRules(accessibility=70),
            recovery=RecoveryRules(speed=RecoverySpeed.FAST),
        ),
        simulation=SimulationRules(
            npc_autonomy=45,
            faction_autonomy=45,
            event_frequency=30,
            time_progression=TimeProgression.ACTION_BASED,
            missed_opportunities=False,
        ),
        chance=ChanceRules(
            transparency=ChanceTransparency.PARTIAL,
            critical_failure=False,
            luck_influence=65,
        ),
        rules=RulesEnforcementRules(enforcement=RulesEnforcement.FLEXIBLE),
        content=ContentRules(
            violence=ContentIntensity.MEDIUM,
            gore=ContentIntensity.LOW,
            horror=ContentIntensity.LOW,
            substance_use=SubstanceUsePolicy.CONTEXTUAL,
            profanity=ContentIntensity.LOW,
        ),
    )


_BUILDERS: dict[WorldRulesPreset, Callable[[], WorldRulesV1]] = {
    WorldRulesPreset.BALANCED: balanced,
    WorldRulesPreset.COZY_FANTASY: cozy_fantasy,
    WorldRulesPreset.SHONEN: shonen,
    WorldRulesPreset.DARK_FANTASY: dark_fantasy,
    WorldRulesPreset.SIMULATIONIST: simulationist,
    WorldRulesPreset.ISEKAI_POWER_FANTASY: isekai_power_fantasy,
}


def build_preset(preset: WorldRulesPreset) -> WorldRulesV1:
    """Resolve a preset name into rules. The one and only place a name is read."""
    return _BUILDERS[preset]()
