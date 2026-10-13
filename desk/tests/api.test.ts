import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { hashPassword } from '../server/auth';
import { resetCacheMemory } from '../server/cache';
import { loadConfig, type Ctx } from '../server/config';
import { MemoryKV } from '../server/kv';
import { handle } from '../server/router';
import { runAlertsTick } from '../server/services/alertsRunner';
import { runBackup } from '../server/services/backup';
import { runDigest } from '../server/services/digests';
import { setSendSpacingMs } from '../server/services/notify';

const NOW = new Date('2026-10-12T05:00:00.000Z'); // Monday 10:30 IST, market open
const PASSWORD = 'correct horse battery staple';
const ORIGIN = 'https://desk.example.netlify.app';
const TG_TOKEN = '123456789:AAFakeTokenForTestsOnly_abcdefghijk';

let hash = '';
beforeAll(async () => {
  hash = await hashPassword(PASSWORD, 100_000);
});

let sent: string[] = [];
let ctx: Ctx;
let cookie = '';

function makeCtx(extraEnv: Record<string, string> = {}): Ctx {
  const fetchImpl = vi.fn(async (_url: unknown, init?: { body?: unknown }) => {
    sent.push(String(init?.body ?? ''));
    return new Response(JSON.stringify({ ok: true, result: { id: 42, username: 'desk_test_bot' } }), { status: 200, headers: { 'content-type': 'application/json' } });
  }) as unknown as typeof fetch;
  return {
    cfg: loadConfig({ SESSION_SECRET: 's'.repeat(40), APP_PASSWORD_HASH: hash, DESK_OFFLINE: '1', DESK_STORAGE: 'memory', ...extraEnv }),
    kv: new MemoryKV(),
    now: () => NOW,
    fetchImpl,
  };
}

function call(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<Response> {
  const h: Record<string, string> = { ...headers };
  if (cookie) h.cookie = cookie;
  if (body !== undefined) h['content-type'] = h['content-type'] ?? 'application/json';
  if (method !== 'GET') h.origin = h.origin ?? ORIGIN;
  return handle(new Request(`${ORIGIN}${path}`, { method, headers: h, body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body) }), ctx);
}

async function json(res: Response): Promise<any> {
  return res.json();
}

async function login(): Promise<void> {
  const res = await call('POST', '/api/auth/login', { password: PASSWORD });
  expect(res.status).toBe(200);
  cookie = (res.headers.get('set-cookie') ?? '').split(';')[0]!;
  expect(cookie).toMatch(/^__Host-desk_session=/);
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  resetCacheMemory();
  setSendSpacingMs(0);
  sent = [];
  cookie = '';
  ctx = makeCtx({ TELEGRAM_BOT_TOKEN: TG_TOKEN, TELEGRAM_CHAT_ID: '99' });
});
afterEach(() => vi.useRealTimers());

const goodTrade = {
  symbol: 'RELIANCE',
  segment: 'EQ_INTRADAY',
  side: 'LONG',
  qty: 100,
  entryPrice: 1000,
  exitPrice: 1010,
  entryAt: '2026-10-12T04:00:00.000Z',
  exitAt: '2026-10-12T04:30:00.000Z',
  stopLoss: 990,
  target: 1020,
  setup: 'ORB',
};

describe('auth & hardening', () => {
  it('serves health publicly but protects everything else', async () => {
    expect((await call('GET', '/api/health')).status).toBe(200);
    const r = await call('GET', '/api/overview');
    expect(r.status).toBe(401);
    expect((await json(r)).error.code).toBe('UNAUTHENTICATED');
  });

  it('logs in, sets a hardened cookie, and logs out', async () => {
    const res = await call('POST', '/api/auth/login', { password: PASSWORD });
    const c = res.headers.get('set-cookie') ?? '';
    expect(c).toContain('HttpOnly');
    expect(c).toContain('Secure');
    expect(c).toContain('SameSite=Strict');
    cookie = c.split(';')[0]!;
    expect((await json(await call('GET', '/api/auth/me'))).data.authenticated).toBe(true);
    const out = await call('POST', '/api/auth/logout', {});
    expect(out.headers.get('set-cookie')).toContain('Max-Age=0');
  });

  it('rejects a wrong password and locks out after repeated failures', async () => {
    vi.useRealTimers(); // login adds a real 400ms delay per failure
    for (let i = 0; i < 5; i++) expect((await call('POST', '/api/auth/login', { password: 'nope' })).status).toBe(401);
    const locked = await call('POST', '/api/auth/login', { password: PASSWORD });
    expect(locked.status).toBe(429);
    expect(locked.headers.get('retry-after')).toBeTruthy();
  }, 20_000);

  it('refuses to log in when the server is not configured', async () => {
    ctx = makeCtx({ SESSION_SECRET: 'short' });
    const res = await call('POST', '/api/auth/login', { password: PASSWORD });
    expect(res.status).toBe(503);
  });

  it('blocks cross-origin mutations, wrong content types and oversized bodies', async () => {
    await login();
    expect((await call('POST', '/api/trades', goodTrade, { origin: 'https://evil.example' })).status).toBe(403);
    expect((await call('POST', '/api/trades', JSON.stringify(goodTrade), { 'content-type': 'text/plain' })).status).toBe(415);
    expect((await call('POST', '/api/trades', '{not json')).status).toBe(400);
    const big = await call('POST', '/api/alerts', JSON.stringify({ note: 'x'.repeat(300_000) }));
    expect(big.status).toBe(413);
  });

  it('sets no-store and nosniff on API responses', async () => {
    const r = await call('GET', '/api/health');
    expect(r.headers.get('cache-control')).toBe('no-store');
    expect(r.headers.get('x-content-type-options')).toBe('nosniff');
  });

  it('returns 404 for unknown routes and 405 for wrong methods', async () => {
    await login();
    expect((await call('GET', '/api/nope')).status).toBe(404);
    expect((await call('DELETE', '/api/overview')).status).toBe(405);
  });
});

describe('trades & journal', () => {
  beforeEach(login);

  it('creates, validates, updates and deletes a trade', async () => {
    const bad = await call('POST', '/api/trades', { ...goodTrade, stopLoss: 1010 });
    expect(bad.status).toBe(400);
    expect((await json(bad)).error.message).toMatch(/stop loss/i);

    const created = await json(await call('POST', '/api/trades', goodTrade));
    expect(created.ok).toBe(true);
    const id = created.data.id as string;

    const upd = await call('PUT', `/api/trades/${id}`, { ...goodTrade, exitPrice: 995, exitAt: '2026-10-12T04:40:00.000Z' });
    expect(upd.status).toBe(200);
    expect((await json(upd)).data.exitPrice).toBe(995);

    expect((await call('DELETE', `/api/trades/${id}`)).status).toBe(200);
    expect((await call('DELETE', `/api/trades/${id}`)).status).toBe(404);
  });

  it('reports journal stats net of Indian charges', async () => {
    await call('POST', '/api/trades', goodTrade);
    const j = (await json(await call('GET', '/api/journal'))).data;
    expect(j.stats.trades).toBe(1);
    expect(j.stats.grossPnl).toBeCloseTo(1000, 2);
    expect(j.stats.netPnl).toBeLessThan(1000); // STT + exchange + SEBI + stamp + GST + brokerage
    expect(j.rows[0].charges).toBeGreaterThan(0);
    expect(j.tax.fy).toBe('2026-27');
  });

  it('keeps every write when many arrive at once (no lost updates)', async () => {
    const results = await Promise.all(Array.from({ length: 25 }, (_, i) => call('POST', '/api/trades', { ...goodTrade, entryPrice: 1000 + i, exitPrice: 1010 + i, stopLoss: 990 + i, target: 1020 + i })));
    expect(results.every((r) => r.status === 201)).toBe(true);
    const j = (await json(await call('GET', '/api/journal'))).data;
    expect(j.rows).toHaveLength(25);
  });

  it('computes position size from stored risk settings', async () => {
    await call('PUT', '/api/settings', { capital: 1_000_000, riskPerTradePct: 1 });
    const r = (await json(await call('GET', '/api/sizing?entry=500&stop=490'))).data;
    expect(r.riskAmount).toBe(10000);
    expect(r.qty).toBe(1000);
  });

  it('rejects invalid settings', async () => {
    expect((await call('PUT', '/api/settings', { capital: -5 })).status).toBe(400);
  });
});

describe('market, watchlist & portfolio (offline demo data)', () => {
  beforeEach(login);

  it('flags demo data in the response metadata', async () => {
    const r = await json(await call('GET', '/api/overview'));
    expect(r.ok).toBe(true);
    expect(r.meta.notes.join(' ')).toMatch(/DEMO DATA/);
    expect(r.data.indices.length).toBeGreaterThan(0);
  });

  it('adds and removes watchlist symbols', async () => {
    const add = await call('POST', '/api/watchlist', { symbol: 'TCS', note: 'results soon', support: 3000, resistance: 3300 });
    expect(add.status).toBe(201);
    const wl = (await json(await call('GET', '/api/watchlist'))).data.rows as { item: { symbol: string } }[];
    expect(wl.some((r) => r.item.symbol === 'TCS')).toBe(true);
    await call('DELETE', '/api/watchlist/TCS');
    const after = (await json(await call('GET', '/api/watchlist'))).data.rows as { item: { symbol: string } }[];
    expect(after.some((r) => r.item.symbol === 'TCS')).toBe(false);
  });

  it('imports holdings from a broker CSV and values the portfolio', async () => {
    const csv = 'Instrument,Qty.,Avg. cost\nRELIANCE,10,1200\nTCS,5,3000\nBAD,x,y\n';
    const prev = await json(await call('POST', '/api/holdings-import', { csv, mode: 'preview' }));
    expect(prev.data.rows).toHaveLength(2);
    expect(prev.data.errors.length).toBe(1);
    expect(prev.data.committed).toBe(false);
    expect((await json(await call('GET', '/api/portfolio'))).data.holdings).toHaveLength(0);

    await call('POST', '/api/holdings-import', { csv, mode: 'append' });
    const p = (await json(await call('GET', '/api/portfolio'))).data;
    expect(p.holdings).toHaveLength(2);
    expect(p.summary.invested).toBe(10 * 1200 + 5 * 3000);

    expect((await call('POST', '/api/holdings-import', { csv, mode: 'replace' })).status).toBe(400); // needs confirm
  });

  it('serves option expiries and a chain with analytics', async () => {
    const ex = (await json(await call('GET', '/api/options/NIFTY/expiries'))).data;
    expect(ex.expiries.length).toBeGreaterThan(0);
    const ch = await json(await call('GET', '/api/options/NIFTY/chain'));
    expect(ch.ok).toBe(true);
    expect(ch.data.analytics.pcrOi).toBeGreaterThan(0);
    expect((await call('GET', '/api/options/NIFTY/chain?expiry=2020-01-01')).status).toBe(400);
    expect((await call('GET', '/api/options/XYZ/chain')).status).toBe(404);
  });
});

describe('alerts runner', () => {
  beforeEach(login);

  it('fires a due alert exactly once and logs the event', async () => {
    await call('POST', '/api/alerts', { type: 'PRICE_ABOVE', symbol: 'RELIANCE', threshold: 1, repeat: 'once' });
    const r1 = await runAlertsTick(ctx);
    expect(r1.fired).toHaveLength(1);
    expect(r1.notified).toBe(1);
    const r2 = await runAlertsTick(ctx);
    expect(r2.fired).toHaveLength(0);
    expect(sent.filter((b) => b.includes('RELIANCE'))).toHaveLength(1);
    const a = (await json(await call('GET', '/api/alerts'))).data;
    expect(a.events[0].delivered).toBe(true);
    expect(a.alerts[0].lastTriggeredAt).toBeTruthy();
  });

  it('does not double-send when two ticks overlap', async () => {
    await call('POST', '/api/alerts', { type: 'PRICE_ABOVE', symbol: 'INFY', threshold: 1, repeat: 'once' });
    const [a, b] = await Promise.all([runAlertsTick(ctx), runAlertsTick(ctx)]);
    expect(a.fired.length + b.fired.length).toBe(1);
    expect(sent.filter((x) => x.includes('INFY'))).toHaveLength(1);
  });

  it('never fires a threshold that is not met', async () => {
    await call('POST', '/api/alerts', { type: 'PRICE_BELOW', symbol: 'RELIANCE', threshold: 1, repeat: 'once' });
    expect((await runAlertsTick(ctx)).fired).toHaveLength(0);
  });

  it('keeps the event and reports failure when Telegram is down', async () => {
    ctx.fetchImpl = vi.fn(async () => new Response('boom', { status: 500 })) as unknown as typeof fetch;
    await call('POST', '/api/alerts', { type: 'PRICE_ABOVE', symbol: 'SBIN', threshold: 1, repeat: 'once' });
    const r = await runAlertsTick(ctx);
    expect(r.fired).toHaveLength(1);
    expect(r.notified).toBe(0);
    expect(r.notifyFailures.length).toBeGreaterThan(0);
    expect((await json(await call('GET', '/api/alerts'))).data.events[0].delivered).toBe(false);
  });

  it('skips everything on a market holiday', async () => {
    const holiday = { ...ctx, now: () => new Date('2026-10-02T05:00:00Z') }; // Gandhi Jayanti
    await call('POST', '/api/alerts', { type: 'PRICE_ABOVE', symbol: 'RELIANCE', threshold: 1, repeat: 'once' });
    const r = await runAlertsTick(holiday);
    expect(r.skippedReason).toMatch(/Not a trading day/);
    expect(r.fired).toHaveLength(0);
  });

  it('warns once per escalation when the daily loss limit is breached', async () => {
    await call('PUT', '/api/settings', { capital: 100000, maxDailyLossPct: 1 });
    await call('POST', '/api/trades', { ...goodTrade, entryPrice: 1000, exitPrice: 900, stopLoss: 950, target: 1100 });
    const r1 = await runAlertsTick(ctx);
    expect(r1.risk?.state).toBe('BREACHED');
    const r2 = await runAlertsTick(ctx);
    expect(r2.risk?.notified).toBe(false);
    expect(sent.filter((b) => b.includes('Risk guard'))).toHaveLength(1);
  });
});

describe('digests', () => {
  beforeEach(login);

  it('sends each digest once per trading day', async () => {
    const a = await runDigest(ctx, 'pre');
    expect(a.sent).toBe(true);
    const b = await runDigest(ctx, 'pre');
    expect(b.sent).toBe(false);
    expect(b.reason).toMatch(/Already sent/);
    expect((await runDigest(ctx, 'eod')).sent).toBe(true);
    expect(sent.some((m) => m.includes('Pre-market brief'))).toBe(true);
    expect(sent.some((m) => m.includes('End-of-day summary'))).toBe(true);
  });

  it('releases the claim when delivery fails so a retry can succeed', async () => {
    ctx.fetchImpl = vi.fn(async () => new Response('down', { status: 500 })) as unknown as typeof fetch;
    expect((await runDigest(ctx, 'pre')).sent).toBe(false);
    ctx.fetchImpl = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })) as unknown as typeof fetch;
    expect((await runDigest(ctx, 'pre')).sent).toBe(true);
  });

  it('does nothing without Telegram configured', async () => {
    ctx = makeCtx();
    expect((await runDigest(ctx, 'pre')).reason).toMatch(/not configured/i);
  });
});

describe('export, import & backups', () => {
  beforeEach(login);

  it('round-trips the whole dataset', async () => {
    await call('POST', '/api/trades', goodTrade);
    await call('POST', '/api/alerts', { type: 'RSI_BELOW', symbol: 'TCS', threshold: 30, repeat: 'daily' });
    await call('PUT', '/api/settings', { capital: 750000 });
    const exp = (await json(await call('GET', '/api/export'))).data;
    expect(exp.trades).toHaveLength(1);

    // wipe by importing an empty file, then restore from the export
    await call('POST', '/api/import', { version: 1, confirm: true });
    expect((await json(await call('GET', '/api/journal'))).data.rows).toHaveLength(0);
    const res = await json(await call('POST', '/api/import', { ...exp, confirm: true }));
    expect(res.data.trades).toBe(1);
    expect((await json(await call('GET', '/api/journal'))).data.rows).toHaveLength(1);
    expect((await json(await call('GET', '/api/settings'))).data.capital).toBe(750000);
  });

  it('rejects a bad import without changing anything', async () => {
    await call('POST', '/api/trades', goodTrade);
    const bad = await call('POST', '/api/import', { version: 1, confirm: true, trades: [{ symbol: 'X' }] });
    expect(bad.status).toBe(422);
    expect((await json(await call('GET', '/api/journal'))).data.rows).toHaveLength(1);
    const noConfirm = await call('POST', '/api/import', { version: 1, trades: [] });
    expect(noConfirm.status).toBe(422); // confirm:true is mandatory
    expect((await json(noConfirm)).error.message).toMatch(/confirm/);
  });

  it('snapshots before an import so it can be undone', async () => {
    await call('POST', '/api/trades', goodTrade);
    const r = await json(await call('POST', '/api/import', { version: 1, confirm: true }));
    const snap = await ctx.kv.get<{ trades: unknown[] }>(r.data.backupKey);
    expect(snap.value?.trades).toHaveLength(1);
  });

  it('keeps only the newest 14 daily backups', async () => {
    for (let d = 1; d <= 17; d++) {
      const c = { ...ctx, now: () => new Date(Date.UTC(2026, 8, d, 20, 30)) };
      await runBackup(c);
    }
    const keys = (await ctx.kv.list('backup/')).filter((k) => k.includes('daily-'));
    expect(keys).toHaveLength(14);
    expect(keys.some((k) => k.endsWith('2026-09-17'))).toBe(true);
    expect(keys.some((k) => k.endsWith('2026-09-01'))).toBe(false);
  });
});

describe('status', () => {
  beforeEach(login);
  it('runs the provider self-test', async () => {
    const s = (await json(await call('GET', '/api/status'))).data;
    expect(s.offline).toBe(true);
    expect(s.providers[0].ok).toBe(true);
    expect(s.problems).toEqual([]);
  });
});

describe('telegram routes', () => {
  beforeEach(login);
  it('reports status and sends one owner-initiated test message, then throttles', async () => {
    const st = (await json(await call('GET', '/api/telegram/status'))).data;
    expect(st.configured).toBe(true);
    expect(st.chatHint).toBe('…99');
    expect(JSON.stringify(st)).not.toContain('AAFake');
    const r = (await json(await call('POST', '/api/telegram/test', {}))).data;
    expect(r).toMatchObject({ connection: true, sent: true });
    expect(sent.filter((b) => b.includes('TIRE Desk test message'))).toHaveLength(1);
    expect((await call('POST', '/api/telegram/test', {})).status).toBe(429);
    const after = (await json(await call('GET', '/api/telegram/status'))).data;
    expect(after.lastSuccessKind).toBe('test');
  });
  it('explains when Telegram is not configured', async () => {
    ctx = makeCtx();
    await login();
    expect((await call('POST', '/api/telegram/test', {})).status).toBe(409);
  });
  it('requires authentication for the test endpoint', async () => {
    cookie = '';
    expect((await call('POST', '/api/telegram/test', {})).status).toBe(401);
  });
});

describe('backup routes', () => {
  beforeEach(login);
  it('verifies and restores a server snapshot', async () => {
    await call('POST', '/api/trades', goodTrade);
    const b = (await json(await call('POST', '/api/backup/run', {}))).data;
    const v = (await json(await call('GET', `/api/backup/verify?key=${encodeURIComponent(b.key)}`))).data;
    expect(v).toMatchObject({ ok: true, counts: { trades: 1 } });
    expect((await call('POST', '/api/backup/restore', { key: b.key })).status).toBe(400); // confirm required
    expect((await call('POST', '/api/backup/restore', { key: '../etc/passwd', confirm: true })).status).toBe(400);
    const r = (await json(await call('POST', '/api/backup/restore', { key: b.key, confirm: true }))).data;
    expect(r.trades).toBe(1);
  });
});
