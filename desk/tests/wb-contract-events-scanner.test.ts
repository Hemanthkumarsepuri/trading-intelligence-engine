import { describe, expect, it } from 'vitest';
import { evaluateBreakout } from '../shared/breakout';
import { blackScholesGreeks, contractId, expiryMatches, parseContractId, parseNseIdentifier, parseSearch, validateContract, type ValidationInput } from '../shared/contract';
import { chartMarkers, dedupeEvents, normalizeNews, normalizeNseAnnouncement, normalizeNseEventCalendar, possibleContext, safeLink, sortEvents, upcomingEvents } from '../shared/events';
import type { OptionLeg } from '../shared/optionchain';
import { DEFAULT_FILTERS, evaluateCandidate, rankCandidates, selectStrikes, summarize, type Candidate, type ScanInput } from '../shared/scanner';
import { dedupeAlerts, deriveAlerts } from '../shared/wbalerts';
import { bars } from './wbhelpers';

const leg = (p: Partial<OptionLeg> = {}): OptionLeg => ({ ltp: 50, oi: 5000, prevOi: 4500, volume: 400, iv: 28, bid: 49.5, ask: 50.5, bidQty: 1000, askQty: 1000, ...p });
const ref = { underlying: 'RELIANCE', expiry: '2026-10-27', type: 'CE' as const, strike: 1250 };
const now = new Date('2026-10-14T05:00:00Z'); // 10:30 IST on a Wednesday
const input = (p: Partial<ValidationInput> = {}): ValidationInput => ({ ref, leg: leg(), lotSize: 500, spot: 1240, asOf: '2026-10-14T04:59:40Z', fetchedAt: '2026-10-14T04:59:50Z', source: 'nse', latency: 'SNAPSHOT', marketOpen: true, now, ...p });

describe('contract identity and search parsing', () => {
  it('canonical ids round trip and reject malformed input', () => {
    const id = contractId({ ...ref, underlying: 'reliance' });
    expect(id).toBe('RELIANCE|2026-10-27|CE|1250');
    expect(parseContractId(id)).toEqual(ref);
    for (const bad of ['', 'X|2026-13-45|CE|100', 'RELIANCE|2026-10-27|XX|100', 'RELIANCE|2026-02-30|CE|100', 'RELIANCE|2026-10-27|CE|0', 'a b|2026-10-27|CE|1']) expect(parseContractId(bad), bad).toBeNull();
  });
  it('same strike spelled differently is the same contract; different expiry/type is not', () => {
    expect(contractId({ ...ref, strike: 1250.001 })).toBe(contractId(ref));
    expect(contractId({ ...ref, type: 'PE' })).not.toBe(contractId(ref));
    expect(contractId({ ...ref, expiry: '2026-11-24' })).not.toBe(contractId(ref));
  });
  it('parses the NSE identifier and cross-checks identity', () => {
    expect(parseNseIdentifier('OPTSTKRELIANCE27-10-2026CE1250.00')).toEqual(ref);
    expect(parseNseIdentifier('OPTIDXNIFTY27-10-2026PE25000.00')).toEqual({ underlying: 'NIFTY', expiry: '2026-10-27', type: 'PE', strike: 25000 });
    expect(parseNseIdentifier('FUTSTKRELIANCE27-10-2026')).toBeNull();
  });
  it('understands natural search text and keeps unknown words as company text', () => {
    expect(parseSearch('reliance 1250 ce 27oct')).toEqual({ text: 'RELIANCE', strike: 1250, type: 'CE', expiry: { day: '27', month: '10' } });
    expect(parseSearch('nifty 25000 put')).toMatchObject({ text: 'NIFTY', strike: 25000, type: 'PE' });
    expect(parseSearch('tata motors')).toEqual({ text: 'TATA MOTORS', strike: null, type: null, expiry: null });
    expect(parseSearch('hdfc bank oct')).toMatchObject({ text: 'HDFC BANK', expiry: { month: '10' } });
    expect(parseSearch('2026-10-27 infy')).toMatchObject({ expiry: { iso: '2026-10-27' } });
    expect(expiryMatches('2026-10-27', { day: '27', month: '10' })).toBe(true);
    expect(expiryMatches('2026-11-24', { month: '10' })).toBe(false);
  });
});

describe('option validation', () => {
  it('computes spread, one-lot premium, intrinsic and time value from provider values', () => {
    const v = validateContract(input());
    expect(v.state).toBe('VALID');
    expect(v.spreadAbs.value).toBeCloseTo(1);
    expect(v.spreadPct.value).toBeCloseTo(2, 1);
    expect(v.oneLotPremium.value).toBe(25000);
    expect(v.intrinsic.value).toBe(0); // 1240 < 1250 strike
    expect(v.timeValue.value).toBe(50);
    expect(v.volume.value).toBe(400);
    expect(v.oi.value).toBe(5000);
    expect(v.oiChange.value).toBe(500);
    expect(v.spreadPct.prov?.note).toBe('calculated');
  });

  it('volume and open interest stay separate measurements', () => {
    const v = validateContract(input({ leg: leg({ volume: 12, oi: 99999 }) }));
    expect(v.volume.value).toBe(12);
    expect(v.oi.value).toBe(99999);
    expect(v.oiChange.value).toBe(99999 - 4500);
  });

  it('missing fields stay unknown and are never zero', () => {
    const v = validateContract(input({ leg: leg({ volume: null, oi: null, prevOi: null, iv: null, bidQty: null, askQty: null }) }));
    expect(v.volume.value).toBeNull();
    expect(v.volume.state).toBe('UNKNOWN');
    expect(v.oi.value).toBeNull();
    expect(v.oiChange.value).toBeNull();
    expect(v.iv.value).toBeNull();
    expect(v.delta.value).toBeNull(); // no IV, so no calculated Greek either
    expect(v.delta.note).toMatch(/cannot be calculated/);
    expect(v.liquidity.cls).toBe('DATA_INSUFFICIENT');
  });

  it('a contract with no trade and no book is not shown as a real quote', () => {
    const v = validateContract(input({ leg: leg({ ltp: 0, bid: null, ask: null }) }));
    expect(v.quoteAvailable).toBe(false);
    expect(v.state).toBe('UNKNOWN');
    expect(v.ltp.value).toBeNull();
    expect(v.ltp.note).toMatch(/No trade/);
    expect(v.oneLotPremium.value).toBeNull();
    expect(v.notes.join(' ')).toMatch(/No quote/);
  });

  it('crossed bid/ask is a CONFLICT with no spread', () => {
    const v = validateContract(input({ leg: leg({ bid: 52, ask: 50 }) }));
    expect(v.state).toBe('CONFLICT');
    expect(v.spreadPct.value).toBeNull();
  });

  it('stale chain during a live session is STALE; closed market is MARKET_CLOSED not an outage', () => {
    expect(validateContract(input({ asOf: '2026-10-14T04:40:00Z' })).state).toBe('STALE');
    const closed = validateContract(input({ marketOpen: false, asOf: '2026-10-13T10:00:00Z' }));
    expect(closed.state).toBe('MARKET_CLOSED');
    expect(closed.ltp.value).toBe(50);
  });

  it('a chain with no provider timestamp cannot be called VALID while the market is open', () => {
    expect(validateContract(input({ asOf: null })).state).toBe('UNKNOWN');
  });

  it('a delayed source is flagged as not verified real-time', () => {
    const v = validateContract(input({ latency: 'DELAYED' }));
    expect(v.notes.join(' ')).toMatch(/not verified as real-time/);
    expect(v.ltp.prov?.latency).toBe('DELAYED');
  });

  it('premium below intrinsic means the quotes are inconsistent: time value withheld', () => {
    const v = validateContract(input({ spot: 1300, leg: leg({ ltp: 20, bid: 19.5, ask: 20.5 }) }));
    expect(v.intrinsic.value).toBe(50);
    expect(v.timeValue.value).toBeNull();
    expect(v.timeValue.state).toBe('CONFLICT');
  });

  it('unknown lot size means no one-lot premium rather than a guess', () => {
    const v = validateContract(input({ lotSize: null }));
    expect(v.oneLotPremium.value).toBeNull();
    expect(v.oneLotPremium.note).toMatch(/lot size/);
  });

  it('an expired contract is UNSUPPORTED', () => {
    const v = validateContract(input({ now: new Date('2026-10-28T05:00:00Z'), asOf: '2026-10-28T04:59:00Z' }));
    expect(v.state).toBe('UNSUPPORTED');
    expect(v.daysToExpiry).toBeNull();
  });

  it('liquidity classes are rule based and list their reasons', () => {
    expect(validateContract(input()).liquidity.cls).toBe('LIQUID');
    expect(validateContract(input({ leg: leg({ bid: 48.5, ask: 50 }) })).liquidity.cls).toBe('MODERATE');
    expect(validateContract(input({ leg: leg({ volume: 3 }) })).liquidity.cls).toBe('ILLIQUID');
    expect(validateContract(input({ leg: leg({ bidQty: 100 }) })).liquidity.cls).toBe('MODERATE'); // 0.2 lot of depth
    expect(validateContract(input()).liquidity.reasons.join(' ')).toMatch(/Spread/);
  });

  it('provider Greeks are used as-is; calculated Greeks are labelled', () => {
    const given = validateContract(input({ leg: leg({ delta: 0.31, gamma: 0.01, theta: -1.2, vega: 0.5 }) }));
    expect(given.delta.value).toBe(0.31);
    expect(given.delta.prov?.note).toBeUndefined();
    const calc = validateContract(input());
    expect(calc.delta.value).toBeGreaterThan(0);
    expect(calc.delta.prov?.note).toBe('calculated');
    expect(calc.delta.note).toMatch(/Black-Scholes/);
    expect(calc.leverage.value).toBeGreaterThan(1);
  });

  it('Black-Scholes sanity: put-call parity of delta, ATM near 0.5, theta negative, none without inputs', () => {
    const c = blackScholesGreeks('CE', 100, 100, 25, 0.1)!;
    const p = blackScholesGreeks('PE', 100, 100, 25, 0.1)!;
    expect(c.delta - p.delta).toBeCloseTo(1, 5);
    expect(c.delta).toBeGreaterThan(0.5);
    expect(c.delta).toBeLessThan(0.6);
    expect(c.theta).toBeLessThan(0);
    expect(c.vega).toBeGreaterThan(0);
    expect(c.gamma).toBeCloseTo(p.gamma, 8);
    expect(blackScholesGreeks('CE', 100, 100, 0, 0.1)).toBeNull();
    expect(blackScholesGreeks('CE', 100, 100, 20, 0)).toBeNull();
  });
});

describe('events', () => {
  const at = '2026-10-14T06:00:00Z';
  it('NSE announcements keep original time, publisher, link host and verification', () => {
    const e = normalizeNseAnnouncement({ symbol: 'RELIANCE', desc: 'Outcome of Board Meeting', attchmntText: 'The board approved the results.', attchmntFile: 'https://nsearchives.nseindia.com/corporate/x.pdf', an_dt: '14-Oct-2026 09:30:00', seq_id: '1' }, 'RELIANCE', at)!;
    expect(e.publishedAt).toBe('2026-10-14T04:00:00.000Z');
    expect(e.sourceZone).toBe('Asia/Kolkata');
    expect(e.timestampReliable).toBe(true);
    expect(e.verification).toBe('CONFIRMED_EXCHANGE');
    expect(e.link).toMatch(/nsearchives/);
    expect(e.retrievedAt).toBe(at);
    expect(e.limitation).toMatch(/lag/);
  });
  it('rejects non-exchange attachment links and javascript: urls', () => {
    expect(normalizeNseAnnouncement({ desc: 'x', attchmntFile: 'https://evil.example/a.pdf', an_dt: '14-Oct-2026 09:30:00' }, 'A', at)!.link).toBeNull();
    expect(safeLink('javascript:alert(1)')).toBeNull();
    expect(safeLink('https://example.com/a')).toBe('https://example.com/a');
  });
  it('an announcement without a parseable time is not placed on a chart', () => {
    const e = normalizeNseAnnouncement({ desc: 'Something', an_dt: 'soon' }, 'A', at)!;
    expect(e.publishedAt).toBeNull();
    expect(e.timestampReliable).toBe(false);
    expect(chartMarkers([e], bars(5), 900)).toEqual([]);
  });
  it('news keeps the outlet as publisher and is only REPORTED', () => {
    const n = normalizeNews({ id: 'a', title: 'Reliance rallies', link: 'https://x.test/a', source: 'Mint', publishedAt: '2026-10-14T05:30:00Z' }, 'RELIANCE', at);
    expect(n.publisher).toBe('Mint');
    expect(n.verification).toBe('REPORTED');
    const bad = normalizeNews({ id: 'b', title: 't', link: 'ftp://x', source: '', publishedAt: null }, null, at);
    expect(bad.timestampReliable).toBe(false);
    expect(bad.link).toBeNull();
    expect(bad.publisher).toBe('Unknown publisher');
  });
  it('event calendar items are scheduled dates, not publication times', () => {
    const e = normalizeNseEventCalendar({ symbol: 'TCS', purpose: 'Financial Results', date: '16-Oct-2026' }, 'TCS', at)!;
    expect(e.type).toBe('EARNINGS');
    expect(e.scheduledFor).toBe('2026-10-16');
    expect(e.timestampReliable).toBe(false);
    expect(upcomingEvents([e], now, 7)[0]).toMatchObject({ kind: 'EARNINGS', daysAway: 2 });
    expect(upcomingEvents([e], now, 1)).toEqual([]);
    expect(normalizeNseEventCalendar({ purpose: '', date: '16-Oct-2026' }, 'TCS', at)).toBeNull();
  });
  it('de-duplicates, preferring exchange-confirmed items', () => {
    const a = normalizeNews({ id: '1', title: 'Board approves results', link: 'https://x.test', source: 'Mint', publishedAt: at }, 'TCS', at);
    const b = { ...a, id: '2', verification: 'CONFIRMED_EXCHANGE' as const };
    const out = dedupeEvents([a, b]);
    expect(out).toHaveLength(1);
    expect(out[0]!.verification).toBe('CONFIRMED_EXCHANGE');
  });
  it('places markers on the containing bar, after-hours events on the next bar, outside events dropped', () => {
    const cs = bars(10, () => ({}), 1_700_000_000);
    const mk = (iso: string) => normalizeNews({ id: iso, title: iso, link: 'https://x.test', source: 's', publishedAt: iso }, 'A', at);
    const inside = mk(new Date((1_700_000_000 + 900 * 3 + 100) * 1000).toISOString());
    const outside = mk(new Date((1_700_000_000 - 5000) * 1000).toISOString());
    const m = chartMarkers([inside, outside], cs, 900);
    expect(m).toHaveLength(1);
    expect(m[0]).toMatchObject({ t: 1_700_000_000 + 2700, afterHours: false });
    const gapped = [cs[0]!, { ...cs[1]!, t: cs[0]!.t + 20000 }];
    const g = chartMarkers([mk(new Date((cs[0]!.t + 5000) * 1000).toISOString())], gapped, 900);
    expect(g[0]).toMatchObject({ t: gapped[1]!.t, afterHours: true });
  });
  it('context wording never claims causation', () => {
    const e = normalizeNews({ id: 'a', title: 'x', link: 'https://x.test', source: 's', publishedAt: new Date(1_700_000_000_000).toISOString() }, 'A', at);
    const near = possibleContext([e], 1_700_000_000 + 600, 1_700_000_000 + 1800);
    expect(near.events).toHaveLength(1);
    expect(near.statement).toMatch(/possible context only/);
    expect(near.statement).not.toMatch(/caused|because/);
    expect(possibleContext([], 0, 1).statement).toMatch(/does not mean none occurred/);
  });
  it('sorts newest first, scheduled events by their date', () => {
    const a = normalizeNews({ id: 'a', title: 'a', link: 'https://x.test', source: 's', publishedAt: '2026-10-14T01:00:00Z' }, 'A', at);
    const b = normalizeNews({ id: 'b', title: 'b', link: 'https://x.test', source: 's', publishedAt: '2026-10-14T03:00:00Z' }, 'A', at);
    expect(sortEvents([a, b]).map((e) => e.id)).toEqual(['news-b', 'news-a']);
  });
});

// ---- scanner ----------------------------------------------------------------------------------------------------

function setupFor(state: 'CONFIRMED' | 'APPROACHING_LEVEL' | 'NOT_READY') {
  const base = bars(40);
  const tail = state === 'CONFIRMED' ? [{ o: 110.5, h: 111.7, l: 110.2, c: 111, v: 3000 }, { o: 111, h: 112.3, l: 110.8, c: 111.6, v: 1200 }] : state === 'APPROACHING_LEVEL' ? [{ o: 108.5, h: 109.8, l: 108, c: 109.3, v: 1000 }] : [{}];
  const all = [...base, ...bars(tail.length, (i) => tail[i]!, base[39]!.t + 900)];
  const level = { id: 'l', price: 110, low: 110, high: 110, kind: 'RESISTANCE' as const, method: 'SWING_CLUSTER' as const, tf: '15m' as const, touches: 3, from: 0, to: 0, explain: '' };
  return evaluateBreakout(all, level, 'UP', '15m', { freshness: 'VALID' });
}
const trendUp = { trend: 'UP' as const, structure: 'HH_HL' as const, evidence: [] };
const scanIn = (symbol: string, state: 'CONFIRMED' | 'APPROACHING_LEVEL' | 'NOT_READY', p: Partial<ScanInput> = {}): ScanInput => {
  const setup = setupFor(state);
  return {
    symbol, name: symbol, sector: 'IT', isIndex: false, lotSize: 500, price: 111, changePct: 1.2, priceAsOf: '2026-10-14T04:59:00Z', freshness: 'VALID',
    tfs: { '15m': { structure: { tf: '15m', setups: [setup], primary: setup }, trend: trendUp, barsCompleted: 40 }, '1d': { structure: { tf: '1d', setups: [], primary: null }, trend: trendUp, barsCompleted: 200 } },
    rvol: { value: 1.8, baseline: 100, samples: 5, method: 'TIME_OF_DAY_CUMULATIVE', note: '' }, sectorChangePct: 0.4, benchChangePct: 0.2, upcoming: [], inFnoBan: false,
    contracts: [validateContract(input({ ref: { ...ref, underlying: symbol } }))], contractNote: null, ...p,
  };
};
const cand = (r: ReturnType<typeof evaluateCandidate>): Candidate => {
  if (!('candidate' in r)) throw new Error(`excluded: ${r.excluded.reason}`);
  return r.candidate;
};

describe('scanner', () => {
  it('qualifies a confirmed setup with an audit trail, reasons, risks and contracts to inspect', () => {
    const c = cand(evaluateCandidate(scanIn('AAA', 'CONFIRMED'), DEFAULT_FILTERS));
    expect(c.setup.state).toBe('CONFIRMED');
    expect(c.contracts).toHaveLength(1);
    expect(c.audit.every((a) => a.passed !== false)).toBe(true);
    expect(c.why[0]).toMatch(/confirmed above 110 on the 15m chart/);
    expect(c.sectorRelPct).toBeCloseTo(0.8);
    expect(c.benchRelPct).toBeCloseTo(1.0);
  });

  it('explains every exclusion', () => {
    const reason = (p: Partial<ScanInput>, f = DEFAULT_FILTERS) => { const r = evaluateCandidate(scanIn('X', 'CONFIRMED', p), f); return 'excluded' in r ? r.excluded.reason : 'included'; };
    expect(reason({ freshness: 'STALE' })).toBe('data stale');
    expect(reason({ freshness: 'CONFLICT' })).toBe('data conflict');
    expect(reason({ tfs: {} })).toBe('insufficient history');
    expect(reason({ contracts: null, contractNote: 'NSE blocked' })).toBe('option chain unavailable');
    expect(reason({ contracts: [] })).toBe('no quoted option contracts');
    expect(reason({}, { ...DEFAULT_FILTERS, direction: 'DOWN' })).toBe('direction filter');
    expect(reason({}, { ...DEFAULT_FILTERS, segment: 'INDEX' })).toBe('segment filter');
    expect(reason({}, { ...DEFAULT_FILTERS, minRvol: 3 })).toBe('relative volume below filter');
    expect(reason({ rvol: null }, { ...DEFAULT_FILTERS, minRvol: 1 })).toBe('relative volume unavailable');
    expect(reason({}, { ...DEFAULT_FILTERS, trend: 'DOWN' })).toBe('trend filter');
    const ev = { kind: 'EARNINGS' as const, daysAway: 1, event: normalizeNseEventCalendar({ purpose: 'Financial Results', date: '15-Oct-2026' }, 'X', '')! };
    expect(reason({ upcoming: [ev] }, { ...DEFAULT_FILTERS, avoidEvents: true })).toBe('scheduled event nearby');
    expect(reason({ upcoming: [ev] })).toBe('included'); // shown, with a risk note
    expect(cand(evaluateCandidate(scanIn('X', 'CONFIRMED', { upcoming: [ev] }), DEFAULT_FILTERS)).risks.join(' ')).toMatch(/Scheduled event/);
    const r = evaluateCandidate(scanIn('N', 'NOT_READY'), DEFAULT_FILTERS);
    expect('excluded' in r && r.excluded.reason).toMatch(/no qualifying setup \(NOT_READY\)/);
  });

  it('liquidity filter drops illiquid contracts and can exclude the name', () => {
    const illiquid = validateContract(input({ leg: leg({ volume: 2 }) }));
    const f = { ...DEFAULT_FILTERS, minLiquidity: 'LIQUID' as const };
    const r = evaluateCandidate(scanIn('L', 'CONFIRMED', { contracts: [illiquid] }), f);
    expect('excluded' in r && r.excluded.reason).toBe('no contract meets the liquidity filter');
  });

  it('option type and expiry filters narrow the contracts shown', () => {
    const ce = validateContract(input());
    const pe = validateContract(input({ ref: { ...ref, type: 'PE' } }));
    const c = cand(evaluateCandidate(scanIn('T', 'CONFIRMED', { contracts: [ce, pe] }), { ...DEFAULT_FILTERS, optionType: 'PE' }));
    expect(c.contracts.map((x) => x.ref.type)).toEqual(['PE']);
  });

  it('never manufactures candidates: fewer than five is reported as such', () => {
    const results = ['A', 'B', 'C'].map((s) => evaluateCandidate(scanIn(s, 'CONFIRMED'), DEFAULT_FILTERS));
    const excl = ['D', 'E'].map((s) => evaluateCandidate(scanIn(s, 'NOT_READY'), DEFAULT_FILTERS));
    const cs = results.map(cand);
    const ex = excl.map((r) => ('excluded' in r ? r.excluded : null)!);
    const sum = summarize(cs, ex, 5, 5);
    expect(sum.stockCandidates).toBe(3);
    expect(sum.message).toMatch(/fewer than 5/);
    expect(sum.message).toMatch(/No candidates were added/);
    expect(sum.contractCount).toBe(3);
    const many = summarize(['A', 'B', 'C', 'D', 'E', 'F'].map((s) => cand(evaluateCandidate(scanIn(s, 'CONFIRMED'), DEFAULT_FILTERS))), [], 6, 6);
    expect(many.message).toMatch(/6 underlyings qualified/);
  });

  it('distinguishes underlyings from contracts', () => {
    const two = validateContract(input({ ref: { ...ref, strike: 1300 } }));
    const c = cand(evaluateCandidate(scanIn('U', 'CONFIRMED', { contracts: [validateContract(input()), two] }), DEFAULT_FILTERS));
    const s = summarize([c], [], 1, 1);
    expect(s.stockCandidates).toBe(1);
    expect(s.contractCount).toBe(2);
  });

  it('ranks by state, timeframe agreement, relative volume, then closeness; deterministic ties', () => {
    const a = cand(evaluateCandidate(scanIn('AAA', 'APPROACHING_LEVEL'), DEFAULT_FILTERS));
    const b = cand(evaluateCandidate(scanIn('BBB', 'CONFIRMED', { rvol: { value: 1.6, baseline: 1, samples: 5, method: 'TIME_OF_DAY_CUMULATIVE', note: '' } }), DEFAULT_FILTERS));
    const c = cand(evaluateCandidate(scanIn('CCC', 'CONFIRMED', { rvol: { value: 2.5, baseline: 1, samples: 5, method: 'TIME_OF_DAY_CUMULATIVE', note: '' } }), DEFAULT_FILTERS));
    expect(rankCandidates([a, b, c]).map((x) => x.symbol)).toEqual(['CCC', 'BBB', 'AAA']);
    expect(rankCandidates([c, a, b]).map((x) => x.symbol)).toEqual(['CCC', 'BBB', 'AAA']);
  });

  it('selects ATM and next-OTM strikes with visible criteria', () => {
    expect(selectStrikes([1200, 1250, 1300, 1350], 1262, 'UP')).toMatchObject({ atm: 1250, otm: 1300 });
    expect(selectStrikes([1200, 1250, 1300, 1350], 1262, 'DOWN')).toMatchObject({ atm: 1250, otm: 1200 });
    expect(selectStrikes([], 100, 'UP')).toBeNull();
    expect(selectStrikes([100], 100, 'UP')?.criteria).toMatch(/can change/);
  });
});

describe('workbench alerts', () => {
  const base = { symbol: 'AAA', asOf: '2026-10-14T04:59:00Z', now, rvol: null, contract: null, previous: null, upcoming: [] };
  it('raises a confirmed-breakout alert with evidence, invalidation and source time', () => {
    const [a] = deriveAlerts({ ...base, freshness: 'VALID', setup: setupFor('CONFIRMED') });
    expect(a!.kind).toBe('CONFIRMED_BREAKOUT');
    expect(a!.evidence.length).toBeGreaterThan(0);
    expect(a!.invalidation).toMatch(/FAILED/);
    expect(a!.sourceAsOf).toBe(base.asOf);
    expect(a!.tf).toBe('15m');
  });
  it('stale data never generates an actionable alert', () => {
    expect(deriveAlerts({ ...base, freshness: 'STALE', setup: setupFor('CONFIRMED') })).toEqual([]);
  });
  it('closed-market replays do not raise a CONFIRMED alert', () => {
    expect(deriveAlerts({ ...base, freshness: 'MARKET_CLOSED', setup: setupFor('CONFIRMED') }).some((x) => x.kind === 'CONFIRMED_BREAKOUT')).toBe(false);
  });
  it('approaching level, abnormal rvol, spread widening, liquidity drop, stale contract, event proximity', () => {
    const rv = { value: 2.4, baseline: 1, samples: 5, method: 'TIME_OF_DAY_CUMULATIVE' as const, note: 'n' };
    const wide = validateContract(input({ leg: leg({ bid: 46, ask: 50 }) })); // ~8% spread
    const out = deriveAlerts({ ...base, freshness: 'VALID', setup: setupFor('APPROACHING_LEVEL'), rvol: rv, contract: wide, previous: { spreadPct: 2, liquidity: 'LIQUID' }, upcoming: [{ kind: 'EARNINGS', daysAway: 1, event: normalizeNseEventCalendar({ purpose: 'Financial Results', date: '15-Oct-2026' }, 'AAA', '')! }] });
    expect(out.map((x) => x.kind).sort()).toEqual(['ABNORMAL_RELATIVE_VOLUME', 'APPROACHING_LEVEL', 'EVENT_PROXIMITY', 'LIQUIDITY_DETERIORATION', 'SPREAD_WIDENING']);
    const stale = deriveAlerts({ ...base, freshness: 'VALID', setup: null, contract: validateContract(input({ asOf: '2026-10-14T04:30:00Z' })) });
    expect(stale.map((x) => [x.kind, x.kindClass])).toEqual([['CONTRACT_DATA_STALE', 'DATA_HEALTH']]);
  });
  it('de-duplicates within a run and across runs inside the cooldown', () => {
    const a = deriveAlerts({ ...base, freshness: 'VALID', setup: setupFor('CONFIRMED') });
    expect(dedupeAlerts([...a, ...a], {}, now)).toHaveLength(1);
    expect(dedupeAlerts(a, { [a[0]!.key]: new Date(now.getTime() - 3600_000).toISOString() }, now)).toHaveLength(0);
    expect(dedupeAlerts(a, { [a[0]!.key]: new Date(now.getTime() - 7 * 3600_000).toISOString() }, now)).toHaveLength(1);
  });
});
