import type { SessionDetail } from '@/api/types';
import { useI18n } from '@/i18n/useI18n';

/**
 * Where the scene is and how many times this world has changed.
 *
 * Three inert values beside the clock, and deliberately nothing more. No map, no
 * dashboard, no state editor, no timeline: what is true in the world is decided by
 * resolutions and typed mutations, and an interface that could edit it would be a way
 * around every rule those enforce. See docs/world-state.md.
 *
 * `state_revision` sits next to the turn count rather than replacing it because they
 * count different things — a turn of pure conversation moves the turn index and leaves
 * the revision where it was, and seeing both is how that becomes visible rather than
 * confusing.
 *
 * `current_location` is free text today. It is the session's legacy position field and
 * moves to CharacterState when characters arrive; showing it here is honest about what
 * the game currently knows, and a second position system invented to make this line
 * prettier would be worse than the string.
 *
 * The inspect link is a developer affordance and only renders in a development build.
 * It opens the `full_debug` snapshot as raw JSON in a new tab — a view for a person
 * with a problem, not a screen this application maintains.
 */
export function WorldStateReadout({ session }: { session: SessionDetail }) {
  const { t } = useI18n();

  return (
    <p className="text-xs text-ink-400" aria-label={t('session.world.label')}>
      {session.current_location}
      <span className="text-ink-500">
        {' · '}
        {t('session.world.revision', { n: session.state_revision })}
      </span>
      {import.meta.env.DEV ? (
        <>
          {' · '}
          <a
            className="text-ink-500 underline hover:text-ink-300"
            href={`/api/v1/dev/sessions/${session.id}/world-state`}
            target="_blank"
            rel="noreferrer"
          >
            {t('session.world.debug')}
          </a>
        </>
      ) : null}
    </p>
  );
}
