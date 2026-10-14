// Provider-behaviour tests: the Workbench against faked NSE / Yahoo endpoints (no network).
// They pin what the user sees when a provider is slow, blocked, rate limited, returns garbage,
// omits fields, serves old data, or disagrees with another provider.

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { hashPassword } from '../server/auth';
import { resetCacheMemory } from '../server/cache';
import { loadConfig, type Ctx } from '../server/config';
import { MemoryKV } from '../server/kv';
import { handle } from '../server/router';
import { resetProviderStats } from '../server/services/wb/data';

const OPEN = new Date('2026-10-14T05:00:00.000Z'); // Wed 10:30 IST
const SAT = new Date('2026-10-17T05:00:00.000Z');
const PASSWORD = 'correct horse battery staple';
const ORIGIN = 'https://desk.example.up.railway.app';
const CONTRACT = 'RELIANCE|2026-10-27|CE|1250';

let hash = '';
beforeAll(async () => {
  hash = await hashPassword(PASSWORD, 100_000);
});

interface Behaviour {
  chain: () => Response | Promise<Response>;
  quote: () => Response | Promise<Response>;
  info: () => Response | Promise<Response>;
}

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'content-type': 'application/json' } });

const lotsCsv = () => ['UNDERLYING,SYMBOL,LOT', 'Reliance,RELIANCE,500', ...Array.from({ length: 60 }, (_, i) => `Name ${i},SYM${i},${100 + i}`)].join('\n');

function leg(over: Record<string, unknown> = {}) {
  return { strikePrice: 1250, lastPrice: 50, openInterest: 5000, changeinOpenInterest: 500, totalTradedVolume: 400, impliedVolatility: 28, buyPrice1: 49.5, sellPrice1: 50.5, buyQuantity1: 1000, sellQuantity1: 1000, identifier: 'OPTSTKRELIANCE27-10-2026CE1250.00', ...over };
}
function chainBody(opts: { ts?: string; spot?: number; ce?: Record<string, unknown> } = {}) {
  return {
    records: {
      timestamp: opts.ts ?? '14-Oct-2026 10:29:40',
      underlyingValue: opts.spot ?? 1240,
      expiryDates: ['27-Oct-2026'],
      data: [
        { strikePrice: 1250, expiryDates: '27-Oct-2026', CE: leg(opts.ce), PE: leg({ lastPrice: 60, identifier: 'OPTSTKRELIANCE27-10-2026PE1250.00' }) },
        { strikePrice: 1300, expiryDates: '27-Oct-2026', CE: leg({ strikePrice: 1300, lastPrice: 25 }), PE: leg({ strikePrice: 1300, lastPrice: 90 }) },
      ],
    },
  };
}
const yahooBody = (price: number, tsSec: number) => ({
  chart: { result: [{ meta: { symbol: 'RELIANCE.NS', regularMarketPrice: price, regularMarketTime: tsSec, previousClose: 1230, regularMarketDayHigh: price + 3, regularMarketDayLow: price - 9 }, timestamp: [tsSec - 86400, tsSec], indicators: { quote: [{ open: [1230, 1232], high: [1245, 1245], low: [1225, 1229], close: [1230, price], volume: [1e6, 9e5] }] } }], error: null },
});

let current: Behaviour;
let ctx: Ctx;
let cookie = '';
let now = OPEN;
let calls: string[] = [];

const defaults = (): Behaviour => ({
  chain: () => json(chainBody()),
  quote: () => json(yahooBody(1240, Math.floor(OPEN.getTime() / 1000) - 60)),
  info: () => json({ expiryDates: ['27-Oct-2026', '24-Nov-2026'], strikePrice: [1250, 1300] }),
});

function makeCtx(): Ctx {
  const fetchImpl = vi.fn(async (input: unknown) => {
    const url = String(input);
    calls.push(url);
    if (url === 'https://www.nseindia.com/') return new Response('<html></html>', { status: 200, headers: { 'set-cookie': 'nsit=abc; Path=/' } });
    if (url.includes('option-chain-contract-info')) return current.info();
    if (url.includes('option-chain-v3')) return current.chain();
    if (url.includes('fo_mktlots.csv')) return new Response(lotsCsv(), { status: 200 });
    if (url.includes('finance.yahoo.com')) return current.quote();
    return new Response('not found', { status: 404 });
  }) as unknown as typeof fetch;
  return { cfg: loadConfig({ SESSION_SECRET: 's'.repeat(40), APP_PASSWORD_HASH: hash, DESK_STORAGE: 'memory' }), kv: new MemoryKV(), now: () => new Date(Date.now()), fetchImpl };
}

async function call(method: string, path: string, body?: unknown): Promise<Response> {
  const h: Record<string, string> = { cookie };
  if (body !== undefined) h['content-type'] = 'application/json';
  if (method !== 'GET') h.origin = ORIGIN;
  return handle(new Request(`${ORIGIN}${path}`, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) }), ctx);
}
const body = async (r: Response) => (await r.json()) as { ok: boolean; data: any; meta: any; error?: { code: string; message: string } };
const contract = async (id = CONTRACT) => call('GET', `/api/workbench/contract?id=${encodeURIComponent(id)}`);

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  now = OPEN;
  vi.setSystemTime(now);
  resetCacheMemory();
  resetProviderStats();
  calls = [];
  current = defaults();
  ctx = makeCtx();
  const res = await handle(new Request(`${ORIGIN}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json', origin: ORIGIN }, body: JSON.stringify({ password: PASSWORD }) }), ctx);
  expect(res.status).toBe(200);
  cookie = (res.headers.get('set-cookie') ?? '').split(';')[0]!;
});
afterEach(() => vi.useRealTimers());

describe('healthy providers', () => {
  it('maps NSE fields faithfully and stamps provenance with the provider timestamp', async () => {
    const r = await body(await contract());
    expect(r.ok).toBe(true);
    const v = r.data.validation;
    expect(v.state).toBe('VALID');
    expect(v.ltp.value).toBe(50);
    expect(v.bid.value).toBe(49.5);
    expect(v.ask.value).toBe(50.5);
    expect(v.bidQty.value).toBe(1000);
    expect(v.volume.value).toBe(400);
    expect(v.oi.value).toBe(5000);
    expect(v.oiChange.value).toBe(500);
    expect(v.iv.value).toBe(28);
    expect(v.lotSize).toBe(500);
    expect(v.oneLotPremium.value).toBe(25000);
    expect(v.ltp.prov).toMatchObject({ source: 'nse', asOf: '2026-10-14T04:59:40.000Z', latency: 'SNAPSHOT' });
    expect(r.data.underlying.independent.prov.latency).toBe('DELAYED');
    expect(r.data.underlying.crossCheck.state).toBe('VALID');
  });
});

describe('provider failures are visible and never become fake data', () => {
  it('a rate-limited chain (429) is a clear upstream error with no values and is counted in Data Health', async () => {
    current.chain = () => new Response('slow down', { status: 429 });
    const r = await contract();
    expect(r.status).toBe(502);
    const j = await body(r);
    expect(j.error?.code).toBe('UPSTREAM');
    expect(j.error?.message).toMatch(/429/);
    expect(JSON.stringify(j)).not.toMatch(/slow down/); // provider bodies never reach the browser
    const h = (await body(await call('GET', '/api/workbench/health'))).data;
    const p = h.providers.find((x: any) => x.id === 'nse-chain');
    expect(p.failed).toBeGreaterThan(0);
    expect(p.lastError).toMatch(/429/);
  });

  it('a blocked host (403) is reported as blocked', async () => {
    current.chain = () => new Response('Access Denied', { status: 403 });
    const j = await body(await contract());
    expect(j.error?.message).toMatch(/403.*access denied/i);
  });

  it('a malformed payload (HTML) is an error, not an empty chain', async () => {
    current.chain = () => new Response('<html>maintenance</html>', { status: 200 });
    const r = await contract();
    expect(r.status).toBe(502);
    expect((await body(r)).error?.message).toMatch(/Non-JSON/);
  });

  it('a payload without records or without the requested expiry is rejected', async () => {
    current.chain = () => json({ nope: true });
    expect((await contract()).status).toBe(502);
    resetCacheMemory();
    current.chain = () => json({ records: { data: [{ strikePrice: 1250, expiryDates: '24-Nov-2026', CE: leg(), PE: leg() }] } });
    expect((await contract()).status).toBe(502);
  });

  it('a timeout surfaces as a timeout', async () => {
    current.chain = () => { throw Object.assign(new Error('This operation was aborted'), { name: 'AbortError' }); };
    const j = await body(await contract());
    expect(j.error?.message).toMatch(/Timed out/);
  });

  it('total provider outage leaves the underlying page honest: no price, explicit reasons', async () => {
    current.quote = () => new Response('', { status: 500 });
    const r = await body(await call('GET', '/api/workbench/underlying/RELIANCE?tf=15m'));
    expect(r.ok).toBe(true);
    expect(r.data.price.value).toBeNull();
    expect(r.data.price.state).toBe('UNKNOWN');
    expect(r.data.availability.available).toBe(false);
    expect(r.data.availability.reason).toBeTruthy();
    expect(r.data.structure.primary).toBeNull();
    expect(r.data.freshness).not.toBe('VALID');
  });
});

describe('stale, partial and conflicting data', () => {
  it('a snapshot older than the freshness limit during a live session is STALE, not live', async () => {
    current.chain = () => json(chainBody({ ts: '14-Oct-2026 10:05:00' }));
    const v = (await body(await contract())).data.validation;
    expect(v.state).toBe('STALE');
    expect(v.ltp.state).toBe('STALE');
    expect(v.ltp.value).toBe(50); // the value is shown, labelled, not hidden
  });

  it('when a refresh fails the last good chain is served and marked stale, with the provider time kept', async () => {
    const ok = await body(await contract());
    expect(ok.data.validation.state).toBe('VALID');
    vi.setSystemTime(new Date(OPEN.getTime() + 5 * 60_000)); // 5 minutes later, past the 20 s cache life and the 180 s limit
    current.chain = () => new Response('', { status: 503 });
    const later = await body(await contract());
    expect(later.ok).toBe(true);
    expect(later.data.chain.stale).toBe(true);
    expect(later.data.validation.state).toBe('STALE');
    expect(later.data.chain.asOf).toBe('2026-10-14T04:59:40.000Z');
    expect(later.meta.stale).toBe(true);
    expect(later.data.notes.join(' ')).toMatch(/last good chain/);
  });

  it('missing quote fields stay unknown (never zero) and block the liquidity verdict', async () => {
    current.chain = () => json(chainBody({ ce: { buyPrice1: undefined, sellPrice1: undefined, totalTradedVolume: undefined, openInterest: undefined, changeinOpenInterest: undefined, impliedVolatility: undefined, buyQuantity1: undefined, sellQuantity1: undefined } }));
    const v = (await body(await contract())).data.validation;
    for (const k of ['bid', 'ask', 'volume', 'oi', 'oiChange', 'iv', 'bidQty', 'spreadPct']) {
      expect(v[k].value, k).toBeNull();
      expect(v[k].state, k).toBe('UNKNOWN');
    }
    expect(v.ltp.value).toBe(50);
    expect(v.liquidity.cls).toBe('DATA_INSUFFICIENT');
  });

  it('an untraded contract with no book is not shown as a quote', async () => {
    current.chain = () => json(chainBody({ ce: { lastPrice: 0, buyPrice1: 0, sellPrice1: 0, totalTradedVolume: 0 } }));
    const v = (await body(await contract())).data.validation;
    expect(v.quoteAvailable).toBe(false);
    expect(v.ltp.value).toBeNull();
    expect(v.state).toBe('UNKNOWN');
    expect(v.volume.value).toBe(0); // an observed zero is kept as zero
  });

  it('two providers that disagree on the underlying are flagged CONFLICT and both values shown', async () => {
    current.quote = () => json(yahooBody(1290, Math.floor(OPEN.getTime() / 1000) - 60)); // +4% vs the chain's 1240
    const d = (await body(await contract())).data;
    expect(d.underlying.crossCheck.state).toBe('CONFLICT');
    expect(d.underlying.spot.value).toBe(1240);
    expect(d.underlying.independent.value).toBe(1290);
    expect(d.notes.join(' ')).toMatch(/differ/);
  });

  it('a closed market (Saturday) reports MARKET_CLOSED and values from the last session, not an outage', async () => {
    now = SAT;
    vi.setSystemTime(SAT);
    resetCacheMemory();
    current.chain = () => json(chainBody({ ts: '16-Oct-2026 15:29:59' }));
    const v = (await body(await contract())).data.validation;
    expect(v.state).toBe('MARKET_CLOSED');
    expect(v.ltp.value).toBe(50);
    expect(v.notes.join(' ')).toMatch(/Market closed/);
  });

  it('stale data cannot create an actionable alert for a tracked contract', async () => {
    expect((await call('POST', '/api/workbench/contracts', { id: CONTRACT })).status).toBe(201);
    current.chain = () => json(chainBody({ ts: '14-Oct-2026 10:00:00' }));
    resetCacheMemory();
    const r = (await body(await call('POST', '/api/workbench/alerts/evaluate', {}))).data;
    const kinds = r.created.map((a: any) => a.kind);
    expect(kinds).not.toContain('CONFIRMED_BREAKOUT');
    for (const a of r.created.filter((x: any) => x.contractId)) expect(a.kindClass).toBe('DATA_HEALTH');
  });
});

describe('request hygiene', () => {
  it('does not hammer providers: repeated contract views inside the cache window reuse one chain fetch', async () => {
    await contract();
    const first = calls.filter((u) => u.includes('option-chain-v3')).length;
    await contract();
    await contract();
    expect(calls.filter((u) => u.includes('option-chain-v3')).length).toBe(first);
    expect(first).toBe(1);
  });

  it('the symbol is encoded and the equity chain type is used for stocks', async () => {
    await contract();
    const u = calls.find((x) => x.includes('option-chain-v3'))!;
    expect(u).toContain('type=Equity');
    expect(u).toContain('symbol=RELIANCE');
    expect(u).toContain('expiry=27-Oct-2026');
  });

  it('a non-F&O stock is refused with an explanation instead of a failed lookup', async () => {
    const r = await call('GET', `/api/workbench/contract?id=${encodeURIComponent('TATAMOTORS|2026-10-27|CE|700')}`);
    expect(r.status).toBe(422);
    expect((await body(r)).error?.message).toMatch(/not in the F&O segment/);
  });
});
