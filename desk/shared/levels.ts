// Reproducible support/resistance, session levels, VWAP and relative volume.
// Every Level records HOW it was found so the UI can explain it. No LLM, no hand-drawn lines.

import { atr, ema, lastOf } from './indicators';
import { fromIst, istDateStr } from './india';
import { SESSION_OPEN_MIN, type TfCandle, type Timeframe } from './candles';

export type LevelMethod = 'SWING_CLUSTER' | 'PREV_SESSION_HIGH' | 'PREV_SESSION_LOW' | 'OPENING_RANGE_HIGH' | 'OPENING_RANGE_LOW' | 'ROLLING_HIGH' | 'ROLLING_LOW';

export interface Level {
  id: string;
  price: number;
  /** zone edges for clustered levels (equal to price for single-line levels) */
  low: number;
  high: number;
  kind: 'SUPPORT' | 'RESISTANCE';
  method: LevelMethod;
  tf: Timeframe;
  touches: number;
  /** epoch seconds of the first/last observation that formed it */
  from: number;
  to: number;
  explain: string;
}

export interface LevelParams {
  /** bars either side that must be lower/higher for a swing point */
  span: number;
  /** cluster tolerance as a fraction of ATR */
  tolAtr: number;
  /** completed bars scanned for swings */
  lookback: number;
  rollingWindow: number;
  openingRangeMin: number;
}
export const DEFAULT_LEVEL_PARAMS: LevelParams = { span: 3, tolAtr: 0.5, lookback: 150, rollingWindow: 20, openingRangeMin: 15 };

const round = (x: number) => Math.round(x * 100) / 100;

export function atrLast(c: TfCandle[], period = 14): number | null {
  return lastOf(atr(c, period));
}

/** Confirmed swing pivots: a pivot needs `span` bars after it, so the newest `span` bars can never be pivots. */
export function pivots(c: TfCandle[], span: number, lookback: number): { highs: { i: number; p: number }[]; lows: { i: number; p: number }[] } {
  const start = Math.max(span, c.length - lookback);
  const highs: { i: number; p: number }[] = [];
  const lows: { i: number; p: number }[] = [];
  for (let i = start; i < c.length - span; i++) {
    let hi = true;
    let lo = true;
    for (let j = 1; j <= span; j++) {
      if (c[i - j]!.h >= c[i]!.h || c[i + j]!.h > c[i]!.h) hi = false;
      if (c[i - j]!.l <= c[i]!.l || c[i + j]!.l < c[i]!.l) lo = false;
    }
    if (hi) highs.push({ i, p: c[i]!.h });
    if (lo) lows.push({ i, p: c[i]!.l });
  }
  return { highs, lows };
}

function cluster(points: { i: number; p: number }[], tol: number): { price: number; low: number; high: number; touches: number; first: number; last: number }[] {
  const sorted = [...points].sort((a, b) => a.p - b.p);
  const out: { ps: { i: number; p: number }[] }[] = [];
  for (const pt of sorted) {
    const g = out[out.length - 1];
    if (g && pt.p - g.ps[g.ps.length - 1]!.p <= tol) g.ps.push(pt);
    else out.push({ ps: [pt] });
  }
  return out.map((g) => {
    const ps = g.ps.map((x) => x.p);
    return { price: ps.reduce((a, b) => a + b, 0) / ps.length, low: Math.min(...ps), high: Math.max(...ps), touches: ps.length, first: Math.min(...g.ps.map((x) => x.i)), last: Math.max(...g.ps.map((x) => x.i)) };
  });
}

/**
 * Levels from completed candles only. Intraday session levels (previous-session extremes, opening range)
 * are produced only for intraday timeframes.
 */
export function computeLevels(all: TfCandle[], tf: Timeframe, params: Partial<LevelParams> = {}): Level[] {
  const p = { ...DEFAULT_LEVEL_PARAMS, ...params };
  const c = all.filter((x) => x.complete);
  if (c.length < 10) return [];
  const a = atrLast(c);
  const out: Level[] = [];
  const last = c[c.length - 1]!;

  if (a !== null && a > 0) {
    const pv = pivots(c, p.span, p.lookback);
    const tol = a * p.tolAtr;
    for (const [kind, pts] of [['RESISTANCE', pv.highs], ['SUPPORT', pv.lows]] as const) {
      for (const z of cluster(pts, tol)) {
        const t0 = c[z.first]!.t;
        const t1 = c[z.last]!.t;
        out.push({
          id: `${tf}:swing:${kind}:${round(z.price)}`,
          price: round(z.price),
          low: round(z.low),
          high: round(z.high),
          kind,
          method: 'SWING_CLUSTER',
          tf,
          touches: z.touches,
          from: t0,
          to: t1,
          explain: `${z.touches} confirmed swing ${kind === 'RESISTANCE' ? 'highs' : 'lows'} (${p.span} bars each side) within ${p.tolAtr}×ATR (${round(tol)}) of each other.`,
        });
      }
    }
  }

  const win = c.slice(-p.rollingWindow - 1, -1);
  if (win.length >= Math.min(10, p.rollingWindow)) {
    const hi = Math.max(...win.map((x) => x.h));
    const lo = Math.min(...win.map((x) => x.l));
    out.push({ id: `${tf}:roll:hi`, price: round(hi), low: round(hi), high: round(hi), kind: 'RESISTANCE', method: 'ROLLING_HIGH', tf, touches: 1, from: win[0]!.t, to: win[win.length - 1]!.t, explain: `Highest high of the ${win.length} completed bars before the latest.` });
    out.push({ id: `${tf}:roll:lo`, price: round(lo), low: round(lo), high: round(lo), kind: 'SUPPORT', method: 'ROLLING_LOW', tf, touches: 1, from: win[0]!.t, to: win[win.length - 1]!.t, explain: `Lowest low of the ${win.length} completed bars before the latest.` });
  }

  if (tf !== '1d') {
    const days = [...new Set(c.map((x) => istDateStr(new Date(x.t * 1000))))];
    const today = istDateStr(new Date(last.t * 1000));
    const prevDay = [...days].reverse().find((d) => d < today);
    if (prevDay) {
      const pd = c.filter((x) => istDateStr(new Date(x.t * 1000)) === prevDay);
      const hi = Math.max(...pd.map((x) => x.h));
      const lo = Math.min(...pd.map((x) => x.l));
      out.push({ id: `${tf}:pdh`, price: round(hi), low: round(hi), high: round(hi), kind: 'RESISTANCE', method: 'PREV_SESSION_HIGH', tf, touches: 1, from: pd[0]!.t, to: pd[pd.length - 1]!.t, explain: `High of the previous session (${prevDay}).` });
      out.push({ id: `${tf}:pdl`, price: round(lo), low: round(lo), high: round(lo), kind: 'SUPPORT', method: 'PREV_SESSION_LOW', tf, touches: 1, from: pd[0]!.t, to: pd[pd.length - 1]!.t, explain: `Low of the previous session (${prevDay}).` });
    }
    const orEnd = fromIst(today, 0, 0).getTime() / 1000 + (SESSION_OPEN_MIN + p.openingRangeMin) * 60;
    const orStart = fromIst(today, 0, 0).getTime() / 1000 + SESSION_OPEN_MIN * 60;
    const or = c.filter((x) => x.t >= orStart && x.t < orEnd);
    const tfMin = tf === '1h' ? 60 : Number.parseInt(tf, 10);
    // the opening range needs the whole window covered by completed bars
    if (or.length > 0 && or.length * tfMin >= p.openingRangeMin && or[or.length - 1]!.t + tfMin * 60 >= orEnd) {
      const hi = Math.max(...or.map((x) => x.h));
      const lo = Math.min(...or.map((x) => x.l));
      out.push({ id: `${tf}:orh`, price: round(hi), low: round(hi), high: round(hi), kind: 'RESISTANCE', method: 'OPENING_RANGE_HIGH', tf, touches: 1, from: or[0]!.t, to: or[or.length - 1]!.t, explain: `High of the first ${p.openingRangeMin} minutes of today's session.` });
      out.push({ id: `${tf}:orl`, price: round(lo), low: round(lo), high: round(lo), kind: 'SUPPORT', method: 'OPENING_RANGE_LOW', tf, touches: 1, from: or[0]!.t, to: or[or.length - 1]!.t, explain: `Low of the first ${p.openingRangeMin} minutes of today's session.` });
    }
  }
  return out.sort((x, y) => x.price - y.price);
}

/** Nearest levels strictly above/below `price`. */
export function nearest(levels: Level[], price: number): { above: Level | null; below: Level | null } {
  let above: Level | null = null;
  let below: Level | null = null;
  for (const l of levels) {
    if (l.price > price && (!above || l.price < above.price)) above = l;
    if (l.price < price && (!below || l.price > below.price)) below = l;
  }
  return { above, below };
}

// ---- VWAP ---------------------------------------------------------------------------------------------------

/** Session-anchored VWAP (typical price × volume). null for a session in which any bar's volume is unknown or total volume is 0. */
export function sessionVwap(c: TfCandle[]): (number | null)[] {
  const out: (number | null)[] = [];
  let day = '';
  let pv = 0;
  let vol = 0;
  let broken = false;
  for (const x of c) {
    const d = istDateStr(new Date(x.t * 1000));
    if (d !== day) { day = d; pv = 0; vol = 0; broken = false; }
    if (x.v === null) broken = true;
    else { pv += ((x.h + x.l + x.c) / 3) * x.v; vol += x.v; }
    out.push(!broken && vol > 0 ? pv / vol : null);
  }
  return out;
}

export interface VwapRead {
  vwap: number | null;
  distPct: number | null;
  /** price relative to VWAP, and a reclaim/rejection if the last completed bars crossed it */
  position: 'ABOVE' | 'BELOW' | 'AT' | 'UNKNOWN';
  event: 'RECLAIM' | 'REJECTION' | null;
  note: string;
}

export function vwapRead(c: TfCandle[], price: number): VwapRead {
  const done = c.filter((x) => x.complete);
  const v = sessionVwap(done);
  const last = v[v.length - 1] ?? null;
  if (last === null) return { vwap: null, distPct: null, position: 'UNKNOWN', event: null, note: 'VWAP unavailable: no intraday volume for this session (indices and illiquid names report none).' };
  const dist = ((price - last) / last) * 100;
  const position = Math.abs(dist) < 0.05 ? 'AT' : dist > 0 ? 'ABOVE' : 'BELOW';
  let event: VwapRead['event'] = null;
  const n = done.length;
  if (n >= 3 && v[n - 2] !== null && v[n - 3] !== null) {
    const prevClose = done[n - 2]!.c;
    const prevV = v[n - 2]!;
    const lastClose = done[n - 1]!.c;
    if (prevClose < prevV && lastClose > last) event = 'RECLAIM';
    else if (prevClose > prevV && lastClose < last) event = 'REJECTION';
  }
  return { vwap: round(last), distPct: dist, position, event, note: 'Session-anchored VWAP of completed bars (typical price × volume).' };
}

// ---- Relative volume --------------------------------------------------------------------------------------

export interface RelVol {
  value: number | null;
  baseline: number | null;
  samples: number;
  method: 'TIME_OF_DAY_CUMULATIVE' | 'PRIOR_BARS_MEAN' | 'NONE';
  note: string;
}

export const MIN_RVOL_SESSIONS = 4;
export const MIN_RVOL_BARS = 10;

/**
 * Today's cumulative volume up to the current time of day vs the average of the same cumulative
 * measure over previous complete sessions. Requires at least MIN_RVOL_SESSIONS prior sessions with
 * known volume. Comparing a half-day volume to a full-day average would understate relative volume,
 * which is why the cumulative-to-same-time form is used.
 */
export function cumulativeRvol(c: TfCandle[]): RelVol {
  const none = (note: string): RelVol => ({ value: null, baseline: null, samples: 0, method: 'NONE', note });
  const done = c.filter((x) => x.complete);
  if (done.length === 0) return none('No completed bars.');
  const lastT = done[done.length - 1]!.t;
  const today = istDateStr(new Date(lastT * 1000));
  const secToday = lastT - fromIst(today, 0, 0).getTime() / 1000;
  const byDay = new Map<string, TfCandle[]>();
  for (const x of done) {
    const d = istDateStr(new Date(x.t * 1000));
    byDay.set(d, [...(byDay.get(d) ?? []), x]);
  }
  const cum = (bars: TfCandle[], d: string): number | null => {
    const cutoff = fromIst(d, 0, 0).getTime() / 1000 + secToday;
    let s = 0;
    for (const b of bars) {
      if (b.t > cutoff) continue;
      if (b.v === null) return null;
      s += b.v;
    }
    return s;
  };
  const todayCum = cum(byDay.get(today) ?? [], today);
  if (todayCum === null) return none('Volume is unknown for part of today\'s session.');
  const prior: number[] = [];
  for (const [d, bars] of byDay) {
    if (d >= today) continue;
    // a prior session counts only if it is complete up to the closing bar
    if (bars.length < 3) continue;
    const v = cum(bars, d);
    if (v !== null && v > 0) prior.push(v);
  }
  if (prior.length < MIN_RVOL_SESSIONS) return none(`Only ${prior.length} prior sessions with known volume (need ${MIN_RVOL_SESSIONS}); relative volume not computed.`);
  const base = prior.reduce((a, b) => a + b, 0) / prior.length;
  return { value: todayCum / base, baseline: base, samples: prior.length, method: 'TIME_OF_DAY_CUMULATIVE', note: `Cumulative volume to this time of day vs the mean of ${prior.length} prior sessions.` };
}

/** Per-bar volume vs the mean of the previous `period` completed bars with known volume (used for the breakout candle itself). */
export function barRvol(c: TfCandle[], idx: number, period = 20): RelVol {
  const none = (note: string): RelVol => ({ value: null, baseline: null, samples: 0, method: 'NONE', note });
  const bar = c[idx];
  if (!bar || bar.v === null) return none('Volume of this bar is unknown.');
  const prior = c.slice(Math.max(0, idx - period), idx).filter((x) => x.complete).map((x) => x.v).filter((v): v is number => v !== null);
  if (prior.length < MIN_RVOL_BARS) return none(`Only ${prior.length} prior bars with known volume (need ${MIN_RVOL_BARS}).`);
  const base = prior.reduce((a, b) => a + b, 0) / prior.length;
  if (!(base > 0)) return none('Prior volume baseline is zero.');
  return { value: bar.v / base, baseline: base, samples: prior.length, method: 'PRIOR_BARS_MEAN', note: `Bar volume vs the mean of the previous ${prior.length} bars.` };
}

// ---- Trend / structure ---------------------------------------------------------------------------------------

export type Trend = 'UP' | 'DOWN' | 'RANGE' | 'UNKNOWN';

export interface TrendRead {
  trend: Trend;
  structure: 'HH_HL' | 'LH_LL' | 'MIXED' | 'UNKNOWN';
  evidence: string[];
}

/** UP: close > EMA20 > EMA50 and EMA20 rising. DOWN is the mirror. Anything else is RANGE. Swing structure is reported alongside. */
export function trendRead(all: TfCandle[]): TrendRead {
  const c = all.filter((x) => x.complete);
  if (c.length < 50) return { trend: 'UNKNOWN', structure: 'UNKNOWN', evidence: [`Only ${c.length} completed bars; the 50-bar EMA needs 50.`] };
  const closes = c.map((x) => x.c);
  const e20 = ema(closes, 20);
  const e50 = ema(closes, 50);
  const a = lastOf(e20)!;
  const b = lastOf(e50)!;
  const a5 = e20[e20.length - 6];
  const last = closes[closes.length - 1]!;
  const rising = a5 != null && a > a5;
  const falling = a5 != null && a < a5;
  const trend: Trend = last > a && a > b && rising ? 'UP' : last < a && a < b && falling ? 'DOWN' : 'RANGE';
  const pv = pivots(c, 3, 150);
  const h = pv.highs.slice(-2);
  const l = pv.lows.slice(-2);
  let structure: TrendRead['structure'] = 'UNKNOWN';
  if (h.length === 2 && l.length === 2) {
    const hh = h[1]!.p > h[0]!.p;
    const hl = l[1]!.p > l[0]!.p;
    structure = hh && hl ? 'HH_HL' : !hh && !hl ? 'LH_LL' : 'MIXED';
  }
  const evidence = [`Close ${round(last)} vs EMA20 ${round(a)} vs EMA50 ${round(b)}; EMA20 ${rising ? 'rising' : falling ? 'falling' : 'flat'} over 5 bars.`];
  if (structure !== 'UNKNOWN') evidence.push(structure === 'HH_HL' ? 'Last two swing highs and lows are both higher.' : structure === 'LH_LL' ? 'Last two swing highs and lows are both lower.' : 'Swing highs and lows disagree.');
  return { trend, structure, evidence };
}

export interface MtfAgreement {
  alignment: 'UP' | 'DOWN' | 'MIXED' | 'UNKNOWN';
  agreeing: Timeframe[];
  disagreeing: Timeframe[];
  unknown: Timeframe[];
  detail: string;
}

/** Timeframes agree only if every known timeframe shows the same non-RANGE trend. At least two known timeframes are required. */
export function mtfAgreement(states: Partial<Record<Timeframe, Trend>>): MtfAgreement {
  const entries = Object.entries(states) as [Timeframe, Trend][];
  const known = entries.filter(([, t]) => t !== 'UNKNOWN');
  const unknown = entries.filter(([, t]) => t === 'UNKNOWN').map(([k]) => k);
  if (known.length < 2) return { alignment: 'UNKNOWN', agreeing: [], disagreeing: [], unknown, detail: 'Fewer than two timeframes have enough data to compare.' };
  const up = known.filter(([, t]) => t === 'UP').map(([k]) => k);
  const down = known.filter(([, t]) => t === 'DOWN').map(([k]) => k);
  const range = known.filter(([, t]) => t === 'RANGE').map(([k]) => k);
  if (up.length === known.length) return { alignment: 'UP', agreeing: up, disagreeing: [], unknown, detail: `All ${known.length} known timeframes trend up.` };
  if (down.length === known.length) return { alignment: 'DOWN', agreeing: down, disagreeing: [], unknown, detail: `All ${known.length} known timeframes trend down.` };
  const majority = up.length >= down.length ? up : down;
  return { alignment: 'MIXED', agreeing: majority, disagreeing: [...(majority === up ? down : up), ...range], unknown, detail: `Up: ${up.join(', ') || 'none'}; down: ${down.join(', ') || 'none'}; range: ${range.join(', ') || 'none'}.` };
}

// ---- Range compression, gaps, price-volume expansion ------------------------------------------------

export function compression(all: TfCandle[], n = 10): { compressed: boolean | null; ratio: number | null; note: string } {
  const c = all.filter((x) => x.complete);
  if (c.length < n * 4) return { compressed: null, ratio: null, note: `Needs ${n * 4} completed bars.` };
  const rng = (cs: TfCandle[]) => cs.reduce((a, x) => a + (x.h - x.l), 0) / cs.length;
  const recent = rng(c.slice(-n));
  const prior = rng(c.slice(-4 * n, -n));
  const ratio = prior > 0 ? recent / prior : null;
  return { compressed: ratio === null ? null : ratio <= 0.6, ratio, note: `Mean bar range of the last ${n} bars vs the ${3 * n} before them (compressed when ≤ 0.60).` };
}

export function gapRead(c: TfCandle[]): { gapPct: number | null; kind: 'GAP_UP' | 'GAP_DOWN' | null; note: string } {
  const done = c.filter((x) => x.complete);
  if (done.length < 2) return { gapPct: null, kind: null, note: 'Not enough bars.' };
  // for intraday the gap is today's first open vs the previous session's last close
  const last = done[done.length - 1]!;
  const today = istDateStr(new Date(last.t * 1000));
  const first = done.find((x) => istDateStr(new Date(x.t * 1000)) === today)!;
  const before = [...done].reverse().find((x) => istDateStr(new Date(x.t * 1000)) < today);
  if (!before || before.c <= 0) return { gapPct: null, kind: null, note: 'No previous session in range.' };
  const gap = ((first.o - before.c) / before.c) * 100;
  return { gapPct: gap, kind: gap >= 0.5 ? 'GAP_UP' : gap <= -0.5 ? 'GAP_DOWN' : null, note: `Open ${round(first.o)} vs previous close ${round(before.c)} (flagged at ±0.5%).` };
}

export function priceVolumeExpansion(all: TfCandle[]): { expanded: boolean | null; note: string } {
  const c = all.filter((x) => x.complete);
  if (c.length < MIN_RVOL_BARS + 15) return { expanded: null, note: 'Not enough bars.' };
  const a = atrLast(c.slice(0, -1));
  const rv = barRvol(c, c.length - 1);
  if (a === null || rv.value === null) return { expanded: null, note: rv.value === null ? rv.note : 'ATR unavailable.' };
  const last = c[c.length - 1]!;
  const rangeAtr = (last.h - last.l) / a;
  return { expanded: rangeAtr >= 1.5 && rv.value >= 1.5, note: `Last bar range ${rangeAtr.toFixed(2)}×ATR with volume ${rv.value.toFixed(2)}× its recent mean (expansion needs both ≥ 1.5).` };
}
