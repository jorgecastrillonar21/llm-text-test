import { useState } from 'react';
import { Link } from 'react-router';
import { useI18n } from '@/i18n/useI18n';
import { useSessions, useWorlds } from '@/api/hooks';
import { Button, Card, EmptyState, Spinner } from '@/components/ui';
import { CreateWorldForm } from '@/features/worlds/CreateWorldForm';

export function HomePage() {
  const { t } = useI18n();
  const [creating, setCreating] = useState(false);
  const worlds = useWorlds();
  const sessions = useSessions();

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <h1 className="text-lg font-semibold">{t('home.heading')}</h1>
          {!creating ? (
            <Button onClick={() => setCreating(true)}>{t('home.createWorld')}</Button>
          ) : null}
        </div>

        {creating ? <CreateWorldForm onDone={() => setCreating(false)} /> : null}

        {worlds.isPending ? <Spinner label={t('common.loading')} /> : null}
        {worlds.data?.length === 0 && !creating ? (
          <EmptyState>{t('home.empty')}</EmptyState>
        ) : null}

        <ul className="flex flex-col gap-2">
          {worlds.data?.map((world) => (
            <li key={world.id}>
              <Link to={`/worlds/${world.id}`} className="block">
                <Card className="transition-colors hover:border-ink-400">
                  <div className="flex items-baseline justify-between gap-3">
                    <h2 className="font-medium">{world.name}</h2>
                    <span className="shrink-0 rounded bg-ink-800 px-1.5 py-0.5 text-xs uppercase text-ink-200">
                      {world.language}
                    </span>
                  </div>
                  {world.genre ? <p className="text-sm text-ink-400">{world.genre}</p> : null}
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">{t('home.continue')}</h2>
        {sessions.data?.length === 0 ? <EmptyState>{t('home.sessionsEmpty')}</EmptyState> : null}
        <ul className="flex flex-col gap-2">
          {sessions.data?.map((session) => (
            <li key={session.id}>
              <Link to={`/sessions/${session.id}`} className="block">
                <Card className="transition-colors hover:border-ink-400">
                  <div className="flex items-baseline justify-between gap-3">
                    <h3 className="font-medium">{session.title}</h3>
                    <span className="shrink-0 text-xs text-ink-400">
                      {t('session.turn', { n: session.turn_index })}
                    </span>
                  </div>
                  <p className="text-sm text-ink-400">{session.player_name}</p>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
