// Yahoo Finance chart API: zero-config default source for Indian indices,
// NSE equities (SYMBOL.NS), global cues, commodities and FX.
//
// Unofficial API: it can rate-limit shared cloud IPs and may change without
// notice. Everything here fails soft, results are cached by the caller, and
// Upstox (official) is preferred when a token is configured.

import { istDateStr } from '../../shared/india';
import { knownVolume } from '../../shared/indicators';
import type { Candle, Quote } from '../../shared/types';
import { fetchJson, mapLimit, ProviderError, type FetchOpts } from '../http';

const BASES = ['https://query1.finance.yahoo.com', 'https://query2.finance.yahoo.com'];

export function toYahooSymbol(symbol: string): string {
  const s = symbol.trim().toUpperCase();
  if (s.startsWith('^') || s.includes('=') || s.endsWith('.NS') || s.endsWith('.BO') || s.endsWith('.SS')) return s;
  return `${s}.NS`;
}

interface YahooChartResponse {
  chart?: {
    result?: {
      meta?: {
        symbol?: string;
        shortName?: string;
        longName?: string;
        regularMarketPrice?: number;
        regularMarketTime?: number;
        regularMarketDayHigh?: number;
        regularMarketDayLow?: number;
        regularMarketVolume?: number;
        chartPreviousClose?: number;
        previousClose?: number;
      };
      timestamp?: number[];
      indicators?: { quote?: { open?: (number | null)[]; high?: (number | null)[]; low?: (number | null)[]; close?: (number | null)[]; volume?: (number | null)[] }[] };
    }[];
    error?: { code?: string; description?: string } | null;
  };
}

export interface YahooChart {
  symbol: string;
  name?: string;
  candles: Candle[];
  meta: NonNullable<NonNullable<NonNullable<YahooChartResponse['chart']>['result']>[number]>['meta'];
}

export function parseYahooChart(body: YahooChartResponse, requested: string): YahooChart {
  const err = body.chart?.error;
  if (err) throw new ProviderError('HTTP', `Yahoo: ${err.description ?? err.code ?? 'error'} for ${requested}`);
  const r = body.chart?.result?.[0];
  if (!r) throw new ProviderError('MALFORMED', `Yahoo: empty result for ${requested}`);
  const ts = r.timestamp ?? [];
  const q = r.indicators?.quote?.[0];
  const candles: Candle[] = [];
  if (q) {
    for (let i = 0; i < ts.length; i++) {
      const c = q.close?.[i];
      const o = q.open?.[i];
      const h = q.high?.[i];
      const l = q.low?.[i];
      if (c == null || o == null || h == null || l == null) continue; // holiday/no-trade rows come back null
      candles.push({ t: ts[i]!, o, h, l, c, v: knownVolume(q.volume?.[i]) });
    }
  }
  return { symbol: r.meta?.symbol ?? requested, name: r.meta?.longName ?? r.meta?.shortName, candles, meta: r.meta };
}

export async function yahooChart(symbol: string, range: string, interval: string, opts: FetchOpts = {}): Promise<YahooChart> {
  const ysym = toYahooSymbol(symbol);
  const path = `/v8/finance/chart/${encodeURIComponent(ysym)}?range=${range}&interval=${interval}&includePrePost=false`;
  let lastErr: unknown;
  for (const base of BASES) {
    try {
      const body = await fetchJson<YahooChartResponse>(base + path, { timeoutMs: 7000, retries: 0, ...opts });
      return parseYahooChart(body, ysym);
    } catch (e) {
      lastErr = e;
      if (e instanceof ProviderError && e.kind === 'HTTP' && e.status === 404) break; // unknown symbol: do not try the mirror
    }
  }
  throw lastErr instanceof Error ? lastErr : new ProviderError('NETWORK', 'Yahoo unreachable');
}

/**
 * Collapse intraday-timestamped duplicates: Yahoo can emit a second bar for the
 * current session. Keep the last bar per IST calendar day.
 */
export function dedupeByIstDay(candles: Candle[]): Candle[] {
  const byDay = new Map<string, Candle>();
  for (const c of candles) byDay.set(istDateStr(new Date(c.t * 1000)), c);
  return [...byDay.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([, c]) => c);
}

/**
 * Build a quote from a short daily series. `prevClose` is the close BEFORE the
 * latest session; using `meta.chartPreviousClose` with a multi-day range gives
 * the close before the *range*, which is a classic wrong-day-change bug.
 */
export function quoteFromChart(symbol: string, chart: YahooChart, fetchedAt: string): Quote {
  const days = dedupeByIstDay(chart.candles);
  const last = days[days.length - 1];
  const price = chart.meta?.regularMarketPrice ?? last?.c;
  if (price == null || !Number.isFinite(price)) throw new ProviderError('MALFORMED', `Yahoo: no price for ${symbol}`);
  let prev = days.length >= 2 ? days[days.length - 2]!.c : undefined;
  if (prev === undefined) prev = chart.meta?.previousClose ?? chart.meta?.chartPreviousClose;
  if (prev === undefined || !Number.isFinite(prev) || prev === 0) throw new ProviderError('MALFORMED', `Yahoo: no previous close for ${symbol}`);
  const mt = chart.meta?.regularMarketTime;
  return {
    symbol,
    name: chart.name,
    price,
    prevClose: prev,
    change: price - prev,
    changePct: ((price - prev) / prev) * 100,
    open: last?.o,
    high: chart.meta?.regularMarketDayHigh ?? last?.h,
    low: chart.meta?.regularMarketDayLow ?? last?.l,
    volume: knownVolume(chart.meta?.regularMarketVolume) ?? last?.v ?? undefined,
    marketTime: mt ? new Date(mt * 1000).toISOString() : null,
    fetchedAt,
    source: 'yahoo',
  };
}

export async function yahooQuote(symbol: string, now: Date, opts: FetchOpts = {}): Promise<Quote> {
  const chart = await yahooChart(symbol, '5d', '1d', opts);
  return quoteFromChart(symbol, chart, now.toISOString());
}

export interface BatchResult<T> {
  ok: Record<string, T>;
  failed: { id: string; reason: string }[];
}

/** Quotes for many symbols with bounded concurrency; partial failure is reported, not hidden. */
export async function yahooQuotes(
  items: { id: string; yahoo: string }[],
  now: Date,
  opts: FetchOpts & { concurrency?: number } = {},
): Promise<BatchResult<Quote>> {
  const res = await mapLimit(items, opts.concurrency ?? 6, (it) => yahooQuote(it.yahoo, now, opts));
  const ok: Record<string, Quote> = {};
  const failed: { id: string; reason: string }[] = [];
  res.forEach((r, i) => {
    const it = items[i]!;
    if (r.status === 'fulfilled') ok[it.id] = { ...r.value, symbol: it.id };
    else failed.push({ id: it.id, reason: r.reason instanceof Error ? r.reason.message : String(r.reason) });
  });
  return { ok, failed };
}

export async function yahooDaily(symbol: string, range: '6mo' | '1y' | '2y' = '1y', opts: FetchOpts = {}): Promise<Candle[]> {
  const chart = await yahooChart(symbol, range, '1d', opts);
  return dedupeByIstDay(chart.candles);
}

export async function yahooIntraday(symbol: string, opts: FetchOpts = {}): Promise<Candle[]> {
  const chart = await yahooChart(symbol, '1d', '5m', opts);
  return chart.candles;
}
