import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { MESSAGES, UI_LOCALES, type MessageKey, type UiLocale } from './messages';
import { I18nContext, type I18nValue } from './context';

const STORAGE_KEY = 'ui-locale';

function isUiLocale(value: string | null): value is UiLocale {
  return value !== null && (UI_LOCALES as readonly string[]).includes(value);
}

function detectLocale(): UiLocale {
  if (typeof window === 'undefined') return 'en';
  const stored = window.localStorage.getItem(STORAGE_KEY);
  if (isUiLocale(stored)) return stored;
  return window.navigator.language.toLowerCase().startsWith('es') ? 'es' : 'en';
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<UiLocale>(detectLocale);

  const setLocale = useCallback((next: UiLocale) => {
    setLocaleState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
    document.documentElement.lang = next;
  }, []);

  const t = useCallback(
    (key: MessageKey, vars?: Record<string, string | number>) => {
      const template = MESSAGES[locale][key];
      if (!vars) return template;
      return Object.entries(vars).reduce(
        (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
        template,
      );
    },
    [locale],
  );

  const value = useMemo<I18nValue>(() => ({ locale, setLocale, t }), [locale, setLocale, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
