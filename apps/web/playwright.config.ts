import { defineConfig, devices } from '@playwright/test';

/**
 * Smoke coverage against the real backend running the mock story provider.
 * No AI runtime is required, so this is safe to run in CI.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'line' : 'list',
  timeout: 30_000,
  use: {
    // `localhost`, not 127.0.0.1: Vite binds the hostname, which resolves to ::1
    // on Windows, and a literal IPv4 URL would never connect.
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // Assumes the API is already running on :8000 — see docs/development.md.
    command: 'pnpm dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
