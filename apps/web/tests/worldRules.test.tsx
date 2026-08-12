import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { levelKey, summarizeRules } from '@/features/worlds/summarizeRules';
import { RulesSummary } from '@/features/worlds/RulesSummary';
import { CreateWorldForm } from '@/features/worlds/CreateWorldForm';
import type { WorldRules } from '@/api/types';
import { mockFetch, renderWithProviders } from './utils';

/** Shonen-shaped: constant trouble, rarely fatal. */
const RULES: WorldRules = {
  version: 1,
  narrative: { plot_armor: { player: 10 } },
  danger: { baseline: 75, lethality: 30 },
  consequences: { severity: 65 },
  progression: { enabled: true, pace: 'anime_fast' },
  simulation: { time_progression: 'action_based' },
  rules: { enforcement: 'strict' },
};

function withRules(overrides: Partial<WorldRules>): WorldRules {
  return { ...RULES, ...overrides };
}

describe('summarizeRules', () => {
  it.each([
    [0, 'rules.level.veryLow'],
    [19, 'rules.level.veryLow'],
    [20, 'rules.level.low'],
    [39, 'rules.level.low'],
    [50, 'rules.level.moderate'],
    [75, 'rules.level.high'],
    [79, 'rules.level.high'],
    [80, 'rules.level.veryHigh'],
    [100, 'rules.level.veryHigh'],
  ])('reads %i as %s', (setting, expected) => {
    expect(levelKey(setting)).toBe(expected);
  });

  it('reports danger and lethality as separate values', () => {
    const rows = summarizeRules(RULES);
    expect(rows).toContainEqual({ label: 'rules.field.danger', value: 'rules.level.high' });
    expect(rows).toContainEqual({ label: 'rules.field.lethality', value: 'rules.level.low' });
  });

  it('shows the pace only while progression is on', () => {
    const on = summarizeRules(RULES);
    expect(on).toContainEqual({
      label: 'rules.field.progression',
      value: 'rules.pace.animeFast',
    });

    const off = summarizeRules(
      withRules({ progression: { enabled: false, pace: 'anime_fast' } }),
    );
    expect(off).toContainEqual({ label: 'rules.field.progression', value: 'rules.pace.off' });
  });

  it('covers every row exactly once', () => {
    const labels = summarizeRules(RULES).map((row) => row.label);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).toHaveLength(7);
  });
});

describe('RulesSummary', () => {
  beforeEach(() => vi.unstubAllGlobals());

  it('renders the summary for a world', async () => {
    vi.stubGlobal('fetch', mockFetch({ '/api/v1/worlds/w1/rules': RULES }));
    renderWithProviders(<RulesSummary worldId="w1" />);

    expect(await screen.findByText('World rules')).toBeInTheDocument();
    expect(screen.getByText('Danger').nextElementSibling).toHaveTextContent('High');
    expect(screen.getByText('Lethality').nextElementSibling).toHaveTextContent('Low');
    expect(screen.getByText('Plot armor').nextElementSibling).toHaveTextContent('Very low');
    expect(screen.getByText('World clock').nextElementSibling).toHaveTextContent('Moves with you');
  });

  it('stays out of the way when the rules cannot be read', async () => {
    const fetchStub = mockFetch({});
    vi.stubGlobal('fetch', fetchStub);
    renderWithProviders(<RulesSummary worldId="w1" />);

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    expect(screen.queryByText('World rules')).not.toBeInTheDocument();
  });
});

describe('CreateWorldForm', () => {
  beforeEach(() => vi.unstubAllGlobals());

  it('creates the world with the chosen preset', async () => {
    const user = userEvent.setup();
    const bodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return new Response(JSON.stringify({ id: 'w1' }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    renderWithProviders(<CreateWorldForm onDone={() => {}} />);

    await user.type(screen.getByLabelText('World name'), 'Ashfall');
    await user.selectOptions(screen.getByLabelText('World style'), 'dark_fantasy');
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ name: 'Ashfall', rules_preset: 'dark_fantasy' });
  });

  it('defaults to balanced rather than sending nothing', async () => {
    const user = userEvent.setup();
    const bodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return new Response(JSON.stringify({ id: 'w1' }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    renderWithProviders(<CreateWorldForm onDone={() => {}} />);

    await user.type(screen.getByLabelText('World name'), 'Ashfall');
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ rules_preset: 'balanced' });
  });
});
