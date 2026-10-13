// Browser tests for TIRE Desk against a fully local, offline stack:
//   - API: server/dev.ts with DESK_OFFLINE=1 (synthetic market data, no providers),
//     file storage in a fresh temporary directory, no Telegram, no scheduler.
//     Outbound HTTPS is forced through a dead proxy, so a provider call that
//     slipped past offline mode fails instead of reaching the internet.
//   - Web: the production build (dist/, `npm run build:web` first) served by
//     `vite preview`, which proxies /api to the local API like Netlify does.
// The password and session secret are generated per run and never printed.

import { defineConfig, devices } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { mkdtempSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const API_PORT = 8799;
const WEB_PORT = 4179;

process.env.E2E_PASSWORD ??= `e2e-${randomBytes(12).toString('hex')}`;
process.env.E2E_DATA_DIR ??= mkdtempSync(path.join(os.tmpdir(), 'desk-e2e-'));
const sessionSecret = randomBytes(32).toString('hex');

const apiEnv: Record<string, string> = {
  PORT: String(API_PORT),
  DESK_OFFLINE: '1',
  DESK_STORAGE: 'file',
  DESK_DATA_DIR: process.env.E2E_DATA_DIR,
  APP_PASSWORD: process.env.E2E_PASSWORD,
  SESSION_SECRET: sessionSecret,
  DESK_SCHEDULER: 'off',
  NODE_USE_ENV_PROXY: '1',
  HTTPS_PROXY: 'http://127.0.0.1:9',
  HTTP_PROXY: 'http://127.0.0.1:9',
  NO_PROXY: 'localhost,127.0.0.1',
};

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.e2e.ts',
  // One worker, files in name order: the login-lockout file (zz-) locks the
  // shared local IP and therefore runs last.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 45_000,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    serviceWorkers: 'block',
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: [
    {
      command: 'npx tsx server/dev.ts',
      cwd: '..',
      url: `http://localhost:${API_PORT}/api/health`,
      env: apiEnv,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx vite preview --port ${WEB_PORT} --strictPort`,
      cwd: '..',
      url: `http://localhost:${WEB_PORT}`,
      env: { DESK_DEV_API_ORIGIN: `http://localhost:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
