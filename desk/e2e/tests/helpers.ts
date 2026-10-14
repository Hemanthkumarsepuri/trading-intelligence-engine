import { expect, type Page, type ConsoleMessage, type Response } from '@playwright/test';
import { fileURLToPath } from 'node:url';

export const PASSWORD = process.env.E2E_PASSWORD!;

/** Screenshots kept for the validation report (git-ignored). */
export const EVIDENCE = fileURLToPath(new URL('../evidence/', import.meta.url));

export const ROUTES = [
  { path: '/', title: 'Today' },
  { path: '/market', title: 'Market' },
  { path: '/options', title: 'Options' },
  { path: '/watchlist', title: 'Watchlist' },
  { path: '/alerts', title: 'Alerts' },
  { path: '/journal', title: 'Journal' },
  { path: '/portfolio', title: 'Portfolio' },
  { path: '/settings', title: 'Settings' },
] as const;

/** Options Workbench screens (added after the original eight). */
export const WORKBENCH_ROUTES = [
  { path: '/workbench', title: 'Options Workbench' },
  { path: '/scanner', title: 'Market scanner' },
  { path: '/explorer', title: 'Contract Explorer' },
  { path: '/events', title: 'News and Events' },
  { path: '/data-health', title: 'Data Health' },
  { path: '/more', title: 'More' },
] as const;

/** Collects console errors, page errors and failed first-party requests for one page. */
export function watch(page: Page) {
  const problems: string[] = [];
  page.on('console', (m: ConsoleMessage) => {
    if (m.type() === 'error') problems.push(`console: ${m.text().slice(0, 300)}`);
  });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message.slice(0, 300)}`));
  page.on('response', (r: Response) => {
    const u = new URL(r.url());
    if (u.hostname === 'localhost' && r.status() >= 500) problems.push(`http ${r.status()} ${u.pathname}`);
  });
  page.on('requestfailed', (r) => {
    const u = new URL(r.url());
    if (u.hostname === 'localhost') problems.push(`requestfailed ${u.pathname}: ${r.failure()?.errorText ?? ''}`);
  });
  return problems;
}

export async function signIn(page: Page, password = PASSWORD) {
  await page.goto('/');
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

export async function signedIn(page: Page) {
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Today' })).toBeVisible();
}

/** Width of content beyond the viewport (0 = no horizontal overflow). */
export async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/** Wait until no skeleton/spinner is left on the page. */
export async function settled(page: Page) {
  await expect(page.locator('.skeleton, [role="progressbar"]')).toHaveCount(0, { timeout: 15_000 });
}
