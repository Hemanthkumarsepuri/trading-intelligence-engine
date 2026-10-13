import { FALLBACK_SYMBOLS, cleanSymbol, isValidSymbol } from '../../shared/universe';
import { cached } from '../cache';
import type { Ctx } from '../config';
import { fetchEquityList, fetchLotSizes, fetchNseHolidays, fetchSectorMap, type SymbolRow } from '../providers/nse';

const DAY = 24 * 3600;

export interface SymbolMaster {
  rows: SymbolRow[];
  source: 'nse' | 'fallback';
}

export async function getSymbolMaster(ctx: Ctx): Promise<SymbolMaster> {
  if (ctx.cfg.offline) return { rows: FALLBACK_SYMBOLS.map((s) => ({ ...s, isin: '' })), source: 'fallback' };
  try {
    const c = await cached(ctx.kv, 'symbol-master', 7 * DAY, () => fetchEquityList({ fetchImpl: ctx.fetchImpl }), { maxStaleSec: 60 * DAY, now: () => ctx.now().getTime() });
    return { rows: c.value, source: 'nse' };
  } catch {
    return { rows: FALLBACK_SYMBOLS.map((s) => ({ ...s, isin: '' })), source: 'fallback' };
  }
}

export async function searchSymbols(ctx: Ctx, q: string, limit = 12): Promise<{ symbol: string; name: string }[]> {
  const query = q.trim().toUpperCase();
  if (query.length < 1) return [];
  const { rows } = await getSymbolMaster(ctx);
  const starts: SymbolRow[] = [];
  const contains: SymbolRow[] = [];
  for (const r of rows) {
    if (r.symbol === query) starts.unshift(r);
    else if (r.symbol.startsWith(query)) starts.push(r);
    else if (r.symbol.includes(query) || r.name.toUpperCase().includes(query)) contains.push(r);
    if (starts.length + contains.length > 400) break;
  }
  return [...starts, ...contains].slice(0, limit).map((r) => ({ symbol: r.symbol, name: r.name }));
}

/** True when the symbol exists on NSE (or in the fallback list when NSE's list is unavailable). */
export async function symbolExists(ctx: Ctx, symbol: string): Promise<{ exists: boolean; verified: boolean }> {
  const s = cleanSymbol(symbol);
  if (!isValidSymbol(s)) return { exists: false, verified: true };
  const m = await getSymbolMaster(ctx);
  const hit = m.rows.some((r) => r.symbol === s);
  // With only the fallback list we cannot prove absence, so allow it but say so.
  return m.source === 'nse' ? { exists: hit, verified: true } : { exists: true, verified: hit };
}

export async function isinFor(ctx: Ctx, symbol: string): Promise<string | null> {
  const m = await getSymbolMaster(ctx);
  const r = m.rows.find((x) => x.symbol === symbol);
  return r && r.isin ? r.isin : null;
}

export async function getSectorMap(ctx: Ctx): Promise<Record<string, string>> {
  if (ctx.cfg.offline) return { RELIANCE: 'Oil Gas & Consumable Fuels', TCS: 'Information Technology', INFY: 'Information Technology', HDFCBANK: 'Financial Services', ICICIBANK: 'Financial Services', SBIN: 'Financial Services', ITC: 'Fast Moving Consumer Goods', LT: 'Construction' };
  try {
    return (await cached(ctx.kv, 'sector-map', 7 * DAY, () => fetchSectorMap({ fetchImpl: ctx.fetchImpl }), { maxStaleSec: 90 * DAY, now: () => ctx.now().getTime() })).value;
  } catch {
    return {};
  }
}

export async function getLotSizes(ctx: Ctx): Promise<Record<string, number>> {
  if (ctx.cfg.offline) return { RELIANCE: 500, TCS: 175, INFY: 400, HDFCBANK: 550, ICICIBANK: 700, SBIN: 750 };
  try {
    return (await cached(ctx.kv, 'lot-sizes', DAY, () => fetchLotSizes({ fetchImpl: ctx.fetchImpl }), { maxStaleSec: 20 * DAY, now: () => ctx.now().getTime() })).value;
  } catch {
    return {};
  }
}

/** Live holiday list from NSE; [] when unavailable (the embedded calendar still applies). */
export async function getLiveHolidays(ctx: Ctx): Promise<string[]> {
  if (ctx.cfg.offline) return [];
  try {
    return (await cached(ctx.kv, 'nse-holidays', DAY, () => fetchNseHolidays({ fetchImpl: ctx.fetchImpl }), { maxStaleSec: 90 * DAY, now: () => ctx.now().getTime() })).value;
  } catch {
    return [];
  }
}
