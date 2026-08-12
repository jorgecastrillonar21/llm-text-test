"""The WorldRules document: what it accepts, what it refuses, and what presets mean.

Preset assertions here are deliberately *relative*. Pinning `dark_fantasy.lethality
== 90` would turn every tuning change into a test edit while proving nothing; what
actually has to hold is that dark fantasy is deadlier than cozy fantasy.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import InvalidWorldRulesError, UnsupportedRulesVersionError
from app.domain.world_rules import (
    SUPPORTED_VERSIONS,
    WORLD_RULES_VERSION,
    WorldRulesPreset,
    WorldRulesV1,
    build_preset,
    default_world_rules,
    parse_world_rules,
)
from app.domain.world_rules.enums import (
    POWER_GAP_ORDER,
    PROGRESSION_PACE_ORDER,
    RARITY_ORDER,
    ContentIntensity,
    DeathFinality,
    PowerGapSignificance,
    Rarity,
    RulesEnforcement,
    rank,
)
from app.domain.world_rules.rules import (
    BreakthroughRules,
    ContentRules,
    DangerRules,
    MortalityRules,
    NarrativeRules,
    PowerRules,
    ProgressionRules,
    ResurrectionRules,
    SupernaturalRules,
)

# -- the document itself -----------------------------------------------------


def test_defaults_are_valid_and_carry_their_version() -> None:
    rules = default_world_rules()
    assert rules.version == WORLD_RULES_VERSION
    assert rules.narrative.optimism == 50
    assert rules.rules.enforcement is RulesEnforcement.STRICT


def test_optimism_and_darkness_are_independent_axes() -> None:
    """A bleak world that is ultimately hopeful must be expressible."""
    bleak_but_hopeful = NarrativeRules(optimism=90, darkness=90)
    assert bleak_but_hopeful.optimism == 90
    assert bleak_but_hopeful.darkness == 90

    flat = NarrativeRules(optimism=0, darkness=0)
    assert flat.darkness == 0  # not derived from optimism


def test_lethal_world_may_still_be_narrated_without_gore() -> None:
    """`danger.lethality = 100` with `content.gore = none` is a coherent world."""
    rules = WorldRulesV1(
        danger=DangerRules(lethality=100),
        content=ContentRules(gore=ContentIntensity.NONE),
    )
    assert rules.danger.lethality == 100
    assert rules.content.gore is ContentIntensity.NONE


@pytest.mark.parametrize("value", [101, -1, 1000])
def test_settings_outside_the_scale_are_rejected(value: int) -> None:
    with pytest.raises(PydanticValidationError):
        NarrativeRules(optimism=value)


def test_invalid_enum_values_are_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        MortalityRules(death_finality="mostly_permanent")


def test_unknown_keys_are_rejected() -> None:
    """`extra="forbid"`: a typo is a mistake, not an extension point."""
    with pytest.raises(PydanticValidationError):
        DangerRules(letality=90)


def test_a_rules_document_is_frozen() -> None:
    rules = default_world_rules()
    with pytest.raises(PydanticValidationError):
        rules.narrative.optimism = 10  # type: ignore[misc]


# -- coherence between sections ----------------------------------------------


def test_resurrection_must_agree_with_death_finality() -> None:
    with pytest.raises(PydanticValidationError):
        MortalityRules(
            death_finality=DeathFinality.PERMANENT,
            resurrection=ResurrectionRules(enabled=True, rarity=Rarity.RARE),
        )
    with pytest.raises(PydanticValidationError):
        MortalityRules(death_finality=DeathFinality.RESURRECTION_POSSIBLE)

    coherent = MortalityRules(
        death_finality=DeathFinality.RESURRECTION_POSSIBLE,
        resurrection=ResurrectionRules(enabled=True, rarity=Rarity.LEGENDARY),
    )
    assert coherent.resurrection.enabled


def test_enabled_resurrection_cannot_be_impossible() -> None:
    with pytest.raises(PydanticValidationError):
        MortalityRules(
            death_finality=DeathFinality.RESURRECTION_POSSIBLE,
            resurrection=ResurrectionRules(enabled=True, rarity=Rarity.IMPOSSIBLE),
        )


def test_power_ceilings_must_ascend() -> None:
    with pytest.raises(PydanticValidationError):
        PowerRules(mundane_ceiling=60, superhuman_ceiling=40, legendary_ceiling=50)

    assert PowerRules(mundane_ceiling=10, superhuman_ceiling=10, legendary_ceiling=10)


def test_nothing_supernatural_exists_when_the_supernatural_is_disabled() -> None:
    # The defaults declare creatures and items exist, so switching `enabled` off
    # alone leaves the section contradicting itself.
    with pytest.raises(PydanticValidationError):
        SupernaturalRules(enabled=False)

    silent = SupernaturalRules(
        enabled=False,
        innate_powers_exist=False,
        learnable_powers_exist=False,
        supernatural_items_exist=False,
        supernatural_creatures_exist=False,
    )
    assert not silent.enabled


def test_progression_cannot_be_off_while_breakthroughs_are_on() -> None:
    with pytest.raises(PydanticValidationError):
        ProgressionRules(enabled=False)

    assert not ProgressionRules(
        enabled=False, breakthroughs=BreakthroughRules(enabled=False)
    ).enabled


# -- the parsing boundary ----------------------------------------------------


def test_a_serialized_document_round_trips() -> None:
    original = default_world_rules()
    assert parse_world_rules(original.model_dump(mode="json")) == original


def test_the_only_supported_version_is_one() -> None:
    assert SUPPORTED_VERSIONS == (1,)


def test_an_unknown_version_fails_clearly() -> None:
    raw = default_world_rules().model_dump(mode="json") | {"version": 2}
    with pytest.raises(UnsupportedRulesVersionError) as caught:
        parse_world_rules(raw)
    assert "2" in str(caught.value)
    assert "1" in str(caught.value)


def test_a_missing_version_is_rejected() -> None:
    raw = default_world_rules().model_dump(mode="json")
    del raw["version"]
    with pytest.raises(InvalidWorldRulesError):
        parse_world_rules(raw)


@pytest.mark.parametrize("version", ["1", 1.0, None, True])
def test_a_non_integer_version_is_rejected(version: object) -> None:
    """`True` matters specifically: bool is an int and would index straight into v1."""
    raw = default_world_rules().model_dump(mode="json") | {"version": version}
    with pytest.raises(UnsupportedRulesVersionError):
        parse_world_rules(raw)


def test_a_non_object_payload_is_rejected() -> None:
    with pytest.raises(InvalidWorldRulesError):
        parse_world_rules([1, 2, 3])


def test_a_malformed_document_reports_which_version_it_failed_as() -> None:
    raw = default_world_rules().model_dump(mode="json")
    raw["danger"]["lethality"] = 500
    with pytest.raises(InvalidWorldRulesError) as caught:
        parse_world_rules(raw)
    assert "v1" in str(caught.value)


# -- presets -----------------------------------------------------------------


@pytest.mark.parametrize("preset", list(WorldRulesPreset))
def test_every_preset_builds_valid_rules_that_survive_a_round_trip(
    preset: WorldRulesPreset,
) -> None:
    rules = build_preset(preset)
    assert isinstance(rules, WorldRulesV1)
    assert parse_world_rules(rules.model_dump(mode="json")) == rules


def test_every_preset_has_a_builder() -> None:
    """A new enum member without a builder is a KeyError at world creation."""
    for preset in WorldRulesPreset:
        assert build_preset(preset) is not None


def test_balanced_is_the_default_document() -> None:
    assert build_preset(WorldRulesPreset.BALANCED) == default_world_rules()


def test_dark_fantasy_is_more_dangerous_than_cozy_fantasy() -> None:
    dark = build_preset(WorldRulesPreset.DARK_FANTASY)
    cozy = build_preset(WorldRulesPreset.COZY_FANTASY)
    assert dark.danger.baseline > cozy.danger.baseline
    assert dark.narrative.darkness > cozy.narrative.darkness
    assert dark.consequences.persistence > cozy.consequences.persistence


def test_simulationist_gives_the_player_less_protection_than_isekai() -> None:
    sim = build_preset(WorldRulesPreset.SIMULATIONIST)
    isekai = build_preset(WorldRulesPreset.ISEKAI_POWER_FANTASY)
    assert sim.narrative.plot_armor.player < isekai.narrative.plot_armor.player
    assert sim.narrative.protagonist_centrality < isekai.narrative.protagonist_centrality
    assert sim.simulation.npc_autonomy > isekai.simulation.npc_autonomy


def test_shonen_is_dangerous_without_being_lethal() -> None:
    """The point of keeping danger and lethality apart, in one assertion."""
    shonen = build_preset(WorldRulesPreset.SHONEN)
    dark = build_preset(WorldRulesPreset.DARK_FANTASY)
    assert shonen.danger.lethality < dark.danger.lethality
    assert shonen.danger.baseline > shonen.danger.lethality


def test_shonen_progresses_faster_than_dark_fantasy() -> None:
    shonen = build_preset(WorldRulesPreset.SHONEN)
    dark = build_preset(WorldRulesPreset.DARK_FANTASY)
    assert rank(shonen.progression.pace, PROGRESSION_PACE_ORDER) > rank(
        dark.progression.pace, PROGRESSION_PACE_ORDER
    )


def test_isekai_is_the_preset_where_death_can_be_undone() -> None:
    isekai = build_preset(WorldRulesPreset.ISEKAI_POWER_FANTASY)
    assert isekai.mortality.resurrection.enabled
    assert isekai.mortality.death_finality is DeathFinality.RESURRECTION_POSSIBLE

    for preset in WorldRulesPreset:
        if preset is not WorldRulesPreset.ISEKAI_POWER_FANTASY:
            assert not build_preset(preset).mortality.resurrection.enabled


def test_ordering_helpers_agree_with_their_enums() -> None:
    assert rank(Rarity.IMPOSSIBLE, RARITY_ORDER) < rank(Rarity.VERY_COMMON, RARITY_ORDER)
    assert rank(PowerGapSignificance.LOW, POWER_GAP_ORDER) < rank(
        PowerGapSignificance.ABSOLUTE, POWER_GAP_ORDER
    )
    assert set(RARITY_ORDER) == set(Rarity)
    assert set(POWER_GAP_ORDER) == set(PowerGapSignificance)
