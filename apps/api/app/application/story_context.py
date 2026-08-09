"""Everything a story provider is allowed to see about a turn.

Providers receive this object and nothing else -- no database session, no ORM
models. Retrieval policy therefore lives in one place (context_builder) and stays
testable and deterministic.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Language, MemoryKind, MessageRole
from app.domain.world_rules.enums import (
    ChanceModel,
    ContentIntensity,
    DeathFinality,
    IncapacitationPolicy,
    PowerGapSignificance,
    PowerScale,
    ProgressionPace,
    PublicAwareness,
    Rarity,
    RomancePolicy,
    RulesEnforcement,
    SexualContentPolicy,
    SubstanceUsePolicy,
    TimeProgression,
)
from app.domain.world_time import TimeOfDay


class WorldContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    description: str
    genre: str
    setting: str
    language: Language = Language.EN


class PlayerContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str


class SessionContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    title: str
    current_location: str
    summary: str
    turn_index: int


class TimeContext(BaseModel):
    """What time it is in the fiction, as the Story Director should read it.

    Every field is derived from the session's `elapsed_minutes`. The raw counter is
    deliberately not here: a narrator has no use for "28980" and would only be tempted
    to do arithmetic on it, while the phrasing below is what actually shapes a scene.

    Read-only in the strongest sense available -- the director's response schema has
    no field that reaches the clock, so the only way time moves is application code.
    """

    model_config = ConfigDict(frozen=True)

    calendar_date: str
    clock: str
    period: TimeOfDay
    elapsed_since_start: str


class FactContext(BaseModel):
    """One established truth, phrased for a reader rather than a query.

    The subject is a *label* -- "King Aldren", "the world" -- resolved from the ids the
    context builder already loaded. The id itself is deliberately absent: the director
    does not address facts, it reads them, and a uuid in a prompt is tokens spent on
    something no sentence will ever use.
    """

    model_config = ConfigDict(frozen=True)

    subject: str
    property: str
    value: str
    """Rendered, not raw. `false` reads the same to a model whether it arrived as a
    bool or a string, and the prompt is prose."""


class WorldFactsContext(BaseModel):
    """What is currently true, split by how much it should weigh on the scene.

    Not every fact: a session accumulates them and a prompt does not grow. Selection is
    importance-ordered and deterministic, which is retrieval policy and therefore lives
    in `context_builder` with every other retrieval decision.
    """

    model_config = ConfigDict(frozen=True)

    critical: list[FactContext] = Field(default_factory=list)
    """Facts that reshape the story. Deaths, ruined places, changed regimes."""

    relevant: list[FactContext] = Field(default_factory=list)
    """Established details that colour a scene without dominating it."""


class CharacterContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    description: str
    appearance: str
    personality: str
    backstory: str
    speech_style: str
    goals: list[str] = Field(default_factory=list)
    # Secrets are given to the director so NPCs can act on them without the
    # narration revealing them. See docs/ai-contract.md.
    secrets: list[str] = Field(default_factory=list)


class MessageContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn_index: int
    role: MessageRole
    speaker: str
    content: str


class MemoryContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: MemoryKind
    summary: str
    importance: int
    character_id: uuid.UUID | None = None


class RelationshipContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    character_id: uuid.UUID
    character_name: str
    trust: int
    affection: int
    respect: int
    fear: int


class PlotArmorContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    player: int
    important_npcs: int
    ordinary_npcs: int


class WorldRulesContext(BaseModel):
    """The rules the Story Director is allowed to see.

    A curated subset of `WorldRulesV1`, not the whole document. Left out on purpose:
    society, resources, and the finer power/consequence dials. Those exist for future
    deterministic systems, and a per-turn narration prompt is not improved by knowing
    that medicine scarcity is 35 -- it is only made longer, and prompt length is a
    measured constraint on this project.

    Flat rather than nested so the renderer can produce short lines without walking a
    tree, and so adding a field is an explicit decision rather than a side effect of
    adding one to the domain.
    """

    model_config = ConfigDict(frozen=True)

    # -- narrative shape
    optimism: int
    darkness: int
    protagonist_centrality: int
    deus_ex_machina: int
    coincidence_frequency: int
    consequence_persistence: int
    plot_armor: PlotArmorContext

    # -- mortality
    player_death: bool
    npc_death: bool
    death_finality: DeathFinality
    permanent_injury: bool
    incapacitation_before_death: IncapacitationPolicy
    resurrection_enabled: bool
    resurrection_rarity: Rarity

    # -- danger
    danger_baseline: int
    encounter_frequency: int
    encounter_severity: int
    escalation_rate: int
    lethality: int
    safe_zones_exist: bool

    # -- consequences
    consequence_severity: int
    social_memory: int
    actions_can_close_content: bool
    irreversible_outcomes: bool

    # -- power
    power_scale: PowerScale
    power_gap_significance: PowerGapSignificance
    rule_breaking_allowed: Rarity
    rule_breaking_requires_explanation: bool

    # -- supernatural
    supernatural_enabled: bool
    supernatural_prevalence: Rarity
    supernatural_public_awareness: PublicAwareness

    # -- progression
    progression_enabled: bool
    progression_pace: ProgressionPace

    # -- simulation
    world_continues_without_player: bool
    npc_autonomy: int
    faction_autonomy: int
    offscreen_events: bool
    missed_opportunities: bool
    time_progression: TimeProgression

    # -- authority
    enforcement: RulesEnforcement
    chance_model: ChanceModel
    narrative_rerolls: bool

    # -- content presentation
    violence: ContentIntensity
    gore: ContentIntensity
    horror: ContentIntensity
    romance: RomancePolicy
    sexual_content: SexualContentPolicy
    substance_use: SubstanceUsePolicy
    profanity: ContentIntensity


class StoryContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    world: WorldContext
    world_rules: WorldRulesContext
    player: PlayerContext
    session: SessionContext
    time: TimeContext
    world_facts: WorldFactsContext = Field(default_factory=WorldFactsContext)
    relevant_characters: list[CharacterContext] = Field(default_factory=list)
    recent_messages: list[MessageContext] = Field(default_factory=list)
    relevant_memories: list[MemoryContext] = Field(default_factory=list)
    relationships: list[RelationshipContext] = Field(default_factory=list)
    player_action: str
