// Deterministic technical analysis on daily candles. Facts and observations
// only - this module never emits BUY/SELL advice (same contract as TIRE).

import type { Candle } from './types';

export function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i]!;
    if (i >= period) sum -= values[i - period]!;
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

export function ema(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length < period) return out;
  const k = 2 / (period + 1);
  let prev = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out[period - 1] = prev;
  for (let i = period; i < values.length; i++) {
    prev = values[i]! * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/** Wilder RSI */
export function rsi(closes: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i]! - closes[i - 1]!;
    if (d >= 0) gain += d;
    else loss -= d;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i]! - closes[i - 1]!;
    avgGain = (avgGain * (period - 1) + Math.max(d, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-d, 0)) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

/** Wilder ATR */
export function atr(candles: Candle[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(candles.length).fill(null);
  if (candles.length <= period) return out;
  const tr: number[] = [];
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]!;
    if (i === 0) tr.push(c.h - c.l);
    else {
      const pc = candles[i - 1]!.c;
      tr.push(Math.max(c.h - c.l, Math.abs(c.h - pc), Math.abs(c.l - pc)));
    }
  }
  let prev = tr.slice(1, period + 1).reduce((a, b) => a + b, 0) / period;
  out[period] = prev;
  for (let i = period + 1; i < candles.length; i++) {
    prev = (prev * (period - 1) + tr[i]!) / period;
    out[i] = prev;
  }
  return out;
}

export function lastOf<T>(arr: (T | null)[]): T | null {
  for (let i = arr.length - 1; i >= 0; i--) if (arr[i] !== null) return arr[i] as T;
  return null;
}

/** Minimum days with a known volume before a 20-day average is trusted. */
export const MIN_VOL_SAMPLES = 10;

/** A valid traded volume (finite, >= 0, a real number) or null. Never coerces missing/garbage to 0. */
export function knownVolume(x: unknown): number | null {
  return typeof x === 'number' && Number.isFinite(x) && x >= 0 ? x : null;
}

export interface PivotLevels {
  pivot: number;
  r1: number;
  r2: number;
  s1: number;
  s2: number;
}

/** Classic floor-trader pivots from a completed session's H/L/C. */
export function classicPivots(h: number, l: number, c: number): PivotLevels {
  const p = (h + l + c) / 3;
  return { pivot: p, r1: 2 * p - l, s1: 2 * p - h, r2: p + (h - l), s2: p - (h - l) };
}

/** Swing highs/lows (fractals) over `span` bars each side. */
export function swingLevels(candles: Candle[], span = 3, lookback = 120): { highs: number[]; lows: number[] } {
  const start = Math.max(span, candles.length - lookback);
  const highs: number[] = [];
  const lows: number[] = [];
  for (let i = start; i < candles.length - span; i++) {
    const c = candles[i]!;
    let isHigh = true;
    let isLow = true;
    for (let j = 1; j <= span; j++) {
      if (candles[i - j]!.h >= c.h || candles[i + j]!.h > c.h) isHigh = false;
      if (candles[i - j]!.l <= c.l || candles[i + j]!.l < c.l) isLow = false;
    }
    if (isHigh) highs.push(c.h);
    if (isLow) lows.push(c.l);
  }
  return { highs, lows };
}

export type Observation =
  | 'NEAR_52W_HIGH'
  | 'NEAR_52W_LOW'
  | 'BREAKOUT_20D_WITH_VOLUME'
  | 'BREAKDOWN_20D_WITH_VOLUME'
  | 'VOLUME_SURGE'
  | 'RSI_OVERBOUGHT'
  | 'RSI_OVERSOLD'
  | 'ABOVE_200DMA'
  | 'BELOW_200DMA'
  | 'GOLDEN_CROSS_RECENT'
  | 'DEATH_CROSS_RECENT'
  | 'GAP_UP'
  | 'GAP_DOWN'
  | 'TIGHT_RANGE';

export interface TechSnapshot {
  lastClose: number;
  asOfT: number;
  sma20: number | null;
  sma50: number | null;
  sma200: number | null;
  ema20: number | null;
  rsi14: number | null;
  atr14: number | null;
  atrPct: number | null;
  high52w: number | null;
  low52w: number | null;
  /** 0 = at 52w low, 100 = at 52w high */
  pos52w: number | null;
  distFromHigh52wPct: number | null;
  volRatio20: number | null;
  trend: 'UP' | 'DOWN' | 'SIDEWAYS' | 'UNKNOWN';
  pivots: PivotLevels | null;
  support: number | null;
  resistance: number | null;
  observations: Observation[];
  bars: number;
}

export const OBSERVATION_LABEL: Record<Observation, string> = {
  NEAR_52W_HIGH: 'Near 52-week high',
  NEAR_52W_LOW: 'Near 52-week low',
  BREAKOUT_20D_WITH_VOLUME: '20-day high break on volume',
  BREAKDOWN_20D_WITH_VOLUME: '20-day low break on volume',
  VOLUME_SURGE: 'Volume surge',
  RSI_OVERBOUGHT: 'RSI above 70',
  RSI_OVERSOLD: 'RSI below 30',
  ABOVE_200DMA: 'Above 200-DMA',
  BELOW_200DMA: 'Below 200-DMA',
  GOLDEN_CROSS_RECENT: '50/200 golden cross (recent)',
  DEATH_CROSS_RECENT: '50/200 death cross (recent)',
  GAP_UP: 'Gap up open',
  GAP_DOWN: 'Gap down open',
  TIGHT_RANGE: 'Contracting range',
};

/**
 * Analyse completed + current daily candles (oldest first). The last candle
 * may be the live session; every "prior N" window excludes it so a breakout is
 * measured against history, not against itself.
 */
export function analyze(candles: Candle[]): TechSnapshot | null {
  if (candles.length < 2) return null;
  const closes = candles.map((c) => c.c);
  const last = candles[candles.length - 1]!;
  const prior = candles.slice(0, -1);

  const s20 = sma(closes, 20);
  const s50 = sma(closes, 50);
  const s200 = sma(closes, 200);
  const e20 = ema(closes, 20);
  const r14 = rsi(closes, 14);
  const a14 = atr(candles, 14);

  const win252 = candles.slice(-252);
  const high52 = Math.max(...win252.map((c) => c.h));
  const low52 = Math.min(...win252.map((c) => c.l));
  const range = high52 - low52;
  const pos52 = range > 0 ? ((last.c - low52) / range) * 100 : null;

  const prior20 = prior.slice(-20);
  const prior20High = prior20.length >= 10 ? Math.max(...prior20.map((c) => c.h)) : null;
  const prior20Low = prior20.length >= 10 ? Math.min(...prior20.map((c) => c.l)) : null;
  // Unknown (null) volume is never treated as zero: the average uses only days
  // with a known volume and needs at least MIN_VOL_SAMPLES of them, and the
  // current day's volume must itself be known. Otherwise there is no ratio.
  const knownPrior = prior20.map((c) => c.v).filter((v): v is number => v !== null);
  const avgVol20 = knownPrior.length >= MIN_VOL_SAMPLES ? knownPrior.reduce((a, v) => a + v, 0) / knownPrior.length : null;
  const volRatio = avgVol20 && avgVol20 > 0 && last.v !== null ? last.v / avgVol20 : null;

  const sma20v = lastOf(s20);
  const sma50v = lastOf(s50);
  const sma200v = lastOf(s200);
  const rsiV = lastOf(r14);
  const atrV = lastOf(a14);

  let trend: TechSnapshot['trend'] = 'UNKNOWN';
  if (sma20v !== null && sma50v !== null) {
    if (last.c > sma20v && sma20v > sma50v) trend = 'UP';
    else if (last.c < sma20v && sma20v < sma50v) trend = 'DOWN';
    else trend = 'SIDEWAYS';
  }

  const obs: Observation[] = [];
  const distHigh = ((high52 - last.c) / high52) * 100;
  const distLow = low52 > 0 ? ((last.c - low52) / low52) * 100 : 100;
  if (candles.length >= 60 && distHigh <= 2) obs.push('NEAR_52W_HIGH');
  if (candles.length >= 60 && distLow <= 2) obs.push('NEAR_52W_LOW');
  if (prior20High !== null && last.c > prior20High && volRatio !== null && volRatio >= 1.5) obs.push('BREAKOUT_20D_WITH_VOLUME');
  if (prior20Low !== null && last.c < prior20Low && volRatio !== null && volRatio >= 1.5) obs.push('BREAKDOWN_20D_WITH_VOLUME');
  if (volRatio !== null && volRatio >= 2) obs.push('VOLUME_SURGE');
  if (rsiV !== null && rsiV >= 70) obs.push('RSI_OVERBOUGHT');
  if (rsiV !== null && rsiV <= 30) obs.push('RSI_OVERSOLD');
  if (sma200v !== null) obs.push(last.c >= sma200v ? 'ABOVE_200DMA' : 'BELOW_200DMA');

  // Cross within the last 10 sessions
  if (s50.length >= 210) {
    for (let i = candles.length - 10; i < candles.length; i++) {
      const a = s50[i];
      const b = s200[i];
      const pa = s50[i - 1];
      const pb = s200[i - 1];
      if (a == null || b == null || pa == null || pb == null) continue;
      if (pa <= pb && a > b) obs.push('GOLDEN_CROSS_RECENT');
      if (pa >= pb && a < b) obs.push('DEATH_CROSS_RECENT');
    }
  }

  const prev = candles[candles.length - 2]!;
  if (prev.c > 0) {
    const gap = ((last.o - prev.c) / prev.c) * 100;
    if (gap >= 1) obs.push('GAP_UP');
    else if (gap <= -1) obs.push('GAP_DOWN');
  }

  // Contracting range: 5-day avg true range under 70% of the 20-day average
  if (candles.length >= 25) {
    const rng = (cs: Candle[]) => cs.reduce((a, c) => a + (c.h - c.l), 0) / cs.length;
    if (rng(candles.slice(-5)) < 0.7 * rng(candles.slice(-20))) obs.push('TIGHT_RANGE');
  }

  // Pivots from the last COMPLETED session
  const pivots = classicPivots(prev.h, prev.l, prev.c);

  // Nearest swing support/resistance from history, relative to last close
  const sw = swingLevels(candles);
  const support = sw.lows.filter((x) => x < last.c).sort((a, b) => b - a)[0] ?? null;
  const resistance = sw.highs.filter((x) => x > last.c).sort((a, b) => a - b)[0] ?? null;

  return {
    lastClose: last.c,
    asOfT: last.t,
    sma20: sma20v,
    sma50: sma50v,
    sma200: sma200v,
    ema20: lastOf(e20),
    rsi14: rsiV,
    atr14: atrV,
    atrPct: atrV !== null && last.c > 0 ? (atrV / last.c) * 100 : null,
    high52w: high52,
    low52w: low52,
    pos52w: pos52,
    distFromHigh52wPct: distHigh,
    volRatio20: volRatio,
    trend,
    pivots,
    support,
    resistance,
    observations: [...new Set(obs)],
    bars: candles.length,
  };
}

export interface BollingerPoint {
  mid: number;
  upper: number;
  lower: number;
}

/** Bollinger bands: SMA(period) ± mult × population standard deviation. null until `period` closes exist. */
export function bollinger(values: number[], period = 20, mult = 2): (BollingerPoint | null)[] {
  const out: (BollingerPoint | null)[] = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += values[j]!;
    const mid = sum / period;
    let sq = 0;
    for (let j = i - period + 1; j <= i; j++) sq += (values[j]! - mid) ** 2;
    const sd = Math.sqrt(sq / period);
    out[i] = { mid, upper: mid + mult * sd, lower: mid - mult * sd };
  }
  return out;
}

export interface MacdPoint {
  macd: number;
  signal: number | null;
  hist: number | null;
}

/** MACD(fast, slow, signal) on closes. The line exists from `slow` bars on; the signal from `slow + signal - 1`. */
export function macd(values: number[], fast = 12, slow = 26, signalPeriod = 9): (MacdPoint | null)[] {
  const f = ema(values, fast);
  const s = ema(values, slow);
  const line: (number | null)[] = values.map((_, i) => (f[i] != null && s[i] != null ? f[i]! - s[i]! : null));
  const first = line.findIndex((x) => x !== null);
  const out: (MacdPoint | null)[] = new Array(values.length).fill(null);
  if (first < 0) return out;
  const sig = ema(line.slice(first) as number[], signalPeriod);
  for (let i = first; i < values.length; i++) {
    const sv = sig[i - first] ?? null;
    out[i] = { macd: line[i]!, signal: sv, hist: sv === null ? null : line[i]! - sv };
  }
  return out;
}
