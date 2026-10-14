// Explainable breakout lifecycle. Deterministic rules on COMPLETED candles only.
//
//   NOT_READY -> APPROACHING_LEVEL -> POTENTIAL_BREAKOUT -> CONFIRMATION_PENDING -> CONFIRMED
//   plus FAILED, INVALIDATED, DATA_INSUFFICIENT, STALE.
//
// A single tick through a line is never a confirmation. CONFIRMED needs, on completed bars:
//   1. a close beyond level ± bufferAtr×ATR,
//   2. breakout-bar volume ≥ minRvol × its recent mean (a documented baseline with enough samples),
//   3. a hold: `confirmCloses` consecutive closes beyond the level, or a retest that holds the level,
//   4. data that is VALID or from a closed market (never STALE / CONFLICT / UNKNOWN).
// Downside setups are the exact mirror and are computed by reflecting prices, so there is one code path.

import { MIN_ANALYSIS_BARS, TF_LABEL, type TfCandle, type Timeframe } from './candles';
import { atrLast, barRvol, nearest, type Level, type RelVol, type Trend } from './levels';
import type { DataState } from './quality';

export type BreakoutState = 'NOT_READY' | 'APPROACHING_LEVEL' | 'POTENTIAL_BREAKOUT' | 'CONFIRMATION_PENDING' | 'CONFIRMED' | 'FAILED' | 'INVALIDATED' | 'DATA_INSUFFICIENT' | 'STALE';

export interface BreakoutParams {
  /** "approaching" when within this many ATR of the level */
  approachAtr: number;
  /** a close must clear the level by this many ATR to count */
  bufferAtr: number;
  confirmCloses: number;
  minRvol: number;
  requireVolume: boolean;
  /** retest = a later bar trades back within this many ATR of the level but closes beyond it */
  retestAtr: number;
  /** bars after the breakout during which a close back through the level is a FAILED breakout */
  failureWindow: number;
  /** closing this many ATR on the wrong side of the level invalidates the setup */
  invalidationAtr: number;
}

export const DEFAULT_BREAKOUT_PARAMS: BreakoutParams = { approachAtr: 0.5, bufferAtr: 0.1, confirmCloses: 2, minRvol: 1.5, requireVolume: true, retestAtr: 0.25, failureWindow: 8, invalidationAtr: 1 };

export interface BreakoutContext {
  /** state of the underlying's freshness (quality.ts) */
  freshness: DataState;
  /** latest traded price if fresher than the last completed bar */
  livePrice?: number | null;
  /** trend of a higher timeframe, as supporting/contradicting evidence */
  htfTrend?: { tf: Timeframe; trend: Trend } | null;
  /** next significant level on the far side, to judge room to run */
  opposing?: Level | null;
}

export interface BreakoutSetup {
  direction: 'UP' | 'DOWN';
  state: BreakoutState;
  tf: Timeframe;
  level: Level;
  price: number | null;
  /** price distance to the level (positive = level not yet reached) */
  distance: number | null;
  distanceAtr: number | null;
  atr: number | null;
  closesBeyond: number;
  breakoutBarT: number | null;
  rvol: RelVol | null;
  retestHeld: boolean | null;
  supporting: string[];
  contradicting: string[];
  missing: string[];
  confirmation: string;
  invalidation: string;
  invalidationPrice: number | null;
  /** epoch seconds of the last completed bar the verdict is based on */
  observedThrough: number | null;
  rules: string[];
}

export const RANK: Record<BreakoutState, number> = { CONFIRMED: 7, CONFIRMATION_PENDING: 6, POTENTIAL_BREAKOUT: 5, FAILED: 4, APPROACHING_LEVEL: 3, INVALIDATED: 2, NOT_READY: 1, STALE: 0, DATA_INSUFFICIENT: 0 };

const r2 = (x: number) => Math.round(x * 100) / 100;

function reflect(c: TfCandle): TfCandle {
  return { ...c, o: -c.o, h: -c.l, l: -c.h, c: -c.c };
}

export function rulesText(p: BreakoutParams, tf: Timeframe): string[] {
  return [
    `Only completed ${TF_LABEL[tf]} bars count; the forming bar can at most make a breakout POTENTIAL.`,
    `Breakout close: beyond the level by ≥ ${p.bufferAtr}×ATR(14).`,
    `Volume: breakout-bar volume ≥ ${p.minRvol}× the mean of the previous 20 bars (needs ≥ 10 bars with known volume${p.requireVolume ? '' : '; not required in this configuration'}).`,
    `Hold: ${p.confirmCloses} consecutive closes beyond the level, or a retest within ${p.retestAtr}×ATR that closes back beyond it.`,
    `FAILED: a completed close back through the level within ${p.failureWindow} bars of the breakout. INVALIDATED: a close ${p.invalidationAtr}×ATR on the wrong side.`,
    'STALE or conflicting data can never produce CONFIRMED.',
  ];
}

export function evaluateBreakout(all: TfCandle[], level: Level, direction: 'UP' | 'DOWN', tf: Timeframe, ctx: BreakoutContext, params: Partial<BreakoutParams> = {}): BreakoutSetup {
  const p = { ...DEFAULT_BREAKOUT_PARAMS, ...params };
  const s = direction === 'UP' ? 1 : -1;
  const sign = (x: number) => s * x;
  const base: BreakoutSetup = {
    direction, state: 'DATA_INSUFFICIENT', tf, level, price: null, distance: null, distanceAtr: null, atr: null, closesBeyond: 0, breakoutBarT: null,
    rvol: null, retestHeld: null, supporting: [], contradicting: [], missing: [], confirmation: '', invalidation: '', invalidationPrice: null, observedThrough: null, rules: rulesText(p, tf),
  };

  const doneRaw = all.filter((x) => x.complete);
  if (doneRaw.length < MIN_ANALYSIS_BARS) return { ...base, missing: [`Only ${doneRaw.length} completed ${TF_LABEL[tf]} bars (need ${MIN_ANALYSIS_BARS}).`] };
  const a = atrLast(doneRaw);
  if (a === null || !(a > 0)) return { ...base, missing: ['ATR(14) could not be computed.'] };
  if (ctx.freshness === 'CONFLICT') return { ...base, atr: a, missing: ['Inputs conflict between sources; no breakout state is asserted.'] };

  const done = direction === 'UP' ? doneRaw : doneRaw.map(reflect);
  const L = sign(level.price);
  const buf = p.bufferAtr * a;
  const n = done.length;
  const lastClose = done[n - 1]!.c;
  const forming = all.find((x) => !x.complete && x.t > doneRaw[n - 1]!.t);
  const livePxRaw = ctx.livePrice ?? null;
  const priceRaw = livePxRaw ?? doneRaw[n - 1]!.c;
  const price = sign(priceRaw);
  const distance = L - price; // >0: level still ahead
  const word = direction === 'UP' ? 'above' : 'below';
  const wrong = direction === 'UP' ? 'below' : 'above';
  const clearPrice = r2(sign(L + buf));
  const invPriceDir = L - p.invalidationAtr * a;
  const invPrice = r2(sign(invPriceDir));

  const supporting: string[] = [];
  const contradicting: string[] = [];
  const missing: string[] = [];
  const out = (state: BreakoutState, extra: Partial<BreakoutSetup> = {}): BreakoutSetup => ({
    ...base,
    state,
    price: r2(priceRaw),
    distance: r2(distance), // already directional: positive while the level is still ahead of price
    distanceAtr: r2(distance / a),
    atr: r2(a),
    supporting,
    contradicting,
    missing,
    confirmation: `Needs a completed ${TF_LABEL[tf]} close ${word} ${clearPrice} (level ${level.price} ${direction === 'UP' ? '+' : '−'} ${p.bufferAtr}×ATR), with breakout-bar volume ≥ ${p.minRvol}× its recent mean, and ${p.confirmCloses} consecutive closes ${word} the level or a retest that holds.`,
    invalidation: `A completed close back ${wrong} ${level.price} marks a FAILED breakout; a close ${wrong} ${invPrice} (${p.invalidationAtr}×ATR) invalidates the setup.`,
    invalidationPrice: invPrice,
    observedThrough: doneRaw[n - 1]!.t,
    ...extra,
  });

  // context shared by every state
  if (level.touches >= 2) supporting.push(`Level has ${level.touches} prior tests (${level.method.replace(/_/g, ' ').toLowerCase()}).`);
  else contradicting.push('Level has a single reference point, so it is weaker than a level tested repeatedly.');
  if (ctx.htfTrend) {
    const want = direction === 'UP' ? 'UP' : 'DOWN';
    if (ctx.htfTrend.trend === want) supporting.push(`${TF_LABEL[ctx.htfTrend.tf]} trend agrees (${ctx.htfTrend.trend}).`);
    else if (ctx.htfTrend.trend === 'UNKNOWN') missing.push(`${TF_LABEL[ctx.htfTrend.tf]} trend unknown (not enough history).`);
    else contradicting.push(`${TF_LABEL[ctx.htfTrend.tf]} trend is ${ctx.htfTrend.trend}, not ${want}.`);
  }
  if (ctx.opposing) {
    const room = (Math.abs(ctx.opposing.price - level.price)) / a;
    if (room < 1.5) contradicting.push(`Only ${room.toFixed(1)}×ATR of room before the next level in the direction of the move (${ctx.opposing.kind.toLowerCase()} at ${ctx.opposing.price}).`);
    else supporting.push(`${room.toFixed(1)}×ATR of room before the next level in the direction of the move (${ctx.opposing.kind.toLowerCase()} at ${ctx.opposing.price}).`);
  }

  if (ctx.freshness === 'STALE') {
    missing.push('Latest data is older than the freshness limit; no breakout can be confirmed or alerted.');
    return out('STALE');
  }
  const actionable = ctx.freshness === 'VALID' || ctx.freshness === 'MARKET_CLOSED';
  if (!actionable) missing.push('Data timestamp cannot be verified as current, so confirmation is withheld.');

  // run of completed closes beyond the level
  let k = 0;
  while (k < n && done[n - 1 - k]!.c > L + buf) k++;

  if (k > 0) {
    const bi = n - k;
    const rv = barRvol(done, bi);
    const volOk = rv.value === null ? null : rv.value >= p.minRvol;
    let retest: boolean | null = null;
    if (k >= 2) {
      retest = done.slice(bi + 1).some((x) => x.l <= L + p.retestAtr * a && x.c > L);
    }
    const holds = k >= p.confirmCloses || retest === true;
    supporting.push(`${k} completed close${k > 1 ? 's' : ''} ${word} ${level.price} since ${new Date(done[bi]!.t * 1000).toISOString()}.`);
    if (rv.value !== null) (volOk ? supporting : contradicting).push(`Breakout-bar volume ${rv.value.toFixed(2)}× its recent mean (${volOk ? '≥' : '<'} ${p.minRvol}).`);
    else missing.push(`Breakout-bar volume ratio unavailable: ${rv.note}`);
    if (retest === true) supporting.push('A retest of the level held.');
    if (!holds) contradicting.push(`Only ${k} close${k > 1 ? 's' : ''} beyond the level; ${p.confirmCloses} (or a held retest) are required.`);
    const ext = (sign(priceRaw) - L) / a;
    if (ext > 3) contradicting.push(`Price is ${ext.toFixed(1)}×ATR beyond the level; the move is extended.`);
    const volGate = p.requireVolume ? volOk === true : volOk !== false;
    const confirmed = holds && volGate && actionable;
    return out(confirmed ? 'CONFIRMED' : 'CONFIRMATION_PENDING', { closesBeyond: k, breakoutBarT: doneRaw[bi]!.t, rvol: rv, retestHeld: retest, distance: r2(distance), distanceAtr: r2(distance / a) });
  }

  // not currently beyond: has a recent break failed?
  const from = Math.max(0, n - p.failureWindow - 1);
  let brokeAt = -1;
  for (let i = from; i < n; i++) if (done[i]!.c > L + buf) { brokeAt = i; break; }
  if (brokeAt >= 0) {
    const invalid = lastClose < invPriceDir;
    contradicting.push(`A completed close ${word} the level at ${new Date(doneRaw[brokeAt]!.t * 1000).toISOString()} was followed by a close back ${wrong} it.`);
    return out(invalid ? 'INVALIDATED' : 'FAILED', { breakoutBarT: doneRaw[brokeAt]!.t, distance: r2(distance), distanceAtr: r2(distance / a) });
  }

  const formingBeyond = forming ? sign(direction === 'UP' ? forming.h : forming.l) > L : false;
  const liveBeyond = livePxRaw !== null && price > L;
  if (liveBeyond || formingBeyond || lastClose > L) {
    supporting.push(lastClose > L ? 'The last completed close is beyond the line but inside the confirmation buffer.' : 'Price has traded through the level in the forming bar; no completed close beyond it yet.');
    return out('POTENTIAL_BREAKOUT');
  }
  if (lastClose < invPriceDir && distance > p.invalidationAtr * a) {
    return out('NOT_READY');
  }
  if (distance <= p.approachAtr * a) {
    supporting.push(`Price is ${(distance / a).toFixed(2)}×ATR from the level.`);
    return out('APPROACHING_LEVEL');
  }
  return out('NOT_READY');
}

export interface StructureResult {
  tf: Timeframe;
  setups: BreakoutSetup[];
  primary: BreakoutSetup | null;
}

/** Evaluate every level within 3×ATR of price in both directions and return them best-first. */
export function evaluateStructure(all: TfCandle[], levels: Level[], tf: Timeframe, ctx: BreakoutContext, params: Partial<BreakoutParams> = {}): StructureResult {
  const done = all.filter((x) => x.complete);
  const a = atrLast(done);
  if (done.length < MIN_ANALYSIS_BARS || a === null) return { tf, setups: [], primary: null };
  const px = ctx.livePrice ?? done[done.length - 1]!.c;
  const near = levels.filter((l) => Math.abs(l.price - px) <= 3 * a);
  const setups: BreakoutSetup[] = [];
  for (const l of near) {
    const dir: 'UP' | 'DOWN' = l.kind === 'RESISTANCE' ? 'UP' : 'DOWN';
    const { above, below } = nearest(levels, l.price);
    const opposing = dir === 'UP' ? above : below;
    setups.push(evaluateBreakout(all, l, dir, tf, { ...ctx, opposing }, params));
  }
  setups.sort((x, y) => RANK[y.state] - RANK[x.state] || Math.abs(x.distanceAtr ?? 99) - Math.abs(y.distanceAtr ?? 99));
  return { tf, setups, primary: setups[0] ?? null };
}
