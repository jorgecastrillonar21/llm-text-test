import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useI18n } from '@/i18n/useI18n';
import {
  newClientActionId,
  useAiStatus,
  useCharacters,
  useMessages,
  useSession,
  useSituations,
  useSubmitTurn,
} from '@/api/hooks';
import type { TurnSubmission } from '@/api/types';
import { Button, Spinner } from '@/components/ui';
import { StatusBadge } from '@/components/StatusBadge';
import { Timeline } from '@/features/sessions/Timeline';
import { Composer } from '@/features/sessions/Composer';
import { FictionalClock } from '@/features/sessions/FictionalClock';
import { OngoingSituations } from '@/features/sessions/OngoingSituations';
import { WorldStateReadout } from '@/features/sessions/WorldStateReadout';

export function SessionPage() {
  const { sessionId = '' } = useParams();
  const { t } = useI18n();

  const session = useSession(sessionId);
  const messages = useMessages(sessionId);
  const characters = useCharacters(session.data?.world_id ?? '');
  const situations = useSituations(sessionId);
  const aiStatus = useAiStatus();
  const submitTurn = useSubmitTurn(sessionId);

  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [submission, setSubmission] = useState<TurnSubmission | null>(null);

  function send(next: TurnSubmission) {
    setSubmission(next);
    submitTurn.mutate(next, {
      onSuccess: (result) => setSuggestions(result.suggested_actions),
    });
  }

  /** A new action from the player: a new submission, and a new id for it. */
  function runTurn(action: string) {
    send({ action, clientActionId: newClientActionId() });
  }

  /**
   * The same submission again, under the id it already had.
   *
   * This is what makes the retry button safe. A failed turn is usually a no-op, but a
   * response that never arrived may have been a turn that was played -- and in that case
   * the server recognises the id and returns the turn it already played instead of
   * playing a second one.
   */
  function retryTurn() {
    if (submission !== null) send(submission);
  }

  if (session.isPending) return <Spinner label={t('common.loading')} />;
  if (session.isError) {
    return (
      <div className="flex flex-col gap-3">
        <p role="alert" className="text-sm text-red-300">
          {session.error.message}
        </p>
        <Button variant="ghost" onClick={() => session.refetch()}>
          {t('common.retry')}
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col">
      <header className="mb-5 flex flex-col gap-2 border-b border-ink-800 pb-4">
        <Link to={`/worlds/${session.data.world_id}`} className="text-sm text-ink-400 hover:text-ink-200">
          ← {session.data.world.name}
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-lg font-semibold">{session.data.title}</h1>
          <div className="flex items-center gap-2">
            <span className="text-xs text-ink-400">
              {t('session.turn', { n: session.data.turn_index })}
            </span>
            {aiStatus.data ? <StatusBadge state={aiStatus.data.story.state} /> : null}
          </div>
        </div>
        {/* Next to the turn count, not instead of it: they measure different things. */}
        <FictionalClock time={session.data.time} />
        {/* And the third counter, under the other two. Fictional minutes, turns and
            state revisions each move for their own reasons; none is derived from
            another, so all three are shown. */}
        <WorldStateReadout session={session.data} />
      </header>

      {/* Above the transcript, not inside it: the world is doing this regardless of
          what was said last turn. */}
      <OngoingSituations
        situations={(situations.data?.situations ?? []).map((entry) => entry.situation)}
      />

      <div className="flex-1">
        {messages.isPending ? (
          <Spinner label={t('common.loading')} />
        ) : (
          <Timeline
            messages={messages.data ?? []}
            characters={characters.data ?? []}
            playerName={session.data.player_name}
            scrollToken={session.data.turn_index}
          />
        )}
      </div>

      {submitTurn.isError ? (
        <div role="alert" className="mt-4 rounded-xl border border-red-500/40 bg-red-500/10 p-3">
          <p className="text-sm font-medium text-red-300">{t('session.turnFailed')}</p>
          <p className="mt-1 text-xs text-red-200/80">{submitTurn.error.message}</p>
          <p className="mt-1 text-xs text-ink-400">{t('session.turnFailedRetry')}</p>
          <Button
            variant="ghost"
            className="mt-2"
            onClick={retryTurn}
            disabled={submitTurn.isPending || submission === null}
          >
            {t('common.retry')}
          </Button>
        </div>
      ) : null}

      <Composer
        suggestions={suggestions}
        pending={submitTurn.isPending}
        onSubmit={runTurn}
      />
    </div>
  );
}
