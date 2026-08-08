import { useEffect, useRef } from 'react';
import { useI18n } from '@/i18n/useI18n';
import { EmptyState } from '@/components/ui';
import type { Character, Message } from '@/api/types';

interface TimelineProps {
  messages: Message[];
  characters: Character[];
  playerName: string;
  /** Bumped when a turn completes, to trigger the scroll-to-bottom. */
  scrollToken: number;
}

export function Timeline({ messages, characters, playerName, scrollToken }: TimelineProps) {
  const { t } = useI18n();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Only auto-scroll on new turns, so reading back through history is not fought.
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [scrollToken]);

  if (messages.length === 0) {
    return <EmptyState>{t('session.empty')}</EmptyState>;
  }

  const nameFor = (id: string | null) =>
    characters.find((character) => character.id === id)?.name ?? t('session.narrator');

  return (
    <ol className="flex flex-col gap-4">
      {messages.map((message) => {
        if (message.role === 'player') {
          return (
            <li key={message.id} className="flex justify-end" data-role="player">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-ink-700 px-3.5 py-2.5">
                <p className="text-xs font-medium text-ink-200">{playerName || t('session.you')}</p>
                <p className="prose-narration text-sm text-ink-50">{message.content}</p>
              </div>
            </li>
          );
        }

        if (message.role === 'character') {
          return (
            <li key={message.id} data-role="character">
              <p className="mb-1 text-sm font-semibold text-ember-400">
                {nameFor(message.speaker_character_id)}
              </p>
              <p className="prose-narration border-l-2 border-ink-700 pl-3 text-sm text-ink-50">
                {message.content}
              </p>
            </li>
          );
        }

        return (
          <li key={message.id} data-role={message.role}>
            <p className="prose-narration text-sm text-ink-200 italic">{message.content}</p>
          </li>
        );
      })}
      <div ref={endRef} />
    </ol>
  );
}
