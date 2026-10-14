// Options Workbench end to end, against the offline demo stack (synthetic data, never live market data).
// The server clock is frozen per test with the demo-only x-desk-demo-now header so session state is deterministic.
import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { EVIDENCE, horizontalOverflow, settled, signIn, watch } from './helpers';

const OPEN = '2026-10-14T05:00:00.000Z'; // Wednesday 10:30 IST
const SATURDAY = '2026-10-17T05:00:00.000Z';

async function enter(page: Page, now = OPEN) {
  await page.context().setExtraHTTPHeaders({ 'x-desk-demo-now': now });
  await signIn(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Today' })).toBeVisible();
}

/** Console/network problems; type-ahead search requests that were intentionally cancelled are not problems. */
const problemsOf = (page: Page): string[] => watch(page);
const clean = (p: string[]) => p.filter((x) => !/workbench\/search.*ERR_ABORTED/.test(x));

test('search a symbol with the keyboard, open the underlying, and see validated quote fields with sources', async ({ page }) => {
  const problems = problemsOf(page);
  await enter(page);
  await page.goto('/#/workbench');
  const box = page.getByRole('combobox', { name: /Search stocks, indices or option contracts/ });
  await box.fill('reliance');
  const list = page.getByRole('listbox');
  await expect(list.getByRole('option', { name: /RELIANCE/ }).first()).toBeVisible();
  await expect(page.getByText('F&O stocks')).toBeVisible();
  await box.press('ArrowDown');
  await box.press('Enter');
  await expect(page).toHaveURL(/#\/workbench\?.*u=RELIANCE/);
  const header = page.getByTestId('underlying-header');
  await expect(header).toContainText('RELIANCE');
  await expect(header).toContainText(/fixture|snapshot/i);
  const v = page.getByTestId('underlying-validation');
  await expect(v).toContainText('Last price');
  await expect(v).toContainText('Relative volume');
  await expect(v).toContainText('VWAP');
  await expect(v).toContainText('ATR (14)');
  await expect(v).toContainText('Previous close');
  await expect(page.locator('[data-testid="wb-chart"] canvas').first()).toBeVisible();
  await settled(page);
  await page.screenshot({ path: path.join(EVIDENCE, 'workbench-underlying.png'), fullPage: true });
  expect(clean(problems)).toEqual([]);
});

test('"/" focuses the search box and Escape closes the list', async ({ page }) => {
  await enter(page);
  await page.goto('/#/workbench');
  await page.locator('body').click({ position: { x: 5, y: 5 } });
  await page.keyboard.press('/');
  const box = page.getByRole('combobox', { name: /Search stocks/ });
  await expect(box).toBeFocused();
  await box.fill('nif');
  await expect(page.getByRole('option', { name: /NIFTY/ }).first()).toBeVisible();
  await box.press('Escape');
  await expect(page.getByRole('listbox')).toHaveCount(0);
});

test('an unknown search shows an explanation instead of an empty box', async ({ page }) => {
  await enter(page);
  await page.goto('/#/workbench');
  await page.getByRole('combobox', { name: /Search stocks/ }).fill('zzzznotasymbol');
  await expect(page.getByText(/No match for/)).toBeVisible();
});

test('pick expiry, call or put and a strike; nothing is chosen for you; add once, duplicate is reported; remove', async ({ page }) => {
  const problems = problemsOf(page);
  await enter(page);
  await page.goto('/#/workbench?u=RELIANCE');
  const builder = page.getByTestId('contract-builder');
  await expect(builder).toBeVisible();
  await expect(builder.getByText(/Default expiry: Nearest expiry/)).toBeVisible();
  await expect(builder.getByText(/Nothing is selected for you/)).toBeVisible();
  const add = builder.getByRole('button', { name: /Add to watchlist/ });
  await expect(add).toBeDisabled();
  await builder.getByRole('button', { name: 'Put (PE)' }).click();
  await builder.getByRole('button', { name: 'Call (CE)' }).click();
  await expect(builder.getByLabel('Strike')).toBeVisible();
  await builder.getByRole('button', { name: /Use ATM/ }).click();
  await expect(page).toHaveURL(/c=RELIANCE%7C2026-\d\d-\d\d%7CCE%7C/);
  await expect(add).toBeEnabled();
  const panel = page.getByTestId('contract-panel');
  await expect(panel).toBeVisible();
  for (const label of ['Option LTP', 'Bid / Ask', 'Spread', 'Premium for one lot', 'Traded volume (contracts)', 'Open interest (contracts)', 'OI change vs previous day', 'Implied volatility', 'Intrinsic / time value', 'Delta', 'Theta (per day)', 'Vega']) {
    await expect(panel).toContainText(label);
  }
  await expect(panel).toContainText('calculated, not provider-supplied');
  await expect(panel).toContainText('Underlying invalidation');
  await add.click();
  await expect(page.getByText('Contract added to your watchlist')).toBeVisible();
  await add.click();
  await expect(page.getByText('Already in your watchlist. Nothing changed.')).toBeVisible();

  await page.goto('/#/watchlist');
  const tracked = page.getByTestId('tracked-contracts');
  await expect(tracked.locator('tbody tr')).toHaveCount(1);
  await expect(tracked).toContainText('RELIANCE');
  page.once('dialog', (d) => void d.accept());
  await tracked.getByRole('button', { name: /^Remove RELIANCE/ }).click();
  await expect(page.getByText(/No option contracts tracked yet/)).toBeVisible();
  expect(clean(problems)).toEqual([]);
});

test('the option chart panel states what it is: snapshots or candles, never silently empty', async ({ page }) => {
  await enter(page);
  await page.goto('/#/workbench?u=RELIANCE');
  await page.getByRole('button', { name: /Use ATM/ }).click();
  const chart = page.getByTestId('option-chart-panel');
  await expect(chart).toBeVisible();
  await expect(chart).toContainText(/not exchange candles|No option candle provider/);
  await page.getByRole('button', { name: '1d' }).click();
  await expect(chart).toContainText(/Daily option candles are not offered/);
});

test('timeframes: switch through all seven, unavailable ones explain themselves, forming bar is flagged', async ({ page }) => {
  await enter(page);
  await page.goto('/#/workbench?u=INFY&tf=15m');
  const group = page.getByRole('group', { name: 'Timeframe' });
  for (const tf of ['1m', '3m', '5m', '15m', '30m', '1h', '1d']) {
    await group.getByRole('button', { name: tf, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`tf=${tf}`));
    await expect(group.getByRole('button', { name: tf, exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('[data-testid="wb-chart"] canvas').first()).toBeVisible();
  }
  // the bars table lists completed and forming bars separately
  await group.getByRole('button', { name: '15m', exact: true }).click();
  await page.getByText('Latest bars as a table').click();
  await expect(page.getByRole('cell', { name: /complete|forming/ }).first()).toBeVisible();
});

test('an unavailable timeframe is disabled with the reason, and the page says which and why', async ({ page }) => {
  await enter(page);
  await page.route('**/api/workbench/underlying/*/mtf', async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    j.data.rows = j.data.rows.map((r: { tf: string }) => (r.tf === '1m' ? { ...r, availability: { tf: '1m', available: false, bars: 12, reason: 'Only 12 completed 1 min bars are available; at least 30 are needed for indicators and structure.' } } : r));
    await route.fulfill({ response: res, json: j });
  });
  await page.goto('/#/workbench?u=INFY&tf=15m');
  const oneMin = page.getByRole('group', { name: 'Timeframe' }).getByRole('button', { name: '1m', exact: true });
  await expect(oneMin).toBeDisabled();
  await expect(oneMin).toHaveAttribute('title', /Only 12 completed/);
  await expect(page.getByTestId('tf-unavailable')).toContainText('Only 12 completed 1 min bars');
});

test('indicators and levels toggle, panes appear, and the choice survives a reload', async ({ page }) => {
  await enter(page);
  await page.goto('/#/workbench?u=TCS&tf=15m');
  await page.getByTestId('chart-options').getByText('Indicators and levels').click();
  await expect(page.getByTestId('pane-rsi')).toHaveCount(0);
  await page.getByLabel('RSI 14').check();
  await page.getByLabel('MACD').check();
  await page.getByLabel('ATR 14').check();
  await expect(page.getByTestId('pane-rsi')).toBeVisible();
  await expect(page.getByTestId('pane-macd')).toBeVisible();
  await expect(page.getByTestId('pane-atr')).toBeVisible();
  await page.getByLabel('Bollinger bands').check();
  await expect(page.getByText('Bollinger 20,2')).toBeVisible();
  await page.getByLabel('EMA 20').uncheck();
  await expect(page.locator('.legend').getByText('EMA 20')).toHaveCount(0);
  await page.getByLabel('Swing support/resistance').uncheck();
  await page.getByLabel('Rolling high/low').check();
  await page.reload();
  await page.getByTestId('chart-options').getByText('Indicators and levels').click();
  await expect(page.getByLabel('RSI 14')).toBeChecked();
  await expect(page.getByLabel('Swing support/resistance')).not.toBeChecked();
});

test('confirmation and invalidation conditions, evidence for and against, rules, and why each line exists', async ({ page }) => {
  await enter(page);
  await page.goto('/#/workbench?u=HDFCBANK&tf=15m');
  const card = page.getByTestId('breakout-card');
  await expect(card).toBeVisible();
  await expect(card).toContainText('Confirmation:');
  await expect(card).toContainText('Invalidation:');
  await expect(card).toContainText('completed 15 min close');
  await card.getByText('Rules used').click();
  await expect(card).toContainText('Only completed 15 min bars count');
  await expect(card).toContainText('STALE or conflicting data can never produce CONFIRMED');
  const levels = page.getByTestId('levels-table');
  await expect(levels).toBeVisible();
  await expect(levels.locator('tbody tr').first()).toContainText(/swing|session|opening|rolling|high|low/i);
  await expect(page.getByTestId('mtf-table')).toBeVisible();
  await expect(page.getByTestId('mtf-table').locator('tbody tr')).toHaveCount(7);
});

test('scanner: run it, see genuine candidates with evidence, expand the audit, open one in the Workbench', async ({ page }) => {
  const problems = problemsOf(page);
  await enter(page);
  await page.goto('/#/scanner');
  await expect(page.getByText(/Nothing is scanned until you do/)).toBeVisible();
  await page.getByRole('button', { name: 'Run scan' }).click();
  const summary = page.getByTestId('scan-summary');
  await expect(summary).toBeVisible({ timeout: 40_000 });
  const cards = page.getByTestId('candidate-card');
  expect(await cards.count()).toBeGreaterThanOrEqual(5);
  await expect(summary).toContainText('contract');
  const first = cards.first();
  await expect(first).toContainText('Why it qualified');
  await expect(first).toContainText('What could invalidate it');
  await expect(first).toContainText('Confirmation:');
  await expect(first).toContainText(/Contracts to inspect/);
  await expect(first).toContainText('not recommendations');
  await first.getByText('Audit trail: every check applied').click();
  await expect(first).toContainText('Segment filter');
  await expect(page.getByText(/no blended score/)).toBeVisible();
  const sym = (await first.locator('h2').innerText()).split(' ')[0]!;
  await page.screenshot({ path: path.join(EVIDENCE, 'scanner.png'), fullPage: true });
  await first.getByRole('button', { name: 'Open in Workbench' }).click();
  await expect(page).toHaveURL(new RegExp(`#/workbench\\?.*u=${sym}`));
  await expect(page.getByTestId('underlying-header')).toContainText(sym);
  expect(clean(problems)).toEqual([]);
});

test('scanner: no qualifying candidates shows the reasons, not a padded list', async ({ page }) => {
  await enter(page);
  await page.route('**/api/workbench/scan**', async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    j.data.candidates = [];
    j.data.summary = { universe: 30, evaluated: 30, stockCandidates: 0, contractCount: 0, excludedByReason: { 'no qualifying setup (NOT_READY)': 21, 'data stale': 9 }, message: '0 of the 30 evaluated underlyings qualified; fewer than 5 met every filter. Most exclusions: 21 × no qualifying setup (NOT_READY); 9 × data stale. No candidates were added to reach 5.' };
    await route.fulfill({ response: res, json: j });
  });
  await page.goto('/#/scanner');
  await page.getByRole('button', { name: 'Run scan' }).click();
  await expect(page.getByTestId('scan-summary')).toContainText('No candidates were added to reach 5');
  await expect(page.getByText(/No underlying met every filter/)).toBeVisible();
  await expect(page.getByTestId('candidate-card')).toHaveCount(0);
});

test('news and events: sources, publisher, original time, exchange-confirmed status, and an honest unsupported macro source', async ({ page }) => {
  const problems = problemsOf(page);
  await enter(page);
  await page.goto('/#/events?symbols=RELIANCE,TCS');
  const sources = page.getByTestId('event-sources');
  await expect(sources).toContainText('NSE corporate announcements');
  await expect(sources).toContainText('UNSUPPORTED');
  await expect(sources).toContainText(/Macro and sector event calendar/);
  const table = page.getByTestId('events-table');
  await expect(table).toBeVisible();
  await expect(table.locator('tbody tr').first()).toContainText(/NSE|publisher|Mint|Times|Economic/i);
  await expect(table).toContainText('confirmed exchange');
  await page.getByText('details').first().click();
  await expect(page.getByText(/retrieved .* IST/).first()).toBeVisible();
  await page.screenshot({ path: path.join(EVIDENCE, 'events.png'), fullPage: true });
  expect(clean(problems)).toEqual([]);
});

test('stale contract data is labelled STALE (value kept, never presented as live)', async ({ page }) => {
  await enter(page);
  await page.route('**/api/workbench/contract?**', async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    const v = j.data.validation;
    v.state = 'STALE';
    for (const k of ['ltp', 'bid', 'ask']) if (v[k]) v[k].state = 'STALE';
    await route.fulfill({ response: res, json: j });
  });
  await page.goto('/#/workbench?u=RELIANCE');
  await page.getByRole('button', { name: /Use ATM/ }).click();
  const panel = page.getByTestId('contract-panel');
  await expect(panel).toBeVisible();
  await expect(page.locator('.badge', { hasText: 'STALE' }).first()).toBeVisible();
  await expect(panel).not.toContainText('VALID');
});

test('missing provider fields show a dash with the reason, never zero', async ({ page }) => {
  await enter(page);
  await page.route('**/api/workbench/contract?**', async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    const v = j.data.validation;
    for (const k of ['volume', 'oi', 'oiChange', 'iv', 'bidQty', 'askQty']) v[k] = { value: null, state: 'UNKNOWN', prov: null, note: `${k} not supplied by the provider.` };
    await route.fulfill({ response: res, json: j });
  });
  await page.goto('/#/workbench?u=RELIANCE');
  await page.getByRole('button', { name: /Use ATM/ }).click();
  const panel = page.getByTestId('contract-panel');
  const vol = panel.locator('dt', { hasText: 'Traded volume' }).locator('xpath=following-sibling::dd[1]');
  await expect(vol).toHaveText(/^-\s*UNKNOWN$/);
  await expect(vol.locator('span[title]').first()).toHaveAttribute('title', /not supplied by the provider/);
  const oi = panel.locator('dt', { hasText: 'Open interest' }).locator('xpath=following-sibling::dd[1]');
  await expect(oi).not.toHaveText(/^0/);
});

test('a contract with no quote says so instead of showing prices', async ({ page }) => {
  await enter(page);
  await page.route('**/api/workbench/contract?**', async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    const v = j.data.validation;
    v.quoteAvailable = false;
    v.state = 'UNKNOWN';
    for (const k of ['ltp', 'bid', 'ask', 'spreadAbs', 'spreadPct', 'oneLotPremium']) v[k] = { value: null, state: 'UNKNOWN', prov: null, note: 'No quote.' };
    v.notes = ['No quote is available for this contract right now (no trades and no two-sided book).'];
    await route.fulfill({ response: res, json: j });
  });
  await page.goto('/#/workbench?u=RELIANCE');
  await page.getByRole('button', { name: /Use ATM/ }).click();
  await expect(page.getByTestId('contract-panel')).toContainText(/No quote is available/);
  await expect(page.getByTestId('contract-panel')).toContainText('Prices below are unknown, not zero');
});

test('API failure is explained with a retry; the rest of the app keeps working', async ({ page }) => {
  await enter(page);
  let fail = true;
  await page.route('**/api/workbench/underlying/INFY?**', async (route) => {
    if (fail) await route.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ ok: false, error: { code: 'UPSTREAM', message: 'Option chain unavailable: 429 from www.nseindia.com' } }) });
    else await route.continue();
  });
  await page.goto('/#/workbench?u=INFY');
  const alert = page.getByRole('alert').filter({ hasText: 'Could not load INFY' });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText('429');
  fail = false;
  await alert.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByTestId('underlying-header')).toContainText('INFY');
  await page.goto('/#/settings');
  await expect(page.locator('h1')).toHaveText('Settings');
});

test('market closed (Saturday): MARKET CLOSED is shown as such, not as an outage or stale data', async ({ page }) => {
  await enter(page, SATURDAY);
  await page.goto('/#/workbench?u=RELIANCE');
  const header = page.getByTestId('underlying-header');
  await expect(header).toContainText(/MARKET CLOSED/);
  await expect(header).not.toContainText('STALE');
  await expect(page.getByTestId('context-strip')).toContainText(/Closed/);
  await page.getByRole('button', { name: /Use ATM/ }).click();
  await expect(page.getByTestId('contract-panel')).toContainText(/Market closed/);
  await expect(page.getByTestId('contract-panel')).not.toContainText('Could not load');
});

test('contract explorer: filters by side, liquidity and volume; unknown values are not counted as zero', async ({ page }) => {
  await enter(page);
  await page.goto('/#/explorer?u=RELIANCE');
  const table = page.getByTestId('explorer-table');
  await expect(table).toBeVisible();
  const all = await table.locator('tbody tr').count();
  expect(all).toBe(25);
  await page.getByRole('button', { name: 'Calls', exact: true }).click();
  await expect(table.getByRole('columnheader', { name: 'PE OI' })).toHaveCount(0);
  await page.getByLabel('Liquidity').selectOption('LIQUID');
  const liquid = await table.locator('tbody tr').count();
  expect(liquid).toBeLessThan(all);
  await page.getByLabel('Min volume').fill('99999999');
  await expect(page.getByText(/No strike matches these filters/)).toBeVisible();
  await page.getByLabel('Min volume').fill('');
  await page.getByLabel('Liquidity').selectOption('ANY');
  await page.getByRole('button', { name: /^Open .* CE in Workbench$/ }).first().click();
  await expect(page).toHaveURL(/#\/workbench\?.*c=RELIANCE/);
});

test('data health shows providers, freshness limits, entitlements and in-app alerts with dedupe', async ({ page }) => {
  await enter(page);
  await page.goto('/#/data-health');
  await expect(page.getByTestId('provider-table')).toContainText('NSE option chain');
  await expect(page.getByTestId('state-legend')).toContainText('MARKET CLOSED');
  await expect(page.getByText(/No broker order API/)).toBeVisible();
  await expect(page.getByText(/Delayed free feed/)).toBeVisible();
  await page.getByRole('button', { name: 'Evaluate now' }).click();
  await expect(page.getByTestId('wb-alerts')).toBeVisible();
  const n = await page.getByTestId('wb-alerts').locator('li').count();
  expect(n).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Evaluate now' }).click();
  await expect(page.getByRole('status').filter({ hasText: '0 new' })).toBeVisible();
  expect(await page.getByTestId('wb-alerts').locator('li').count()).toBe(n);
});

test('compare tracked contracts side by side', async ({ page }) => {
  await enter(page);
  await page.goto('/#/explorer?u=RELIANCE');
  const adds = page.getByRole('button', { name: /^Add .* CE to watchlist$/ });
  await adds.nth(11).click();
  await expect(page.getByText(/Contract added/)).toBeVisible();
  await adds.nth(12).click();
  await page.goto('/#/watchlist');
  const t = page.getByTestId('tracked-contracts');
  await expect(t.locator('tbody tr')).toHaveCount(2);
  await t.getByRole('checkbox').nth(0).check();
  await t.getByRole('checkbox').nth(1).check();
  const cmp = page.getByTestId('compare-table');
  await expect(cmp).toBeVisible();
  await expect(cmp).toContainText('Open interest (contracts)');
  await expect(cmp).toContainText('Data quality');
});

test('no order can be placed: no buy/sell/order controls exist and no order endpoint answers', async ({ page }) => {
  await enter(page);
  const calls: string[] = [];
  page.on('request', (r) => { if (/order|trade-exec|place/i.test(new URL(r.url()).pathname) && new URL(r.url()).pathname.startsWith('/api')) calls.push(r.url()); });
  for (const p of ['/workbench?u=RELIANCE', '/scanner', '/explorer?u=NIFTY', '/events', '/data-health', '/watchlist']) {
    await page.goto(`/#${p}`);
    await page.waitForTimeout(600);
    for (const name of [/^buy\b/i, /^sell\b/i, /place order/i, /^order\b/i, /square off/i, /execute/i]) {
      await expect(page.getByRole('button', { name })).toHaveCount(0);
      await expect(page.getByRole('link', { name })).toHaveCount(0);
    }
  }
  for (const route of ['/api/orders', '/api/order', '/api/workbench/order', '/api/workbench/orders']) {
    expect((await page.request.post(route, { data: {}, headers: { origin: new URL(page.url()).origin } })).status(), route).toBeGreaterThanOrEqual(404);
  }
  expect(calls).toEqual([]);
});

test('mobile: workbench, scanner and explorer fit a phone, controls reachable by keyboard', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await enter(page);
  for (const p of ['/workbench?u=RELIANCE', '/scanner', '/explorer?u=NIFTY', '/events', '/data-health', '/more']) {
    await page.goto(`/#${p}`);
    await settled(page);
    expect(await horizontalOverflow(page), p).toBeLessThanOrEqual(0);
  }
  await page.goto('/#/workbench?u=RELIANCE');
  await page.getByRole('button', { name: /Use ATM/ }).click();
  await expect(page.getByTestId('contract-panel')).toBeVisible();
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: path.join(EVIDENCE, 'workbench-mobile.png'), fullPage: true });
  // keyboard: tab order reaches the timeframe buttons and Enter selects one
  const five = page.getByRole('group', { name: 'Timeframe' }).getByRole('button', { name: '5m', exact: true });
  await five.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/tf=5m/);
});
