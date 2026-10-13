import { analyzeChain, type ChainAnalytics, type ChainRow, type NormalizedChain } from '../../shared/optionchain';
import { addDays, istDateStr } from '../../shared/india';
import { OPTION_UNDERLYINGS, type OptionUnderlying } from '../../shared/universe';
import { cached } from '../cache';
import type { Ctx } from '../config';
import { fixtureChain } from '../fixtures';
import { fetchNseChainRaw, nseExpiries, parseNseChain } from '../providers/nse';
import { upstoxOptionChain, upstoxOptionContracts } from '../providers/upstox';
import { fixtureQuote } from '../fixtures';
import { ttlFor } from './quotes';
import type { Svc } from './market';

export function findUnderlying(id: string): OptionUnderlying | undefined {
  return OPTION_UNDERLYINGS.find((u) => u.id === id.toUpperCase());
}

export interface Expiries {
  underlying: string;
  expiries: string[];
  lotSize: number | null;
}

export async function getExpiries(ctx: Ctx, u: OptionUnderlying): Promise<Svc<Expiries>> {
  if (ctx.cfg.offline) {
    const base = istDateStr(ctx.now());
    const out: string[] = [];
    let d = base;
    while (out.length < 5) { d = addDays(d, 1); if (new Date(d + 'T00:00:00Z').getUTCDay() === 2) out.push(d); }
    return { data: { underlying: u.id, expiries: out, lotSize: u.id === 'NIFTY' ? 65 : u.id === 'BANKNIFTY' ? 30 : 60 }, source: 'fixture', stale: false, notes: ['DEMO DATA'] };
  }
  const notes: string[] = [];
  const ttl = await ttlFor(ctx, 1800, 6 * 3600);

  if (ctx.cfg.upstoxToken) {
    const token = ctx.cfg.upstoxToken;
    try {
      const c = await cached(ctx.kv, `expiries/up/${u.id}`, ttl, () => upstoxOptionContracts(token, u.upstoxKey, { fetchImpl: ctx.fetchImpl }), { now: () => ctx.now().getTime(), maxStaleSec: 3 * 86400 });
      const today = istDateStr(ctx.now());
      const exp = c.value.expiries.filter((e) => e >= today);
      return { data: { underlying: u.id, expiries: exp, lotSize: c.value.lotSize }, source: 'upstox', stale: c.stale, notes };
    } catch (e) {
      notes.push(`Upstox expiries failed (${e instanceof Error ? e.message : 'error'}); trying NSE.`);
    }
  }
  try {
    const c = await cached(ctx.kv, `expiries/nse/${u.id}`, ttl, async () => nseExpiries(await fetchNseChainRaw(u.nseSymbol, { fetchImpl: ctx.fetchImpl })), { now: () => ctx.now().getTime(), maxStaleSec: 3 * 86400 });
    if (c.value.length === 0) throw new Error('empty expiry list');
    return { data: { underlying: u.id, expiries: c.value.filter((e) => e >= istDateStr(ctx.now())), lotSize: null }, source: 'nse', stale: c.stale, notes };
  } catch (e) {
    throw new Error(`Option expiries unavailable. ${notes.join(' ')} NSE: ${e instanceof Error ? e.message : 'error'}. Set UPSTOX_ANALYTICS_TOKEN for reliable option-chain data.`);
  }
}

export interface ChainView {
  analytics: ChainAnalytics;
  /** strikes within a window of the money, for the table and OI chart */
  rows: ChainRow[];
  lotSize: number | null;
  source: string;
  fetchedAt: string;
}

export function windowRows(rows: ChainRow[], spot: number | null, each = 14): ChainRow[] {
  if (rows.length === 0) return rows;
  const sorted = [...rows].sort((a, b) => a.strike - b.strike);
  if (spot === null) return sorted.slice(Math.max(0, Math.floor(sorted.length / 2) - each), Math.floor(sorted.length / 2) + each + 1);
  let ai = 0;
  for (let i = 0; i < sorted.length; i++) if (Math.abs(sorted[i]!.strike - spot) < Math.abs(sorted[ai]!.strike - spot)) ai = i;
  return sorted.slice(Math.max(0, ai - each), ai + each + 1);
}

export async function getChain(ctx: Ctx, u: OptionUnderlying, expiry: string, lotSize: number | null): Promise<Svc<ChainView>> {
  const now = ctx.now();
  const notes: string[] = [];
  const ttl = await ttlFor(ctx, 30, 900);

  const load = async (): Promise<NormalizedChain> => {
    if (ctx.cfg.offline) {
      const spot = fixtureQuote(u.id, u.yahoo, now).price;
      return fixtureChain(u.id, spot, expiry, u.id === 'BANKNIFTY' ? 100 : 50, now);
    }
    if (ctx.cfg.upstoxToken) {
      try {
        return await upstoxOptionChain(ctx.cfg.upstoxToken, u.upstoxKey, u.id, expiry, now, { fetchImpl: ctx.fetchImpl });
      } catch (e) {
        notes.push(`Upstox chain failed (${e instanceof Error ? e.message : 'error'}); trying NSE.`);
      }
    }
    try {
      return parseNseChain(await fetchNseChainRaw(u.nseSymbol, { fetchImpl: ctx.fetchImpl }), u.id, expiry, now.toISOString());
    } catch (e) {
      throw new Error(`Option chain unavailable. ${notes.join(' ')} NSE: ${e instanceof Error ? e.message : 'error'}. Set UPSTOX_ANALYTICS_TOKEN for reliable option-chain data.`);
    }
  };

  const c = await cached(ctx.kv, `chain/${u.id}/${expiry}`, ttl, load, { now: () => now.getTime(), maxStaleSec: 2 * 86400 });
  const chain = c.value;
  const analytics = analyzeChain(chain);
  if (ctx.cfg.offline) notes.unshift('DEMO DATA: offline mode, values are synthetic.');
  if (c.stale) notes.push(`Showing last good chain (${chain.fetchedAt}); refresh failed: ${c.error}`);
  return {
    data: { analytics, rows: windowRows(chain.rows, chain.spot), lotSize, source: chain.source, fetchedAt: chain.fetchedAt },
    source: chain.source,
    stale: c.stale,
    notes,
  };
}
