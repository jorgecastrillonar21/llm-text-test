import type { ProgressionPace, RulesEnforcement, TimeProgression, WorldRules } from '@/api/types';
import type { MessageKey } from '@/i18n/messages';

export interface RulesSummaryRow {
  label: MessageKey;
  value: MessageKey;
}

/**
 * A 0..100 setting as one of the five bands the scale documents.
 *
 * The bands are a reading aid, not semantics: the backend never buckets these
 * values, and nothing in the engine branches on "high". See docs/world-rules.md.
 */
export function levelKey(setting: number): MessageKey {
  if (setting < 20) return 'rules.level.veryLow';
  if (setting < 40) return 'rules.level.low';
  if (setting < 60) return 'rules.level.moderate';
  if (setting < 80) return 'rules.level.high';
  return 'rules.level.veryHigh';
}

// Keyed by the union types so a new backend variant is a compile error here
// rather than a blank cell on the world screen.
const PACE_KEYS: Record<ProgressionPace, MessageKey> = {
  very_slow: 'rules.pace.verySlow',
  slow: 'rules.pace.slow',
  medium: 'rules.pace.medium',
  fast: 'rules.pace.fast',
  anime_fast: 'rules.pace.animeFast',
};

const TIME_KEYS: Record<TimeProgression, MessageKey> = {
  paused: 'rules.time.paused',
  action_based: 'rules.time.actionBased',
  active: 'rules.time.active',
};

const ENFORCEMENT_KEYS: Record<RulesEnforcement, MessageKey> = {
  cinematic: 'rules.enforcement.cinematic',
  flexible: 'rules.enforcement.flexible',
  strict: 'rules.enforcement.strict',
  simulationist: 'rules.enforcement.simulationist',
};

/**
 * The seven values worth showing at a glance, as translation keys.
 *
 * Danger and lethality are listed separately on purpose: a world can be
 * constantly threatening and rarely fatal, and collapsing them into one
 * "difficulty" row would hide the distinction the rules are built on.
 */
export function summarizeRules(rules: WorldRules): RulesSummaryRow[] {
  return [
    { label: 'rules.field.danger', value: levelKey(rules.danger.baseline) },
    { label: 'rules.field.lethality', value: levelKey(rules.danger.lethality) },
    { label: 'rules.field.plotArmor', value: levelKey(rules.narrative.plot_armor.player) },
    { label: 'rules.field.consequences', value: levelKey(rules.consequences.severity) },
    {
      label: 'rules.field.progression',
      value: rules.progression.enabled ? PACE_KEYS[rules.progression.pace] : 'rules.pace.off',
    },
    { label: 'rules.field.simulation', value: TIME_KEYS[rules.simulation.time_progression] },
    { label: 'rules.field.enforcement', value: ENFORCEMENT_KEYS[rules.rules.enforcement] },
  ];
}
