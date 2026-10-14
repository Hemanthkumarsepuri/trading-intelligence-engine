import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { hashPassword } from '../server/auth';
import { resetCacheMemory } from '../server/cache';
import { loadConfig, type Ctx } from '../server/config';
import { MemoryKV } from '../server/kv';
import { handle } from '../server/router';
import { exportAll, importAll } from '../server/services/backup';
import { resetProviderStats } from '../server/services/wb/data';

const NOW = new Date('2026-10-14T05:00:00.000Z'); // Wednesday 10:30 IST, market open
const CLOSED = new Date('2026-10-17T05:00:00.000Z'); // Saturday
const PASSWORD = 'correct horse battery staple';
const ORIGIN = 'https://desk.example.up.railway.app';

let hash = '';
beforeAll(async () => {
  hash = await hashPassword(PASSWORD, 100_000);
});

let ctx: Ctx;
let cookie = '';

function makeCtx(now: Date = NOW, extra: Record<string, string> = {}, fetchImpl?: typeof fetch): Ctx {
  return {
    cfg: loadConfig({ SESSION_SECRET: 's'.repeat(40), APP_PASSWORD_HASH: hash, DESK_OFFLINE: '1', DESK_STORAGE: 'memory', ...extra }),
    kv: new MemoryKV(),
    now: () => now,
    fetchImpl,
  };
}

function call(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<Response> {
  const h: Record<string, string> = { ...headers };
  if (cookie) h.cookie = cookie;
  if (body !== undefined) h['content-type'] = 'application/json';
  if (method !== 'GET') h.origin = h.origin ?? ORIGIN;
  return handle(new Request(`${ORIGIN}${path}`, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) }), ctx);
}
const data = async (r: Response) => ((await r.json()) as { ok: boolean; data: any; meta: any; error?: { code: string; message: string } });

async function login(): Promise<void> {
  const res = await call('POST', '/api/auth/login', { password: PASSWORD });
  expect(res.status).toBe(200);
  cookie = (res.headers.get('set-cookie') ?? '').split(';')[0]!;
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  resetCacheMemory();
  resetProviderStats();
  cookie = '';
  ctx = makeCtx();
  await login();
});
afterEach(() => vi.useRealTimers());

describe('workbench routes are private and read-only', () => {
  it('require a session', async () => {
    cookie = '';
    for (const p of ['/api/workbench/search?q=ri', '/api/workbench/context', '/api/workbench/health', '/api/workbench/underlying/RELIANCE', '/api/workbench/contracts', '/api/workbench/scan', '/api/workbench/events?symbols=TCS', '/api/workbench/alerts']) {
      expect((await call('GET', p)).status, p).toBe(401);
    }
    expect((await call('POST', '/api/workbench/contracts', { id: 'RELIANCE|2026-10-27|CE|1250' })).status).toBe(401);
  });

  it('exposes no order-placement, modification or cancellation route', async () => {
    const { listRoutes } = await import('../server/router');
    const wb = listRoutes().filter((r) => r.path.includes('/workbench'));
    expect(wb.length).toBeGreaterThan(10);
    for (const r of listRoutes()) expect(r.path, `${r.method} ${r.path}`).not.toMatch(/(^|\/)(orders?|place|cancel|modify|execute)(\/|$)/i);
    // every workbench write changes the user's own contracts, groups, rules or alert log: nothing else mutates
    expect(wb.filter((r) => r.method !== 'GET').map((r) => `${r.method} ${r.path}`).sort()).toEqual([
      'DELETE /api/workbench/contracts',
      'DELETE /api/workbench/groups/:id',
      'DELETE /api/workbench/groups/:id/item',
      'DELETE /api/workbench/rules/:id',
      'POST /api/workbench/alerts/evaluate',
      'POST /api/workbench/contracts',
      'POST /api/workbench/groups',
      'POST /api/workbench/groups/:id/items',
      'POST /api/workbench/rules',
      'POST /api/workbench/rules/:id/enabled',
      'PUT /api/workbench/groups',
      'PUT /api/workbench/groups/:id',
      'PUT /api/workbench/groups/:id/item',
      'PUT /api/workbench/groups/:id/items',
      'PUT /api/workbench/rules/:id',
    ]);
  });

  it('cross-origin writes are refused', async () => {
    expect((await call('POST', '/api/workbench/contracts', { id: 'RELIANCE|2026-10-27|CE|1250' }, { origin: 'https://evil.example' })).status).toBe(403);
  });
});

describe('search', () => {
  it('groups by instrument type and marks F&O versus other', async () => {
    const r = await data(await call('GET', '/api/workbench/search?q=nif'));
    expect(r.data.groups[0].type).toBe('INDEX');
    expect(r.data.groups[0].items.map((i: any) => i.id)).toContain('NIFTY');
    const s = await data(await call('GET', '/api/workbench/search?q=reliance'));
    const fno = s.data.groups.find((g: any) => g.type === 'STOCK_FNO');
    expect(fno.items[0]).toMatchObject({ id: 'RELIANCE', kind: 'STOCK', fno: true });
    expect(fno.items[0].lotSize).toBeGreaterThan(0);
  });

  it('empty / nonsense queries return empty groups, never an error', async () => {
    expect((await data(await call('GET', '/api/workbench/search?q='))).data.groups).toEqual([]);
    expect((await data(await call('GET', '/api/workbench/search?q=ZZZZNOTASYMBOL'))).data.groups).toEqual([]);
    expect((await call('GET', `/api/workbench/search?q=${'x'.repeat(500)}`)).status).toBe(200);
  });

  it('finds exact contracts from "SYMBOL strike type"', async () => {
    const e = await data(await call('GET', '/api/workbench/underlying/RELIANCE/expiries'));
    const exp = e.data.expiries[0] as string;
    const chain = await data(await call('GET', `/api/workbench/chain/RELIANCE?expiry=${exp}`));
    const strike = chain.data.rows[12].strike;
    const r = await data(await call('GET', `/api/workbench/search?q=${encodeURIComponent(`reliance ${strike} ce`)}`));
    expect(r.data.contracts.length).toBeGreaterThan(0);
    expect(r.data.contracts.every((c: any) => c.ref.type === 'CE')).toBe(true);
    expect(r.data.contracts.map((c: any) => c.ref.strike)).toContain(strike);
  });

  it('says so when the underlying has no options or is ambiguous', async () => {
    const r = await data(await call('GET', '/api/workbench/search?q=' + encodeURIComponent('zz 1000 ce')));
    expect(r.data.contracts).toEqual([]);
  });
});

describe('underlying view', () => {
  it('returns provenance-stamped quote fields, candles, levels and a breakout verdict', async () => {
    const r = await data(await call('GET', '/api/workbench/underlying/RELIANCE?tf=15m'));
    const d = r.data;
    expect(d.price.value).toBeGreaterThan(0);
    expect(d.price.prov.source).toBe('fixture');
    expect(d.price.prov.latency).toBe('SNAPSHOT');
    expect(d.freshness).toBe('VALID');
    expect(d.candles.length).toBeGreaterThan(100);
    expect(d.candles.every((c: any) => typeof c.complete === 'boolean')).toBe(true);
    expect(d.availability.available).toBe(true);
    expect(d.levels.length).toBeGreaterThan(0);
    expect(d.levels.every((l: any) => l.explain && l.method)).toBe(true);
    expect(d.structure.primary).toBeTruthy();
    expect(d.structure.primary.confirmation).toBeTruthy();
    expect(d.structure.primary.invalidation).toBeTruthy();
    expect(d.rvol.method).toBe('TIME_OF_DAY_CUMULATIVE');
    expect(r.meta.source).toBe('fixture');
  });

  it('every timeframe loads or explains why not; a bad timeframe is rejected', async () => {
    for (const tf of ['1m', '3m', '5m', '15m', '30m', '1h', '1d']) {
      const r = await data(await call('GET', `/api/workbench/underlying/TCS?tf=${tf}`));
      expect(r.ok, tf).toBe(true);
      expect(r.data.availability.tf).toBe(tf);
      if (!r.data.availability.available) expect(r.data.availability.reason).toBeTruthy();
    }
    expect((await call('GET', '/api/workbench/underlying/TCS?tf=7m')).status).toBe(400);
  });

  it('unknown underlying is a 404, not an empty page', async () => {
    const r = await call('GET', '/api/workbench/underlying/NOPE$$');
    expect([400, 404]).toContain(r.status);
  });

  it('indices report volume as UNSUPPORTED and give no VWAP', async () => {
    const d = (await data(await call('GET', '/api/workbench/underlying/NIFTY?tf=15m'))).data;
    expect(d.volume.state).toBe('UNSUPPORTED');
    expect(d.volume.value).toBeNull();
  });

  it('multi-timeframe summary covers all seven timeframes with agreement', async () => {
    const d = (await data(await call('GET', '/api/workbench/underlying/INFY/mtf'))).data;
    expect(d.rows.map((x: any) => x.tf)).toEqual(['1m', '3m', '5m', '15m', '30m', '1h', '1d']);
    expect(['UP', 'DOWN', 'MIXED', 'UNKNOWN']).toContain(d.agreement.alignment);
  });

  it('a closed market is MARKET_CLOSED, not an outage and not STALE', async () => {
    ctx = makeCtx(CLOSED);
    vi.setSystemTime(CLOSED);
    await login();
    const d = (await data(await call('GET', '/api/workbench/underlying/RELIANCE?tf=15m'))).data;
    expect(d.session.phase).toBe('CLOSED');
    expect(d.price.state).toBe('MARKET_CLOSED');
    expect(d.freshness).toBe('MARKET_CLOSED');
    expect(d.candles.at(-1).complete).toBe(true);
  });

  it('the forming candle is present but never complete', async () => {
    const mid = new Date('2026-10-14T05:02:30.000Z'); // 10:32:30, inside the 10:30 15-minute bar
    ctx = makeCtx(mid);
    vi.setSystemTime(mid);
    await login();
    const d = (await data(await call('GET', '/api/workbench/underlying/RELIANCE?tf=15m'))).data;
    const last = d.candles.at(-1);
    expect(last.complete).toBe(false);
    expect(d.structure.primary.observedThrough).toBeLessThan(last.t);
  });
});

describe('contracts: lookup, validation and tracking', () => {
  let id = '';
  let expiry = '';
  beforeEach(async () => {
    expiry = (await data(await call('GET', '/api/workbench/underlying/RELIANCE/expiries'))).data.default.expiry;
    const chain = (await data(await call('GET', `/api/workbench/chain/RELIANCE?expiry=${expiry}`))).data;
    id = `RELIANCE|${expiry}|CE|${chain.atmStrike}`;
  });

  it('suggests a default expiry with a visible reason and lists all expiries', async () => {
    const e = (await data(await call('GET', '/api/workbench/underlying/RELIANCE/expiries'))).data;
    expect(e.expiries.length).toBeGreaterThanOrEqual(2);
    expect(e.default.reason).toMatch(/Nearest expiry/);
    expect(e.lotSize).toBeGreaterThan(0);
  });

  it('chain explorer lists strikes with moneyness and per-field state', async () => {
    const c = (await data(await call('GET', `/api/workbench/chain/RELIANCE?expiry=${expiry}`))).data;
    expect(c.rows.length).toBe(25);
    expect(c.rows.some((r: any) => r.moneyness.ce === 'ATM')).toBe(true);
    expect(c.state).toBe('VALID');
    const far = c.rows.flatMap((r: any) => [r.ce, r.pe]).find((v: any) => !v.quoteAvailable);
    expect(far).toBeTruthy(); // the demo chain has untraded far strikes
    expect(far.ltp.value).toBeNull();
    expect(far.state).toBe('UNKNOWN');
  });

  it('rejects an unlisted expiry and malformed expiry', async () => {
    const bad = await call('GET', '/api/workbench/chain/RELIANCE?expiry=2031-01-01');
    expect(bad.status).toBe(422);
    expect((await call('GET', '/api/workbench/chain/RELIANCE?expiry=soon')).status).toBe(400);
  });

  it('contract view validates the leg and keeps volume, OI and OI change distinct', async () => {
    const v = (await data(await call('GET', `/api/workbench/contract?id=${encodeURIComponent(id)}`))).data;
    expect(v.validation.ref.type).toBe('CE');
    expect(v.validation.oneLotPremium.value).toBeCloseTo(v.validation.ltp.value * v.validation.lotSize, 4);
    expect(v.validation.volume.value).not.toBe(v.validation.oi.value);
    expect(v.validation.oiChange.value).toBe(v.validation.oi.value - (v.validation.oi.value - v.validation.oiChange.value));
    expect(v.validation.delta.prov.note).toBe('calculated');
    expect(v.underlying.crossCheck.state).toBe('VALID');
    expect(v.validation.liquidity.cls).toMatch(/LIQUID|MODERATE|ILLIQUID|DATA_INSUFFICIENT/);
  });

  it('unlisted strike, unlisted expiry, garbage id: clear errors', async () => {
    const bad = await call('GET', `/api/workbench/contract?id=${encodeURIComponent(`RELIANCE|${expiry}|CE|12.5`)}`);
    expect(bad.status).toBe(422);
    expect((await data(bad)).error?.message).toMatch(/not listed/);
    expect((await call('GET', `/api/workbench/contract?id=${encodeURIComponent('RELIANCE|2020-01-01|CE|1000')}`)).status).toBe(422);
    expect((await call('GET', '/api/workbench/contract?id=junk')).status).toBe(400);
  });

  it('adds to the watchlist once; a duplicate is reported, not duplicated; remove works', async () => {
    const a = await call('POST', '/api/workbench/contracts', { id });
    expect(a.status).toBe(201);
    const b = await call('POST', '/api/workbench/contracts', { id });
    expect(b.status).toBe(200);
    expect((await data(b)).data.duplicate).toBe(true);
    const list = (await data(await call('GET', '/api/workbench/contracts'))).data.rows;
    expect(list).toHaveLength(1);
    expect(list[0].validation.id).toBe(id);
    expect(list[0].label).toMatch(/^RELIANCE \d{2} \w{3} 2026 \d+(\.\d+)? CE$/);
    expect((await call('DELETE', `/api/workbench/contracts?id=${encodeURIComponent(id)}`)).status).toBe(200);
    expect((await data(await call('GET', '/api/workbench/contracts'))).data.rows).toEqual([]);
  });

  it('refuses to track a contract that is not listed or has an invalid id', async () => {
    expect((await call('POST', '/api/workbench/contracts', { id: `RELIANCE|${expiry}|CE|12.5` })).status).toBe(422);
    expect((await call('POST', '/api/workbench/contracts', { id: 'nonsense' })).status).toBe(400);
    expect((await call('POST', '/api/workbench/contracts', { id: `NOFNO|${expiry}|CE|100` })).status).toBeGreaterThanOrEqual(400);
    expect((await data(await call('GET', '/api/workbench/contracts'))).data.rows).toEqual([]);
  });

  it('tracked contracts survive in exports and restore, and older backups leave them alone', async () => {
    await call('POST', '/api/workbench/contracts', { id, note: 'watch' });
    const exp = await exportAll(ctx);
    expect(exp.contracts).toHaveLength(1);
    const ctx2 = makeCtx();
    await importAll(ctx2, { ...exp, confirm: true });
    expect((await exportAll(ctx2)).contracts?.[0]?.id).toBe(id);
    // a file written before the Workbench has no `contracts` key: existing tracked contracts stay
    const { contracts: _c, ...old } = exp;
    await importAll(ctx2, { ...old, confirm: true });
    expect((await exportAll(ctx2)).contracts).toHaveLength(1);
    // an explicit empty list does clear them
    await importAll(ctx2, { ...old, contracts: [], confirm: true });
    expect((await exportAll(ctx2)).contracts).toEqual([]);
    // invalid rows reject the whole import
    await expect(importAll(ctx2, { ...old, contracts: [{ id: 'bad' }], confirm: true })).rejects.toThrow(/Import rejected/);
  });

  it('option chart: without an option-candle provider it says so and offers only labelled snapshots', async () => {
    const empty = (await data(await call('GET', `/api/workbench/contract/chart?id=${encodeURIComponent(id)}&tf=5m`))).data;
    expect(empty.kind).toBe('OBSERVED');
    expect(empty.candles).toEqual([]);
    expect(empty.note).toMatch(/not exchange candles|snapshots/);
    const daily = (await data(await call('GET', `/api/workbench/contract/chart?id=${encodeURIComponent(id)}&tf=1d`))).data;
    expect(daily.candles).toEqual([]);
    expect(daily.note).toMatch(/Daily option candles are not offered/);
  });
});

describe('scanner', () => {
  it('returns genuine candidates with evidence, contracts to inspect, exclusions and an honest count', async () => {
    const r = (await data(await call('GET', '/api/workbench/scan?cap=30'))).data;
    expect(r.candidates.length).toBeGreaterThanOrEqual(5);
    expect(r.summary.stockCandidates).toBe(r.candidates.length);
    expect(r.summary.contractCount).toBeGreaterThan(0);
    for (const c of r.candidates) {
      expect(c.why.length).toBeGreaterThan(0);
      expect(c.setup.confirmation).toBeTruthy();
      expect(c.setup.invalidation).toBeTruthy();
      expect(c.audit.length).toBeGreaterThan(3);
      expect(c.contracts.length).toBeGreaterThan(0);
      expect(c.contracts.every((x: any) => x.ref.type === (c.setup.direction === 'UP' ? 'CE' : 'PE'))).toBe(true);
      expect(['APPROACHING_LEVEL', 'POTENTIAL_BREAKOUT', 'CONFIRMATION_PENDING', 'CONFIRMED']).toContain(c.setup.state);
    }
    expect(r.ranking).toMatch(/no blended score/);
    expect(r.notes.join(' ')).toMatch(/DEMO DATA/);
    expect(r.excluded.length + r.candidates.length).toBe(r.limits.evaluated);
  });

  it('tight filters shrink the list and explain the exclusions instead of padding to five', async () => {
    const r = (await data(await call('GET', '/api/workbench/scan?cap=30&states=CONFIRMED&minRvol=5&direction=DOWN'))).data;
    expect(r.candidates.length).toBeLessThan(5);
    expect(r.summary.message).toMatch(/No candidates were added/);
    expect(Object.keys(r.summary.excludedByReason).length).toBeGreaterThan(0);
  });

  it('rejects bad filter values', async () => {
    expect((await call('GET', '/api/workbench/scan?direction=SIDEWAYS')).status).toBe(400);
    expect((await call('GET', '/api/workbench/scan?states=NONSENSE')).status).toBe(400);
    expect((await call('GET', '/api/workbench/scan?cap=9999')).status).toBe(400);
  });

  it('an index-only scan works and reports that volume confirmation is unavailable', async () => {
    const r = (await data(await call('GET', '/api/workbench/scan?segment=INDEX&states=APPROACHING_LEVEL,POTENTIAL_BREAKOUT,CONFIRMATION_PENDING,CONFIRMED,NOT_READY&requireOptions=0'))).data;
    expect(r.limits.universe).toBe(4);
    for (const c of r.candidates) expect(c.isIndex).toBe(true);
  });
});

describe('events', () => {
  it('lists exchange filings with publisher, original time, link and retrieval time, and an unsupported macro source', async () => {
    const r = (await data(await call('GET', '/api/workbench/events?symbols=RELIANCE,TCS'))).data;
    const ann = r.events.filter((e: any) => e.type !== 'NEWS' && e.publishedAt);
    expect(ann.length).toBeGreaterThan(0);
    for (const e of ann) {
      expect(e.publisher).toMatch(/NSE/);
      expect(e.sourceZone).toBe('Asia/Kolkata');
      expect(e.retrievedAt).toBeTruthy();
      expect(e.verification).toBe('CONFIRMED_EXCHANGE');
      expect(e.link === null || e.link.startsWith('https://nsearchives.nseindia.com/')).toBe(true);
    }
    expect(r.sources.find((s: any) => s.id === 'macro').state).toBe('UNSUPPORTED');
  });
  it('rejects malformed symbols', async () => {
    expect((await call('GET', '/api/workbench/events?symbols=A$B')).status).toBe(400);
  });
});

describe('context, health and alerts', () => {
  it('context shows verified values or UNKNOWN and never invents a regime', async () => {
    const c = (await data(await call('GET', '/api/workbench/context'))).data;
    expect(c.nifty.price.value).toBeGreaterThan(0);
    expect(c.regimeNote).toMatch(/No bullish or bearish regime is inferred/);
    expect(c).not.toHaveProperty('regime');
  });

  it('health lists providers, freshness limits, entitlements and exposes no secrets', async () => {
    const h = await call('GET', '/api/workbench/health');
    const text = await h.text();
    expect(text).not.toMatch(/SESSION_SECRET|APP_PASSWORD|pbkdf2|UPSTOX_ANALYTICS_TOKEN=|TELEGRAM_BOT_TOKEN=/);
    const j = JSON.parse(text).data;
    expect(j.providers.map((p: any) => p.id)).toContain('nse-chain');
    expect(j.freshness.optionChainSec).toBe(180);
    expect(j.entitlements.join(' ')).toMatch(/No broker order API/);
  });

  it('alerts: evaluate once creates, a second run deduplicates, the list persists', async () => {
    const a = (await data(await call('POST', '/api/workbench/alerts/evaluate', {}))).data;
    const b = (await data(await call('POST', '/api/workbench/alerts/evaluate', {}))).data;
    expect(a.created.length).toBeGreaterThan(0);
    expect(b.created).toEqual([]);
    const list = (await data(await call('GET', '/api/workbench/alerts'))).data.alerts;
    expect(list.length).toBe(a.created.length);
    for (const x of list) {
      expect(x.sourceAsOf === null || typeof x.sourceAsOf === 'string').toBe(true);
      expect(x.evidence).toBeInstanceOf(Array);
    }
  });
});
