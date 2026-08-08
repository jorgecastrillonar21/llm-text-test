import { QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router';
import { I18nProvider } from '@/i18n/I18nProvider';
import { AppRoutes } from '@/routes/AppRoutes';
import { queryClient } from '@/api/queryClient';

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}
