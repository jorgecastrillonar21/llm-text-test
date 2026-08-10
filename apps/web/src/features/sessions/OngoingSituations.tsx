import type { Situation, SituationStatus } from '@/api/types';
import type { MessageKey } from '@/i18n/messages';
import { useI18n } from '@/i18n/useI18n';

// Keyed by the union so a new backend status is a compile error here rather than a
// blank word in the panel.
const STATUS_KEYS: Record<SituationStatus, MessageKey> = {
  planned: 'session.situations.planned',
  active: 'session.situations.active',
  dormant: 'session.situations.dormant',
  resolved: 'session.situations.resolved',
  cancelled: 'session.situations.cancelled',
};

const MAX_SHOWN = 4;

/**
 * How a process is moving, as a word rather than a number.
 *
 * Neutral on purpose, matching the backend's rendering: "growing" covers a fire
 * spreading and a festival filling the streets. Calling growth "worsening" would make
 * every positive process in the game read as a threat, which is the exact mistake the
 * three-number model exists to avoid.
 */
function momentumKey(momentum: number): MessageKey {
  if (momentum > 0) return 'session.situations.growing';
  if (momentum < 0) return 'session.situations.fading';
  return 'session.situations.steady';
}

/**
 * What the world is currently doing, beside the story.
 *
 * Deliberately inert: no control, no editor, nothing to click. Situations move when a
 * game system moves them — narration cannot, and neither can this panel. It is the same
 * decision `FictionalClock` makes for the same reason. See docs/world-state-situations.md.
 *
 * Not a dashboard, not a quest tracker and not a map. Four lines at most, because the
 * point is that the player can see the world is doing something, not that they can
 * manage it.
 */
export function OngoingSituations({ situations }: { situations: Situation[] }) {
  const { t } = useI18n();
  if (situations.length === 0) return null;

  // Already ordered by the backend (importance, then most recently progressed, then
  // title). Re-sorting here would be a second policy that quietly disagrees with the
  // one the prompt uses.
  const shown = situations.slice(0, MAX_SHOWN);

  return (
    <section className="mb-4 rounded-xl border border-ink-800 bg-ink-900/40 p-3">
      <h2 className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-400">
        {t('session.situations.title')}
      </h2>
      <ul className="flex flex-col gap-1.5">
        {shown.map((situation) => (
          <li key={situation.id} className="text-sm">
            <span className="text-ink-200">{situation.title}</span>
            <span className="text-xs text-ink-500">
              {' · '}
              {t(STATUS_KEYS[situation.status])}
              {' · '}
              {t('session.situations.intensity', { n: situation.intensity })}
              {' · '}
              {t(momentumKey(situation.momentum))}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
