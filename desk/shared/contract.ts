// Option-contract identity, search parsing and validation. Provider values only: a field the
// provider omits stays unknown (null) and is never turned into 0. Calculated values (spread,
// premium for one lot, intrinsic/time value, Black-Scholes Greeks) are labelled as calculated.

import { istParts } from './india';
import type { OptionLeg } from './optionchain';
import { classifyFreshness, MAX_AGE_SEC, type DataState, type Latency, type Sourced } from './quality';

export type OptType = 'CE' | 'PE';

export interface ContractRef {
  underlying: string;
  /** YYYY-MM-DD */
  expiry: string;
  type: OptType;
  strike: number;
}

/** A contract the user tracks (stored privately, included in exports and backups). */
export interface ContractWatch {
  id: string;
  ref: ContractRef;
  note: string;
  addedAt: string;
}

export const normalizeStrike = (x: number): number => Math.round(x * 100) / 100;

/** Canonical identity: UNDERLYING|YYYY-MM-DD|CE|strike. Two contracts are the same only if all four parts are equal. */
export function contractId(r: ContractRef): string {
  return `${r.underlying.toUpperCase()}|${r.expiry}|${r.type}|${normalizeStrike(r.strike)}`;
}

export function parseContractId(id: string): ContractRef | null {
  const m = /^([A-Z0-9&-]{1,20})\|(\d{4}-\d{2}-\d{2})\|(CE|PE)\|(\d{1,7}(?:\.\d{1,2})?)$/.exec(id);
  if (!m) return null;
  const d = Date.parse(`${m[2]}T00:00:00Z`);
  if (Number.isNaN(d) || new Date(d).toISOString().slice(0, 10) !== m[2]) return null;
  const strike = Number(m[4]);
  if (!(strike > 0)) return null;
  return { underlying: m[1]!, expiry: m[2]!, type: m[3] as OptType, strike };
}

const MON: Record<string, string> = { JAN: '01', FEB: '02', MAR: '03', APR: '04', MAY: '05', JUN: '06', JUL: '07', AUG: '08', SEP: '09', OCT: '10', NOV: '11', DEC: '12' };

/** NSE identifier such as OPTSTKRELIANCE27-10-2026CE1250.00 or OPTIDXNIFTY27-10-2026PE25000.00. */
export function parseNseIdentifier(id: string): ContractRef | null {
  const m = /^OPT(?:STK|IDX)([A-Z0-9&-]+?)(\d{2})-(\d{2})-(\d{4})(CE|PE)(\d+(?:\.\d+)?)$/.exec(id);
  if (!m) return null;
  const expiry = `${m[4]}-${m[3]}-${m[2]}`;
  const strike = normalizeStrike(Number(m[6]));
  return strike > 0 ? { underlying: m[1]!, expiry, type: m[5] as OptType, strike } : null;
}

export interface SearchQuery {
  /** the symbol/company text left after structured tokens were removed */
  text: string;
  strike: number | null;
  type: OptType | null;
  expiry: { iso?: string; month?: string; day?: string; year?: string } | null;
}

/**
 * "reliance 1250 ce 27oct", "NIFTY 25000 PUT", "tcs oct" -> structured query.
 * Unrecognised words stay in `text` (company names), so nothing is silently dropped.
 */
export function parseSearch(raw: string): SearchQuery {
  const tokens = raw.trim().toUpperCase().split(/\s+/).filter(Boolean);
  const text: string[] = [];
  let strike: number | null = null;
  let type: OptType | null = null;
  let expiry: SearchQuery['expiry'] = null;
  for (const t of tokens) {
    if (/^(CE|CALL|C)$/.test(t) && type === null && text.length > 0) { type = 'CE'; continue; }
    if (/^(PE|PUT|P)$/.test(t) && type === null && text.length > 0) { type = 'PE'; continue; }
    let m: RegExpExecArray | null;
    if ((m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t))) { expiry = { iso: t }; continue; }
    if ((m = /^(\d{1,2})-?([A-Z]{3})(?:-?(\d{2,4}))?$/.exec(t)) && MON[m[2]!]) {
      expiry = { day: m[1]!.padStart(2, '0'), month: MON[m[2]!], ...(m[3] ? { year: m[3].length === 2 ? `20${m[3]}` : m[3] } : {}) };
      continue;
    }
    if (MON[t] && text.length > 0) { expiry = { month: MON[t] }; continue; }
    if (/^\d+(\.\d{1,2})?$/.test(t) && strike === null && text.length > 0) { strike = Number(t); continue; }
    text.push(t);
  }
  return { text: text.join(' '), strike, type, expiry };
}

export function expiryMatches(iso: string, q: NonNullable<SearchQuery['expiry']>): boolean {
  if (q.iso) return iso === q.iso;
  const [y, m, d] = iso.split('-');
  return (!q.year || q.year === y) && (!q.month || q.month === m) && (!q.day || q.day === d);
}

// ---- Greeks (calculated only when the provider gave none) ---------------------------------------------------

/** Assumed annual risk-free rate for calculated Greeks. Disclosed wherever Greeks are shown as calculated. */
export const ASSUMED_RATE = 0.065;

const pdf = (x: number) => Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
function cdf(x: number): number {
  // Abramowitz & Stegun 7.1.26 via erf; max error ~1.5e-7
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = pdf(x);
  const p = d * t * (0.31938153 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  return x >= 0 ? 1 - p : p;
}

export interface Greeks {
  delta: number;
  gamma: number;
  /** per calendar day, in premium points */
  theta: number;
  /** per 1 volatility point (1% IV), in premium points */
  vega: number;
}

/** Black-Scholes (European, no dividends) from implied vol in percent. null when inputs cannot support it. */
export function blackScholesGreeks(type: OptType, spot: number, strike: number, ivPct: number, years: number, rate = ASSUMED_RATE): Greeks | null {
  if (!(spot > 0) || !(strike > 0) || !(ivPct > 0) || !(years > 0)) return null;
  const s = ivPct / 100;
  const sq = s * Math.sqrt(years);
  const d1 = (Math.log(spot / strike) + (rate + (s * s) / 2) * years) / sq;
  const d2 = d1 - sq;
  const disc = Math.exp(-rate * years);
  const delta = type === 'CE' ? cdf(d1) : cdf(d1) - 1;
  const gamma = pdf(d1) / (spot * sq);
  const vega = (spot * pdf(d1) * Math.sqrt(years)) / 100;
  const thetaYear = type === 'CE' ? (-spot * pdf(d1) * s) / (2 * Math.sqrt(years)) - rate * strike * disc * cdf(d2) : (-spot * pdf(d1) * s) / (2 * Math.sqrt(years)) + rate * strike * disc * cdf(-d2);
  return { delta, gamma, theta: thetaYear / 365, vega };
}

// ---- Validation -----------------------------------------------------------------------------------------------------

export type LiquidityClass = 'LIQUID' | 'MODERATE' | 'ILLIQUID' | 'DATA_INSUFFICIENT';

/** Thresholds shown to the user. They describe tradability of the quote, never safety or quality of a trade idea. */
export const LIQUIDITY_RULES = {
  liquid: { maxSpreadPct: 2, minVolume: 100, minOi: 1000, depthLots: 1 },
  moderate: { maxSpreadPct: 5, minVolume: 20, minOi: 100 },
} as const;

export interface ValidationInput {
  ref: ContractRef;
  leg: OptionLeg;
  lotSize: number | null;
  spot: number | null;
  /** provider's own snapshot time, if it states one */
  asOf: string | null;
  fetchedAt: string;
  source: string;
  latency: Latency;
  marketOpen: boolean;
  now: Date;
}

export interface ContractValidation {
  id: string;
  ref: ContractRef;
  lotSize: number | null;
  daysToExpiry: number | null;
  /** overall: worst state among the quote fields; UNKNOWN means no usable quote exists */
  state: DataState;
  quoteAvailable: boolean;
  ltp: Sourced<number>;
  bid: Sourced<number>;
  ask: Sourced<number>;
  bidQty: Sourced<number>;
  askQty: Sourced<number>;
  spreadAbs: Sourced<number>;
  spreadPct: Sourced<number>;
  /** traded volume in CONTRACTS during the session */
  volume: Sourced<number>;
  /** open interest in CONTRACTS: positions outstanding, not trading activity */
  oi: Sourced<number>;
  oiChange: Sourced<number>;
  iv: Sourced<number>;
  delta: Sourced<number>;
  gamma: Sourced<number>;
  theta: Sourced<number>;
  vega: Sourced<number>;
  oneLotPremium: Sourced<number>;
  intrinsic: Sourced<number>;
  timeValue: Sourced<number>;
  /** delta × spot / premium: % move in premium per 1% move in the underlying, when all inputs exist */
  leverage: Sourced<number>;
  liquidity: { cls: LiquidityClass; reasons: string[]; rules: typeof LIQUIDITY_RULES };
  notes: string[];
}

const END_OF_EXPIRY_UTC_OFFSET_MIN = 15 * 60 + 30 - 330; // 15:30 IST expressed in UTC minutes (10:00 UTC)

export function expiryCloseMs(expiryIso: string): number {
  return Date.parse(`${expiryIso}T00:00:00Z`) + END_OF_EXPIRY_UTC_OFFSET_MIN * 60_000;
}

export function validateContract(i: ValidationInput): ContractValidation {
  const notes: string[] = [];
  const prov = { source: i.source, asOf: i.asOf, fetchedAt: i.fetchedAt, latency: i.latency };
  const fresh = (has: boolean): DataState => classifyFreshness({ hasValue: has, asOf: i.asOf, now: i.now, marketOpen: i.marketOpen, maxAgeSec: MAX_AGE_SEC.optionChain });
  const field = (v: number | null, why: string): Sourced<number> => {
    if (v === null) return { value: null, state: 'UNKNOWN', prov: null, note: why };
    return { value: v, state: fresh(true), prov };
  };
  const calc = (v: number | null, why: string, state: DataState, note?: string): Sourced<number> => (v === null ? { value: null, state: state === 'VALID' ? 'UNKNOWN' : state, prov: null, note: why } : { value: v, state, prov: { ...prov, note: 'calculated' }, note });

  const l = i.leg;
  const ltpRaw = l.ltp !== null && l.ltp > 0 ? l.ltp : null;
  const bidRaw = l.bid !== null && l.bid > 0 ? l.bid : null;
  const askRaw = l.ask !== null && l.ask > 0 ? l.ask : null;
  const ltp = field(ltpRaw, l.ltp === 0 ? 'No trade has been reported for this contract.' : 'Provider did not supply a last price.');
  const bid = field(bidRaw, 'No bid in the book (or not supplied).');
  const ask = field(askRaw, 'No ask in the book (or not supplied).');
  const bidQty = field(bidRaw !== null && l.bidQty != null && l.bidQty >= 0 ? l.bidQty : null, 'Bid quantity not supplied.');
  const askQty = field(askRaw !== null && l.askQty != null && l.askQty >= 0 ? l.askQty : null, 'Ask quantity not supplied.');

  let spreadAbs: Sourced<number>;
  let spreadPct: Sourced<number>;
  if (bidRaw !== null && askRaw !== null) {
    if (bidRaw > askRaw) {
      spreadAbs = { value: null, state: 'CONFLICT', prov: null, note: `Crossed book: bid ${bidRaw} is above ask ${askRaw}.` };
      spreadPct = { ...spreadAbs };
    } else {
      const a = askRaw - bidRaw;
      const mid = (askRaw + bidRaw) / 2;
      spreadAbs = calc(a, '', bid.state === 'VALID' && ask.state === 'VALID' ? 'VALID' : bid.state);
      spreadPct = calc((a / mid) * 100, '', spreadAbs.state);
    }
  } else {
    spreadAbs = calc(null, 'Spread needs both a bid and an ask.', 'UNKNOWN');
    spreadPct = calc(null, 'Spread needs both a bid and an ask.', 'UNKNOWN');
  }

  const volume = field(l.volume, 'Traded volume not supplied.');
  const oi = field(l.oi, 'Open interest not supplied.');
  const oiChangeRaw = l.oi !== null && l.prevOi !== null ? l.oi - l.prevOi : null;
  const oiChange = field(oiChangeRaw, 'Previous-day open interest not supplied, so the change is unknown.');
  const iv = field(l.iv !== null && l.iv > 0 ? l.iv : null, 'Implied volatility not supplied (or non-positive).');

  const expMs = expiryCloseMs(i.ref.expiry);
  const msLeft = expMs - i.now.getTime();
  const daysToExpiry = msLeft > 0 ? msLeft / 86_400_000 : null;
  const expired = msLeft <= 0;
  if (expired) notes.push('This contract has expired.');

  // Greeks: provider first; calculated only if absent and the inputs are sufficient
  const years = daysToExpiry !== null ? daysToExpiry / 365 : 0;
  const bs = iv.value !== null && i.spot !== null && !expired ? blackScholesGreeks(i.ref.type, i.spot, i.ref.strike, iv.value, years) : null;
  const g = (provided: number | null | undefined, calcVal: number | undefined, name: string): Sourced<number> => {
    if (provided != null) return field(provided, '');
    if (calcVal !== undefined) return calc(calcVal, '', iv.state === 'VALID' ? 'VALID' : iv.state, `Calculated (Black-Scholes, r=${ASSUMED_RATE * 100}%, no dividends) from the provider's IV; not provider-supplied.`);
    return { value: null, state: 'UNKNOWN', prov: null, note: `${name} not supplied and cannot be calculated (needs IV, spot and time to expiry).` };
  };
  const delta = g(l.delta, bs?.delta, 'Delta');
  const gamma = g(l.gamma, bs?.gamma, 'Gamma');
  const theta = g(l.theta, bs?.theta, 'Theta');
  const vega = g(l.vega, bs?.vega, 'Vega');

  const lotOk = i.lotSize !== null && i.lotSize > 0;
  const oneLotPremium = ltp.value !== null && lotOk ? calc(ltp.value * i.lotSize!, '', ltp.state, `Premium ${ltp.value} × lot size ${i.lotSize}`) : calc(null, lotOk ? 'No last price.' : 'Official lot size unavailable.', 'UNKNOWN');

  let intrinsic: Sourced<number>;
  let timeValue: Sourced<number>;
  if (i.spot !== null && !expired) {
    const iv0 = Math.max(i.ref.type === 'CE' ? i.spot - i.ref.strike : i.ref.strike - i.spot, 0);
    intrinsic = calc(iv0, '', 'VALID', 'Spot vs strike; spot is a separate observation from the option quote.');
    if (ltp.value !== null) {
      const tv = ltp.value - iv0;
      timeValue = tv < -0.05 ? { value: null, state: 'CONFLICT', prov: null, note: `Premium ${ltp.value} is below intrinsic ${iv0.toFixed(2)}: the spot and option quotes are not simultaneous or one is stale. Time value withheld.` } : calc(Math.max(tv, 0), '', ltp.state);
    } else timeValue = calc(null, 'No last price.', 'UNKNOWN');
  } else {
    intrinsic = calc(null, expired ? 'Contract expired.' : 'Underlying price unavailable.', 'UNKNOWN');
    timeValue = calc(null, expired ? 'Contract expired.' : 'Underlying price unavailable.', 'UNKNOWN');
  }

  const leverage = delta.value !== null && i.spot !== null && ltp.value !== null ? calc((Math.abs(delta.value) * i.spot) / ltp.value, '', 'VALID', 'Approximate % change of premium per 1% move in the underlying, at the current delta only; it changes as price, time and volatility change.') : calc(null, 'Needs delta, spot and premium.', 'UNKNOWN');

  // liquidity: classification of the quote, with every reason listed
  const reasons: string[] = [];
  let cls: LiquidityClass;
  const sp = spreadPct.value;
  const vol = volume.value;
  const oiV = oi.value;
  const depthLots = bidQty.value !== null && askQty.value !== null && lotOk ? Math.min(bidQty.value, askQty.value) / i.lotSize! : null;
  if (sp === null || vol === null || oiV === null) {
    cls = 'DATA_INSUFFICIENT';
    if (sp === null) reasons.push(spreadPct.state === 'CONFLICT' ? 'Bid/ask conflict.' : 'No two-sided quote (bid and ask).');
    if (vol === null) reasons.push('Traded volume unknown.');
    if (oiV === null) reasons.push('Open interest unknown.');
  } else {
    const L = LIQUIDITY_RULES.liquid;
    const M = LIQUIDITY_RULES.moderate;
    const depthOk = depthLots === null ? true : depthLots >= L.depthLots;
    if (sp <= L.maxSpreadPct && vol >= L.minVolume && oiV >= L.minOi && depthOk) cls = 'LIQUID';
    else if (sp <= M.maxSpreadPct && vol >= M.minVolume && oiV >= M.minOi) cls = 'MODERATE';
    else cls = 'ILLIQUID';
    reasons.push(`Spread ${sp.toFixed(2)}% (liquid ≤ ${L.maxSpreadPct}%, moderate ≤ ${M.maxSpreadPct}%).`, `Volume ${vol} contracts (liquid ≥ ${L.minVolume}, moderate ≥ ${M.minVolume}).`, `Open interest ${oiV} contracts (liquid ≥ ${L.minOi}, moderate ≥ ${M.minOi}).`);
    if (depthLots !== null) reasons.push(`Best-level depth ${depthLots.toFixed(1)} lots on the thinner side (liquid ≥ ${L.depthLots}).`);
    else reasons.push('Best-level depth not supplied; depth not used in the class.');
  }

  const quoteAvailable = ltp.value !== null || (bid.value !== null && ask.value !== null);
  const core = [ltp, bid, ask].filter((f) => f.value !== null);
  const order: DataState[] = ['CONFLICT', 'STALE', 'UNKNOWN', 'MARKET_CLOSED', 'VALID'];
  let state: DataState = expired ? 'UNSUPPORTED' : !quoteAvailable ? 'UNKNOWN' : (order.find((s) => core.some((f) => f.state === s)) ?? 'UNKNOWN');
  if (spreadAbs.state === 'CONFLICT') state = 'CONFLICT';
  if (!quoteAvailable) notes.push(i.marketOpen ? 'No quote is available for this contract right now (no trades and no two-sided book).' : 'No quote for this contract in the last session.');
  if (i.latency !== 'SNAPSHOT') notes.push('Quote latency is not verified as real-time.');
  if (i.spot !== null && !i.marketOpen) notes.push(`Market closed (${istParts(i.now).dateStr} IST): values are from the last session.`);

  return {
    id: contractId(i.ref), ref: i.ref, lotSize: i.lotSize, daysToExpiry, state, quoteAvailable,
    ltp, bid, ask, bidQty, askQty, spreadAbs, spreadPct, volume, oi, oiChange, iv, delta, gamma, theta, vega,
    oneLotPremium, intrinsic, timeValue, leverage, liquidity: { cls, reasons, rules: LIQUIDITY_RULES }, notes,
  };
}
