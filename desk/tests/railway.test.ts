// Railway integration: production config gates, the Node adapter trust
// boundary, the Netlify edge proxy, persistence across a restart, the
// scheduler (single owner, restart catch-up, no double runs), Telegram
// delivery (rate limits, retries, no duplicates) and backup verify/restore.

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { promises as fs } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { hashPassword } from '../server/auth';
import { resetCacheMemory } from '../server/cache';
import { fatalProductionProblems, loadConfig, storageLayoutProblems, type Ctx } from '../server/config';
import { listRoutes } from '../server/router';
import { fetchText, ProviderError } from '../server/http';
import { FileKV, MemoryKV } from '../server/kv';
import { createHandler } from '../server/node-http';
import { parseBanTradeDate } from '../server/providers/nse';
import { sendTelegram, TelegramError } from '../server/providers/telegram';
import { Scheduler, schedulerStatus, type SchedulerState } from '../server/scheduler';
import { runAlertsTick } from '../server/services/alertsRunner';
import { runBackup, verifyBackup, restoreBackup } from '../server/services/backup';
import { retryUndelivered, setSendSpacingMs } from '../server/services/notify';
import * as U from '../server/services/userdata';
import { proxyApi, upstreamOrigin } from '../netlify/edge-functions/api-proxy';

const PASSWORD = 'correct horse battery staple';
const PUBLIC = 'https://tire-desk.example.netlify.app';
const PROXY_SECRET = 'p'.repeat(40);
const TG_TOKEN = '123456789:AAFakeTokenForTestsOnly_abcdefghijk';
let hash = '';
beforeAll(async () => {
  hash = await hashPassword(PASSWORD, 100_000);
});
beforeEach(() => {
  resetCacheMemory();
  setSendSpacingMs(0);
});

const prodEnv = (dir: string, extra: Record<string, string> = {}) => ({
  NODE_ENV: 'production',
  SESSION_SECRET: 's'.repeat(40),
  APP_PASSWORD_HASH: hash,
  DESK_DATA_DIR: dir,
  DESK_PUBLIC_ORIGIN: PUBLIC,
  DESK_PROXY_SECRET: PROXY_SECRET,
  DESK_SCHEDULER: 'off',
  ...extra,
});

// ---------------------------------------------------------------------------------------

describe('production configuration gate', () => {
  it('accepts a complete Railway configuration', () => {
    expect(fatalProductionProblems(loadConfig(prodEnv('/data/desk')))).toEqual([]);
  });
  it('treats any Railway deployment as production even without NODE_ENV', () => {
    const cfg = loadConfig({ RAILWAY_ENVIRONMENT_NAME: 'production' });
    expect(cfg.production).toBe(true);
    expect(cfg.schedulerEnabled).toBe(true);
    expect(fatalProductionProblems(cfg).length).toBeGreaterThan(0);
  });
  it('refuses unsafe production settings', () => {
    const bad = fatalProductionProblems(loadConfig(prodEnv('relative/dir', { DESK_OFFLINE: '1', DESK_PROXY_SECRET: 'short', DESK_ALLOWED_ORIGINS: 'http://localhost:5173,*' })));
    const text = bad.join(' ');
    expect(text).toMatch(/DESK_OFFLINE/);
    expect(text).toMatch(/absolute path/);
    expect(text).toMatch(/DESK_PROXY_SECRET/);
    expect(text).toMatch(/localhost/);
    expect(text).toMatch(/"\*"/);
  });
  it('ignores a plain-text password in production', () => {
    expect(loadConfig({ ...prodEnv('/data/desk'), APP_PASSWORD: 'x', APP_PASSWORD_HASH: '' }).plainPassword).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------

interface Running {
  server: Server;
  base: string;
  ctx: Ctx;
  close: () => Promise<void>;
}

async function startApi(dir: string, extra: Record<string, string> = {}): Promise<Running> {
  const cfg = loadConfig(prodEnv(dir, { DESK_OFFLINE: '', ...extra }));
  // offline fixtures keep market data deterministic; production gate is tested separately
  cfg.offline = true;
  const ctx: Ctx = { cfg, kv: new FileKV(dir), now: () => new Date() };
  const server = createServer(createHandler(async () => ctx, { requireProxyKey: true }));
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  return { server, base, ctx, close: () => new Promise((r) => server.close(() => r())) };
}

function viaProxy(api: Running, cookie = '', ip = '203.0.113.7') {
  // The real edge function, with its upstream fetch pointed at the local server.
  const fetchImpl = ((url: string | URL, init?: RequestInit) => fetch(String(url).replace('https://desk-api.example.up.railway.app', api.base), init)) as typeof fetch;
  return async (method: string, p: string, body?: unknown, headers: Record<string, string> = {}) => {
    const h: Record<string, string> = { origin: PUBLIC, 'sec-fetch-site': 'same-origin', ...headers };
    if (cookie) h.cookie = cookie;
    if (body !== undefined) h['content-type'] = 'application/json';
    const req = new Request(`${PUBLIC}${p}`, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
    return proxyApi(req, { DESK_API_ORIGIN: 'https://desk-api.example.up.railway.app', DESK_PROXY_SECRET: PROXY_SECRET }, ip, fetchImpl);
  };
}

async function login(api: Running, ip?: string): Promise<string> {
  const res = await viaProxy(api, '', ip)('POST', '/api/auth/login', { password: PASSWORD });
  expect(res.status).toBe(200);
  return (res.headers.get('set-cookie') ?? '').split(';')[0]!;
}

describe('Railway API behind the Netlify edge proxy', () => {
  let dir: string;
  let api: Running;
  beforeEach(async () => {
    dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-railway-'));
    api = await startApi(dir);
  });
  afterEach(async () => api.close());

  it('refuses direct access without the proxy secret, except the public health check', async () => {
    expect((await fetch(`${api.base}/api/health`)).status).toBe(200);
    const direct = await fetch(`${api.base}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json', origin: PUBLIC }, body: JSON.stringify({ password: PASSWORD }) });
    expect(direct.status).toBe(403);
    expect((await fetch(`${api.base}/api/overview`, { headers: { 'x-desk-proxy-key': 'wrong'.repeat(8) } })).status).toBe(403);
  });

  it('rejects every private route without the proxy secret, and without a session behind it', async () => {
    const routes = listRoutes();
    expect(routes.length).toBeGreaterThan(40);
    const concrete = (p: string) => p.replace(':symbol', 'TCS').replace(':id', 'NIFTY').replace(':kind', 'pre');
    for (const r of routes) {
      const p = concrete(r.path);
      const init: RequestInit = { method: r.method, headers: { origin: PUBLIC, 'content-type': 'application/json' }, body: r.method === 'GET' ? undefined : '{}' };
      const direct = await fetch(`${api.base}${p}`, init);
      if (p === '/api/health') expect(direct.status, `${r.method} ${p}`).toBe(200);
      else expect(direct.status, `direct ${r.method} ${p}`).toBe(403);
      if (r.public) continue;
      const proxied = await viaProxy(api)(r.method, p, r.method === 'GET' ? undefined : {});
      expect(proxied.status, `proxied, no session: ${r.method} ${p}`).toBe(401);
    }
  });

  it('exposes nothing sensitive on the public health endpoint', async () => {
    const body = await (await fetch(`${api.base}/api/health`)).text();
    expect(JSON.parse(body).data).toEqual({ ok: true });
    for (const secret of [PROXY_SECRET, 's'.repeat(40), hash, dir]) expect(body).not.toContain(secret);
  });

  it('issues a Secure __Host- session cookie for the public https origin', async () => {
    const res = await viaProxy(api)('POST', '/api/auth/login', { password: PASSWORD });
    const c = res.headers.get('set-cookie') ?? '';
    expect(c).toMatch(/^__Host-desk_session=/);
    expect(c).toContain('Secure');
    expect(c).toContain('HttpOnly');
    expect(c).toContain('SameSite=Strict');
    const me = await (await viaProxy(api, c.split(';')[0]!)('GET', '/api/auth/me')).json();
    expect(me.data.authenticated).toBe(true);
  });

  it('enforces authentication on every data route', async () => {
    for (const p of ['/api/overview', '/api/journal', '/api/portfolio', '/api/alerts', '/api/settings', '/api/export', '/api/telegram/status', '/api/status']) {
      expect((await viaProxy(api)('GET', p)).status, p).toBe(401);
    }
  });

  it('allows writes only from the exact configured origin', async () => {
    const cookie = await login(api);
    const call = viaProxy(api, cookie);
    const trade = { symbol: 'TCS', segment: 'EQ_DELIVERY', side: 'LONG', qty: 1, entryPrice: 100, entryAt: '2026-10-01T04:00:00.000Z' };
    expect((await call('POST', '/api/trades', trade, { origin: 'https://evil.example' })).status).toBe(403);
    expect((await call('POST', '/api/trades', trade, { origin: 'https://tire-desk.example.netlify.app.evil.example' })).status).toBe(403);
    expect((await call('POST', '/api/trades', trade, { 'sec-fetch-site': 'cross-site' })).status).toBe(403);
    expect((await call('POST', '/api/trades', trade)).status).toBe(201);
  });

  it('requires an Origin header on writes in production', async () => {
    const cookie = await login(api);
    const req = new Request(`${PUBLIC}/api/trades`, { method: 'POST', headers: { 'content-type': 'application/json', cookie }, body: '{}' });
    const fetchImpl = ((url: string | URL, init?: RequestInit) => fetch(String(url).replace('https://desk-api.example.up.railway.app', api.base), init)) as typeof fetch;
    const res = await proxyApi(req, { DESK_API_ORIGIN: 'https://desk-api.example.up.railway.app', DESK_PROXY_SECRET: PROXY_SECRET }, '198.51.100.1', fetchImpl);
    expect(res.status).toBe(403);
  });

  it('keys the login lockout on the proxy-reported IP and ignores spoofed IP headers', async () => {
    const attacker = viaProxy(api, '', '198.51.100.66');
    for (let i = 0; i < 5; i++) {
      // a spoofed header must not let the attacker rotate identities
      expect((await attacker('POST', '/api/auth/login', { password: 'nope' }, { 'x-desk-client-ip': `10.0.0.${i}` })).status).toBe(401);
    }
    expect((await attacker('POST', '/api/auth/login', { password: PASSWORD })).status).toBe(429);
    // the owner on another IP is unaffected
    expect((await viaProxy(api, '', '203.0.113.9')('POST', '/api/auth/login', { password: PASSWORD })).status).toBe(200);
  }, 30_000);

  it('keeps every record after the API process restarts', async () => {
    const cookie = await login(api);
    const call = viaProxy(api, cookie);
    await call('POST', '/api/trades', { symbol: 'TCS', segment: 'EQ_DELIVERY', side: 'LONG', qty: 2, entryPrice: 3000, entryAt: '2026-10-01T04:00:00.000Z' });
    await call('POST', '/api/watchlist', { symbol: 'INFY', note: 'results' });
    await call('POST', '/api/alerts', { type: 'PRICE_ABOVE', symbol: 'TCS', threshold: 99999, repeat: 'once' });
    await call('POST', '/api/holdings-import', { csv: 'symbol,qty,avg price\nITC,10,400\n', mode: 'append' });
    await call('PUT', '/api/settings', { capital: 777000 });

    await api.close();
    resetCacheMemory();
    api = await startApi(dir); // new process view of the same volume
    const after = viaProxy(api, cookie); // the session cookie survives (stateless, signed)
    const j = await (await after('GET', '/api/journal')).json();
    expect(j.data.rows).toHaveLength(1);
    const w = await (await after('GET', '/api/watchlist')).json();
    expect(w.data.rows.some((r: { item: { symbol: string } }) => r.item.symbol === 'INFY')).toBe(true);
    const a = await (await after('GET', '/api/alerts')).json();
    expect(a.data.alerts).toHaveLength(1);
    const p = await (await after('GET', '/api/portfolio')).json();
    expect(p.data.holdings[0].symbol).toBe('ITC');
    expect((await (await after('GET', '/api/settings')).json()).data.capital).toBe(777000);
  });

  it('serialises concurrent writes through the proxy without losing any', async () => {
    const call = viaProxy(api, await login(api));
    const res = await Promise.all(Array.from({ length: 15 }, (_, i) => call('POST', '/api/trades', { symbol: `T${i}`, segment: 'EQ_DELIVERY', side: 'LONG', qty: 1, entryPrice: 100 + i, entryAt: '2026-10-01T04:00:00.000Z' })));
    expect(res.every((r) => r.status === 201)).toBe(true);
    expect((await (await call('GET', '/api/journal')).json()).data.rows).toHaveLength(15);
  });
});

// ---------------------------------------------------------------------------------------

describe('storage isolation from the TIRE engine volume', () => {
  it('requires a sub-directory and refuses to sit beside engine data', () => {
    expect(storageLayoutProblems('/data/desk', ['lost+found'])).toEqual([]);
    expect(storageLayoutProblems('/data', [])[0]).toMatch(/sub-directory/);
    expect(storageLayoutProblems('/data/desk', ['persistence', 'reference', 'desk']).join(' ')).toMatch(/engine data \(persistence, reference\)/);
  });
});

describe('backup retention on the real file store', () => {
  it('keeps the newest 14 daily snapshots using the encoded on-disk keys', async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'desk-retention-'));
    const kv = new FileKV(dir);
    for (let d = 1; d <= 17; d++) {
      const ctx: Ctx = { cfg: loadConfig({ DESK_OFFLINE: '1' }), kv, now: () => new Date(Date.UTC(2026, 8, d, 21, 0)) };
      await runBackup(ctx);
    }
    const keys = await kv.list('backup/daily-');
    expect(keys).toHaveLength(14);
    expect(keys[0]).toBe('backup/daily-2026-09-05'); // IST dates: 21:00 UTC is the next IST day
    expect(keys.at(-1)).toBe('backup/daily-2026-09-18');
    const files = await fs.readdir(dir);
    expect(files.filter((f) => f.startsWith('backup')).length).toBe(14);
    const ctx: Ctx = { cfg: loadConfig({ DESK_OFFLINE: '1' }), kv, now: () => new Date() };
    expect((await verifyBackup(ctx, keys.at(-1)!)).ok).toBe(true);
  });
});

describe('Netlify edge proxy', () => {
  it('only accepts an https, non-local upstream', () => {
    expect(upstreamOrigin('https://tire-desk-api.up.railway.app/')).toBe('https://tire-desk-api.up.railway.app');
    for (const bad of ['http://x.up.railway.app', 'https://localhost:8788', 'https://127.0.0.1', 'https://10.0.0.5', 'not a url', undefined]) expect(upstreamOrigin(bad as string)).toBeNull();
  });
  it('fails closed when it is not configured', async () => {
    const r = await proxyApi(new Request(`${PUBLIC}/api/health`), {}, undefined);
    expect(r.status).toBe(503);
  });
  it('forwards only allow-listed headers, adds the secret and the visitor IP', async () => {
    let seen: Headers | null = null;
    const fetchImpl = (async (_u: string | URL, init?: RequestInit) => {
      seen = new Headers(init?.headers);
      return new Response('{}', { status: 200, headers: { 'set-cookie': 'a=1; Path=/', 'x-upstream': 'y' } });
    }) as typeof fetch;
    const req = new Request(`${PUBLIC}/api/overview?x=1`, { headers: { cookie: 'c=1', 'x-desk-proxy-key': 'spoofed', 'x-desk-client-ip': '1.2.3.4', authorization: 'Bearer leak', 'x-forwarded-for': '9.9.9.9' } });
    const res = await proxyApi(req, { DESK_API_ORIGIN: 'https://api.example.up.railway.app', DESK_PROXY_SECRET: PROXY_SECRET }, '203.0.113.5', fetchImpl);
    expect(res.status).toBe(200);
    expect(res.headers.get('set-cookie')).toBe('a=1; Path=/');
    const h = seen as unknown as Headers;
    expect(h.get('x-desk-proxy-key')).toBe(PROXY_SECRET);
    expect(h.get('x-desk-client-ip')).toBe('203.0.113.5');
    expect(h.get('cookie')).toBe('c=1');
    expect(h.get('authorization')).toBeNull();
    expect(h.get('x-forwarded-for')).toBeNull();
  });
  it('returns a clear error when Railway is unreachable', async () => {
    const fetchImpl = (async () => { throw new TypeError('connect ECONNREFUSED'); }) as typeof fetch;
    const r = await proxyApi(new Request(`${PUBLIC}/api/overview`), { DESK_API_ORIGIN: 'https://api.example.up.railway.app', DESK_PROXY_SECRET: PROXY_SECRET }, undefined, fetchImpl);
    expect(r.status).toBe(502);
    expect((await r.json()).error.code).toBe('UPSTREAM_UNREACHABLE');
  });
});

// ---------------------------------------------------------------------------------------

function memCtx(now: () => Date, extra: Record<string, string> = {}, fetchImpl?: typeof fetch): Ctx {
  const cfg = loadConfig({ SESSION_SECRET: 's'.repeat(40), APP_PASSWORD_HASH: hash, DESK_OFFLINE: '1', DESK_STORAGE: 'memory', ...extra });
  return { cfg, kv: new MemoryKV(), now, fetchImpl };
}

describe('scheduler (single owner, restart-safe)', () => {
  // Monday 2026-10-12. 08:50 IST = 03:20 UTC.
  const at = (iso: string) => () => new Date(iso);

  it('runs alerts only inside the IST window and at most every 5 minutes', async () => {
    let now = new Date('2026-10-12T04:00:00Z'); // 09:30 IST
    const ctx = memCtx(() => now);
    const s = new Scheduler(ctx, 'test');
    await s.tick();
    await s.drain();
    let st = (await ctx.kv.get<SchedulerState>(U.KEYS.scheduler)).value!;
    expect(st.jobs.alerts?.runs).toBe(1);
    now = new Date('2026-10-12T04:02:00Z');
    await s.tick();
    await s.drain();
    st = (await ctx.kv.get<SchedulerState>(U.KEYS.scheduler)).value!;
    expect(st.jobs.alerts?.runs).toBe(1); // too soon
    now = new Date('2026-10-12T04:05:00Z');
    await s.tick();
    await s.drain();
    st = (await ctx.kv.get<SchedulerState>(U.KEYS.scheduler)).value!;
    expect(st.jobs.alerts?.runs).toBe(2);
    now = new Date('2026-10-12T12:00:00Z'); // 17:30 IST: outside the alert window
    await s.tick();
    await s.drain();
    expect((await ctx.kv.get<SchedulerState>(U.KEYS.scheduler)).value!.jobs.alerts?.runs).toBe(2);
  });

  it('never double-runs a job when two scheduler instances tick together', async () => {
    const ctx = memCtx(at('2026-10-12T04:00:00Z'));
    const a = new Scheduler(ctx, 'a');
    const b = new Scheduler(ctx, 'b');
    await Promise.all([a.tick(), b.tick()]);
    await Promise.all([a.drain(), b.drain()]);
    expect((await ctx.kv.get<SchedulerState>(U.KEYS.scheduler)).value!.jobs.alerts?.runs).toBe(1);
  });

  it('catches up a missed pre-market brief after a restart inside the window, once', async () => {
    const sent: string[] = [];
    const fetchImpl = (async (_u: string | URL, init?: RequestInit) => {
      sent.push(String(init?.body));
      return new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 });
    }) as typeof fetch;
    const ctx = memCtx(at('2026-10-12T03:20:00Z'), { TELEGRAM_BOT_TOKEN: TG_TOKEN, TELEGRAM_CHAT_ID: '99' }, fetchImpl); // 08:50 IST
    // "before the crash": the scheduler state exists but the brief never ran
    await ctx.kv.put(U.KEYS.scheduler, { heartbeatAt: '2026-10-12T02:00:00Z', startedAt: null, instance: 'old', jobs: {} }, '*');
    const restarted = new Scheduler(ctx, 'new');
    await restarted.tick();
    await restarted.drain();
    await restarted.tick();
    await restarted.drain();
    expect(sent.filter((b) => b.includes('Pre-market brief'))).toHaveLength(1);
    const st = (await ctx.kv.get<SchedulerState>(U.KEYS.scheduler)).value!;
    expect(st.jobs['digest-pre']?.lastOk).toBe(true);
    expect(st.jobs['digest-pre']?.lastOkDate).toBe('2026-10-12');
  });

  it('does not send a pre-market brief outside its window (no late surprise after a restart at noon)', async () => {
    const sent: string[] = [];
    const fetchImpl = (async (_u: string | URL, init?: RequestInit) => { sent.push(String(init?.body)); return new Response(JSON.stringify({ ok: true, result: {} })); }) as typeof fetch;
    const ctx = memCtx(at('2026-10-12T06:30:00Z'), { TELEGRAM_BOT_TOKEN: TG_TOKEN, TELEGRAM_CHAT_ID: '99' }, fetchImpl); // 12:00 IST
    const s = new Scheduler(ctx, 'x');
    await s.tick();
    await s.drain();
    expect(sent.filter((b) => b.includes('Pre-market'))).toHaveLength(0);
  });

  it('runs the nightly backup once per IST day and catches up after downtime', async () => {
    let now = new Date('2026-10-12T22:00:00Z'); // 03:30 IST on the 13th
    const ctx = memCtx(() => now);
    const s = new Scheduler(ctx, 'b');
    await s.tick();
    await s.drain();
    expect(await ctx.kv.list('backup/daily-')).toEqual(['backup/daily-2026-10-13']);
    now = new Date('2026-10-12T23:30:00Z');
    await s.tick();
    await s.drain();
    expect((await ctx.kv.get<SchedulerState>(U.KEYS.scheduler)).value!.jobs.backup?.runs).toBe(1);
  });

  it('reports an unhealthy scheduler when the heartbeat stops', async () => {
    let now = new Date('2026-10-12T04:00:00Z');
    const ctx = memCtx(() => now, { DESK_SCHEDULER: 'on' });
    await new Scheduler(ctx, 'h').tick();
    expect((await schedulerStatus(ctx)).healthy).toBe(true);
    now = new Date('2026-10-12T04:10:00Z');
    expect((await schedulerStatus(ctx)).healthy).toBe(false);
  });
});

// ---------------------------------------------------------------------------------------

describe('Telegram client', () => {
  const ok = () => new Response(JSON.stringify({ ok: true, result: { message_id: 1 } }), { status: 200 });
  it('waits and retries once when Telegram asks for a short pause', async () => {
    const calls: number[] = [];
    const sleeps: number[] = [];
    const fetchImpl = (async () => {
      calls.push(1);
      return calls.length === 1 ? new Response(JSON.stringify({ ok: false, error_code: 429, description: 'Too Many Requests: retry after 2', parameters: { retry_after: 2 } }), { status: 429 }) : ok();
    }) as typeof fetch;
    await sendTelegram(TG_TOKEN, '99', 'hi', fetchImpl, async (ms) => { sleeps.push(ms); });
    expect(calls).toHaveLength(2);
    expect(sleeps).toEqual([2000]);
  });
  it('gives up with RATE_LIMITED and the retry_after when the pause is long', async () => {
    const fetchImpl = (async () => new Response(JSON.stringify({ ok: false, error_code: 429, description: 'Too Many Requests', parameters: { retry_after: 120 } }), { status: 429 })) as typeof fetch;
    const err = await sendTelegram(TG_TOKEN, '99', 'hi', fetchImpl).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(TelegramError);
    expect((err as TelegramError).kind).toBe('RATE_LIMITED');
    expect((err as TelegramError).retryAfterSec).toBe(120);
  });
  it('maps bad tokens and bad chats to clear errors that never contain the token', async () => {
    for (const [status, kind] of [[401, 'UNAUTHORIZED'], [400, 'BAD_REQUEST']] as const) {
      const fetchImpl = (async () => new Response(JSON.stringify({ ok: false, description: 'Bad Request: chat not found' }), { status })) as typeof fetch;
      const e = (await sendTelegram(TG_TOKEN, '99', 'hi', fetchImpl).catch((x: unknown) => x)) as TelegramError;
      expect(e.kind).toBe(kind);
      expect(e.message).not.toContain(TG_TOKEN);
      expect(e.message).not.toContain('AAFake');
    }
  });
  it('reports a network failure without leaking the request URL', async () => {
    const fetchImpl = (async (u: string | URL) => { throw new TypeError(`fetch failed for ${String(u)}`); }) as typeof fetch;
    const e = (await sendTelegram(TG_TOKEN, '99', 'hi', fetchImpl).catch((x: unknown) => x)) as TelegramError;
    expect(e.kind).toBe('NETWORK');
    expect(e.message).not.toContain('AAFake');
  });
  it('rejects a malformed token before any network call', async () => {
    const fetchImpl = vi.fn() as unknown as typeof fetch;
    await expect(sendTelegram('not-a-token', '99', 'hi', fetchImpl)).rejects.toMatchObject({ kind: 'NOT_CONFIGURED' });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe('alert delivery: outbox, retries, no duplicates', () => {
  const NOW = new Date('2026-10-12T05:00:00.000Z'); // 10:30 IST, market open
  afterEach(() => vi.useRealTimers());

  it('records a failed send, retries it later exactly once, and stops after success', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
    let now = NOW;
    let failNext = true;
    const bodies: string[] = [];
    const fetchImpl = (async (_u: string | URL, init?: RequestInit) => {
      bodies.push(String(init?.body));
      if (failNext) return new Response(JSON.stringify({ ok: false, description: 'Internal' }), { status: 502 });
      return new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 });
    }) as typeof fetch;
    const ctx = memCtx(() => now, { TELEGRAM_BOT_TOKEN: TG_TOKEN, TELEGRAM_CHAT_ID: '99' }, fetchImpl);
    await U.createAlert(ctx, { type: 'PRICE_ABOVE', symbol: 'RELIANCE', threshold: 1, repeat: 'once', note: '', enabled: true });

    const r1 = await runAlertsTick(ctx);
    expect(r1.fired).toHaveLength(1);
    expect(r1.notified).toBe(0);
    let ev = (await U.listEvents(ctx))[0]!;
    expect(ev.delivered).toBe(false);
    expect(ev.attempts).toBe(1);

    // too early: back-off not elapsed
    now = new Date(NOW.getTime() + 30_000);
    expect((await retryUndelivered(ctx)).retried).toBe(0);

    // after back-off, two overlapping retry passes send it once
    failNext = false;
    now = new Date(NOW.getTime() + 2 * 60_000);
    const [a, b] = await Promise.all([retryUndelivered(ctx), retryUndelivered(ctx)]);
    expect(a.retried + b.retried).toBe(1);
    ev = (await U.listEvents(ctx))[0]!;
    expect(ev.delivered).toBe(true);
    expect(ev.attempts).toBe(2);
    now = new Date(NOW.getTime() + 10 * 60_000);
    expect((await retryUndelivered(ctx)).retried).toBe(0);
    expect(bodies.filter((x) => x.includes('RELIANCE'))).toHaveLength(2); // 1 failed + 1 delivered
  });

  it('stops retrying after 3 attempts and records every failure', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
    let now = NOW;
    const fetchImpl = (async () => new Response(JSON.stringify({ ok: false, description: 'Bad Gateway' }), { status: 502 })) as typeof fetch;
    const ctx = memCtx(() => now, { TELEGRAM_BOT_TOKEN: TG_TOKEN, TELEGRAM_CHAT_ID: '99' }, fetchImpl);
    await U.createAlert(ctx, { type: 'PRICE_ABOVE', symbol: 'SBIN', threshold: 1, repeat: 'once', note: '', enabled: true });
    await runAlertsTick(ctx);
    for (let i = 1; i <= 5; i++) {
      now = new Date(NOW.getTime() + i * 2 * 60_000);
      await retryUndelivered(ctx);
    }
    const ev = (await U.listEvents(ctx))[0]!;
    expect(ev.attempts).toBe(3);
    expect(ev.delivered).toBe(false);
    const log = (await ctx.kv.get<{ failures: unknown[] }>(U.KEYS.telegramLog)).value!;
    expect(log.failures).toHaveLength(3);
  });

  it('does nothing (and logs nothing as failed) when Telegram is not configured', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
    const ctx = memCtx(() => NOW);
    await U.createAlert(ctx, { type: 'PRICE_ABOVE', symbol: 'RELIANCE', threshold: 1, repeat: 'once', note: '', enabled: true });
    const r = await runAlertsTick(ctx);
    expect(r.fired).toHaveLength(1);
    expect((await retryUndelivered(ctx)).retried).toBe(0);
    expect((await U.listEvents(ctx))[0]!.delivered).toBe(false);
  });
});

// ---------------------------------------------------------------------------------------

describe('backup verify and restore', () => {
  it('verifies a snapshot without writing and restores it after snapshotting current data', async () => {
    const ctx = memCtx(() => new Date('2026-10-12T21:00:00Z'));
    await U.createTrade(ctx, { symbol: 'TCS', segment: 'EQ_DELIVERY', side: 'LONG', qty: 1, entryPrice: 100, exitPrice: null, entryAt: '2026-10-01T04:00:00.000Z', exitAt: null, stopLoss: null, target: null, setup: '', tags: [], emotion: null, planFollowed: null, notes: '' });
    const { key } = await runBackup(ctx);
    const v = await verifyBackup(ctx, key);
    expect(v).toMatchObject({ ok: true, counts: { trades: 1 } });

    await U.deleteTrade(ctx, (await U.listTrades(ctx))[0]!.id);
    expect(await U.listTrades(ctx)).toHaveLength(0);
    const r = await restoreBackup(ctx, key);
    expect(r.trades).toBe(1);
    expect(await U.listTrades(ctx)).toHaveLength(1);
    // the pre-restore state (0 trades) was snapshotted first, so the restore is reversible
    const pre = await ctx.kv.get<{ trades: unknown[] }>(r.backupKey);
    expect(pre.value?.trades).toHaveLength(0);
  });
  it('flags a corrupted snapshot instead of restoring it', async () => {
    const ctx = memCtx(() => new Date('2026-10-12T21:00:00Z'));
    await ctx.kv.put('backup/daily-2026-10-12', { version: 1, app: 'tire-desk', exportedAt: 'x', trades: [{ symbol: '' }], holdings: [], watchlist: [], alerts: [], settings: {} }, '*');
    const v = await verifyBackup(ctx, 'backup/daily-2026-10-12');
    expect(v.ok).toBe(false);
    await expect(restoreBackup(ctx, 'backup/daily-2026-10-12')).rejects.toThrow(/Import rejected/);
  });
});

// ---------------------------------------------------------------------------------------

describe('market-data boundary', () => {
  it('reads the F&O ban trade date from the file, never assuming today', () => {
    expect(parseBanTradeDate('Securities in Ban For Trade Date 13-OCT-2026:\n1,SAIL')).toBe('2026-10-13');
    expect(parseBanTradeDate('1,SAIL\n2,RBLBANK')).toBeNull();
  });
  it('turns a hung provider into a TIMEOUT error instead of waiting forever', async () => {
    const fetchImpl = ((_u: string | URL, init?: RequestInit) => new Promise((_, rej) => init?.signal?.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError'))))) as typeof fetch;
    const e = await fetchText('https://query1.finance.yahoo.com/x', { fetchImpl, timeoutMs: 50, retries: 0 }).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ProviderError);
    expect((e as ProviderError).kind).toBe('TIMEOUT');
  });
});
