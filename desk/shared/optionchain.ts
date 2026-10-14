// Option-chain analytics. Input is a normalized chain (from Upstox or NSE);
// output is facts + conservative positioning reads. No trade recommendations.

export interface OptionLeg {
  ltp: number | null;
  oi: number | null;
  prevOi: number | null;
  volume: number | null;
  iv: number | null;
  bid: number | null;
  ask: number | null;
  /** best bid/ask quantity in UNITS (shares), as the exchange publishes it; divide by lot size for lots */
  bidQty?: number | null;
  askQty?: number | null;
  /** provider-supplied Greeks (never calculated here) */
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  /** provider's own contract identifier / instrument key, used to fetch contract candles */
  instrumentKey?: string | null;
}

export interface ChainRow {
  strike: number;
  ce: OptionLeg;
  pe: OptionLeg;
}

export interface NormalizedChain {
  underlying: string;
  expiry: string; // YYYY-MM-DD
  spot: number | null;
  rows: ChainRow[];
  source: 'upstox' | 'nse' | 'fixture';
  fetchedAt: string;
  /** the provider's own snapshot time (ISO UTC) when it states one; null when it does not */
  asOf?: string | null;
}

export interface StrikeLevel {
  strike: number;
  oi: number | null;
  /** null when previous-day OI is unknown */
  oiChange: number | null;
}

export interface ChainAnalytics {
  underlying: string;
  expiry: string;
  spot: number | null;
  atmStrike: number | null;
  strikeStep: number | null;
  pcrOi: number | null;
  pcrVolume: number | null;
  /** null when any strike in the chain is missing OI (missing OI is never counted as zero) */
  totalCallOi: number | null;
  totalPutOi: number | null;
  totalCallOiChange: number | null;
  totalPutOiChange: number | null;
  /** strikes with OI missing on either side */
  strikesMissingOi: number;
  maxPain: number | null;
  /** strikes with the highest call OI (overhead supply), descending */
  callWalls: StrikeLevel[];
  /** strikes with the highest put OI (support), descending */
  putWalls: StrikeLevel[];
  /** where fresh writing is concentrated today */
  freshCallWriting: StrikeLevel[];
  freshPutWriting: StrikeLevel[];
  atmStraddle: number | null;
  /** straddle as % of spot - the market-implied move to expiry */
  impliedMovePct: number | null;
  impliedRange: { low: number; high: number } | null;
  atmIv: number | null;
  /** OTM put IV minus OTM call IV, 3 steps away (volatility-points) */
  ivSkew: number | null;
  pcrRead: { label: string; detail: string };
  notes: string[];
}

/** Sum that refuses to guess: any missing value makes the total unknown (null), never zero. */
function sumStrict(rows: ChainRow[], f: (r: ChainRow) => number | null): number | null {
  let s = 0;
  for (const r of rows) {
    const v = f(r);
    if (v === null || !Number.isFinite(v)) return null;
    s += v;
  }
  return s;
}

export function inferStrikeStep(strikes: number[]): number | null {
  const sorted = [...new Set(strikes)].sort((a, b) => a - b);
  if (sorted.length < 2) return null;
  const diffs = new Map<number, number>();
  for (let i = 1; i < sorted.length; i++) {
    const d = Math.round((sorted[i]! - sorted[i - 1]!) * 100) / 100;
    diffs.set(d, (diffs.get(d) ?? 0) + 1);
  }
  let best: number | null = null;
  let bestN = 0;
  for (const [d, n] of diffs) if (n > bestN) { best = d; bestN = n; }
  return best;
}

/**
 * Strike minimizing aggregate intrinsic value paid to option buyers at expiry.
 * Not computed (null) if any strike lacks OI on either side: treating a missing
 * value as zero would silently move the answer.
 */
export function maxPain(rows: ChainRow[]): number | null {
  if (rows.some((r) => r.ce.oi === null || r.pe.oi === null)) return null;
  const withOi = rows.filter((r) => (r.ce.oi ?? 0) > 0 || (r.pe.oi ?? 0) > 0);
  if (withOi.length === 0) return null;
  let bestStrike: number | null = null;
  let bestPain = Infinity;
  for (const cand of withOi) {
    let pain = 0;
    for (const r of withOi) {
      if (cand.strike > r.strike) pain += (cand.strike - r.strike) * (r.ce.oi ?? 0);
      if (cand.strike < r.strike) pain += (r.strike - cand.strike) * (r.pe.oi ?? 0);
    }
    if (pain < bestPain) { bestPain = pain; bestStrike = cand.strike; }
  }
  return bestStrike;
}

function topLevels(rows: ChainRow[], pick: (r: ChainRow) => { oi: number | null; chg: number | null }, by: 'oi' | 'chg', n: number): StrikeLevel[] {
  const out: StrikeLevel[] = [];
  for (const r of rows) {
    const p = pick(r);
    const v = by === 'oi' ? p.oi : p.chg;
    if (v === null || !(v > 0)) continue; // unknown values are excluded, not ranked as zero
    out.push({ strike: r.strike, oi: p.oi, oiChange: p.chg });
  }
  return out.sort((x, y) => (by === 'oi' ? (y.oi ?? 0) - (x.oi ?? 0) : (y.oiChange ?? 0) - (x.oiChange ?? 0))).slice(0, n);
}

/**
 * Descriptive reading of the put-call OI ratio. Deliberately not directional:
 * PCR alone is positioning, not confirmation of anything (see the TIRE engine's
 * evidence rules), so it is never labelled bullish or bearish here.
 */
export function pcrRead(pcr: number | null): { label: string; detail: string } {
  const caveat = 'PCR describes positioning only; it is not a directional signal on its own.';
  if (pcr === null) return { label: 'Unavailable', detail: 'PCR not computed: open interest is missing or zero for part of the chain.' };
  if (pcr < 0.7) return { label: 'Call OI well above put OI', detail: caveat };
  if (pcr <= 1.0) return { label: 'Call OI slightly above put OI', detail: caveat };
  if (pcr <= 1.3) return { label: 'Put OI slightly above call OI', detail: caveat };
  return { label: 'Put OI well above call OI', detail: `${caveat} High put OI can be writing or hedging.` };
}

export function analyzeChain(chain: NormalizedChain): ChainAnalytics {
  const rows = [...chain.rows].sort((a, b) => a.strike - b.strike);
  const notes: string[] = [];
  const spot = chain.spot;
  const step = inferStrikeStep(rows.map((r) => r.strike));

  let atm: ChainRow | null = null;
  if (spot !== null && rows.length > 0) {
    atm = rows.reduce((best, r) => (Math.abs(r.strike - spot) < Math.abs(best.strike - spot) ? r : best), rows[0]!);
  }

  const strikesMissingOi = rows.filter((r) => r.ce.oi === null || r.pe.oi === null).length;
  const totalCallOi = sumStrict(rows, (r) => r.ce.oi);
  const totalPutOi = sumStrict(rows, (r) => r.pe.oi);
  const callVol = sumStrict(rows, (r) => r.ce.volume);
  const putVol = sumStrict(rows, (r) => r.pe.volume);
  const chg = (l: OptionLeg): number | null => (l.oi !== null && l.prevOi !== null ? l.oi - l.prevOi : null);
  const totalCallOiChange = sumStrict(rows, (r) => chg(r.ce));
  const totalPutOiChange = sumStrict(rows, (r) => chg(r.pe));
  if (strikesMissingOi > 0) notes.push(`Open interest is missing for ${strikesMissingOi} of ${rows.length} strikes. PCR, max pain and OI totals are not computed, and OI walls rank only strikes with data. Missing OI is never treated as zero.`);
  if (rows.some((r) => r.ce.prevOi === null || r.pe.prevOi === null)) notes.push('Previous-day OI is missing for some strikes, so OI-change totals are not computed.');

  const pcrOi = totalCallOi !== null && totalPutOi !== null && totalCallOi > 0 ? totalPutOi / totalCallOi : null;
  // Volume PCR is reported as a fact only; call volume above put volume is not treated as confirmation of anything.
  const pcrVolume = callVol !== null && putVol !== null && callVol > 0 ? putVol / callVol : null;

  let atmStraddle: number | null = null;
  let atmIv: number | null = null;
  if (atm && atm.ce.ltp !== null && atm.pe.ltp !== null && atm.ce.ltp > 0 && atm.pe.ltp > 0) {
    atmStraddle = atm.ce.ltp + atm.pe.ltp;
  } else if (atm) notes.push('ATM premiums unavailable (market may be closed); implied move not computed.');
  if (atm) {
    const ivs = [atm.ce.iv, atm.pe.iv].filter((x): x is number => x !== null && x > 0);
    if (ivs.length > 0) atmIv = ivs.reduce((a, b) => a + b, 0) / ivs.length;
  }

  let ivSkew: number | null = null;
  if (atm && step) {
    const put = rows.find((r) => Math.abs(r.strike - (atm!.strike - 3 * step)) < 1e-6);
    const call = rows.find((r) => Math.abs(r.strike - (atm!.strike + 3 * step)) < 1e-6);
    if (put?.pe.iv && call?.ce.iv && put.pe.iv > 0 && call.ce.iv > 0) ivSkew = put.pe.iv - call.ce.iv;
  }

  const impliedMovePct = atmStraddle !== null && spot ? (atmStraddle / spot) * 100 : null;
  const impliedRange = atmStraddle !== null && spot ? { low: spot - atmStraddle, high: spot + atmStraddle } : null;

  return {
    underlying: chain.underlying,
    expiry: chain.expiry,
    spot,
    atmStrike: atm?.strike ?? null,
    strikeStep: step,
    pcrOi,
    pcrVolume,
    totalCallOi,
    totalPutOi,
    totalCallOiChange,
    totalPutOiChange,
    strikesMissingOi,
    maxPain: maxPain(rows),
    callWalls: topLevels(rows, (r) => ({ oi: r.ce.oi, chg: chg(r.ce) }), 'oi', 3),
    putWalls: topLevels(rows, (r) => ({ oi: r.pe.oi, chg: chg(r.pe) }), 'oi', 3),
    freshCallWriting: topLevels(rows, (r) => ({ oi: r.ce.oi, chg: chg(r.ce) }), 'chg', 3),
    freshPutWriting: topLevels(rows, (r) => ({ oi: r.pe.oi, chg: chg(r.pe) }), 'chg', 3),
    atmStraddle,
    impliedMovePct,
    impliedRange,
    atmIv,
    ivSkew,
    pcrRead: pcrRead(pcrOi),
    notes,
  };
}

/** Classic price-vs-OI interpretation for one contract. */
export function buildup(priceChangePct: number | null, oiChangePct: number | null): 'LONG_BUILDUP' | 'SHORT_BUILDUP' | 'SHORT_COVERING' | 'LONG_UNWINDING' | 'NEUTRAL' {
  if (priceChangePct === null || oiChangePct === null) return 'NEUTRAL';
  if (Math.abs(priceChangePct) < 0.05 || Math.abs(oiChangePct) < 0.5) return 'NEUTRAL';
  if (priceChangePct > 0 && oiChangePct > 0) return 'LONG_BUILDUP';
  if (priceChangePct < 0 && oiChangePct > 0) return 'SHORT_BUILDUP';
  if (priceChangePct > 0 && oiChangePct < 0) return 'SHORT_COVERING';
  return 'LONG_UNWINDING';
}
