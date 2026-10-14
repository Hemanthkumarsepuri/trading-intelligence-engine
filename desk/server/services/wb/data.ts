// Options Workbench data access: instruments, intraday candles, expiries and option chains, with
// provenance, freshness and per-provider health tracking. Market data stays separate from analysis.

import { aggregate, markDaily, availability, SOURCE_FOR, TF_MINUTES, type TfAvailability, type TfCandle, type Timeframe } from '../../../shared/candles';
import { addDays, istDateStr, isLiveSession } from '../../../shared/india';
import type { NormalizedChain } from '../../../shared/optionchain';
import { classifyFreshness, MAX_AGE_SEC, type DataState, type Latency, type Provenance } from '../../../shared/quality';
import type { Candle, Quote } from '../../../shared/types';
import { FALLBACK_SYMBOLS, OPTION_UNDERLYINGS } from '../../../shared/universe';
import { cached } from '../../cache';
import type { Ctx } from '../../config';
import { demoLotSize, fixtureEquityChain, fixtureIntraday, fixtureMinuteBars, fixtureStockExpiries, strikeStepFor } from '../../fixtures-wb';
import { fixtureQuote } from '../../fixtures';
import { mapLimit, ProviderError } from '../../http';
import { fetchNseChainRaw, fetchNseExpiriesRaw, nseExpiries, parseNseChain } from '../../providers/nse';
import { upstoxOptionChain, upstoxOptionContracts } from '../../providers/upstox';
import { toYahooSymbol, yahooChart } from '../../providers/yahoo';
import { equityTarget, getDaily, getQuotes, ttlFor } from '../quotes';
import { getExpiries } from '../options';
import { getLotSizes, getSectorMap, getSymbolMaster, isinFor } from '../symbols';

// ---- provider health (in-process; resets on restart, which the Data Health page states) -------------------------------------

export interface ProviderStat {
  id: string;
  label: string;
  lastOkAt: string | null;
  lastErrorAt: string | null;
  lastError: string | null;
  lastLatencyMs: number | null;
  ok: number;
  failed: number;
}
const stats = new Map<string, ProviderStat>();

export const PROVIDERS: Record<string, string> = {
  'yahoo-intraday': 'Yahoo Finance chart API (intraday candles, unofficial, delayed)',
  'yahoo-daily': 'Yahoo Finance chart API (daily candles, unofficial)',
  'nse-chain': 'NSE option chain (public website API)',
  'nse-expiries': 'NSE option contract info (public website API)',
  'nse-announcements': 'NSE corporate announcements',
  'nse-events': 'NSE event calendar',
  'upstox-chain': 'Upstox option chain (needs UPSTOX_ANALYTICS_TOKEN)',
  'rss-news': 'Public RSS headlines',
};

export async function track<T>(id: string, fn: () => Promise<T>, now: () => Date = () => new Date()): Promise<T> {
  const s = stats.get(id) ?? { id, label: PROVIDERS[id] ?? id, lastOkAt: null, lastErrorAt: null, lastError: null, lastLatencyMs: null, ok: 0, failed: 0 };
  const t0 = Date.now();
  try {
    const r = await fn();
    s.ok += 1;
    s.lastOkAt = now().toISOString();
    s.lastLatencyMs = Date.now() - t0;
    stats.set(id, s);
    return r;
  } catch (e) {
    s.failed += 1;
    s.lastErrorAt = now().toISOString();
    s.lastError = e instanceof Error ? e.message.slice(0, 200) : 'error';
    s.lastLatencyMs = Date.now() - t0;
    stats.set(id, s);
    throw e;
  }
}
export const providerStats = (): ProviderStat[] => Object.keys(PROVIDERS).map((id) => stats.get(id) ?? { id, label: PROVIDERS[id]!, lastOkAt: null, lastErrorAt: null, lastError: null, lastLatencyMs: null, ok: 0, failed: 0 });
export function resetProviderStats(): void {
  stats.clear();
}

// ---- instruments -----------------------------------------------------------------------------------------------------------------

export interface Instrument {
  id: string;
  name: string;
  kind: 'INDEX' | 'STOCK';
  yahoo: string;
  nseSymbol: string;
  /** index instrument key; stocks resolve through their ISIN */
  upstoxKey: string | null;
  fno: boolean;
  lotSize: number | null;
  sector: string | null;
}

const INDEX_IDS = new Set(OPTION_UNDERLYINGS.map((u) => u.id).concat(['NIFTYNXT50']));

/** F&O stocks with their lot sizes. Offline: a deterministic demo universe. */
export async function fnoStocks(ctx: Ctx): Promise<Record<string, number>> {
  if (ctx.cfg.offline) {
    const out: Record<string, number> = {};
    for (const s of FALLBACK_SYMBOLS.slice(0, 30)) out[s.symbol] = demoLotSize(s.symbol);
    return out;
  }
  const lots = await getLotSizes(ctx);
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(lots)) if (!INDEX_IDS.has(k) && Number.isFinite(v) && v > 0) out[k] = v;
  return out;
}

export async function resolveInstrument(ctx: Ctx, rawId: string): Promise<Instrument | null> {
  const id = rawId.trim().toUpperCase();
  const ix = OPTION_UNDERLYINGS.find((u) => u.id === id);
  if (ix) return { id: ix.id, name: ix.name, kind: 'INDEX', yahoo: ix.yahoo, nseSymbol: ix.nseSymbol, upstoxKey: ix.upstoxKey, fno: true, lotSize: null, sector: null };
  if (!/^[A-Z0-9&-]{1,20}$/.test(id)) return null;
  const [master, lots, sectors] = await Promise.all([getSymbolMaster(ctx), fnoStocks(ctx), getSectorMap(ctx)]);
  const row = master.rows.find((r) => r.symbol === id);
  if (!row && master.source === 'nse') return null;
  return { id, name: row?.name ?? id, kind: 'STOCK', yahoo: toYahooSymbol(id), nseSymbol: id, upstoxKey: null, fno: id in lots, lotSize: lots[id] ?? null, sector: sectors[id] ?? null };
}

// ---- latency / freshness ------------------------------------------------------------------------------------------------------------

export function latencyOf(source: string): Latency {
  if (source.includes('yahoo')) return 'DELAYED';
  if (source === 'nse') return 'SNAPSHOT';
  if (source === 'fixture') return 'SNAPSHOT';
  return 'UNVERIFIED';
}

/** Free Yahoo data is delayed by minutes, so it is judged against a delayed-feed limit and always labelled DELAYED. */
export function maxAgeFor(source: string, kind: 'quote' | 'chain' | 'intraday'): number {
  if (source.includes('yahoo')) return MAX_AGE_SEC.delayedFeed;
  return kind === 'chain' ? MAX_AGE_SEC.optionChain : kind === 'quote' ? MAX_AGE_SEC.quote : MAX_AGE_SEC.intradayCandle;
}

export function isLive(ctx: Ctx, holidays: readonly string[] = []): boolean {
  return isLiveSession(ctx.now(), holidays);
}

export function provOf(source: string, asOf: string | null, fetchedAt: string, note?: string): Provenance {
  return { source, asOf, fetchedAt, latency: latencyOf(source), ...(note ? { note } : {}) };
}

export function stateFor(ctx: Ctx, source: string, asOf: string | null, hasValue: boolean, kind: 'quote' | 'chain' | 'intraday', marketOpen: boolean): DataState {
  return classifyFreshness({ hasValue, asOf, now: ctx.now(), marketOpen, maxAgeSec: maxAgeFor(source, kind) });
}

// ---- candles ------------------------------------------------------------------------------------------------------------------------------

export interface IntradaySet {
  candles: Candle[];
  source: string;
  stale: boolean;
  fetchedAt: string;
  error?: string;
}

export async function loadIntraday(ctx: Ctx, inst: Instrument, interval: '1m' | '5m' | '15m'): Promise<IntradaySet> {
  const now = ctx.now();
  if (ctx.cfg.offline) return { candles: fixtureIntraday(inst.yahoo, interval, now), source: 'fixture', stale: false, fetchedAt: now.toISOString() };
  const range = interval === '1m' ? '5d' : '1mo';
  const live = isLive(ctx);
  const c = await cached(
    ctx.kv,
    `wb/intraday/${inst.id}/${interval}`,
    live ? 45 : 3 * 3600,
    () => track('yahoo-intraday', async () => (await yahooChart(inst.yahoo, range, interval, { fetchImpl: ctx.fetchImpl })).candles, ctx.now),
    { now: () => now.getTime(), maxStaleSec: 3 * 86400 },
  );
  return { candles: c.value, source: 'yahoo', stale: c.stale, fetchedAt: c.savedAt, error: c.error };
}

export interface TfData {
  tf: Timeframe;
  candles: TfCandle[];
  availability: TfAvailability;
  source: string;
  stale: boolean;
  fetchedAt: string | null;
}

export async function loadTf(ctx: Ctx, inst: Instrument, tf: Timeframe): Promise<TfData> {
  const now = ctx.now();
  try {
    if (tf === '1d') {
      const d = await track('yahoo-daily', () => getDaily(ctx, inst.id, { yahoo: inst.yahoo }), ctx.now);
      const candles = markDaily(d.candles, now);
      return { tf, candles, availability: availability(tf, candles), source: d.source, stale: d.stale, fetchedAt: now.toISOString() };
    }
    const spec = SOURCE_FOR[tf];
    const set = await loadIntraday(ctx, inst, spec.interval);
    const candles = aggregate(set.candles, spec.srcMin, TF_MINUTES[tf], now);
    return { tf, candles, availability: availability(tf, candles), source: set.source, stale: set.stale, fetchedAt: set.fetchedAt };
  } catch (e) {
    return { tf, candles: [], availability: availability(tf, null, `Data for ${tf} could not be retrieved: ${e instanceof Error ? e.message : 'error'}`), source: 'none', stale: false, fetchedAt: null };
  }
}

// ---- quote ----------------------------------------------------------------------------------------------------------------------------------

/** Demo only: the price is the last synthetic minute close, so the quote, the candles and the chain tell one story. */
function demoQuote(inst: Instrument, now: Date): Quote {
  const base = fixtureQuote(inst.id, inst.yahoo, now);
  const last = fixtureMinuteBars(inst.yahoo, now, 1).at(-1);
  if (!last) return base;
  const price = +last.c.toFixed(2);
  return { ...base, price, change: price - base.prevClose, changePct: ((price - base.prevClose) / base.prevClose) * 100, high: Math.max(base.high ?? price, price), low: Math.min(base.low ?? price, price) };
}

export async function loadQuote(ctx: Ctx, inst: Instrument): Promise<{ quote: Quote | null; error?: string; stale: boolean }> {
  try {
    if (ctx.cfg.offline) return { quote: demoQuote(inst, ctx.now()), stale: false };
    const target = inst.kind === 'INDEX' ? { id: inst.id, yahoo: inst.yahoo, upstoxKey: inst.upstoxKey ?? undefined } : equityTarget(inst.id);
    const r = await getQuotes(ctx, `wb/${inst.id}`, [target], 20, 600);
    return { quote: r.quotes[inst.id] ?? null, stale: r.stale };
  } catch (e) {
    return { quote: null, stale: false, error: e instanceof Error ? e.message : 'error' };
  }
}

// ---- expiries and chains -----------------------------------------------------------------------------------------------------------------

export interface ExpirySet {
  expiries: string[];
  lotSize: number | null;
  source: string;
  stale: boolean;
}

export async function loadExpiries(ctx: Ctx, inst: Instrument): Promise<ExpirySet> {
  const today = istDateStr(ctx.now());
  if (inst.kind === 'INDEX') {
    const u = OPTION_UNDERLYINGS.find((x) => x.id === inst.id)!;
    const r = await getExpiries(ctx, u);
    return { expiries: r.data.expiries.filter((e) => e >= today), lotSize: r.data.lotSize, source: r.source, stale: r.stale };
  }
  if (!inst.fno) throw new ProviderError('NOT_CONFIGURED', `${inst.id} is not in the F&O segment, so it has no listed option contracts.`);
  if (ctx.cfg.offline) return { expiries: fixtureStockExpiries(ctx.now()), lotSize: inst.lotSize, source: 'fixture', stale: false };
  const ttl = await ttlFor(ctx, 1800, 6 * 3600);
  if (ctx.cfg.upstoxToken) {
    const isin = await isinFor(ctx, inst.id);
    if (isin) {
      try {
        const token = ctx.cfg.upstoxToken;
        const c = await cached(ctx.kv, `wb/expiries/up/${inst.id}`, ttl, () => track('upstox-chain', () => upstoxOptionContracts(token, `NSE_EQ|${isin}`, { fetchImpl: ctx.fetchImpl }), ctx.now), { now: () => ctx.now().getTime(), maxStaleSec: 3 * 86400 });
        return { expiries: c.value.expiries.filter((e) => e >= today), lotSize: c.value.lotSize ?? inst.lotSize, source: 'upstox', stale: c.stale };
      } catch {
        /* fall through to NSE */
      }
    }
  }
  const c = await cached(ctx.kv, `wb/expiries/nse/${inst.id}`, ttl, () => track('nse-expiries', async () => nseExpiries(await fetchNseExpiriesRaw(inst.nseSymbol, { fetchImpl: ctx.fetchImpl })), ctx.now), { now: () => ctx.now().getTime(), maxStaleSec: 3 * 86400 });
  if (c.value.length === 0) throw new ProviderError('MALFORMED', `NSE listed no expiries for ${inst.id}.`);
  return { expiries: c.value.filter((e) => e >= today), lotSize: inst.lotSize, source: 'nse', stale: c.stale };
}

export interface ChainSet {
  chain: NormalizedChain;
  stale: boolean;
  notes: string[];
}

export async function loadChain(ctx: Ctx, inst: Instrument, expiry: string, spotHint: number | null = null): Promise<ChainSet> {
  const now = ctx.now();
  const notes: string[] = [];
  if (ctx.cfg.offline) {
    const spot = spotHint ?? demoQuote(inst, now).price;
    return { chain: fixtureEquityChain(inst.id, spot, expiry, inst.lotSize ?? 100, now), stale: false, notes: ['DEMO DATA: synthetic option chain.'] };
  }
  const load = async (): Promise<NormalizedChain> => {
    if (ctx.cfg.upstoxToken) {
      try {
        const key = inst.upstoxKey ?? (await isinFor(ctx, inst.id).then((i) => (i ? `NSE_EQ|${i}` : null)));
        if (key) {
          const token = ctx.cfg.upstoxToken;
          return await track('upstox-chain', () => upstoxOptionChain(token, key, inst.id, expiry, now, { fetchImpl: ctx.fetchImpl }), ctx.now);
        }
      } catch (e) {
        notes.push(`Upstox chain failed (${e instanceof Error ? e.message : 'error'}); using NSE.`);
      }
    }
    const type = inst.kind === 'INDEX' ? 'Indices' : 'Equity';
    return track('nse-chain', async () => parseNseChain(await fetchNseChainRaw(inst.nseSymbol, expiry, { fetchImpl: ctx.fetchImpl }, type), inst.id, expiry, now.toISOString()), ctx.now);
  };
  const ttl = isLive(ctx) ? 20 : 900;
  const c = await cached(ctx.kv, `wb/chain/${inst.id}/${expiry}`, ttl, load, { now: () => now.getTime(), maxStaleSec: 2 * 86400 });
  if (c.stale) notes.push(`Showing the last good chain (${c.value.asOf ?? c.value.fetchedAt}); the refresh failed: ${c.error}`);
  return { chain: c.value, stale: c.stale, notes };
}

/** First listed expiry at least `minDays` away (else the first listed one), with the reason. */
export function defaultExpiry(expiries: string[], today: string, minDays = 5): { expiry: string | null; reason: string } {
  const future = expiries.filter((e) => e >= today).sort();
  if (future.length === 0) return { expiry: null, reason: 'No upcoming expiry is listed.' };
  const floor = addDays(today, minDays);
  const pick = future.find((e) => e >= floor);
  if (pick) return { expiry: pick, reason: `Nearest expiry at least ${minDays} days away (shorter-dated contracts decay fastest).` };
  return { expiry: future[future.length - 1]!, reason: `No expiry is ${minDays}+ days away; showing the furthest listed one.` };
}

export const strikeStep = strikeStepFor;
export { mapLimit };
