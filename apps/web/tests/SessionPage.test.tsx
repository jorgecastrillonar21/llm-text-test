import { describe, expect, it, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { SessionPage } from '@/pages/SessionPage';
import { renderWithProviders } from './utils';

const SESSION = {
  id: 's1',
  world_id: 'w1',
  title: 'Run 1',
  player_name: 'Rin',
  player_description: '',
  current_location: 'the market',
  summary: '',
  turn_index: 0,
  created_at: '',
  updated_at: '',
  world: {
    id: 'w1',
    name: 'The Fractured Crown',
    description: '',
    genre: 'fantasy',
    setting: '',
    language: 'en',
    created_at: '',
    updated_at: '',
  },
};

const TURN_RESPONSE = {
  session_id: 's1',
  turn_index: 1,
  messages: [],
  suggested_actions: ['Ask Elena what she wants', 'Look around', 'Wait'],
  relationships: [],
  memories_created: 0,
  events_created: 0,
  visual_cue_generated: false,
};

function renderSession() {
  return renderWithProviders(
    <Routes>
      <Route path="/sessions/:sessionId" element={<SessionPage />} />
    </Routes>,
    { route: '/sessions/s1' },
  );
}

/** Route table for the happy path; individual tests override `/turns`. */
function baseRoutes(messages: unknown[] = []) {
  return {
    '/api/v1/sessions/s1/messages': messages,
    '/api/v1/worlds/w1/characters': [],
    '/api/v1/ai/status': {
      story: { provider: 'mock', state: 'ready', detail: '', model: null, extra: {} },
      image: { provider: 'disabled', state: 'disabled', detail: '', model: null, extra: {} },
    },
    '/api/v1/sessions/s1': SESSION,
  };
}

function installFetch(handler: (url: string) => Response | Promise<Response>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => handler(input.toString())),
  );
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function routeTo(routes: Record<string, unknown>, url: string, status = 200) {
  // Longest match wins so `/messages` is not shadowed by `/sessions/s1`.
  const key = Object.keys(routes)
    .filter((candidate) => url.includes(candidate))
    .sort((a, b) => b.length - a.length)[0];
  if (key === undefined) return json({ error: 'not_found', detail: url }, 404);
  return json(routes[key], status);
}

describe('SessionPage', () => {
  beforeEach(() => vi.unstubAllGlobals());

  it('renders the session and its transcript', async () => {
    const messages = [
      {
        id: 'm1',
        session_id: 's1',
        turn_index: 1,
        role: 'narrator',
        speaker_character_id: null,
        content: 'The market is loud.',
        created_at: '',
      },
    ];
    installFetch((url) => routeTo(baseRoutes(messages), url));
    renderSession();

    expect(await screen.findByText('Run 1')).toBeInTheDocument();
    expect(await screen.findByText('The market is loud.')).toBeInTheDocument();
  });

  it('sends an action and shows the returned suggestions', async () => {
    const user = userEvent.setup();
    installFetch((url) =>
      url.includes('/turns') ? json(TURN_RESPONSE) : routeTo(baseRoutes(), url),
    );
    renderSession();

    await screen.findByText('Run 1');
    await user.type(screen.getByPlaceholderText(/type anything/i), 'I greet Elena');
    await user.click(screen.getByRole('button', { name: /send/i }));

    expect(
      await screen.findByRole('button', { name: 'Ask Elena what she wants' }),
    ).toBeInTheDocument();
  });

  it('submits only one turn when send is clicked repeatedly', async () => {
    const user = userEvent.setup();
    let turnCalls = 0;
    installFetch((url) => {
      if (url.includes('/turns')) {
        turnCalls += 1;
        return new Promise((resolve) => setTimeout(() => resolve(json(TURN_RESPONSE)), 50));
      }
      return routeTo(baseRoutes(), url);
    });
    renderSession();

    await screen.findByText('Run 1');
    await user.type(screen.getByPlaceholderText(/type anything/i), 'I greet Elena');
    const send = screen.getByRole('button', { name: /send/i });
    await user.click(send);
    await user.click(send);
    await user.click(send);

    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
    expect(turnCalls).toBe(1);
  });

  it('surfaces a provider failure with a retry affordance', async () => {
    const user = userEvent.setup();
    installFetch((url) =>
      url.includes('/turns')
        ? json(
            {
              error: 'story_generation_failed',
              detail: 'Cannot reach Ollama at http://127.0.0.1:11434.',
              provider: 'ollama',
              retryable: true,
            },
            502,
          )
        : routeTo(baseRoutes(), url),
    );
    renderSession();

    await screen.findByText('Run 1');
    await user.type(screen.getByPlaceholderText(/type anything/i), 'I greet Elena');
    await user.click(screen.getByRole('button', { name: /send/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/could not be generated/i);
    expect(alert).toHaveTextContent(/Cannot reach Ollama/);
    // The turn was a server-side no-op, so the user is told nothing was saved.
    expect(alert).toHaveTextContent(/nothing was saved/i);
    expect(screen.getByRole('button', { name: /retry/i })).toBeEnabled();
  });

  it('reports an unreachable backend distinctly from a rejected request', async () => {
    const user = userEvent.setup();
    installFetch((url) => {
      if (url.includes('/turns')) return Promise.reject(new TypeError('Failed to fetch'));
      return routeTo(baseRoutes(), url);
    });
    renderSession();

    await screen.findByText('Run 1');
    await user.type(screen.getByPlaceholderText(/type anything/i), 'I greet Elena');
    await user.click(screen.getByRole('button', { name: /send/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/cannot reach the api/i);
  });
});
