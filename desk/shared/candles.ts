// Candle aggregation aligned to the NSE cash session (09:15-15:30 IST).
//
// Rules (also documented in WORKBENCH.md):
//  * Buckets are anchored to 09:15 IST, not to the clock hour: 1h bars start 09:15, 10:15 ... 15:15 (the last is 15 minutes).
//  * Only source bars inside the session are used; pre/post-session rows are dropped.
//  * A bucket is `complete` only when every expected source bar is present, none is an off-grid
//    snapshot bar, and the bucket's end time has passed. Incomplete buckets are returned (so a chart can
//    show the forming bar) but flagged, and analysis must never treat them as a closed candle.
//  * Missing source bars are never invented. A bucket with a hole is incomplete.
//  * Volume is summed only when every contributing bar has a known volume; otherwise it is unknown (null), not 0.

import { fromIst, istDateStr, istParts } from './india';
import type { Candle } from './types';

export type Timeframe = '1m' | '3m' | '5m' | '15m' | '30m' | '1h' | '1d';
export const TIMEFRAMES: readonly Timeframe[] = ['1m', '3m', '5m', '15m', '30m', '1h', '1d'];
export const TF_LABEL: Record<Timeframe, string> = { '1m': '1 min', '3m': '3 min', '5m': '5 min', '15m': '15 min', '30m': '30 min', '1h': '1 hour', '1d': 'Daily' };
export const TF_MINUTES: Record<Exclude<Timeframe, '1d'>, number> = { '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60 };

export const SESSION_OPEN_MIN = 9 * 60 + 15;
export const SESSION_CLOSE_MIN = 15 * 60 + 30;
export const SESSION_MINUTES = SESSION_CLOSE_MIN - SESSION_OPEN_MIN; // 375

/** Fewest completed bars on which any indicator or structure is computed. */
export const MIN_ANALYSIS_BARS = 30;

export interface TfCandle extends Candle {
  complete: boolean;
  /** source bars present / expected for this bucket */
  have: number;
  want: number;
}

/** Which provider interval/range feeds a timeframe, and the source granularity it is built from. */
export const SOURCE_FOR: Record<Exclude<Timeframe, '1d'>, { interval: '1m' | '5m' | '15m'; range: '5d' | '1mo'; srcMin: number }> = {
  '1m': { interval: '1m', range: '5d', srcMin: 1 },
  '3m': { interval: '1m', range: '5d', srcMin: 1 },
  '5m': { interval: '5m', range: '1mo', srcMin: 5 },
  '15m': { interval: '15m', range: '1mo', srcMin: 15 },
  '30m': { interval: '15m', range: '1mo', srcMin: 15 },
  '1h': { interval: '15m', range: '1mo', srcMin: 15 },
};

function secondsIntoSession(t: number): number {
  const p = istParts(new Date(t * 1000));
  return (p.hour * 60 + p.minute) * 60 + p.second - SESSION_OPEN_MIN * 60;
}

interface Snapped {
  day: string;
  slot: number; // index of the source-grid slot within the session
  offGrid: boolean;
  c: Candle;
}

function snap(source: Candle[], srcMin: number): Snapped[] {
  const out = new Map<string, Snapped>();
  for (const c of source) {
    const s = secondsIntoSession(c.t);
    if (s < 0 || s >= SESSION_MINUTES * 60) continue;
    const step = srcMin * 60;
    const slot = Math.floor(s / step);
    const offGrid = s % step !== 0;
    const day = istDateStr(new Date(c.t * 1000));
    const key = `${day}#${slot}`;
    const prev = out.get(key);
    // an exact-grid bar always beats an off-grid snapshot of the same slot; otherwise keep the later observation
    if (prev && !prev.offGrid && offGrid) continue;
    out.set(key, { day, slot, offGrid, c });
  }
  return [...out.values()].sort((a, b) => (a.day === b.day ? a.slot - b.slot : a.day < b.day ? -1 : 1));
}

/**
 * Aggregate session-aligned source bars of `srcMin` minutes into `targetMin`-minute bars.
 * `targetMin` must be a whole multiple of `srcMin`.
 */
export function aggregate(source: Candle[], srcMin: number, targetMin: number, now: Date): TfCandle[] {
  if (targetMin < srcMin || targetMin % srcMin !== 0) throw new Error(`Cannot build ${targetMin}-minute bars from ${srcMin}-minute bars`);
  const per = targetMin / srcMin;
  const groups = new Map<string, Snapped[]>();
  for (const s of snap(source, srcMin)) {
    const b = Math.floor(s.slot / per);
    const key = `${s.day}#${b}`;
    const g = groups.get(key);
    if (g) g.push(s);
    else groups.set(key, [s]);
  }
  const out: TfCandle[] = [];
  for (const [key, g] of groups) {
    const [day, bs] = key.split('#') as [string, string];
    const b = Number(bs);
    const startMin = SESSION_OPEN_MIN + b * targetMin;
    const lenMin = Math.min(targetMin, SESSION_CLOSE_MIN - startMin);
    const want = Math.ceil(lenMin / srcMin);
    const start = fromIst(day, Math.floor(startMin / 60), startMin % 60);
    const end = new Date(start.getTime() + lenMin * 60_000);
    const cs = g.map((x) => x.c);
    const vols = cs.map((c) => c.v);
    const v = vols.every((x): x is number => x !== null) ? vols.reduce((a, x) => a + x, 0) : null;
    const have = g.length;
    const anyOffGrid = g.some((x) => x.offGrid);
    out.push({
      t: Math.floor(start.getTime() / 1000),
      o: cs[0]!.o,
      h: Math.max(...cs.map((c) => c.h)),
      l: Math.min(...cs.map((c) => c.l)),
      c: cs[cs.length - 1]!.c,
      v,
      have,
      want,
      complete: have === want && !anyOffGrid && end.getTime() <= now.getTime(),
    });
  }
  return out.sort((a, b) => a.t - b.t);
}

/** Daily bars: the latest session's bar is complete only after the 15:30 IST close. */
export function markDaily(candles: Candle[], now: Date): TfCandle[] {
  const today = istDateStr(now);
  const p = istParts(now);
  const afterClose = p.hour * 60 + p.minute >= SESSION_CLOSE_MIN;
  return candles.map((c) => {
    const day = istDateStr(new Date(c.t * 1000));
    const complete = day < today || (day === today && afterClose);
    return { ...c, complete, have: 1, want: 1 };
  });
}

export interface TfAvailability {
  tf: Timeframe;
  available: boolean;
  /** completed bars usable for analysis */
  bars: number;
  reason: string | null;
}

export function availability(tf: Timeframe, candles: TfCandle[] | null, providerError?: string): TfAvailability {
  if (!candles) return { tf, available: false, bars: 0, reason: providerError ?? 'No data was retrieved for this interval.' };
  const bars = candles.filter((c) => c.complete).length;
  if (bars < MIN_ANALYSIS_BARS) return { tf, available: false, bars, reason: `Only ${bars} completed ${TF_LABEL[tf]} bars are available; at least ${MIN_ANALYSIS_BARS} are needed for indicators and structure.` };
  return { tf, available: true, bars, reason: null };
}

export function completed(candles: TfCandle[]): TfCandle[] {
  return candles.filter((c) => c.complete);
}
