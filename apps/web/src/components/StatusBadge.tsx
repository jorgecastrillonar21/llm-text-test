import { useI18n } from '@/i18n/useI18n';
import type { MessageKey } from '@/i18n/messages';
import type { ProviderState } from '@/api/types';

const PRESENTATION: Record<ProviderState, { key: MessageKey; className: string }> = {
  ready: { key: 'status.ready', className: 'bg-emerald-500/15 text-emerald-300' },
  unreachable: { key: 'status.unreachable', className: 'bg-red-500/15 text-red-300' },
  misconfigured: { key: 'status.misconfigured', className: 'bg-amber-500/15 text-amber-300' },
  disabled: { key: 'status.disabled', className: 'bg-ink-700 text-ink-200' },
};

export function StatusBadge({ state }: { state: ProviderState }) {
  const { t } = useI18n();
  const { key, className } = PRESENTATION[state];
  return (
    <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${className}`}>{t(key)}</span>
  );
}
