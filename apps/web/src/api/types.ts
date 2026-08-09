/**
 * Hand-maintained mirror of the backend DTOs.
 *
 * `pnpm api:generate` writes the authoritative types from the live OpenAPI
 * document to `generated.ts`. These narrow types are what the app actually
 * imports, so a contract drift shows up as a type error in one place rather
 * than scattered across components. See docs/development.md.
 */

export type Language = 'en' | 'es';
export type MessageRole = 'player' | 'narrator' | 'character' | 'system';
export type MemoryKind = 'episodic' | 'fact' | 'relationship' | 'goal' | 'world';
export type ProviderState = 'ready' | 'unreachable' | 'misconfigured' | 'disabled';

export interface World {
  id: string;
  name: string;
  description: string;
  genre: string;
  setting: string;
  language: Language;
  created_at: string;
  updated_at: string;
}

export interface WorldCreate {
  name: string;
  description?: string;
  genre?: string;
  setting?: string;
  language: Language;
  /**
   * A named starting point for the world's rules. Omitted means balanced defaults.
   *
   * The backend also accepts a full `rules` document instead, and rejects a request
   * carrying both. This client only ever sends a preset, so it cannot hit that case.
   */
  rules_preset?: WorldRulesPreset;
}

export const WORLD_RULES_PRESETS = [
  'balanced',
  'cozy_fantasy',
  'shonen',
  'dark_fantasy',
  'simulationist',
  'isekai_power_fantasy',
] as const;

export type WorldRulesPreset = (typeof WORLD_RULES_PRESETS)[number];

export type ProgressionPace = 'very_slow' | 'slow' | 'medium' | 'fast' | 'anime_fast';
export type TimeProgression = 'paused' | 'action_based' | 'active';
export type RulesEnforcement = 'cinematic' | 'flexible' | 'strict' | 'simulationist';

/**
 * The parts of a world's rules this interface reads.
 *
 * Deliberately partial. The stored document has thirteen sections and the summary
 * card shows seven values; typing the rest would be a second copy of the domain
 * model to keep in sync for no benefit. Extra keys arrive over the wire and are
 * simply ignored. See docs/world-rules.md for the whole document.
 */
export interface WorldRules {
  version: number;
  narrative: { plot_armor: { player: number } };
  danger: { baseline: number; lethality: number };
  consequences: { severity: number };
  progression: { enabled: boolean; pace: ProgressionPace };
  simulation: { time_progression: TimeProgression };
  rules: { enforcement: RulesEnforcement };
}

export interface Character {
  id: string;
  world_id: string;
  name: string;
  description: string;
  appearance: string;
  personality: string;
  backstory: string;
  speech_style: string;
  goals: string[];
  secrets: string[];
  created_at: string;
  updated_at: string;
}

export interface CharacterCreate {
  name: string;
  description?: string;
  appearance?: string;
  personality?: string;
  backstory?: string;
  speech_style?: string;
  goals?: string[];
  secrets?: string[];
}

export type TimeOfDay = 'dawn' | 'morning' | 'afternoon' | 'evening' | 'night' | 'late_night';

/**
 * The session's fictional clock.
 *
 * `elapsed_minutes` is the only number the backend stores; everything under
 * `display` is derived from it and the world's start date on every read. Nothing
 * here is writable — time moves when the game moves it. See docs/world-state-time.md.
 */
export interface SessionTime {
  elapsed_minutes: number;
  display: {
    date: string;
    time: string;
    period: TimeOfDay;
    elapsed: string;
  };
}

export interface GameSession {
  id: string;
  world_id: string;
  title: string;
  player_name: string;
  player_description: string;
  current_location: string;
  summary: string;
  turn_index: number;
  /** Independent of `turn_index`: neither can be computed from the other. */
  elapsed_minutes: number;
  /**
   * How many batches of state changes this session has applied. A third independent
   * counter — turns, minutes and changes each move for their own reasons, and a turn
   * of pure conversation moves only the first. See docs/world-state-facts.md.
   */
  state_revision: number;
  created_at: string;
  updated_at: string;
}

export interface SessionDetail extends GameSession {
  world: World;
  time: SessionTime;
}

export interface SessionCreate {
  world_id: string;
  title: string;
  player_name: string;
  player_description?: string;
  current_location?: string;
}

export interface Message {
  id: string;
  session_id: string;
  turn_index: number;
  role: MessageRole;
  speaker_character_id: string | null;
  content: string;
  created_at: string;
}

export interface TurnMessage {
  id: string;
  turn_index: number;
  role: MessageRole;
  speaker: string;
  speaker_character_id: string | null;
  content: string;
}

export interface AppliedRelationship {
  character_id: string;
  trust: number;
  affection: number;
  respect: number;
  fear: number;
  reason: string;
}

export interface TurnResponse {
  session_id: string;
  turn_index: number;
  messages: TurnMessage[];
  suggested_actions: string[];
  relationships: AppliedRelationship[];
  memories_created: number;
  events_created: number;
  /** Fact proposals that survived review and are now established truth. */
  facts_established: number;
  /** Proposals the review refused. Normal, and usually the larger number. */
  facts_rejected: number;
  /** New places this turn established as canon for this session. */
  locations_created: number;
  locations_rejected: number;
  visual_cue_generated: boolean;
}

export interface ProviderStatus {
  provider: string;
  state: ProviderState;
  detail: string;
  model: string | null;
  extra: Record<string, string>;
}

export interface AiStatus {
  story: ProviderStatus;
  image: ProviderStatus;
}

export interface Health {
  status: string;
  app_env: string;
  database_ready: boolean;
}
