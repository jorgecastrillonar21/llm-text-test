import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { Timeline } from '@/features/sessions/Timeline';
import { renderWithProviders } from './utils';
import type { Character, Message } from '@/api/types';

const ELENA: Character = {
  id: 'c1',
  world_id: 'w1',
  name: 'Elena',
  description: '',
  appearance: '',
  personality: '',
  backstory: '',
  speech_style: '',
  goals: [],
  secrets: [],
  created_at: '',
  updated_at: '',
};

function message(overrides: Partial<Message> & Pick<Message, 'id' | 'role' | 'content'>): Message {
  return {
    session_id: 's1',
    turn_index: 1,
    speaker_character_id: null,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('Timeline', () => {
  it('shows an empty state before the story starts', () => {
    renderWithProviders(
      <Timeline messages={[]} characters={[]} playerName="Rin" scrollToken={0} />,
    );
    expect(screen.getByText(/has not started/i)).toBeInTheDocument();
  });

  it('renders narration, dialogue and the player action distinctly', () => {
    const messages = [
      message({ id: 'm1', role: 'player', content: 'I greet her.' }),
      message({ id: 'm2', role: 'narrator', content: 'The market is loud.' }),
      message({
        id: 'm3',
        role: 'character',
        content: 'You are late.',
        speaker_character_id: 'c1',
      }),
    ];
    const { container } = renderWithProviders(
      <Timeline messages={messages} characters={[ELENA]} playerName="Rin" scrollToken={1} />,
    );

    expect(screen.getByText('I greet her.')).toBeInTheDocument();
    expect(screen.getByText('The market is loud.')).toBeInTheDocument();
    expect(screen.getByText('You are late.')).toBeInTheDocument();

    expect(container.querySelector('[data-role="player"]')).not.toBeNull();
    expect(container.querySelector('[data-role="narrator"]')).not.toBeNull();
    expect(container.querySelector('[data-role="character"]')).not.toBeNull();
  });

  it('attributes dialogue to the speaking character', () => {
    const messages = [
      message({
        id: 'm1',
        role: 'character',
        content: 'You are late.',
        speaker_character_id: 'c1',
      }),
    ];
    renderWithProviders(
      <Timeline messages={messages} characters={[ELENA]} playerName="Rin" scrollToken={1} />,
    );
    expect(screen.getByText('Elena')).toBeInTheDocument();
  });

  it('falls back to the narrator label for an unknown speaker', () => {
    const messages = [
      message({ id: 'm1', role: 'character', content: '…', speaker_character_id: 'gone' }),
    ];
    renderWithProviders(
      <Timeline messages={messages} characters={[]} playerName="Rin" scrollToken={1} />,
    );
    expect(screen.getByText('Narrator')).toBeInTheDocument();
  });
});
