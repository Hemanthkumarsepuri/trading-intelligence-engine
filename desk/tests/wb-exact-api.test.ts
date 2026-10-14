// Exact-contract workflow against faked NSE/Yahoo endpoints for ANGELONE (no network, no real data).
// The contracts below exist only in this fake chain; nothing here claims they are listed today.

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { hashPassword } from '../server/auth';
import { resetCacheMemory } from '../server/cache';
import { loadConfig, type Ctx } from '../server/config';
import { MemoryKV } from '../server/kv';
import { handle } from '../server/router';
import { exportAll, importAll } from '../server/services/backup';
import { resetProviderStats } from '../server/services/wb/data';

const OPEN = new Date('2026-10-14T05:00:00.000Z');
const PASSWORD = 'correct horse battery staple';
const ORIGIN = 'https://desk.example.up.railway.app';
const C300 = 'ANGELONE|2026-10-27|CE|300';
const C305 = 'ANGELONE|2026-10-27|CE|305';

let hash = '';
beforeAll(async () => {
  hash = await hashPassword(PASSWORD, 100_000);
});

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'content-type': 'application/json' } });
const lotsCsv = () => ['UNDERLYING,SYMBOL,LOT', 'Angel One,ANGELONE,250', 'Nifty,NIFTY,75', ...Array.from({ length: 60 }, (_, i) => `Name ${i},SYM${i},${100 + i}`)].join('\n');

function legFor(strike: number, type: 'CE' | 'PE', over: Record<string, unknown> = {}) {
  return { strikePrice: strike, lastPrice: type === 'CE' ? 14 - (strike - 300) * 0.5 : 9, openInterest: 5000, changeinOpenInterest: 300, totalTradedVolume: 800, impliedVolatility: 41, buyPrice1: 13.9, sellPrice1: 14.1, buyQuantity1: 5000, sellQuantity1: 5000, identifier: `OPTSTKANGELONE27-10-2026${type}${strike}.00`, ...over };
}

let leg305: Record<string, unknown> = {};
const chain = (ts = '14-Oct-2026 10:29:40') => ({
  records: {
    timestamp: ts,
    underlyingValue: 295,
    expiryDates: ['27-Oct-2026'],
    data: [300, 305, 310].map((k) => ({ strikePrice: k, expiryDates: '27-Oct-2026', CE: legFor(k, 'CE', k === 305 ? leg305 : {}), PE: legFor(k, 'PE') })),
  },
});
const yahoo = (price: number, t: number) => ({ chart: { result: [{ meta: { symbol: 'ANGELONE.NS', regularMarketPrice: price, regularMarketTime: t, previousClose: 290, regularMarketDayHigh: price + 2, regularMarketDayLow: price - 4 }, timestamp: [t - 86400, t], indicators: { quote: [{ open: [290, 291], high: [296, 297], low: [288, 289], close: [290, price], volume: [1e6, 9e5] }] } }], error: null } });

let ctx: Ctx;
let cookie = '';
let chainTs = '14-Oct-2026 10:29:40';
let expiries = ['27-Oct-2026', '24-Nov-2026'];

function makeCtx(kv: Ctx['kv'] = new MemoryKV()): Ctx {
  const fetchImpl = vi.fn(async (input: unknown) => {
    const url = String(input);
    if (url === 'https://www.nseindia.com/') return new Response('<html></html>', { status: 200, headers: { 'set-cookie': 'nsit=abc; Path=/' } });
    if (url.includes('option-chain-contract-info')) return json({ expiryDates: expiries, strikePrice: [300, 305, 310] });
    if (url.includes('option-chain-v3')) return json(chain(chainTs));
    if (url.includes('fo_mktlots.csv')) return new Response(lotsCsv(), { status: 200 });
    if (url.includes('finance.yahoo.com') && url.includes('range=5d&interval=1d')) return json(yahoo(295, Math.floor(OPEN.getTime() / 1000) - 60));
    return new Response('not found', { status: 404 });
  }) as unknown as typeof fetch;
  return { cfg: loadConfig({ SESSION_SECRET: 's'.repeat(40), APP_PASSWORD_HASH: hash, DESK_STORAGE: 'memory' }), kv, now: () => new Date(Date.now()), fetchImpl };
}

async function login() {
  const res = await handle(new Request(`${ORIGIN}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json', origin: ORIGIN }, body: JSON.stringify({ password: PASSWORD }) }), ctx);
  cookie = (res.headers.get('set-cookie') ?? '').split(';')[0]!;
}
async function call(method: string, path: string, body?: unknown, headers: Record<string, string> = {}): Promise<Response> {
  const h: Record<string, string> = { cookie, ...headers };
  if (body !== undefined) h['content-type'] = 'application/json';
  if (method !== 'GET') h.origin = h.origin ?? ORIGIN;
  return handle(new Request(`${ORIGIN}${path}`, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) }), ctx);
}
const b = async (r: Response) => (await r.json()) as { ok: boolean; data: any; meta: any; error?: { code: string; message: string } };
const resolve = async (q: string) => (await b(await call('GET', `/api/workbench/resolve?q=${encodeURIComponent(q)}`))).data;

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(OPEN);
  resetCacheMemory();
  resetProviderStats();
  chainTs = '14-Oct-2026 10:29:40';
  expiries = ['27-Oct-2026', '24-Nov-2026'];
  leg305 = {};
  ctx = makeCtx();
  await login();
});
afterEach(() => vi.useRealTimers());

describe('exact instrument resolution', () => {
  it('"ANGELONE 300 CE OCT" resolves to the provider\'s exact contract with its real identity', async () => {
    const r = await resolve('ANGELONE 300 CE OCT');
    expect(r.status).toBe('RESOLVED');
    expect(r.parsed).toEqual({ underlying: 'ANGELONE', strike: 300, type: 'CE', expiry: '2026-10-27' });
    expect(r.contract.id).toBe(C300);
    expect(r.contract.label).toBe('ANGELONE 27 Oct 2026 300 CE');
    expect(r.contract.identity).toMatchObject({ canonicalSymbol: 'OPTSTKANGELONE27-10-2026CE300.00', lotSize: 250, source: 'nse', expiry: '2026-10-27', strike: 300, type: 'CE' });
    expect(r.notes.join(' ')).toMatch(/year was not typed/);
  });

  it('305 CE resolves to the SAME expiry as 300 CE: two strikes, one expiry, different identities', async () => {
    const [a, c] = [await resolve('ANGELONE 300 CE OCT'), await resolve('ANGELONE 305 CE OCT')];
    expect(a.contract.ref.expiry).toBe(c.contract.ref.expiry);
    expect(a.contract.identity.canonicalSymbol).not.toBe(c.contract.identity.canonicalSymbol);
    expect(c.contract.identity.canonicalSymbol).toBe('OPTSTKANGELONE27-10-2026CE305.00');
  });

  it('CE and PE of the same strike are different contracts', async () => {
    const ce = await resolve('ANGELONE 300 CE OCT');
    const pe = await resolve('ANGELONE 300 PE OCT');
    expect(pe.contract.id).toBe('ANGELONE|2026-10-27|PE|300');
    expect(pe.contract.id).not.toBe(ce.contract.id);
    expect(pe.contract.identity.canonicalSymbol).toBe('OPTSTKANGELONE27-10-2026PE300.00');
  });

  it('a missing expiry is a choice among the LISTED expiries, never guessed', async () => {
    const r = await resolve('ANGELONE 300 PE');
    expect(r.status).toBe('NEEDS_SELECTION');
    expect(r.field).toBe('expiry');
    expect(r.options.map((o: any) => o.value)).toEqual(['2026-10-27', '2026-11-24']);
    expect(r.parsed).toMatchObject({ underlying: 'ANGELONE', strike: 300, type: 'PE', expiry: null });
  });

  it('missing type and missing strike ask for a selection; strikes offered come from the live chain', async () => {
    expect((await resolve('ANGELONE 300 OCT')).field).toBe('type');
    const s = await resolve('ANGELONE CE OCT');
    expect(s.field).toBe('strike');
    expect(s.options.map((o: any) => o.value)).toEqual(['300', '305', '310']);
  });

  it('several matching expiries ask for the exact one', async () => {
    expiries = ['27-Oct-2026', '20-Oct-2026', '24-Nov-2026'];
    const r = await resolve('ANGELONE 300 CE OCT');
    expect(r.status).toBe('NEEDS_SELECTION');
    expect(r.field).toBe('expiry');
    expect(r.options.map((o: any) => o.value)).toEqual(['2026-10-20', '2026-10-27']);
  });

  it('rejects an unlisted strike with the nearest listed strikes', async () => {
    const r = await resolve('ANGELONE 999 CE OCT');
    expect(r.status).toBe('REJECTED');
    expect(r.reason).toMatch(/Strike 999 is not listed.*300, 305, 310/);
    expect(r.contract).toBeUndefined();
  });

  it('rejects an unlisted expiry month, an expired date, an unknown symbol and a non-F&O stock', async () => {
    expect((await resolve('ANGELONE 300 CE SEP')).reason).toMatch(/No listed expiry matches/);
    const expired = await b(await call('GET', '/api/workbench/resolve?u=ANGELONE&expiry=2026-10-01&type=CE&strike=300'));
    expect(expired.data).toMatchObject({ status: 'REJECTED', reason: expect.stringMatching(/already expired/) });
    expect((await resolve('ZZZZQ 300 CE OCT')).reason).toMatch(/not a known NSE symbol/);
    expect((await resolve('ITC 700 CE OCT')).reason).toMatch(/not in the F&O segment/);
  });

  it('an index query resolves the underlying and asks for the expiry', async () => {
    const r = await resolve('NIFTY 25000 CE');
    expect(r.parsed).toMatchObject({ underlying: 'NIFTY', strike: 25000, type: 'CE' });
    expect(r.status).toBe('NEEDS_SELECTION');
    expect(r.field).toBe('expiry');
  });

  it('refuses a provider identifier that disagrees with the requested contract', async () => {
    leg305 = { identifier: 'OPTSTKANGELONE27-10-2026CE300.00' };
    const r = await resolve('ANGELONE 305 CE OCT');
    expect(r.status).toBe('REJECTED');
    expect(r.reason).toMatch(/does not match/);
  });

  it('empty and oversize queries are handled', async () => {
    expect((await resolve('   ')).status).toBe('REJECTED');
    expect((await call('GET', `/api/workbench/resolve?q=${'x'.repeat(200)}`)).status).toBe(400);
    expect((await call('GET', '/api/workbench/resolve')).status).toBe(400);
  });
});

describe('custom groups', () => {
  const group = async (name = 'Angel One — October Calls') => (await b(await call('POST', '/api/workbench/groups', { name, note: 'earnings week' }))).data;
  const add = (gid: string, id: string, note = '') => call('POST', `/api/workbench/groups/${gid}/items`, { id, note });

  it('creates a group, adds 300 CE and 305 CE (same expiry), stores the verified identity, detects duplicates', async () => {
    const g = await group();
    expect(g.name).toBe('Angel One — October Calls');
    const a = await add(g.id, C300, 'primary');
    expect(a.status).toBe(201);
    expect((await b(a)).data.item.identity).toMatchObject({ canonicalSymbol: 'OPTSTKANGELONE27-10-2026CE300.00', lotSize: 250, source: 'nse' });
    const dup = await add(g.id, C300);
    expect(dup.status).toBe(200);
    expect((await b(dup)).data.duplicate).toBe(true);
    expect((await add(g.id, C305)).status).toBe(201);
    expect((await add(g.id, 'ANGELONE|2026-10-27|PE|300')).status).toBe(201);
    const v = (await b(await call('GET', '/api/workbench/groups'))).data;
    expect(v.groups[0].items.map((x: any) => x.item.id)).toEqual([C300, C305, 'ANGELONE|2026-10-27|PE|300']);
    expect(v.groups[0].items[0].row.validation.state).toBe('VALID');
    expect(v.groups[0].items[0].item.note).toBe('primary');
  });

  it('never stores a contract the provider does not list', async () => {
    const g = await group();
    for (const id of ['ANGELONE|2026-10-27|CE|999', 'ANGELONE|2026-12-29|CE|300', 'ANGELONE|2026-10-01|CE|300', 'NOFNO|2026-10-27|CE|300']) {
      const r = await add(g.id, id);
      expect(r.status, id).toBe(422);
    }
    expect((await add(g.id, 'garbage')).status).toBe(400);
    expect((await b(await call('GET', '/api/workbench/groups'))).data.groups[0].items).toEqual([]);
  });

  it('group names are unique (case/space-insensitive); rename, note and delete work', async () => {
    const g = await group();
    expect((await call('POST', '/api/workbench/groups', { name: '  angel one — OCTOBER calls ' })).status).toBe(409);
    expect((await call('POST', '/api/workbench/groups', { name: '   ' })).status).toBe(400);
    const g2 = await group('Puts');
    expect((await call('PUT', `/api/workbench/groups/${g2.id}`, { name: 'Angel One — October Calls' })).status).toBe(409);
    const renamed = await b(await call('PUT', `/api/workbench/groups/${g2.id}`, { name: 'Angel One — Puts', note: 'hedges' }));
    expect(renamed.data).toMatchObject({ name: 'Angel One — Puts', note: 'hedges' });
    expect((await call('DELETE', `/api/workbench/groups/${g2.id}`)).status).toBe(200);
    expect((await call('DELETE', `/api/workbench/groups/${g2.id}`)).status).toBe(404);
    expect((await b(await call('GET', '/api/workbench/groups'))).data.groups.map((x: any) => x.name)).toEqual([g.name]);
  });

  it('reorders groups and contracts and persists the order', async () => {
    const a = await group('A');
    const c = await group('C');
    const d = await group('D');
    await call('PUT', '/api/workbench/groups', { order: [d.id, a.id, c.id] });
    await add(a.id, C300);
    await add(a.id, C305);
    await call('PUT', `/api/workbench/groups/${a.id}/items`, { order: [C305, C300] });
    const v = (await b(await call('GET', '/api/workbench/groups'))).data;
    expect(v.groups.map((x: any) => x.name)).toEqual(['D', 'A', 'C']);
    expect(v.groups[1].items.map((x: any) => x.item.id)).toEqual([C305, C300]);
    // a new request through a fresh handler on the same storage sees the same order (persistence)
    const again = makeCtx(ctx.kv);
    const res = await handle(new Request(`${ORIGIN}/api/workbench/groups`, { headers: { cookie } }), again);
    expect(((await res.json()) as any).data.groups.map((x: any) => x.name)).toEqual(['D', 'A', 'C']);
  });

  it('contract-level and group-level notes and saved chart preferences, validated', async () => {
    const g = await group();
    await add(g.id, C300);
    const prefs = { tf: '5m', overlays: { vwap: true, ema20: false, ema50: true, bb: true }, panes: { rsi: true, macd: false, atr: true }, levelGroups: { swing: true, session: false, opening: true, rolling: false }, showSetup: true, showEvents: false, view: 'option' };
    expect((await b(await call('PUT', `/api/workbench/groups/${g.id}`, { prefs }))).data.prefs).toEqual(prefs);
    const item = await b(await call('PUT', `/api/workbench/groups/${g.id}/item?contract=${encodeURIComponent(C300)}`, { note: 'watch spread', prefs: { tf: '1h', panes: { rsi: false, macd: true, atr: false } } }));
    expect(item.data).toMatchObject({ note: 'watch spread', prefs: { tf: '1h' } });
    expect((await call('PUT', `/api/workbench/groups/${g.id}`, { prefs: { tf: '7m' } })).status).toBe(400);
    expect((await call('PUT', `/api/workbench/groups/${g.id}`, { prefs: { surprise: true } })).status).toBe(400);
    const cleared = await b(await call('PUT', `/api/workbench/groups/${g.id}`, { prefs: null }));
    expect(cleared.data.prefs).toBeUndefined();
    const v = (await b(await call('GET', '/api/workbench/groups'))).data;
    expect(v.groups[0].items[0].item.prefs.tf).toBe('1h');
  });

  it('removes a contract from a group without touching the others', async () => {
    const g = await group();
    await add(g.id, C300);
    await add(g.id, C305);
    expect((await call('DELETE', `/api/workbench/groups/${g.id}/item?contract=${encodeURIComponent(C300)}`)).status).toBe(200);
    expect((await b(await call('GET', '/api/workbench/groups'))).data.groups[0].items.map((x: any) => x.item.id)).toEqual([C305]);
  });

  it('adding does not require the scanner, and works with the contract in the old flat list too', async () => {
    expect((await call('POST', '/api/workbench/contracts', { id: C300 })).status).toBe(201);
    const g = await group();
    expect((await add(g.id, C300)).status).toBe(201);
    const v = (await b(await call('GET', '/api/workbench/groups'))).data;
    expect(v.ungrouped).toHaveLength(1);
    expect(v.groups[0].items).toHaveLength(1);
  });

  it('an expired contract stays visible, explained, and counted', async () => {
    const g = await group();
    await add(g.id, C300);
    vi.setSystemTime(new Date('2026-10-28T05:00:00Z'));
    resetCacheMemory();
    const v = (await b(await call('GET', '/api/workbench/groups'))).data;
    expect(v.groups[0].expiredCount).toBe(1);
    expect(v.groups[0].items[0].row.problem).toBe('This contract has expired.');
    expect(v.groups[0].items[0].row.validation).toBeNull();
    expect((await call('DELETE', `/api/workbench/groups/${g.id}/item?contract=${encodeURIComponent(C300)}`)).status).toBe(200);
  });

  it('survives export and import, including identity and preferences; older files leave groups alone', async () => {
    const g = await group();
    await add(g.id, C300, 'n1');
    await call('PUT', `/api/workbench/groups/${g.id}`, { prefs: { tf: '15m' } });
    await call('POST', '/api/workbench/rules', { contractId: C300, kind: 'PREMIUM_CROSS', params: { price: 15, direction: 'ABOVE' } });
    const exp = await exportAll(ctx);
    expect(exp.contractGroups?.[0]?.items[0]?.identity?.canonicalSymbol).toBe('OPTSTKANGELONE27-10-2026CE300.00');
    expect(exp.contractRules).toHaveLength(1);
    const ctx2 = makeCtx();
    await importAll(ctx2, { ...exp, confirm: true });
    const back = await exportAll(ctx2);
    expect(back.contractGroups?.[0]).toMatchObject({ name: g.name, note: 'earnings week', prefs: { tf: '15m' } });
    expect(back.contractGroups?.[0]?.items[0]).toMatchObject({ id: C300, note: 'n1' });
    expect(back.contractRules?.[0]?.kind).toBe('PREMIUM_CROSS');
    const { contractGroups: _g, contractRules: _r, ...old } = exp;
    await importAll(ctx2, { ...old, confirm: true });
    expect((await exportAll(ctx2)).contractGroups).toHaveLength(1);
    await importAll(ctx2, { ...old, contractGroups: [], confirm: true });
    expect((await exportAll(ctx2)).contractGroups).toEqual([]);
    await expect(importAll(ctx2, { ...old, contractGroups: [{ name: 'x', items: [{ id: 'bad' }] }], confirm: true })).rejects.toThrow(/Import rejected/);
    await expect(importAll(ctx2, { ...old, contractRules: [{ contractId: C300, kind: 'PREMIUM_CROSS', params: { price: -1, direction: 'ABOVE' } }], confirm: true })).rejects.toThrow(/Import rejected/);
  });

  it('requires authentication and a same-origin write', async () => {
    cookie = '';
    expect((await call('GET', '/api/workbench/groups')).status).toBe(401);
    expect((await call('POST', '/api/workbench/groups', { name: 'x' })).status).toBe(401);
    await login();
    expect((await call('POST', '/api/workbench/groups', { name: 'x' }, { origin: 'https://evil.example' })).status).toBe(403);
  });
});

describe('contract comparison', () => {
  it('compares 300 CE and 305 CE side by side with each contract\'s own values, source and timestamps', async () => {
    leg305 = { lastPrice: 11, totalTradedVolume: 120, openInterest: 900, buyPrice1: 10.5, sellPrice1: 11.5, impliedVolatility: 44 };
    const r = (await b(await call('GET', `/api/workbench/compare?ids=${encodeURIComponent(`${C300},${C305}`)}`))).data;
    const [x, y] = r.rows;
    expect(x.label).toBe('ANGELONE 27 Oct 2026 300 CE');
    expect(y.label).toBe('ANGELONE 27 Oct 2026 305 CE');
    expect(x.spot).toBe(295);
    expect(x.chain).toMatchObject({ source: 'nse', asOf: '2026-10-14T04:59:40.000Z' });
    expect(x.validation.lotSize).toBe(250);
    expect(x.validation.oneLotPremium.value).toBeCloseTo(x.validation.ltp.value * 250);
    // not assumed equal
    expect(y.validation.ltp.value).toBe(11);
    expect(y.validation.volume.value).toBe(120);
    expect(y.validation.liquidity.cls).not.toBe(x.validation.liquidity.cls);
    expect(y.validation.spreadPct.value).toBeGreaterThan(x.validation.spreadPct.value);
    expect(y.validation.iv.value).toBe(44);
    expect(x.validation.ltp.prov.latency).toBe('SNAPSHOT');
  });

  it('missing fields stay unknown in the comparison, never zero', async () => {
    leg305 = { openInterest: undefined, changeinOpenInterest: undefined, totalTradedVolume: undefined, impliedVolatility: undefined, buyPrice1: undefined, sellPrice1: undefined, buyQuantity1: undefined, sellQuantity1: undefined };
    const r = (await b(await call('GET', `/api/workbench/compare?ids=${encodeURIComponent(`${C300},${C305}`)}`))).data;
    const v = r.rows[1].validation;
    for (const k of ['oi', 'oiChange', 'volume', 'iv', 'bid', 'ask', 'bidQty', 'spreadPct']) expect(v[k].value, k).toBeNull();
    expect(v.ltp.value).not.toBeNull();
    expect(v.liquidity.cls).toBe('DATA_INSUFFICIENT');
    expect(r.rows[0].validation.oi.value).toBe(5000);
  });

  it('a stale snapshot is labelled STALE in the comparison; unlisted or expired contracts explain themselves', async () => {
    chainTs = '14-Oct-2026 10:00:00';
    const stale = (await b(await call('GET', `/api/workbench/compare?ids=${encodeURIComponent(C300)}`))).data;
    expect(stale.rows[0].validation.state).toBe('STALE');
    const mixed = (await b(await call('GET', `/api/workbench/compare?ids=${encodeURIComponent(`${C300},ANGELONE|2026-10-27|CE|999,ANGELONE|2026-10-01|CE|300`)}`))).data;
    expect(mixed.rows[1].problem).toMatch(/no longer listed/);
    expect(mixed.rows[2].problem).toBe('This contract has expired.');
    expect((await call('GET', '/api/workbench/compare')).status).toBe(400);
  });

  it('compares at most four contracts and says so', async () => {
    const ids = ['300', '305', '310'].flatMap((k) => ['CE', 'PE'].map((t) => `ANGELONE|2026-10-27|${t}|${k}`));
    const r = (await b(await call('GET', `/api/workbench/compare?ids=${encodeURIComponent(ids.join(','))}`))).data;
    expect(r.rows).toHaveLength(4);
    expect(r.notes.join(' ')).toMatch(/Only the first 4/);
  });
});

describe('user alert rules', () => {
  const rule = (over: Record<string, unknown> = {}) => call('POST', '/api/workbench/rules', { contractId: C300, kind: 'PREMIUM_CROSS', params: { price: 13, direction: 'ABOVE' }, ...over });

  it('create, duplicate, validate, disable, edit and delete', async () => {
    const a = await rule();
    expect(a.status).toBe(201);
    const id = (await b(a)).data.rule.id;
    const dup = await rule();
    expect(dup.status).toBe(200);
    expect((await b(dup)).data.duplicate).toBe(true);
    expect((await rule({ params: { price: -5, direction: 'ABOVE' } })).status).toBe(400);
    expect((await rule({ kind: 'CLOSE_BEYOND_LEVEL', params: { level: 300, direction: 'ABOVE', tf: '1m' } })).status).toBe(400);
    expect((await rule({ contractId: 'junk' })).status).toBe(400);
    expect((await b(await call('POST', `/api/workbench/rules/${id}/enabled`, { enabled: false }))).data.enabled).toBe(false);
    const edited = await b(await call('PUT', `/api/workbench/rules/${id}`, { contractId: C300, kind: 'PREMIUM_CROSS', params: { price: 20, direction: 'BELOW' }, enabled: true, note: 'x' }));
    expect(edited.data).toMatchObject({ params: { price: 20, direction: 'BELOW' }, lastTriggeredAt: null });
    expect((await b(await call('GET', '/api/workbench/rules'))).data.rules).toHaveLength(1);
    expect((await call('DELETE', `/api/workbench/rules/${id}`)).status).toBe(200);
    expect((await call('DELETE', `/api/workbench/rules/${id}`)).status).toBe(404);
  });

  it('a premium rule fires from fresh data, once; disabling stops it', async () => {
    await rule({ params: { price: 13, direction: 'ABOVE' }, note: 'look at spread' });
    const first = (await b(await call('POST', '/api/workbench/alerts/evaluate', {}))).data;
    const mine = first.created.filter((a: any) => a.ruleId);
    expect(mine).toHaveLength(1);
    expect(mine[0]).toMatchObject({ kind: 'USER_RULE', contractId: C300, kindClass: 'CONDITION' });
    expect(mine[0].evidence.join(' ')).toContain('look at spread');
    expect(mine[0].sourceAsOf).toBe('2026-10-14T04:59:40.000Z');
    const second = (await b(await call('POST', '/api/workbench/alerts/evaluate', {}))).data;
    expect(second.created.filter((a: any) => a.ruleId)).toEqual([]);
    expect((await b(await call('GET', '/api/workbench/rules'))).data.rules[0].lastTriggeredAt).toBeTruthy();
  });

  it('stale data rejects price rules and reports why; the stale rule itself fires', async () => {
    chainTs = '14-Oct-2026 10:00:00';
    await rule();
    await rule({ kind: 'DATA_STALE', params: {} });
    const r = (await b(await call('POST', '/api/workbench/alerts/evaluate', {}))).data;
    const rules = r.created.filter((a: any) => a.ruleId);
    expect(rules.map((a: any) => a.kindClass)).toEqual(['DATA_HEALTH']);
    expect(r.withheld.map((w: any) => w.reason).join(' ')).toMatch(/Premium rule withheld: data is STALE/);
  });

  it('a candle-close rule is withheld when no fresh completed candles exist', async () => {
    await rule({ kind: 'CLOSE_BEYOND_LEVEL', params: { level: 300, direction: 'ABOVE', tf: '15m' } });
    const r = (await b(await call('POST', '/api/workbench/alerts/evaluate', {}))).data;
    expect(r.created.filter((a: any) => a.ruleId)).toEqual([]);
    expect(r.withheld[0].reason).toMatch(/Candle-close rule withheld/);
  });

  it('an expiry rule and a volume rule fire', async () => {
    await rule({ kind: 'EXPIRY_WITHIN', params: { days: 20 } });
    await rule({ kind: 'OPTION_VOLUME_ABOVE', params: { volume: 500 } });
    await rule({ kind: 'SPREAD_ABOVE', params: { spreadPct: 5 } });
    const r = (await b(await call('POST', '/api/workbench/alerts/evaluate', {}))).data;
    expect(r.created.filter((a: any) => a.ruleId)).toHaveLength(2);
    expect(r.evaluated.rules).toBe(3);
  });
});
