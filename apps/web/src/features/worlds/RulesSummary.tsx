import { useWorldRules } from '@/api/hooks';
import { Card } from '@/components/ui';
import { useI18n } from '@/i18n/useI18n';
import { summarizeRules } from './summarizeRules';

/**
 * What kind of world this is, at a glance.
 *
 * Read-only: rules are fixed at creation, and editing them mid-story is a
 * deferred design question (see docs/world-rules.md).
 */
export function RulesSummary({ worldId }: { worldId: string }) {
  const { t } = useI18n();
  const rules = useWorldRules(worldId);

  // Context, not gameplay. If the document cannot be read the rest of the
  // screen still works, so this stays quiet instead of raising an error.
  if (!rules.data) return null;

  return (
    <Card>
      <h2 className="text-sm font-semibold text-ink-200">{t('rules.heading')}</h2>
      <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {summarizeRules(rules.data).map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3">
            <dt className="text-ink-400">{t(row.label)}</dt>
            <dd className="text-ink-100">{t(row.value)}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
