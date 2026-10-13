// Upstox (official, read-only). Uses the free Analytics token, which cannot
// place or modify orders - consistent with the TIRE safety contract. Response
// shapes below were verified live by the TIRE engine in Aug-Sep 2026.

import type { NormalizedChain, ChainRow, OptionLeg } from '../../shared/optionchain';
import type { Candle, Quote } from '../../shared/types';
import { fetchJson, ProviderError, type FetchOpts } from '../http';

const BASE = 'https://api.upstox.com';

function auth(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}`, Accept: 'application/json' };
}

const num = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null);

interface QuoteEntry {
  instrument_token?: string;
  last_price?: number;
  net_change?: number;
  volume?: number;
  average_price?: number;
  ohlc?: { open?: number; high?: number; low?: number; close?: number };
  last_trade_time?: string | number;
}

export function parseUpstoxQuotes(body: { status?: string; data?: Record<string, QuoteEntry> }, fetchedAt: string): Record<string, Quote> {
  if (body.status !== 'success' || !body.data) throw new ProviderError('MALFORMED', 'Upstox quotes: non-success response');
  const out: Record<string, Quote> = {};
  for (const e of Object.values(body.data)) {
    const key = e.instrument_token;
    const price = num(e.last_price);
    const net = num(e.net_change);
    if (!key || price === null || net === null) continue;
    const prev = price - net; // verified against /v3 ltp 'cp' by TIRE
    if (!(prev > 0)) continue;
    const ms = e.last_trade_time !== undefined ? Number(e.last_trade_time) : NaN;
    out[key] = {
      symbol: key,
      price,
      prevClose: prev,
      change: net,
      changePct: (net / prev) * 100,
      open: num(e.ohlc?.open) ?? undefined,
      high: num(e.ohlc?.high) ?? undefined,
      low: num(e.ohlc?.low) ?? undefined,
      volume: num(e.volume) ?? undefined,
      marketTime: Number.isFinite(ms) && ms > 0 ? new Date(ms).toISOString() : null,
      fetchedAt,
      source: 'upstox',
    };
  }
  return out;
}

export async function upstoxQuotes(token: string, keys: string[], now: Date, opts: FetchOpts = {}): Promise<Record<string, Quote>> {
  if (keys.length === 0) return {};
  const out: Record<string, Quote> = {};
  // Upstox accepts many keys per call; chunk conservatively.
  for (let i = 0; i < keys.length; i += 100) {
    const chunk = keys.slice(i, i + 100);
    const url = `${BASE}/v2/market-quote/quotes?${new URLSearchParams({ instrument_key: chunk.join(',') })}`;
    const body = await fetchJson<{ status?: string; data?: Record<string, QuoteEntry> }>(url, { headers: auth(token), timeoutMs: 8000, ...opts });
    Object.assign(out, parseUpstoxQuotes(body, now.toISOString()));
  }
  return out;
}

export function parseUpstoxCandles(rows: unknown): Candle[] {
  if (!Array.isArray(rows)) throw new ProviderError('MALFORMED', 'Upstox candles: not an array');
  const out: Candle[] = [];
  for (const r of rows) {
    if (!Array.isArray(r) || r.length < 6) continue;
    const t = Date.parse(String(r[0]));
    const [o, h, l, c, v] = [num(r[1]), num(r[2]), num(r[3]), num(r[4]), num(r[5])];
    if (Number.isNaN(t) || o === null || h === null || l === null || c === null) continue;
    out.push({ t: Math.floor(t / 1000), o, h, l, c, v: v ?? 0 });
  }
  return out.sort((a, b) => a.t - b.t);
}

/** Daily candles: history from v3 plus the current session from the intraday endpoint. */
export async function upstoxDaily(token: string, key: string, from: string, to: string, opts: FetchOpts = {}): Promise<Candle[]> {
  const enc = encodeURIComponent(key);
  const hist = await fetchJson<{ status?: string; data?: { candles?: unknown } }>(`${BASE}/v3/historical-candle/${enc}/days/1/${to}/${from}`, { headers: auth(token), timeoutMs: 9000, ...opts });
  if (hist.status !== 'success') throw new ProviderError('MALFORMED', 'Upstox history: non-success');
  const candles = parseUpstoxCandles(hist.data?.candles);
  try {
    const intra = await fetchJson<{ status?: string; data?: { candles?: unknown } }>(`${BASE}/v3/historical-candle/intraday/${enc}/days/1`, { headers: auth(token), timeoutMs: 6000, ...opts });
    if (intra.status === 'success') {
      const byT = new Map(candles.map((c) => [c.t, c]));
      for (const c of parseUpstoxCandles(intra.data?.candles)) byT.set(c.t, c);
      return [...byT.values()].sort((a, b) => a.t - b.t);
    }
  } catch {
    // the current-session bar is optional; history alone is still correct
  }
  return candles;
}

export interface OptionContractInfo {
  expiries: string[];
  lotSize: number | null;
}

export async function upstoxOptionContracts(token: string, underlyingKey: string, opts: FetchOpts = {}): Promise<OptionContractInfo> {
  const url = `${BASE}/v2/option/contract?${new URLSearchParams({ instrument_key: underlyingKey })}`;
  const body = await fetchJson<{ status?: string; data?: { expiry?: string; lot_size?: number }[] }>(url, { headers: auth(token), timeoutMs: 9000, ...opts });
  if (body.status !== 'success' || !Array.isArray(body.data)) throw new ProviderError('MALFORMED', 'Upstox option contracts: unexpected shape');
  const set = new Set<string>();
  let lot: number | null = null;
  for (const r of body.data) {
    if (typeof r.expiry === 'string') set.add(r.expiry.slice(0, 10));
    if (lot === null && typeof r.lot_size === 'number' && r.lot_size > 0) lot = r.lot_size;
  }
  return { expiries: [...set].sort(), lotSize: lot };
}

interface ChainSide {
  market_data?: { ltp?: number; volume?: number; oi?: number; prev_oi?: number; bid_price?: number; ask_price?: number };
  option_greeks?: { iv?: number };
}

const leg = (s: ChainSide | undefined): OptionLeg => ({
  ltp: num(s?.market_data?.ltp),
  oi: num(s?.market_data?.oi),
  prevOi: num(s?.market_data?.prev_oi),
  volume: num(s?.market_data?.volume),
  iv: num(s?.option_greeks?.iv),
  bid: num(s?.market_data?.bid_price),
  ask: num(s?.market_data?.ask_price),
});

export function parseUpstoxChain(body: { status?: string; data?: { strike_price?: number; underlying_spot_price?: number; call_options?: ChainSide; put_options?: ChainSide }[] }, underlying: string, expiry: string, fetchedAt: string): NormalizedChain {
  if (body.status !== 'success' || !Array.isArray(body.data)) throw new ProviderError('MALFORMED', 'Upstox option chain: unexpected shape');
  let spot: number | null = null;
  const rows: ChainRow[] = [];
  for (const r of body.data) {
    const strike = num(r.strike_price);
    if (strike === null) continue;
    const s = num(r.underlying_spot_price);
    if (s !== null) spot = s;
    rows.push({ strike, ce: leg(r.call_options), pe: leg(r.put_options) });
  }
  if (rows.length === 0) throw new ProviderError('MALFORMED', `Upstox option chain: no strikes for ${underlying} ${expiry}`);
  return { underlying, expiry, spot, rows: rows.sort((a, b) => a.strike - b.strike), source: 'upstox', fetchedAt };
}

export async function upstoxOptionChain(token: string, underlyingKey: string, underlying: string, expiry: string, now: Date, opts: FetchOpts = {}): Promise<NormalizedChain> {
  const url = `${BASE}/v2/option/chain?${new URLSearchParams({ instrument_key: underlyingKey, expiry_date: expiry })}`;
  const body = await fetchJson<Parameters<typeof parseUpstoxChain>[0]>(url, { headers: auth(token), timeoutMs: 10000, ...opts });
  return parseUpstoxChain(body, underlying, expiry, now.toISOString());
}

export interface IpoRow {
  id: string;
  name: string;
  symbol: string | null;
  status: string;
  type: string;
  issueSizeCr: number | null;
  priceBand: string | null;
  opens: string | null;
  closes: string | null;
  subscription: string | null;
}

export async function upstoxIpos(token: string, status: 'open' | 'upcoming' | 'closed', opts: FetchOpts = {}): Promise<IpoRow[]> {
  const url = `${BASE}/v2/ipos?${new URLSearchParams({ status, page_number: '1' })}`;
  const body = await fetchJson<{ status?: string; data?: Record<string, unknown>[] }>(url, { headers: auth(token), timeoutMs: 8000, ...opts });
  if (body.status !== 'success' || !Array.isArray(body.data)) throw new ProviderError('MALFORMED', 'Upstox IPOs: unexpected shape');
  return body.data.map((r) => {
    const lo = num(r.minimum_price);
    const hi = num(r.maximum_price);
    const size = num(r.issue_size);
    return {
      id: String(r.id ?? r.name),
      name: String(r.name ?? ''),
      symbol: typeof r.symbol === 'string' ? r.symbol : null,
      status: String(r.status ?? status),
      type: String(r.issue_type ?? ''),
      // Unit is not documented: an INR figure for even a small IPO exceeds 1e6,
      // while no issue is anywhere near a million crore, so the split is safe.
      issueSizeCr: size !== null ? (size >= 1e6 ? size / 1e7 : size) : null,
      priceBand: lo !== null && hi !== null ? (lo === hi ? `${hi}` : `${lo}-${hi}`) : null,
      opens: typeof r.bidding_start_date === 'string' ? r.bidding_start_date.slice(0, 10) : null,
      closes: typeof r.bidding_end_date === 'string' ? r.bidding_end_date.slice(0, 10) : null,
      subscription: typeof r.total_subscription === 'string' ? r.total_subscription : null,
    };
  });
}
