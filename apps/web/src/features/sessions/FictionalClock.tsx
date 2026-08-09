import type { SessionTime, TimeOfDay } from '@/api/types';
import type { MessageKey } from '@/i18n/messages';
import { useI18n } from '@/i18n/useI18n';

// Keyed by the union so a new backend period is a compile error here rather than a
// blank word in the header.
const PERIOD_KEYS: Record<TimeOfDay, MessageKey> = {
  dawn: 'session.time.dawn',
  morning: 'session.time.morning',
  afternoon: 'session.time.afternoon',
  evening: 'session.time.evening',
  night: 'session.time.night',
  late_night: 'session.time.lateNight',
};

/**
 * What day and hour it is in the story.
 *
 * Deliberately inert: no control, no editor, nothing to click. The clock moves when
 * a game system moves it, and none exist yet — a turn leaves it where it was. See
 * docs/world-state-time.md.
 *
 * The date and clock arrive as strings the backend derived from the world's calendar;
 * only the part of day is translated, because it is the one value that is a fixed
 * vocabulary rather than a number.
 */
export function FictionalClock({ time }: { time: SessionTime }) {
  const { t } = useI18n();

  return (
    <p className="text-xs text-ink-400" aria-label={t('session.time.label')}>
      {time.display.date} · {time.display.time}
      <span className="text-ink-500"> · {t(PERIOD_KEYS[time.display.period])}</span>
    </p>
  );
}
