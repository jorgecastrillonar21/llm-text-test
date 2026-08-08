import { createContext } from 'react';
import type { MessageKey, UiLocale } from './messages';

export interface I18nValue {
  locale: UiLocale;
  setLocale: (locale: UiLocale) => void;
  t: (key: MessageKey, vars?: Record<string, string | number>) => string;
}

export const I18nContext = createContext<I18nValue | null>(null);
