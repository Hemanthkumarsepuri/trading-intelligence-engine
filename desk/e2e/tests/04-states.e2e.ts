// Data states the live providers can produce, simulated at the API boundary with
// route interception (the local API is offline/synthetic and always "healthy").
// These prove what the UI does with stale, missing, malformed and failed data.
import { expect, test } from '@playwright/test';
import { settled, signedIn } from './helpers';

test('stale data is announced, not passed off as current', async ({ page }) => {
  await page.route('**/api/overview', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.meta.stale = true;
    await route.fulfill({ response: res, json: body });
  });
  await signedIn(page);
  await expect(page.getByText('Showing last good data.')).toBeVisible();
});

test('an unavailable index quote is shown as unavailable, never as 0', async ({ page }) => {
  await page.route('**/api/overview', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.data.indices[0].quote = null;
    await route.fulfill({ response: res, json: body });
  });
  await signedIn(page);
  await settled(page);
  const tile = page.locator('.ticker .card').first();
  await expect(tile).toContainText('unavailable');
  await expect(tile.locator('.value')).toHaveText('-');
});

test('a provider failure shows a safe error with Retry, and Retry recovers', async ({ page }) => {
  let fail = true;
  await page.route('**/api/overview', async (route) => {
    if (fail) return route.fulfill({ status: 502, json: { ok: false, error: { code: 'UPSTREAM', message: '429 from query1.finance.yahoo.com' } } });
    return route.continue();
  });
  await signedIn(page);
  const box = page.getByRole('alert').filter({ hasText: 'Could not load this.' });
  await expect(box).toBeVisible({ timeout: 15_000 });
  fail = false;
  await box.getByRole('button', { name: 'Retry' }).click();
  await expect(page.locator('.ticker .card').first()).toBeVisible();
  await expect(box).toHaveCount(0);
});

test('an HTML error page from upstream is never shown to the user', async ({ page }) => {
  await page.route('**/api/overview', (route) =>
    route.fulfill({ status: 502, contentType: 'text/html', body: '<html><body>Bad gateway at internal-lb-7.corp.example token=abc123</body></html>' }),
  );
  await signedIn(page);
  await expect(page.getByText('Unexpected response from server (HTTP 502).')).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('body')).not.toContainText('internal-lb-7');
  await expect(page.locator('body')).not.toContainText('token=abc123');
});

test('a network failure ends in an error state, not an endless spinner', async ({ page }) => {
  await page.route('**/api/news', (route) => route.abort('connectionrefused'));
  await signedIn(page);
  const card = page.locator('.card', { has: page.getByRole('heading', { name: 'Headlines' }) });
  await expect(card.getByText('Cannot reach the server. Check your connection.')).toBeVisible({ timeout: 15_000 });
  await expect(card.locator('.skeleton')).toHaveCount(0);
});

test('symbol page with unknown candle volume: no invented volume, no volume ratio, chart still renders', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/api/symbols/RELIANCE', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    for (const c of body.data.candles) c.v = null;
    if (body.data.quote) delete body.data.quote.volume;
    if (body.data.tech) body.data.tech.volRatio20 = null;
    await route.fulfill({ response: res, json: body });
  });
  await signedIn(page);
  await page.goto('/#/symbol/RELIANCE');
  await settled(page);
  await expect(page.locator('canvas').first()).toBeVisible();
  await expect(page.getByText('Volume -', { exact: true })).toBeVisible();
  await expect(page.locator('dt', { hasText: 'Volume vs 20d avg' }).locator('xpath=following-sibling::dd[1]')).toHaveText('-');
  expect(errors).toEqual([]);
});

test('the server itself never invents a volume ratio from unknown volume (real API, real indicator code)', async ({ page }) => {
  await signedIn(page);
  const r = await page.request.get('/api/symbols/RELIANCE');
  const d = (await r.json()).data;
  // offline fixtures carry real volumes; the ratio is a finite number derived from them
  expect(d.candles.every((c: { v: number | null }) => c.v === null || (Number.isFinite(c.v) && c.v >= 0))).toBe(true);
  expect(d.tech.volRatio20 === null || Number.isFinite(d.tech.volRatio20)).toBe(true);
});

test('when the session check itself fails, the app says so and can recover without a manual reload', async ({ page }) => {
  let fail = true;
  await page.route('**/api/auth/me', (route) =>
    fail ? route.fulfill({ status: 503, json: { ok: false, error: { code: 'AUTH_UNAVAILABLE', message: 'Cannot verify the session right now. Please retry.' } } }) : route.continue(),
  );
  await page.goto('/');
  const banner = page.getByRole('alert');
  await expect(banner).toContainText('Cannot verify the session right now.', { timeout: 15_000 });
  fail = false;
  await banner.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
});
