import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useI18n } from '@/i18n/useI18n';
import { useCharacters, useSessions, useWorld } from '@/api/hooks';
import { Button, Card, EmptyState, Spinner } from '@/components/ui';
import { CreateCharacterForm } from '@/features/characters/CreateCharacterForm';
import { StartSessionForm } from '@/features/sessions/StartSessionForm';

export function WorldPage() {
  const { worldId = '' } = useParams();
  const { t } = useI18n();
  const [addingCharacter, setAddingCharacter] = useState(false);
  const [startingSession, setStartingSession] = useState(false);

  const world = useWorld(worldId);
  const characters = useCharacters(worldId);
  const sessions = useSessions(worldId);

  if (world.isPending) return <Spinner label={t('common.loading')} />;
  if (world.isError) {
    return (
      <div className="flex flex-col gap-3">
        <p role="alert" className="text-sm text-red-300">
          {world.error.message}
        </p>
        <Button variant="ghost" onClick={() => world.refetch()}>
          {t('common.retry')}
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-2">
        <Link to="/" className="text-sm text-ink-400 hover:text-ink-200">
          ← {t('nav.back')}
        </Link>
        <div className="flex items-baseline justify-between gap-3">
          <h1 className="text-xl font-semibold">{world.data.name}</h1>
          <span className="shrink-0 rounded bg-ink-800 px-1.5 py-0.5 text-xs uppercase text-ink-200">
            {world.data.language}
          </span>
        </div>
        {world.data.genre ? <p className="text-sm text-ink-400">{world.data.genre}</p> : null}
        {world.data.description ? (
          <p className="prose-narration text-sm text-ink-200">{world.data.description}</p>
        ) : null}
      </header>

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-lg font-semibold">{t('world.characters')}</h2>
          {!addingCharacter ? (
            <Button variant="subtle" onClick={() => setAddingCharacter(true)}>
              {t('world.addCharacter')}
            </Button>
          ) : null}
        </div>

        {addingCharacter ? (
          <CreateCharacterForm worldId={worldId} onDone={() => setAddingCharacter(false)} />
        ) : null}

        {characters.data?.length === 0 && !addingCharacter ? (
          <EmptyState>{t('world.charactersEmpty')}</EmptyState>
        ) : null}

        <ul className="flex flex-col gap-2">
          {characters.data?.map((character) => (
            <li key={character.id}>
              <Card>
                <h3 className="font-medium">{character.name}</h3>
                {character.personality ? (
                  <p className="text-sm text-ink-400">{character.personality}</p>
                ) : null}
                {character.description ? (
                  <p className="mt-1 text-sm text-ink-200">{character.description}</p>
                ) : null}
              </Card>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-lg font-semibold">{t('world.sessions')}</h2>
          {!startingSession ? (
            <Button onClick={() => setStartingSession(true)}>{t('world.startSession')}</Button>
          ) : null}
        </div>

        {startingSession ? (
          <StartSessionForm worldId={worldId} onDone={() => setStartingSession(false)} />
        ) : null}

        {sessions.data?.length === 0 && !startingSession ? (
          <EmptyState>{t('home.sessionsEmpty')}</EmptyState>
        ) : null}

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
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
