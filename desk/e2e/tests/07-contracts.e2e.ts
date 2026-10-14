// Exact contracts, custom groups, comparison, saved settings and alert rules, end to end (offline demo stack).
import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { EVIDENCE, horizontalOverflow, settled, signIn, watch } from './helpers';

const OPEN = '2026-10-14T05:00:00.000Z';

async function enter(page: Page) {
  await page.context().setExtraHTTPHeaders({ 'x-desk-demo-now': OPEN });
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Today' })).toBeVisible();
}

const dialog = (page: Page) => page.getByRole('dialog', { name: 'Add exact contract' });
const quick = (page: Page) => dialog(page).getByLabel(/Quick entry/);

async function openAdd(page: Page) {
  await page.goto('/#/workbench?u=RELIANCE');
  await page.getByTestId('add-contract-button').click();
  await expect(dialog(page)).toBeVisible();
}

test('quick entry shows what was parsed, then adds the exact contract to a NEW named group', async ({ page }) => {
  const problems = watch(page);
  await enter(page);
  await openAdd(page);
  await quick(page).fill('RELIANCE 2200 CE OCT');
  const parsed = dialog(page).getByTestId('parsed-fields');
  await expect(parsed).toContainText('RELIANCE');
  await expect(parsed).toContainText('2200');
  await expect(parsed).toContainText('CE');
  await expect(parsed).toContainText('2026-10-27');
  const resolved = dialog(page).getByTestId('resolved');
  await expect(resolved).toContainText('RELIANCE 27 Oct 2026 2200 CE');
  await expect(resolved).toContainText('Exchange symbol');
  await expect(resolved).toContainText('Lot size');
  await expect(resolved).toContainText(/year was not typed/);
  await resolved.getByLabel('Add to').selectOption('__new');
  await resolved.getByLabel('New group name').fill('Reliance — October Calls');
  await resolved.getByLabel('Note (optional)').fill('first strike');
  await resolved.getByRole('button', { name: 'Add to watchlist' }).click();
  await expect(dialog(page)).toHaveCount(0);
  await expect(page.getByTestId('underlying-header')).toContainText('RELIANCE');
  await expect(page).toHaveURL(/c=RELIANCE%7C2026-10-27%7CCE%7C2200/);

  // second strike, same expiry, into the existing group
  await page.getByTestId('add-contract-button').click();
  await quick(page).fill('RELIANCE 2250 CE OCT');
  const r2 = dialog(page).getByTestId('resolved');
  await expect(r2).toContainText('RELIANCE 27 Oct 2026 2250 CE');
  await r2.getByLabel('Add to').selectOption({ label: 'Reliance — October Calls' });
  await r2.getByRole('button', { name: 'Add to watchlist' }).click();
  await expect(dialog(page)).toHaveCount(0);

  await page.goto('/#/watchlist');
  const card = page.getByTestId('contract-group').filter({ hasText: 'Reliance — October Calls' });
  await expect(card.getByTestId('group-items').locator('tbody tr')).toHaveCount(2);
  await page.screenshot({ path: path.join(EVIDENCE, 'groups.png'), fullPage: true });
  expect(problems.filter((p) => !/workbench\/resolve.*ERR_ABORTED/.test(p))).toEqual([]);
});

test('step by step: pick expiry, call or put and strike when the text does not say; nothing is guessed', async ({ page }) => {
  await enter(page);
  await openAdd(page);
  await quick(page).fill('RELIANCE');
  const need = dialog(page).getByTestId('needs-selection');
  await expect(need).toContainText('Choose an expiry. None is picked for you.');
  await expect(dialog(page).getByTestId('parsed-expiry')).toContainText('choose below');
  await need.getByRole('button', { name: '2026-11-24' }).click();
  await expect(dialog(page).getByTestId('needs-selection')).toContainText('Choose call or put');
  await dialog(page).getByRole('button', { name: 'Put (PE)' }).click();
  await expect(dialog(page).getByTestId('needs-selection')).toContainText('Choose a listed strike');
  await dialog(page).getByLabel('Select strike').selectOption('2200');
  const resolved = dialog(page).getByTestId('resolved');
  await expect(resolved).toContainText('RELIANCE 24 Nov 2026 2200 PE');
  await expect(resolved.getByRole('button', { name: 'Add to watchlist' })).toBeEnabled();
  await dialog(page).getByRole('button', { name: 'Start over' }).click();
  await expect(dialog(page).getByTestId('resolved')).toHaveCount(0);
});

test('nonexistent, expired, unlisted and non-F&O requests are rejected with a reason and cannot be added', async ({ page }) => {
  await enter(page);
  await openAdd(page);
  const cases: [string, RegExp][] = [
    ['RELIANCE 2201 CE OCT', /Strike 2201 is not listed.*Nearest listed strikes/],
    ['RELIANCE 2200 CE SEP', /No listed expiry matches/],
    ['ZZZZQ 100 CE OCT', /not a known NSE symbol/],
    ['RELIANCE 2200 CE 2026-10-01', /already expired/],
  ];
  for (const [text, reason] of cases) {
    await quick(page).fill(text);
    await expect(dialog(page).getByTestId('rejected')).toContainText(reason);
    await expect(dialog(page).getByRole('button', { name: 'Add to watchlist' })).toHaveCount(0);
  }
});

test('group management: duplicate detection, rename, notes, reorder, saved chart settings that reopen with the contract, compare, remove', async ({ page }) => {
  await enter(page);
  // two groups with two contracts each, through the API the UI uses
  const api = async (method: string, url: string, data?: unknown) => (await page.request.fetch(url, { method, data, headers: { origin: new URL(page.url()).origin, 'x-desk-demo-now': OPEN } })).json();
  await page.goto('/#/watchlist');
  const g1 = (await api('POST', '/api/workbench/groups', { name: 'Alpha', note: '' })).data;
  const g2 = (await api('POST', '/api/workbench/groups', { name: 'Beta', note: '' })).data;
  const exp = (await api('GET', '/api/workbench/underlying/RELIANCE/expiries')).data.default.expiry as string;
  const id = (k: number) => `RELIANCE|${exp}|CE|${k}`;
  await api('POST', `/api/workbench/groups/${g1.id}/items`, { id: id(2200) });
  await api('POST', `/api/workbench/groups/${g1.id}/items`, { id: id(2250) });
  await page.reload();
  const alpha = page.getByTestId('contract-group').filter({ hasText: 'Alpha' });
  await expect(alpha.getByTestId('group-items').locator('tbody tr')).toHaveCount(2);

  // duplicate group name is refused with a message
  await page.getByLabel('New group').fill('  alpha ');
  await page.getByRole('button', { name: 'Create' }).click();
  await expect(page.getByText(/already exists/)).toBeVisible();

  // rename
  await alpha.getByRole('button', { name: 'Rename' }).click();
  await alpha.getByLabel('Group name').fill('Angel One — October Calls');
  await alpha.getByRole('button', { name: 'Save' }).click();
  const renamed = page.getByTestId('contract-group').filter({ hasText: 'Angel One — October Calls' });
  await expect(renamed).toBeVisible();

  // contract note persists across reload
  await renamed.getByLabel(/^Note for RELIANCE .* 2200 CE/).fill('primary strike');
  await renamed.getByLabel(/^Note for RELIANCE .* 2250 CE/).click();
  await page.reload();
  await expect(page.getByTestId('contract-group').filter({ hasText: 'Angel One — October Calls' }).getByLabel(/^Note for RELIANCE .* 2200 CE/)).toHaveValue('primary strike');

  // reorder contracts and groups
  const g = () => page.getByTestId('contract-group').filter({ hasText: 'Angel One — October Calls' });
  await g().getByRole('button', { name: /Move RELIANCE .* 2250 CE up/ }).click();
  await expect(g().locator('tbody tr').first()).toContainText('2250 CE');
  const order = async () => (await page.getByTestId('contract-group').locator('h2').allTextContents()).map((t) => t.trim());
  const before = await order();
  await page.getByRole('button', { name: /Move group Beta up/ }).click();
  await expect.poll(async () => (await order()).indexOf('Beta')).toBe(before.indexOf('Beta') - 1);
  await page.reload();
  await expect(page.getByTestId('contract-group').first()).toBeVisible();
  expect((await order()).indexOf('Beta')).toBe(before.indexOf('Beta') - 1);

  // saved chart settings: save for the group, clear the browser's own preferences, reopen from the group
  await g().getByText('Notes and saved chart settings').click();
  await g().getByLabel('RSI', { exact: true }).check();
  await g().getByLabel('Default timeframe').selectOption('5m');
  await g().getByRole('button', { name: 'Save chart settings' }).click();
  await expect(page.getByText('Chart settings saved')).toBeVisible();
  await page.evaluate(() => window.localStorage.removeItem('desk.wb.prefs'));
  await g().getByRole('link', { name: /2200 CE/ }).first().click();
  await expect(page).toHaveURL(/tf=5m/);
  await expect(page.getByTestId('pane-rsi')).toBeVisible();
  await expect(page.getByRole('group', { name: 'Timeframe' }).getByRole('button', { name: '5m', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await page.getByTestId('save-prefs').getByRole('button', { name: 'Save for this contract' }).click();
  await expect(page.getByText('Saved for this contract')).toBeVisible();

  // compare the two strikes side by side
  await page.goto('/#/watchlist');
  await g().getByLabel(/^Compare RELIANCE .* 2200 CE/).check();
  await g().getByLabel(/^Compare RELIANCE .* 2250 CE/).check();
  await g().getByRole('button', { name: /Compare selected \(2\)/ }).click();
  const cmp = page.getByTestId('compare-table');
  await expect(cmp).toBeVisible();
  await expect(cmp.locator('thead th')).toHaveCount(3);
  for (const row of ['Exact expiry', 'Underlying price', 'Underlying price time', 'Option premium (LTP)', 'Premium time', 'Bid / Ask', 'Spread', 'Traded volume (contracts)', 'Open interest (contracts)', 'OI change vs previous day', 'Implied volatility', 'Delta', 'Lot size', 'One-lot premium outlay', 'Source / latency', 'Data quality']) {
    await expect(cmp).toContainText(row);
  }
  await expect(cmp).toContainText(/snapshot|delayed/i);
  await expect(page.getByText(/Premium history: option candles need an entitled provider/)).toBeVisible();
  await page.screenshot({ path: path.join(EVIDENCE, 'compare.png'), fullPage: true });

  // remove one contract, then the group
  await page.goto('/#/watchlist');
  page.on('dialog', (d) => void d.accept());
  await g().getByRole('button', { name: /^Remove RELIANCE .* 2250 CE from/ }).click();
  await expect(g().locator('tbody tr')).toHaveCount(1);
  await g().getByRole('button', { name: 'Delete' }).click();
  await expect(page.getByTestId('contract-group').filter({ hasText: 'Angel One — October Calls' })).toHaveCount(0);
  await expect(page.getByTestId('contract-group').filter({ hasText: 'Beta' })).toBeVisible();
  void g2;
});

test('adding the same contract twice is reported, not duplicated', async ({ page }) => {
  await enter(page);
  await openAdd(page);
  await quick(page).fill('RELIANCE 2300 CE OCT');
  await dialog(page).getByTestId('resolved').getByRole('button', { name: 'Add to watchlist' }).click();
  await expect(dialog(page)).toHaveCount(0);
  await page.getByTestId('add-contract-button').click();
  await quick(page).fill('RELIANCE 2300 CE OCT');
  await dialog(page).getByTestId('resolved').getByRole('button', { name: 'Add to watchlist' }).click();
  await expect(page.getByText('Already in that list. Nothing changed.')).toBeVisible();
  await page.goto('/#/watchlist');
  await expect(page.getByTestId('tracked-contracts').locator('tbody tr', { hasText: '2300 CE' })).toHaveCount(1);
});

test('alert rules: create, validate, disable, edit and delete for a contract', async ({ page }) => {
  await enter(page);
  await page.goto('/#/workbench?u=RELIANCE');
  await page.getByRole('button', { name: /Use ATM/ }).click();
  const rules = page.getByTestId('rules-manager');
  await expect(rules).toBeVisible();
  await rules.getByRole('button', { name: 'New rule' }).click();
  const form = rules.getByRole('form', { name: 'New rule' });
  await expect(form.getByRole('button', { name: 'Add rule' })).toBeDisabled();
  await expect(form.getByText('Premium price must be positive.')).toBeVisible();
  await form.getByLabel('Premium', { exact: true }).fill('12.5');
  await form.getByRole('button', { name: 'Add rule' }).click();
  const row = rules.getByTestId('rule-row').first();
  await expect(row).toContainText('premium at or above 12.5');
  await row.getByRole('button', { name: 'Disable' }).click();
  await expect(row).toContainText('off');
  await row.getByRole('button', { name: 'Edit' }).click();
  await rules.getByLabel('Direction').selectOption('BELOW');
  await rules.getByRole('button', { name: 'Save rule' }).click();
  await expect(row).toContainText('premium at or below 12.5');
  // candle-close rules explain their data requirements and reject too-short timeframes at the source
  await rules.getByRole('button', { name: 'New rule' }).click();
  await rules.getByLabel('Condition').selectOption('CLOSE_BEYOND_LEVEL');
  await expect(rules.getByText(/completed candles only/)).toBeVisible();
  await expect(rules.getByLabel('Timeframe').locator('option')).toHaveText(['5m', '15m', '30m', '1h', '1d']);
  await rules.getByRole('button', { name: 'Cancel' }).click();
  page.on('dialog', (d) => void d.accept());
  await row.getByRole('button', { name: 'Delete' }).click();
  await expect(rules.getByText(/No rules for this contract/)).toBeVisible();
});

test('workbench separates underlying breakout evidence from option premium behaviour, and the compare screen works on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await enter(page);
  await page.goto('/#/workbench?u=RELIANCE');
  await page.getByRole('button', { name: /Use ATM/ }).click();
  await expect(page.getByText('Underlying breakout evidence')).toBeVisible();
  await expect(page.getByText(/Option premium behaviour/)).toBeVisible();
  await expect(page.getByText(/Levels on this chart come from the option's own candles/)).toBeVisible();
  for (const p of ['/workbench?u=RELIANCE', '/watchlist', '/compare']) {
    await page.goto(`/#${p}`);
    await settled(page);
    expect(await horizontalOverflow(page), p).toBeLessThanOrEqual(0);
  }
  await page.goto('/#/compare');
  await expect(page.getByText(/Tick two or more contracts|No contracts to compare yet/)).toBeVisible();
});
