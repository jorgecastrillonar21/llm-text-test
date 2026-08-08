import { useI18n } from '@/i18n/useI18n';
import { useAiStatus } from '@/api/hooks';
import { Card, Field, Spinner } from '@/components/ui';
import { StatusBadge } from '@/components/StatusBadge';
import { LOCALE_LABELS, UI_LOCALES, type UiLocale } from '@/i18n/messages';
import type { ProviderStatus } from '@/api/types';

function ProviderCard({ heading, status }: { heading: string; status: ProviderStatus }) {
  const { t } = useI18n();
  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-ink-200">{heading}</h3>
          <p className="font-medium">{status.provider}</p>
        </div>
        <StatusBadge state={status.state} />
      </div>
      {status.model ? (
        <p className="text-sm text-ink-400">
          {t('settings.model')}: <span className="text-ink-200">{status.model}</span>
        </p>
      ) : null}
      <p className="text-sm text-ink-400">{status.detail}</p>
      {Object.entries(status.extra).map(([key, value]) => (
        <p key={key} className="text-xs text-ink-400">
          {key}: <span className="text-ink-200">{value}</span>
        </p>
      ))}
    </Card>
  );
}

export function SettingsPage() {
  const { t, locale, setLocale } = useI18n();
  const aiStatus = useAiStatus();

  return (
    <div className="flex flex-col gap-8">
      <h1 className="text-lg font-semibold">{t('settings.heading')}</h1>

      <section className="flex flex-col gap-3">
        <Field label={t('settings.uiLanguage')} hint={t('settings.uiLanguageHelp')}>
          {(id) => (
            <select
              id={id}
              value={locale}
              onChange={(event) => setLocale(event.target.value as UiLocale)}
              className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-base text-ink-50 focus:border-arcane-400 focus:outline-none"
            >
              {UI_LOCALES.map((option) => (
                <option key={option} value={option}>
                  {LOCALE_LABELS[option]}
                </option>
              ))}
            </select>
          )}
        </Field>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">{t('settings.aiStatus')}</h2>
        <p className="text-sm text-ink-400">{t('settings.configuredVia')}</p>

        {aiStatus.isPending ? <Spinner label={t('common.loading')} /> : null}
        {aiStatus.isError ? (
          <p role="alert" className="text-sm text-red-300">
            {t('status.offlineHelp')}
          </p>
        ) : null}

        {aiStatus.data ? (
          <>
            <ProviderCard heading={t('settings.storyProvider')} status={aiStatus.data.story} />
            <ProviderCard heading={t('settings.imageProvider')} status={aiStatus.data.image} />
          </>
        ) : null}
      </section>
    </div>
  );
}
