// Core user workflows end to end: UI action -> real local API -> persisted -> rendered.
import { expect, test } from '@playwright/test';
import { settled, signedIn, watch } from './helpers';

test.beforeEach(async ({ page }) => {
  page.on('dialog', (d) => void d.accept()); // confirm() before deletes
});

test('watchlist: add a symbol, see a quote, open its page, then remove it', async ({ page }) => {
  const problems = watch(page);
  await signedIn(page);
  await page.goto('/#/watchlist');
  await settled(page);
  await page.getByLabel('NSE symbol').fill('SBIN');
  await page.getByRole('button', { name: 'Add' }).click();
  const row = page.locator('tr', { has: page.getByText('SBIN', { exact: true }) });
  await expect(row).toBeVisible();
  await expect(row.locator('td.num').first()).not.toHaveText('-');

  // instrument navigation
  await page.getByText('SBIN', { exact: true }).first().click();
  await expect(page).toHaveURL(/#\/symbol\/SBIN/);
  await expect(page.locator('h1')).toContainText('SBIN');
  await expect(page.locator('canvas').first()).toBeVisible();

  await page.goto('/#/watchlist');
  await page.getByRole('button', { name: 'Remove SBIN' }).click();
  await expect(page.locator('tr', { has: page.getByText('SBIN', { exact: true }) })).toHaveCount(0);
  expect(problems).toEqual([]);
});

test('watchlist: a symbol the server cannot verify is added only with a visible warning', async ({ page }) => {
  // Offline (and in production when the NSE symbol list is unreachable) the server cannot prove a
  // symbol does not exist, so it accepts it with a note. The UI must show that note.
  await signedIn(page);
  await page.goto('/#/watchlist');
  await settled(page);
  const added = page.waitForResponse((r) => r.url().endsWith('/api/watchlist') && r.request().method() === 'POST');
  await page.getByLabel('NSE symbol').fill('NOTAREALSYMBOLX');
  await page.getByRole('button', { name: 'Add' }).click();
  const res = await added;
  expect(res.status()).toBe(201);
  expect((await res.json()).meta.notes.join(' ')).toMatch(/could not be verified/);
  await expect(page.getByText(/NOTAREALSYMBOLX added. Symbol could not be verified/)).toBeVisible();
  // clean up
  await page.getByRole('button', { name: 'Remove NOTAREALSYMBOLX' }).click();
  await expect(page.locator('tr', { has: page.getByText('NOTAREALSYMBOLX', { exact: true }) })).toHaveCount(0);
});

test('watchlist: a symbol the server rejects is not added and the reason is shown', async ({ page }) => {
  // The live path (NSE list reachable) answers 422; simulated here at the API boundary.
  await page.route('**/api/watchlist', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 422, json: { ok: false, error: { code: 'UNKNOWN_SYMBOL', message: 'ZZZZ is not a listed NSE symbol.' } } })
      : route.continue(),
  );
  await signedIn(page);
  await page.goto('/#/watchlist');
  await settled(page);
  const done = page.waitForResponse((r) => r.url().endsWith('/api/watchlist') && r.request().method() === 'POST');
  await page.getByLabel('NSE symbol').fill('ZZZZ');
  await page.getByRole('button', { name: 'Add' }).click();
  await done;
  await expect(page.getByText('ZZZZ is not a listed NSE symbol.')).toBeVisible();
  await expect(page.locator('tr', { has: page.getByText('ZZZZ', { exact: true }) })).toHaveCount(0);
});

test('alerts: create, see it listed, run a check (no Telegram configured, nothing sent), delete', async ({ page }) => {
  const problems = watch(page);
  await signedIn(page);
  await page.goto('/#/alerts');
  await expect(page.getByText('Telegram is not configured')).toBeVisible();
  await page.getByLabel('Condition').selectOption({ label: 'Price rises above' });
  await page.getByLabel('Symbol').fill('TCS');
  await page.getByLabel('Level').fill('999999');
  await page.getByLabel('Note (shown in the notification)').fill('e2e alert');
  await page.getByRole('button', { name: 'Create alert' }).click();
  const yours = page.locator('.card', { has: page.getByRole('heading', { name: 'Your alerts' }) });
  await expect(yours.getByText('TCS')).toBeVisible();

  const tick = page.waitForResponse((r) => r.url().endsWith('/api/alerts-run'));
  await page.getByRole('button', { name: 'Check now' }).click();
  const res = await tick;
  expect(res.status()).toBe(200);
  const report = (await res.json()).data;
  expect(report.notified).toBe(0); // unreachable level and no Telegram: nothing may be delivered
  expect(report.fired).toEqual([]);

  await yours.getByRole('button', { name: 'Delete alert' }).click();
  await expect(yours.getByText('No alerts yet.')).toBeVisible();
  expect(problems).toEqual([]);
});

test('journal: log a closed trade, see it with charges, then delete it', async ({ page }) => {
  const problems = watch(page);
  await signedIn(page);
  await page.goto('/#/journal');
  await page.getByRole('button', { name: 'Trades' }).click();
  await page.getByRole('button', { name: 'Log trade' }).click();
  await page.getByLabel('Symbol / contract').fill('INFY');
  await page.getByLabel('Quantity (units)').fill('10');
  await page.getByLabel('Entry price').fill('1500');
  await page.getByLabel('Exit price').fill('1550');
  // entry time defaults to "now", so pin both times (an exit price needs an exit time, and it must follow entry)
  await page.getByLabel('Entry time (IST)').fill('2026-01-05T10:00');
  await page.getByLabel('Exit time (IST)').fill('2026-01-05T14:00');
  await page.getByRole('button', { name: 'Log trade' }).last().click();
  const row = page.locator('tr', { has: page.getByText('INFY', { exact: true }) });
  await expect(row).toBeVisible();
  await expect(row).toContainText('500'); // gross 10 x 50
  await page.getByRole('button', { name: 'Overview' }).click();
  // overview statistics include the trade (net = gross 500 minus charges)
  await expect(page.getByText('1 trades · gross 500')).toBeVisible();
  // regression: per-group tables must not claim there are no closed trades
  await expect(page.getByText('No closed trades yet', { exact: false })).toHaveCount(0);

  await page.getByRole('button', { name: 'Trades' }).click();
  await row.getByRole('button', { name: 'Delete' }).click();
  await expect(page.locator('tr', { has: page.getByText('INFY', { exact: true }) })).toHaveCount(0);
  expect(problems).toEqual([]);
});

test('settings: a change persists across a reload; invalid values are refused', async ({ page }) => {
  const problems = watch(page);
  await signedIn(page);
  await page.goto('/#/settings');
  const capital = page.getByLabel('Trading capital (₹)');
  await capital.fill('750000');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(page.getByText(/saved/i).first()).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Trading capital (₹)')).toHaveValue('750000');

  await page.getByLabel('Risk per trade (%)').fill('99');
  await page.getByRole('button', { name: 'Save settings' }).click();
  await expect(page.locator('.toast, [role="alert"]').filter({ hasText: /risk|10|less|max/i }).first()).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Risk per trade (%)')).not.toHaveValue('99');
  expect(problems.filter((p) => !p.includes('400'))).toEqual([]);
});

test('settings: self-test and snapshot work locally without contacting providers', async ({ page }) => {
  const problems = watch(page);
  await signedIn(page);
  await page.goto('/#/settings');
  await page.getByRole('button', { name: 'Run self-test' }).click();
  await expect(page.getByRole('button', { name: 'Run self-test' })).toBeEnabled();
  await page.getByRole('button', { name: 'Snapshot now' }).click();
  await expect(page.getByText('Snapshot saved')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send test message' })).toBeDisabled(); // Telegram not configured
  expect(problems).toEqual([]);
});

test('portfolio: CSV preview then add, holding and portfolio value shown', async ({ page }) => {
  const problems = watch(page);
  await signedIn(page);
  await page.goto('/#/portfolio');
  await page.getByRole('button', { name: 'Import CSV' }).click();
  await page.getByPlaceholder('…or paste CSV here').fill('Instrument,Qty.,Avg. cost\nTCS,5,3000\n');
  await page.getByRole('button', { name: 'Preview' }).click();
  await page.getByRole('button', { name: 'Add to holdings' }).click();
  await expect(page.locator('tr', { has: page.getByText('TCS', { exact: true }) })).toBeVisible();
  await expect(page.getByText('Current value')).toBeVisible();
  expect(problems).toEqual([]);
});

test('options: switching underlying and expiry renders a chain', async ({ page }) => {
  const problems = watch(page);
  await signedIn(page);
  await page.goto('/#/options');
  await settled(page);
  await page.getByRole('button', { name: 'Bank Nifty' }).click();
  await settled(page);
  await expect(page.getByText('Spot', { exact: true })).toBeVisible();
  const expiry = page.locator('select').first();
  const options = await expiry.locator('option').allTextContents();
  if (options.length > 1) {
    await expiry.selectOption({ index: 1 });
    await settled(page);
  }
  await expect(page.locator('table tbody tr').first()).toBeVisible();
  expect(problems).toEqual([]);
});
