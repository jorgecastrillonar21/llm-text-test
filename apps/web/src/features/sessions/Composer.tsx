import { useState, type FormEvent, type KeyboardEvent } from 'react';
import { useI18n } from '@/i18n/useI18n';
import { Button, Spinner } from '@/components/ui';

interface ComposerProps {
  suggestions: string[];
  pending: boolean;
  onSubmit: (action: string) => void;
}

export function Composer({ suggestions, pending, onSubmit }: ComposerProps) {
  const { t } = useI18n();
  const [action, setAction] = useState('');

  function submit(value: string) {
    const trimmed = value.trim();
    // The pending guard is what prevents a double submit turning into two turns.
    if (!trimmed || pending) return;
    setAction('');
    onSubmit(trimmed);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submit(action);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit(action);
    }
  }

  return (
    <div className="sticky bottom-0 -mx-4 mt-4 border-t border-ink-800 bg-ink-900/95 px-4 pt-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] backdrop-blur">
      {suggestions.length > 0 && !pending ? (
        <div className="mb-3 flex flex-col gap-2">
          <p className="text-xs text-ink-400">{t('session.suggestions')}</p>
          <div className="flex flex-wrap gap-2">
            {suggestions.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => submit(suggestion)}
                className="rounded-full border border-ink-600 px-3 py-1.5 text-left text-sm text-ink-200 transition-colors hover:border-arcane-400 hover:text-ink-50"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {pending ? (
        <div className="mb-3">
          <Spinner label={t('session.sending')} />
        </div>
      ) : null}

      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <label htmlFor="player-action" className="sr-only">
          {t('session.inputPlaceholder')}
        </label>
        <textarea
          id="player-action"
          value={action}
          rows={1}
          disabled={pending}
          placeholder={t('session.inputPlaceholder')}
          onChange={(event) => setAction(event.target.value)}
          onKeyDown={handleKeyDown}
          className="max-h-32 min-h-11 flex-1 resize-none rounded-xl border border-ink-700 bg-ink-850 px-3.5 py-2.5 text-base text-ink-50 placeholder:text-ink-400 focus:border-arcane-400 focus:outline-none disabled:opacity-60"
        />
        <Button type="submit" disabled={pending || action.trim() === ''}>
          {t('session.send')}
        </Button>
      </form>
    </div>
  );
}
