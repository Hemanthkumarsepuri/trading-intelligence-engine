// Synthetic data for DESK_OFFLINE=1 only (demo and browser tests). Never reachable in production:
// the production config refuses DESK_OFFLINE=1. Everything produced here is labelled DEMO DATA by
// the services that call it.

import { aggregate, SESSION_OPEN_MIN, SESSION_CLOSE_MIN } from '../shared/candles';
import type { OptionLeg, ChainRow, NormalizedChain } from '../shared/optionchain';
import { dayStatus, fromIst, istDateStr, istParts, previousTradingDay } from '../shared/india';
import type { Candle } from '../shared/types';
import { basePrice, fixtureDaily } from './fixtures';

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}
function rng(seed: number): () => number {
  let a = seed || 1;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export type Scenario = 'BREAKOUT_UP' | 'APPROACH_UP' | 'BREAKDOWN' | 'APPROACH_DOWN' | 'RANGE';
const SCENARIOS: Scenario[] = ['BREAKOUT_UP', 'APPROACH_UP', 'BREAKDOWN', 'RANGE', 'BREAKOUT_UP', 'APPROACH_DOWN', 'RANGE'];
export function scenarioFor(symbol: string): Scenario {
  return SCENARIOS[hash(symbol) % SCENARIOS.length]!;
}

/** The instant up to which intraday data exists: now if the session is live, else the last session's close. */
export function dataEnd(now: Date): Date {
  const p = istParts(now);
  const mins = p.hour * 60 + p.minute;
  if (dayStatus(p.dateStr).trading && mins >= SESSION_OPEN_MIN) {
    if (mins < SESSION_CLOSE_MIN) return new Date(Math.floor(now.getTime() / 60000) * 60000);
    return fromIst(p.dateStr, 15, 30);
  }
  const prev = dayStatus(p.dateStr).trading && mins < SESSION_OPEN_MIN ? previousTradingDay(p.dateStr) : previousTradingDay(p.dateStr);
  return fromIst(prev, 15, 30);
}

function sessionsEndingAt(end: Date, count: number): string[] {
  const days: string[] = [];
  let d = istDateStr(new Date(end.getTime() - 60_000));
  while (days.length < count) {
    if (dayStatus(d).trading) days.unshift(d);
    d = previousTradingDay(d);
  }
  return days;
}

/** Deterministic 1-minute bars: a range-bound market whose last 45 minutes follow the symbol's scenario. */
export function fixtureMinuteBars(yahooSymbol: string, now: Date, sessions: number): Candle[] {
  const r = rng(hash(`${yahooSymbol}|min`));
  const end = dataEnd(now);
  const anchor = fixtureDaily(yahooSymbol, now, 4).at(-1)!.c;
  const scenario = scenarioFor(yahooSymbol);
  const phase = r() * 6;
  const out: Candle[] = [];
  const days = sessionsEndingAt(end, sessions);
  const A = 0.0065; // band half-width
  const price = (g: number) => anchor * (1 + A * (0.7 * Math.sin(g / 140 + phase) + 0.3 * Math.sin(g / 41 + phase * 2)));
  let g = 0;
  const raw: { t: number; mid: number; day: string; i: number }[] = [];
  for (const day of days) {
    for (let i = 0; i < SESSION_CLOSE_MIN - SESSION_OPEN_MIN; i++, g++) {
      const t = fromIst(day, 9, 15).getTime() / 1000 + i * 60;
      if (t * 1000 >= end.getTime()) continue;
      raw.push({ t, mid: price(g), day, i });
    }
  }
  const tail = 45;
  const bandTop = Math.max(...raw.slice(0, Math.max(0, raw.length - tail)).map((x) => x.mid)) * (1 + 0.0015);
  const bandBottom = Math.min(...raw.slice(0, Math.max(0, raw.length - tail)).map((x) => x.mid)) * (1 - 0.0015);
  let prev = raw[0]?.mid ?? anchor;
  raw.forEach((x, k) => {
    const fromEnd = raw.length - 1 - k;
    let mid = x.mid;
    let volMult = 1;
    if (fromEnd < tail) {
      const f = (tail - fromEnd) / tail; // 0 -> 1 across the tail
      const startMid = raw[raw.length - tail]?.mid ?? mid;
      if (scenario === 'BREAKOUT_UP') { mid = startMid + (bandTop * 1.004 - startMid) * Math.min(1, f * 1.15); volMult = 1 + 2.6 * f; }
      else if (scenario === 'APPROACH_UP') { mid = startMid + (bandTop * 0.9985 - startMid) * f; volMult = 1 + 0.6 * f; }
      else if (scenario === 'BREAKDOWN') { mid = startMid + (bandBottom * 0.996 - startMid) * Math.min(1, f * 1.15); volMult = 1 + 2.6 * f; }
      else if (scenario === 'APPROACH_DOWN') { mid = startMid + (bandBottom * 1.0015 - startMid) * f; volMult = 1 + 0.6 * f; }
    }
    const noise = (r() - 0.5) * anchor * 0.0004;
    const c = mid + noise;
    const o = prev;
    const h = Math.max(o, c) + r() * anchor * 0.0003;
    const l = Math.min(o, c) - r() * anchor * 0.0003;
    const uShape = 1 + 0.9 * Math.exp(-x.i / 25) + 0.7 * Math.exp(-(374 - x.i) / 25);
    out.push({ t: x.t, o, h, l, c, v: Math.round((900 + r() * 500) * uShape * volMult) });
    prev = c;
  });
  return out;
}

/** Bars at 1m, 5m or 15m, built from the same minute series so every timeframe agrees. */
const memo = new Map<string, Candle[]>();

export function fixtureIntraday(yahooSymbol: string, interval: '1m' | '5m' | '15m', now: Date): Candle[] {
  const memoKey = `${yahooSymbol}|${interval}|${dataEnd(now).getTime()}`;
  const hit = memo.get(memoKey);
  if (hit) return hit;
  const out = buildIntraday(yahooSymbol, interval, now);
  if (memo.size > 400) memo.clear();
  memo.set(memoKey, out);
  return out;
}

function buildIntraday(yahooSymbol: string, interval: '1m' | '5m' | '15m', now: Date): Candle[] {
  const sessions = interval === '1m' ? 5 : 22;
  const mins = fixtureMinuteBars(yahooSymbol, now, sessions);
  if (interval === '1m') return mins;
  const target = interval === '5m' ? 5 : 15;
  // only whole buckets are emitted: a partial final bucket would be an off-grid snapshot in a real feed
  return aggregate(mins, 1, target, new Date(8.64e15)).filter((c) => c.complete || c.have > 0).map(({ complete: _c, have: _h, want: _w, ...c }) => c);
}

// ---- option chain ---------------------------------------------------------------------------------------------

export function strikeStepFor(spot: number): number {
  if (spot < 100) return 2.5;
  if (spot < 250) return 5;
  if (spot < 500) return 10;
  if (spot < 1000) return 20;
  if (spot < 2500) return 50;
  if (spot < 5000) return 100;
  return 200;
}

/** Last Tuesdays of the next three months (NSE monthly stock-option expiry day), strictly after today. */
export function fixtureStockExpiries(now: Date): string[] {
  const out: string[] = [];
  const base = istParts(now);
  let y = base.year;
  let m = base.month;
  while (out.length < 3) {
    const last = new Date(Date.UTC(y, m, 0));
    while (last.getUTCDay() !== 2) last.setUTCDate(last.getUTCDate() - 1);
    const iso = last.toISOString().slice(0, 10);
    if (iso > base.dateStr) out.push(iso);
    m += 1;
    if (m > 12) { m = 1; y += 1; }
  }
  return out;
}

export function fixtureEquityChain(underlying: string, spot: number, expiry: string, lotSize: number, now: Date): NormalizedChain {
  const r = rng(hash(underlying + expiry));
  const step = strikeStepFor(spot);
  const atm = Math.round(spot / step) * step;
  const daysLeft = Math.max(1, (Date.parse(`${expiry}T10:00:00Z`) - now.getTime()) / 86_400_000);
  const rows: ChainRow[] = [];
  const leg = (kind: 'CE' | 'PE', strike: number, k: number): OptionLeg => {
    const dist = Math.abs(k);
    const intrinsic = Math.max(0, kind === 'CE' ? spot - strike : strike - spot);
    const tv = Math.max(0.05, spot * 0.012 * Math.sqrt(daysLeft / 20) * Math.exp(-(dist * dist) / 14));
    const ltp = +(intrinsic + tv).toFixed(2);
    const far = dist >= 9; // far OTM: often untraded, no quote: exercises the "no quote" state
    const noQuote = far && r() < 0.7;
    const spreadFrac = 0.004 + dist * 0.004 + r() * 0.01;
    const bid = noQuote ? null : +(ltp * (1 - spreadFrac / 2)).toFixed(2);
    const ask = noQuote ? null : +(ltp * (1 + spreadFrac / 2)).toFixed(2);
    const oi = Math.round((4000 + r() * 6000) * Math.exp(-(dist * dist) / 60) * 3);
    const delta = (kind === 'CE' ? 1 : -1) * Math.max(0.02, Math.min(0.98, 0.5 - ((kind === 'CE' ? strike - spot : spot - strike) / (spot * 0.06))));
    void delta;
    return {
      ltp: noQuote ? 0 : ltp,
      oi,
      prevOi: Math.round(oi * (0.9 + r() * 0.2)),
      volume: noQuote ? 0 : Math.round(oi * (0.02 + r() * 0.15) * Math.exp(-dist / 6)),
      iv: noQuote ? null : +(24 + dist * 0.5 + r() * 2).toFixed(2),
      bid,
      ask,
      bidQty: bid === null ? null : Math.round((1 + r() * 20) * lotSize),
      askQty: ask === null ? null : Math.round((1 + r() * 20) * lotSize),
      instrumentKey: `DEMO-${underlying}-${expiry}-${kind}-${strike}`,
    };
  };
  for (let k = -12; k <= 12; k++) {
    const strike = +(atm + k * step).toFixed(2);
    rows.push({ strike, ce: leg('CE', strike, k), pe: leg('PE', strike, k) });
  }
  return { underlying, expiry, spot, rows, source: 'fixture', fetchedAt: now.toISOString(), asOf: now.toISOString() };
}

// ---- events -------------------------------------------------------------------------------------------------------

export function fixtureAnnouncements(symbol: string, now: Date): { symbol: string; desc: string; attchmntText: string; attchmntFile: string; an_dt: string; seq_id: string }[] {
  const fmt = (d: Date) => {
    const ist = new Date(d.getTime() + 330 * 60_000);
    const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][ist.getUTCMonth()]!;
    const p = (n: number) => String(n).padStart(2, '0');
    return `${p(ist.getUTCDate())}-${mon}-${ist.getUTCFullYear()} ${p(ist.getUTCHours())}:${p(ist.getUTCMinutes())}:${p(ist.getUTCSeconds())}`;
  };
  return [
    { symbol, desc: 'Analysts/Institutional Investor Meet/Con. Call Updates', attchmntText: 'DEMO DATA: schedule of an investor call.', attchmntFile: `https://nsearchives.nseindia.com/corporate/DEMO_${symbol}_1.pdf`, an_dt: fmt(new Date(now.getTime() - 3 * 3600_000)), seq_id: `demo-${symbol}-1` },
    { symbol, desc: 'Disclosure under SEBI Takeover Regulations', attchmntText: 'DEMO DATA: routine disclosure.', attchmntFile: `https://nsearchives.nseindia.com/corporate/DEMO_${symbol}_2.pdf`, an_dt: fmt(new Date(now.getTime() - 26 * 3600_000)), seq_id: `demo-${symbol}-2` },
  ];
}

export function fixtureEventCalendar(symbol: string, now: Date): { symbol: string; purpose: string; bm_desc: string; date: string }[] {
  if (hash(symbol) % 7 !== 0) return [];
  const d = new Date(now.getTime() + 2 * 86_400_000 + 330 * 60_000);
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()]!;
  return [{ symbol, purpose: 'Financial Results', bm_desc: 'DEMO DATA: quarterly results', date: `${String(d.getUTCDate()).padStart(2, '0')}-${mon}-${d.getUTCFullYear()}` }];
}

export const demoLotSize = (symbol: string): number => 50 * (1 + (hash(symbol) % 30));
export const demoStockBase = (symbol: string): number => basePrice(`${symbol}.NS`);
