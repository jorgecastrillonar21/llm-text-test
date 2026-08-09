"""The WorldRules document: thirteen sections describing how a universe works.

Every value here is a *setting*. None of it is state. "Resurrection is impossible"
belongs here; "the king is dead" does not.

# The 0..100 scale

Most numbers are `Setting`, a constrained int:

    0    effectively absent
    25   low
    50   moderate
    75   high
    100  extreme

These are **intensities, not probabilities**. `danger.lethality = 40` does not mean
"40% of encounters kill someone"; it means lethality sits somewhat below the middle
of what this universe is capable of. Turning any of them into odds is a decision for
a future rules-resolution layer, which will need context this document does not have
(who, where, under what pressure). Nothing may read these as percentages today.

# Frozen and closed

Sections are frozen and reject unknown keys. A misspelled setting is a loud error
rather than a value that silently keeps its default -- which, for a document whose
whole job is to constrain a language model, would be the worst possible failure mode.
Forward compatibility comes from the explicit version dispatch in `parsing`, not from
tolerating unknown fields.
"""

from __future__ import annotations

from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.world_rules.enums import (
    ChanceModel,
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
    RomancePolicy,
    RulesEnforcement,
    SexualContentPolicy,
    SimulationScope,
    SubstanceUsePolicy,
    TimeProgression,
    TrainingRequirement,
)

# `Final` rather than a bare assignment so it narrows to `Literal[1]` and can be used
# as the default for the `version` field below.
WORLD_RULES_VERSION: Final = 1

Setting = Annotated[int, Field(ge=0, le=100)]
"""A normalized world-configuration intensity. See the module docstring: not a probability."""


class _RulesModel(BaseModel):
    """Shared configuration for every section.

    `frozen` because a rules document is decided once and then read many times;
    `extra="forbid"` because an unrecognised key is a mistake, not an extension.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------


class PlotArmorRules(_RulesModel):
    """How much narrative protection each class of character carries.

    Plot armor acts **before** an outcome and never after it. Higher player armor
    may make it likelier that a plausible escape route exists in the first place.
    Once something is resolved, plot armor may not:

    - alter a rolled result
    - undo damage that was dealt
    - resurrect anyone
    - invalidate a recorded event
    - rewrite what already happened

    This is the most abusable value in the document, which is why the constraint is
    stated here in the code and repeated in the Story Director prompt.
    """

    player: Setting = 25
    important_npcs: Setting = 10
    ordinary_npcs: Setting = 0


class NarrativeRules(_RulesModel):
    optimism: Setting = 50
    """Tendency toward hope, recovery, and chances to rebuild. Never touches a roll."""

    darkness: Setting = 50
    """Tolerance for tragedy, betrayal, cruelty and moral ambiguity.

    Independent of `optimism` -- deliberately *not* `100 - optimism`. A world can be
    bleak and still ultimately hopeful, and collapsing the two axes would make that
    world unrepresentable. Also distinct from `danger`: darkness is moral, danger is
    physical.
    """

    protagonist_centrality: Setting = 40
    """How strongly major events gravitate toward the player.

    0 makes the player one person in a large indifferent world; 100 puts them at the
    centre of everything that matters. This is **not** plot armor: events finding the
    player is not the player surviving them.
    """

    deus_ex_machina: Setting = 5
    """Willingness to introduce an unexpected external solution.

    Low by default, and it may never override a resolved deterministic outcome.
    """

    coincidence_frequency: Setting = 20
    """How often plausible coincidences may link characters and events.

    Even at 100 causality still holds: a coincidence connects things that could have
    met, it does not manufacture something out of nothing.
    """

    consequence_persistence: Setting = 90
    """How strongly earlier consequences stay relevant.

    Influences what future context and simulation carry forward. It does not itself
    create or delete state.
    """

    plot_armor: PlotArmorRules = PlotArmorRules()


# ---------------------------------------------------------------------------
# Mortality
# ---------------------------------------------------------------------------


class ResurrectionRules(_RulesModel):
    enabled: bool = False
    rarity: Rarity = Rarity.IMPOSSIBLE


class MortalityRules(_RulesModel):
    player_death: bool = True
    npc_death: bool = True
    death_finality: DeathFinality = DeathFinality.PERMANENT
    permanent_injury: bool = True
    dismemberment: bool = False
    aging: bool = True
    incapacitation_before_death: IncapacitationPolicy = IncapacitationPolicy.CONTEXTUAL
    resurrection: ResurrectionRules = ResurrectionRules()

    @model_validator(mode="after")
    def _resurrection_must_agree_with_death_finality(self) -> Self:
        """Reject documents that both allow and forbid coming back.

        `RESPAWN` and `NARRATIVE_CHECKPOINT` are game conventions rather than
        in-world resurrection, so they are free to leave resurrection disabled.
        Only `RESURRECTION_POSSIBLE` makes a claim about the universe's physics,
        and that claim has to match the resurrection block.
        """
        possible = self.death_finality is DeathFinality.RESURRECTION_POSSIBLE
        if possible != self.resurrection.enabled:
            raise ValueError(
                "death_finality='resurrection_possible' and resurrection.enabled must agree; "
                f"got death_finality={self.death_finality.value!r} "
                f"and resurrection.enabled={self.resurrection.enabled}"
            )
        if self.resurrection.enabled and self.resurrection.rarity is Rarity.IMPOSSIBLE:
            raise ValueError("resurrection.enabled is true but its rarity is 'impossible'")
        return self


# ---------------------------------------------------------------------------
# Danger
# ---------------------------------------------------------------------------


class DangerRules(_RulesModel):
    """Physical risk, split into dimensions that are routinely conflated.

    frequency  how often danger appears
    severity   how serious a dangerous encounter tends to be
    lethality  how likely a serious encounter is to kill or maim permanently
    escalation how fast an unresolved situation gets worse

    None of these is enemy health scaling, and none of them is `content.gore`: how
    deadly the world is has nothing to do with how graphically it is described.
    """

    baseline: Setting = 50
    encounter_frequency: Setting = 40
    encounter_severity: Setting = 50
    escalation_rate: Setting = 40
    environmental_hazards: Setting = 30
    hostile_actor_frequency: Setting = 45
    lethality: Setting = 40
    safe_zones_exist: bool = True


# ---------------------------------------------------------------------------
# Consequences
# ---------------------------------------------------------------------------


class ConsequenceRules(_RulesModel):
    severity: Setting = 70
    persistence: Setting = 90
    social_memory: Setting = 80
    """How strongly people and communities remember what was done."""

    legal_response: Setting = 70
    faction_response: Setting = 75
    economic_response: Setting = 40

    collateral_damage_matters: bool = True

    actions_can_close_content: bool = True
    """Destroyed opportunities stay destroyed.

    Kill the only merchant who sold a thing and that thread is gone. The Story
    Director may not conjure an equivalent replacement because the loss was
    inconvenient.
    """

    irreversible_outcomes: bool = True
    """Deaths, ruined places, broken bonds and collapsed factions can be permanent.

    The permanent things themselves are events and state; this only says the universe
    permits them.
    """


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------


class RuleBreakingRules(_RulesModel):
    """Exceptional violations of the established power expectations.

    Awakenings, transformations, artifacts, hidden heritage, extreme sacrifice. This
    is emphatically *not* permission to ignore the rules whenever it would be
    convenient: when one of these fires it has to become a recorded event that the
    rest of the story can see.
    """

    allowed: Rarity = Rarity.RARE
    requires_explanation: bool = True


class HiddenPowerRules(_RulesModel):
    allowed: bool = True


class AwakeningRules(_RulesModel):
    enabled: bool = True
    rarity: Rarity = Rarity.RARE


class PowerRules(_RulesModel):
    scale: PowerScale = PowerScale.SUPERHUMAN

    mundane_ceiling: Setting = 10
    superhuman_ceiling: Setting = 25
    legendary_ceiling: Setting = 50

    power_gap_significance: PowerGapSignificance = PowerGapSignificance.HIGH

    training_matters: Setting = 80
    talent_matters: Setting = 60
    experience_matters: Setting = 75
    improvisation: Setting = 40

    rule_breaking: RuleBreakingRules = RuleBreakingRules()
    hidden_power: HiddenPowerRules = HiddenPowerRules()
    awakenings: AwakeningRules = AwakeningRules()

    @model_validator(mode="after")
    def _ceilings_must_ascend(self) -> Self:
        """Tiers that overlap or invert are not a configuration, they are a bug."""
        if not self.mundane_ceiling <= self.superhuman_ceiling <= self.legendary_ceiling:
            raise ValueError(
                "power ceilings must be non-decreasing "
                f"(mundane={self.mundane_ceiling}, superhuman={self.superhuman_ceiling}, "
                f"legendary={self.legendary_ceiling})"
            )
        return self


# ---------------------------------------------------------------------------
# Supernatural
# ---------------------------------------------------------------------------


class SupernaturalRules(_RulesModel):
    """High-level constraints only. There is no magic system here and none implied.

    A future PowerSystem domain owns actual abilities, costs and resources. This
    section says how much of that exists and who knows about it.
    """

    enabled: bool = True
    prevalence: Rarity = Rarity.UNCOMMON
    public_awareness: PublicAwareness = PublicAwareness.COMMON
    regulation: Regulation = Regulation.MODERATE
    discoverability: Discoverability = Discoverability.HIGH

    innate_powers_exist: bool = True
    learnable_powers_exist: bool = True
    supernatural_items_exist: bool = True
    supernatural_creatures_exist: bool = True

    @model_validator(mode="after")
    def _nothing_supernatural_exists_when_disabled(self) -> Self:
        if self.enabled:
            return self
        present = [
            name
            for name in (
                "innate_powers_exist",
                "learnable_powers_exist",
                "supernatural_items_exist",
                "supernatural_creatures_exist",
            )
            if getattr(self, name)
        ]
        if present:
            raise ValueError(
                f"supernatural.enabled is false but these are still true: {', '.join(present)}"
            )
        return self


# ---------------------------------------------------------------------------
# Progression
# ---------------------------------------------------------------------------


class BreakthroughRules(_RulesModel):
    enabled: bool = True
    rarity: Rarity = Rarity.RARE


class PowerLossRules(_RulesModel):
    possible: bool = True


class RegressionRules(_RulesModel):
    possible: bool = True


class ProgressionRules(_RulesModel):
    """How characters get stronger. No XP, no levels, no skills -- only the tone."""

    enabled: bool = True
    pace: ProgressionPace = ProgressionPace.MEDIUM
    ceiling: ProgressionCeiling = ProgressionCeiling.SOFT
    skill_improvement_from_use: bool = True
    training_required: TrainingRequirement = TrainingRequirement.CONTEXTUAL
    breakthroughs: BreakthroughRules = BreakthroughRules()
    power_loss: PowerLossRules = PowerLossRules()
    regression: RegressionRules = RegressionRules()

    @model_validator(mode="after")
    def _no_breakthroughs_without_progression(self) -> Self:
        if not self.enabled and self.breakthroughs.enabled:
            raise ValueError("progression.enabled is false but breakthroughs are still enabled")
        return self


# ---------------------------------------------------------------------------
# Society
# ---------------------------------------------------------------------------


class SlaveryRules(_RulesModel):
    exists: bool = False


class DiscriminationRules(_RulesModel):
    intensity: Setting = 10


class SocietyRules(_RulesModel):
    """Broad systemic tendencies. No concrete factions, laws, cultures or governments.

    Those are world content and belong to WorldState. This section only says what
    kind of society the invented ones are likely to be.
    """

    law_strength: Setting = 60
    corruption: Setting = 40
    inequality: Setting = 50
    social_mobility: Setting = 45
    violence_tolerance: Setting = 30
    institutional_stability: Setting = 70
    information_spread: InformationSpread = InformationSpread.MEDIUM
    reputation_matters: Setting = 75
    slavery: SlaveryRules = SlaveryRules()
    discrimination: DiscriminationRules = DiscriminationRules()


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class ScarcityRules(_RulesModel):
    """One resource kind. Higher means harder to come by."""

    scarcity: Setting = 40


class HealingRules(_RulesModel):
    accessibility: Setting = 50


class RecoveryRules(_RulesModel):
    speed: RecoverySpeed = RecoverySpeed.MEDIUM


class ResourceRules(_RulesModel):
    scarcity: Setting = 40
    """Overall scarcity, for anything the specific kinds below do not cover."""

    food: ScarcityRules = ScarcityRules(scarcity=20)
    medicine: ScarcityRules = ScarcityRules(scarcity=35)
    weapons: ScarcityRules = ScarcityRules(scarcity=30)
    supernatural_resources: ScarcityRules = ScarcityRules(scarcity=65)
    money: ScarcityRules = ScarcityRules(scarcity=45)
    healing: HealingRules = HealingRules()
    recovery: RecoveryRules = RecoveryRules()


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


class SimulationRules(_RulesModel):
    """Configuration for a world simulation that does not exist yet.

    Nothing in this release schedules an offscreen event or advances a clock. These
    values tell the Story Director how autonomous the world is meant to feel, and
    they are the settings a future simulation engine will read.
    """

    world_continues_without_player: bool = True
    npc_autonomy: Setting = 70
    faction_autonomy: Setting = 60
    offscreen_events: bool = True
    event_frequency: Setting = 40
    simulation_scope: SimulationScope = SimulationScope.RELEVANT
    time_progression: TimeProgression = TimeProgression.ACTIVE

    missed_opportunities: bool = True
    """Opportunities need not wait for the player.

    An NPC leaves, a shop closes, a meeting happens without them, a deadline passes.
    The scheduling that would make this real belongs to a future time system.
    """


# ---------------------------------------------------------------------------
# Chance
# ---------------------------------------------------------------------------


class ChanceRules(_RulesModel):
    """How mechanical uncertainty is produced -- and by what, which is the point.

    **Language-model sampling is not gameplay randomness.** `temperature` and token
    sampling must never resolve a mechanical outcome. When checks arrive they will
    run through a dedicated seeded RNG service so a result is reproducible and
    auditable. Nothing in this section licenses the model to decide success itself.
    """

    model: ChanceModel = ChanceModel.SEEDED
    transparency: ChanceTransparency = ChanceTransparency.HIDDEN
    critical_success: bool = True
    critical_failure: bool = True
    luck_influence: Setting = 25

    narrative_rerolls: bool = False
    """False by default and meant to stay that way.

    A Story Director may never quietly reroll a resolved outcome because it was
    narratively inconvenient.
    """


# ---------------------------------------------------------------------------
# Enforcement and content
# ---------------------------------------------------------------------------


class RulesEnforcementRules(_RulesModel):
    """Which authority wins when drama and the rules disagree. Not a difficulty knob."""

    enforcement: RulesEnforcement = RulesEnforcement.STRICT


class ContentRules(_RulesModel):
    """How intense the *description* is. Presentation only.

    Orthogonal to danger by design: `danger.lethality = 100` with `gore = none` is a
    perfectly coherent world where people die often and off the page. These settings
    steer narration; they never change a deterministic outcome.
    """

    violence: ContentIntensity = ContentIntensity.HIGH
    gore: ContentIntensity = ContentIntensity.LOW
    horror: ContentIntensity = ContentIntensity.MEDIUM
    romance: RomancePolicy = RomancePolicy.ENABLED
    sexual_content: SexualContentPolicy = SexualContentPolicy.FADE_TO_BLACK
    substance_use: SubstanceUsePolicy = SubstanceUsePolicy.CONTEXTUAL
    profanity: ContentIntensity = ContentIntensity.MEDIUM


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


class WorldRulesV1(_RulesModel):
    """Version 1 of a world's rules. `WorldRulesV1()` is the balanced default.

    Constructing this directly is fine anywhere inside the application. Loading one
    from storage or from a client must go through `parsing.parse_world_rules`, which
    checks the version before choosing this model.
    """

    version: Literal[1] = WORLD_RULES_VERSION

    narrative: NarrativeRules = NarrativeRules()
    mortality: MortalityRules = MortalityRules()
    danger: DangerRules = DangerRules()
    consequences: ConsequenceRules = ConsequenceRules()
    power: PowerRules = PowerRules()
    supernatural: SupernaturalRules = SupernaturalRules()
    progression: ProgressionRules = ProgressionRules()
    society: SocietyRules = SocietyRules()
    resources: ResourceRules = ResourceRules()
    simulation: SimulationRules = SimulationRules()
    chance: ChanceRules = ChanceRules()
    rules: RulesEnforcementRules = RulesEnforcementRules()
    content: ContentRules = ContentRules()
