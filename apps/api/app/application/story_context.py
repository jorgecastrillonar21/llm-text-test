"""Everything a story provider is allowed to see.

Providers receive one of these objects and nothing else -- no database session, no ORM
models. Retrieval policy therefore lives in one place (context_builder) and stays
testable and deterministic.

Two of them, for the two things a provider is asked to do. `StoryContext` is the turn:
here is the world, here is what the player typed, narrate what happens. `OutcomeContext`
is narration after the fact: here is what the game has already decided and committed,
describe it. The second is much smaller on purpose -- there is nothing left to decide,
so there is nothing left to show.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import Language, MemoryKind, MessageRole
from app.domain.resolution import ResolutionDisposition
from app.domain.vocabulary import MetadataValue
from app.domain.world_locations import LocationAccessibility, LocationCondition
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
from app.domain.world_situations import SituationScope, SituationStatus
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


class PlaceContext(BaseModel):
    """One place, phrased for a reader.

    No id, like `FactContext`. The director narrates geography and never addresses it;
    a uuid in the prompt is tokens spent on something no sentence will use, and an id a
    model has seen is an id it will eventually invent a sibling for.

    Condition and accessibility are None when they are the ordinary case. Sending
    "intact, open" for every place would drown the one entry that says "destroyed".
    """

    model_config = ConfigDict(frozen=True)

    name: str
    kind: str
    """The subtype if the world gave one, otherwise the category: `tavern`, `structure`."""

    condition: LocationCondition | None = None
    accessibility: LocationAccessibility | None = None


class ConnectedPlaceContext(BaseModel):
    """Somewhere reachable in one step, and what stands between."""

    model_config = ConfigDict(frozen=True)

    name: str
    via: str
    """How: `door`, `stairs`, `imperial highway`. Rendered from the connection's
    subtype or category."""

    passable: bool
    """False for a blocked, sealed or collapsed crossing. Sent rather than filtered:
    a model that cannot see the barred gate writes the player straight through it."""

    travel_minutes: int | None = None
    """The connection's nominal crossing time, when the world declared one. Not a
    promise about how long a journey takes -- that is the future TravelEngine's, and
    it will read more than this number."""


class SpatialContext(BaseModel):
    """Where the scene is, and what that place is next to and inside of.

    A slice of the graph, never the graph. Selection is deterministic and lives in
    `spatial_context.py` with the reasoning about tiers.

    Absent from this model on purpose: proximity between characters, line of sight,
    interaction range and anything tactical. Sharing a location is not being near
    someone, and a field here saying otherwise would make every scene wrong in the
    same way.
    """

    model_config = ConfigDict(frozen=True)

    current: PlaceContext
    zones: list[str] = Field(default_factory=list)
    """Named areas within the current place -- the bar, the fireplace. Names only:
    zones carry no state, and a scene needs somewhere to stand, not a record."""

    contains: list[PlaceContext] = Field(default_factory=list)
    exits: list[ConnectedPlaceContext] = Field(default_factory=list)
    within: list[PlaceContext] = Field(default_factory=list)
    """Containers, nearest first. `District, City, Region` -- enough for a scene to
    know where in the world it is."""


class SituationContext(BaseModel):
    """One ongoing process, phrased for a reader.

    No id, like `PlaceContext` and `FactContext`. The director narrates what is going
    on; it does not address it, and cannot -- there is no field in its response that
    names a situation, which is what stops a model from resolving a war by mentioning
    one.

    The numbers are sent, unlike a location's default condition. There is no
    uninteresting value for intensity: a director that knows the siege is at 78 writes
    a different scene from one that knows it is at 20, and "78" is shorter than any
    sentence that would convey it.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    kind: str
    """The subtype if the world gave one, otherwise the category: `siege`, `conflict`."""

    status: SituationStatus
    intensity: int
    threat: int
    momentum: int
    """Negative is winding down, positive is growing. Growing is not the same as
    worsening -- a festival at +50 is getting livelier."""

    scope: SituationScope
    duration: str
    """Rendered: "3 days", "40 min". The authoritative minute counts stay in the
    database, because a prompt has no use for 4320 and would only do arithmetic on it."""


class SituationsContext(BaseModel):
    """What is currently under way that this scene should know about.

    A bounded selection, never everything. Which ones, and why, is deterministic and
    lives in `situation_context.py` with the reasoning about priority bands.

    Absent on purpose: anything a world has tagged secret. There is no KnowledgeState
    yet, so the alternative to that convention was narrating every conspiracy the turn
    it began. See `situation_context` for how limited a protection that is.
    """

    model_config = ConfigDict(frozen=True)

    ongoing: list[SituationContext] = Field(default_factory=list)


class EventContext(BaseModel):
    """One thing that has already happened, phrased for a reader.

    No id, like `FactContext` and `PlaceContext`, and for the same reason. The director
    is also given no payload: the structured detail an event carries is mechanical input
    for game systems, and a narrator that reads `{"damage": 12}` starts doing arithmetic
    in prose. What reaches the prompt is the summary that was written when the event was
    recorded, and when it happened.
    """

    model_config = ConfigDict(frozen=True)

    summary: str
    kind: str
    """The subtype if there was one, otherwise the category: `bridge_collapsed`,
    `action`."""

    when: str
    """Rendered relative to now -- "3 hours ago", "just now". The absolute minute stays
    out of the prompt for the same reason the raw clock does."""


class HistoryContext(BaseModel):
    """What has happened in this session, in two bands.

    Never the whole history. A session accumulates events indefinitely and a prompt does
    not grow, so retrieval is bounded and deterministic; the limits and the reasoning
    live in `context_builder` with every other retrieval decision.

    The split exists because recency and weight answer different questions. A siege that
    began forty turns ago still shapes every scene; what happened in the last hour is
    what a character would actually mention.
    """

    model_config = ConfigDict(frozen=True)

    landmarks: list[EventContext] = Field(default_factory=list)
    """The heaviest events of the whole session, oldest first. These are what the story
    is about."""

    recent: list[EventContext] = Field(default_factory=list)
    """What has happened lately, oldest first, excluding anything already listed as a
    landmark."""


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
    space: SpatialContext | None = None
    """None when the world has no geography, or when the session is not in a place the
    graph knows. Optional rather than empty: an empty spatial block tells a model the
    game tracks places and has none, which reads worse than saying nothing."""

    situations: SituationsContext | None = None
    """None when nothing relevant is under way, which is most turns. Optional for the
    same reason `space` is."""

    world_facts: WorldFactsContext = Field(default_factory=WorldFactsContext)
    history: HistoryContext = Field(default_factory=HistoryContext)
    """What has already happened, bounded. Empty on a session that has not recorded
    anything worth remembering, which is most early sessions."""

    relevant_characters: list[CharacterContext] = Field(default_factory=list)
    recent_messages: list[MessageContext] = Field(default_factory=list)
    relevant_memories: list[MemoryContext] = Field(default_factory=list)
    relationships: list[RelationshipContext] = Field(default_factory=list)
    player_action: str


class OutcomeContext(BaseModel):
    """What a story provider is shown when it narrates an outcome that already happened.

    Small, and deliberately so. Everything a turn's context carries in order to help a
    model *decide* something -- the rules, the geography, the characters' secrets, the
    relationship vectors -- is absent, because the deciding is over. What is left is the
    verdict, the events history kept, and enough of the scene to write a paragraph in
    the right language at the right time of day.

    The mechanics in here are authoritative and already committed. A narrator's contract
    (`OutcomeNarration`) has one field, so there is nowhere for it to disagree.
    """

    model_config = ConfigDict(frozen=True)

    world: WorldContext
    player: PlayerContext
    time: TimeContext

    disposition: ResolutionDisposition
    """`applied`, `rejected` or `no_effect`. Never `success` or `failure`: an attempt
    that happened and went badly is `applied`, and prose that called it a failure would
    read as the world refusing it."""

    reason_code: str | None = None
    """Why, when there is a machine-readable why: `already_progressed`, `invalid_target`.
    Given to the narrator as a hint about what to write, never as a phrase to print."""

    resolver: str
    """What decided this, by name. Not shown to the player -- it is here so a narrator
    of an unfamiliar outcome has something better than a shrug."""

    events: list[EventContext] = Field(default_factory=list)
    """What history kept, oldest first. Often empty: most outcomes change the world
    without producing anything worth remembering, and the prose still has the detail
    below to work from."""

    detail: dict[str, MetadataValue] = Field(default_factory=dict)
    """The resolver's own `narrative_context` -- flat, structured, and the authoritative
    account of what happened. Empty when a stored resolution is being narrated after the
    fact: the outcome object was a value in a process that has finished, and it is not
    reconstructed from the rows. See `app.application.narration_service`."""
