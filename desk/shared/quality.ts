// Data-quality vocabulary for the Options Workbench.
//
// Every number shown to the user carries a state, a source and a timestamp. The rules here are
// deliberately conservative: a missing value is UNKNOWN (never 0), a quote from a closed market is
// MARKET_CLOSED (never "API down"), and an old quote during a live session is STALE (never "live").

export type DataState = 'VALID' | 'STALE' | 'DATA_INSUFFICIENT' | 'CONFLICT' | 'UNKNOWN' | 'UNSUPPORTED' | 'MARKET_CLOSED';

/** How current the provider claims/permits the data to be. Nothing here is ever labelled REALTIME unless entitlement is proven. */
export type Latency = 'DELAYED' | 'SNAPSHOT' | 'UNVERIFIED';

export interface Provenance {
  source: string;
  /** exchange/provider timestamp of the observation (ISO UTC), null if the provider gave none */
  asOf: string | null;
  /** when this server retrieved it (ISO UTC) */
  fetchedAt: string;
  latency: Latency;
  note?: string;
}

export interface Sourced<T> {
  value: T | null;
  state: DataState;
  prov: Provenance | null;
  /** why the state is not VALID, or a caveat on a VALID value */
  note?: string;
}

export const missing = <T>(note: string, state: DataState = 'UNKNOWN'): Sourced<T> => ({ value: null, state, prov: null, note });

/** Freshness limits in seconds while the market is open. */
export const MAX_AGE_SEC = { quote: 120, optionChain: 180, intradayCandle: 240, /** free delayed feeds (Yahoo): accepted up to this age, always labelled DELAYED */ delayedFeed: 20 * 60 } as const;

export interface FreshnessInput {
  hasValue: boolean;
  /** provider observation time; null = provider did not say */
  asOf: string | null;
  now: Date;
  marketOpen: boolean;
  maxAgeSec: number;
}

/**
 * Classify one observation.
 *  - no value                         -> UNKNOWN
 *  - market closed                    -> MARKET_CLOSED (the last session's value; not an outage)
 *  - market open, no timestamp        -> UNKNOWN (cannot prove it is current)
 *  - market open, older than maxAge   -> STALE
 *  - otherwise                        -> VALID
 */
export function classifyFreshness(i: FreshnessInput): DataState {
  if (!i.hasValue) return 'UNKNOWN';
  if (!i.marketOpen) return 'MARKET_CLOSED';
  if (!i.asOf) return 'UNKNOWN';
  const t = Date.parse(i.asOf);
  if (Number.isNaN(t)) return 'UNKNOWN';
  const age = (i.now.getTime() - t) / 1000;
  if (age < -60) return 'CONFLICT'; // a timestamp from the future means a clock or parsing problem, not fresh data
  return age > i.maxAgeSec ? 'STALE' : 'VALID';
}

/** Only these states may drive a confirmed breakout or an actionable alert. */
export function isActionable(state: DataState): boolean {
  return state === 'VALID';
}

const MON: Record<string, string> = { Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12' };

/** NSE timestamps such as "14-Oct-2026 15:24:23" are IST. Returns ISO UTC or null. */
export function parseNseTimestamp(s: string | null | undefined): string | null {
  const m = (s ?? '').trim().match(/^(\d{2})-([A-Za-z]{3})-(\d{4})(?: (\d{2}):(\d{2})(?::(\d{2}))?)?$/);
  if (!m || !MON[m[2]!]) return null;
  const [, dd, mon, yyyy, hh = '00', mi = '00', ss = '00'] = m;
  const t = Date.parse(`${yyyy}-${MON[mon!]}-${dd}T${hh}:${mi}:${ss}+05:30`);
  return Number.isNaN(t) ? null : new Date(t).toISOString();
}

export interface Reconciled {
  state: DataState;
  /** the value to show; null on CONFLICT (neither silently wins) */
  value: number | null;
  diffPct: number | null;
  note: string;
}

/** Two providers quote the same thing. Agreement within tolerance is VALID; otherwise CONFLICT and no value is chosen. */
export function reconcile(a: number | null, b: number | null, tolPct: number, labels: [string, string]): Reconciled {
  if (a === null && b === null) return { state: 'UNKNOWN', value: null, diffPct: null, note: 'No provider supplied a value.' };
  if (a === null || b === null) return { state: 'VALID', value: a ?? b, diffPct: null, note: `Only ${a === null ? labels[1] : labels[0]} supplied a value; not cross-checked.` };
  const base = Math.abs(a) > 0 ? Math.abs(a) : Math.abs(b);
  const diffPct = base > 0 ? (Math.abs(a - b) / base) * 100 : 0;
  if (diffPct > tolPct) return { state: 'CONFLICT', value: null, diffPct, note: `${labels[0]} ${a} and ${labels[1]} ${b} differ by ${diffPct.toFixed(2)}% (> ${tolPct}%); neither is used.` };
  return { state: 'VALID', value: a, diffPct, note: `${labels[0]} and ${labels[1]} agree within ${tolPct}%.` };
}
