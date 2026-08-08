import { describe, expect, it, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useI18n } from '@/i18n/useI18n';
import { MESSAGES, UI_LOCALES, type MessageKey } from '@/i18n/messages';
import { renderWithProviders } from './utils';

function Probe() {
  const { t, locale, setLocale } = useI18n();
  return (
    <div>
      <p data-testid="locale">{locale}</p>
      <p data-testid="heading">{t('home.heading')}</p>
      <p data-testid="turn">{t('session.turn', { n: 7 })}</p>
      <button onClick={() => setLocale('es')}>switch</button>
    </div>
  );
}

describe('i18n', () => {
  beforeEach(() => window.localStorage.clear());

  it('every key is translated in every locale', () => {
    const keys = Object.keys(MESSAGES.en) as MessageKey[];
    for (const locale of UI_LOCALES) {
      for (const key of keys) {
        expect(MESSAGES[locale][key], `${locale} is missing ${key}`).toBeTruthy();
      }
      expect(Object.keys(MESSAGES[locale])).toHaveLength(keys.length);
    }
  });

  it('interpolates variables', () => {
    renderWithProviders(<Probe />);
    expect(screen.getByTestId('turn')).toHaveTextContent('Turn 7');
  });

  it('switches locale and persists the choice', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Probe />);

    expect(screen.getByTestId('heading')).toHaveTextContent('Your worlds');
    await user.click(screen.getByRole('button', { name: 'switch' }));

    expect(screen.getByTestId('heading')).toHaveTextContent('Tus mundos');
    expect(window.localStorage.getItem('ui-locale')).toBe('es');
  });

  it('restores a stored locale on mount', () => {
    window.localStorage.setItem('ui-locale', 'es');
    renderWithProviders(<Probe />);
    expect(screen.getByTestId('locale')).toHaveTextContent('es');
  });
});
