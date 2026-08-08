import { Link, Outlet, useLocation } from 'react-router';
import { useI18n } from '@/i18n/useI18n';
import { useAiStatus } from '@/api/hooks';
import { OfflineError } from '@/api/client';

export function AppLayout() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const { error } = useAiStatus();
  const offline = error instanceof OfflineError;

  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-3xl flex-col">
      <header className="sticky top-0 z-10 border-b border-ink-800 bg-ink-900/95 backdrop-blur">
        <div className="flex items-center justify-between px-4 py-3">
          <Link to="/" className="text-sm font-semibold tracking-tight">
            {t('app.title')}
          </Link>
          <nav className="flex items-center gap-4 text-sm">
            <Link
              to="/"
              className={pathname === '/' ? 'text-ink-50' : 'text-ink-400 hover:text-ink-200'}
            >
              {t('nav.home')}
            </Link>
            <Link
              to="/settings"
              className={
                pathname === '/settings' ? 'text-ink-50' : 'text-ink-400 hover:text-ink-200'
              }
            >
              {t('nav.settings')}
            </Link>
          </nav>
        </div>
        {offline ? (
          <p role="alert" className="bg-red-500/15 px-4 py-2 text-xs text-red-300">
            {t('status.offline')} — {t('status.offlineHelp')}
          </p>
        ) : null}
      </header>

      <main className="flex flex-1 flex-col px-4 py-5">
        <Outlet />
      </main>
    </div>
  );
}
