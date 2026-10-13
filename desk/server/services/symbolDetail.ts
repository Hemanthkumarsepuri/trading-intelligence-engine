import { analyze, type TechSnapshot } from '../../shared/indicators';
import type { Candle, Quote } from '../../shared/types';
import { cached } from '../cache';
import type { Ctx } from '../config';
import { fetchDeliveryMap, type DeliveryRow } from '../providers/nse';
import type { NewsItem } from '../providers/news';
import { equityTarget, getDaily, getQuotes, ttlFor } from './quotes';
import { getBan, getNews, type Svc } from './market';
import { getLiveHolidays, getLotSizes, getSectorMap, getSymbolMaster } from './symbols';

export interface SymbolDetail {
  symbol: string;
  name: string | null;
  sector: string | null;
  quote: Quote | null;
  candles: Candle[];
  tech: TechSnapshot | null;
  delivery: DeliveryRow | null;
  lotSize: number | null;
  inFnoBan: boolean;
  news: NewsItem[];
}

export async function getDeliveryMap(ctx: Ctx): Promise<{ date: string; rows: Record<string, DeliveryRow> } | null> {
  if (ctx.cfg.offline) return null;
  try {
    const holidays = await getLiveHolidays(ctx);
    return (await cached(ctx.kv, 'delivery-map', await ttlFor(ctx, 6 * 3600, 6 * 3600), () => fetchDeliveryMap(ctx.now(), holidays, { fetchImpl: ctx.fetchImpl }), { now: () => ctx.now().getTime(), maxStaleSec: 5 * 86400 })).value;
  } catch {
    return null;
  }
}

export async function getSymbolDetail(ctx: Ctx, symbol: string): Promise<Svc<SymbolDetail>> {
  const notes: string[] = [];
  const [qr, daily, master, sectors, lots, delivery, ban] = await Promise.all([
    getQuotes(ctx, `sym/${symbol}`, [equityTarget(symbol)], 15, 600).catch((e: Error) => { notes.push(`Quote unavailable: ${e.message}`); return null; }),
    getDaily(ctx, symbol).catch((e: Error) => { notes.push(`Daily history unavailable: ${e.message}`); return null; }),
    getSymbolMaster(ctx),
    getSectorMap(ctx),
    getLotSizes(ctx),
    getDeliveryMap(ctx),
    getBan(ctx).then((b) => b.data.symbols).catch(() => [] as string[]),
  ]);
  const name = master.rows.find((r) => r.symbol === symbol)?.name ?? null;
  const news = await getNews(ctx, symbol, name ?? undefined).then((n) => n.data.items.slice(0, 12)).catch(() => [] as NewsItem[]);
  const candles = daily?.candles ?? [];
  if (qr) notes.push(...qr.notes);
  if (daily?.stale) notes.push('Daily history is cached (refresh failed).');
  if (delivery === null && !ctx.cfg.offline) notes.push('Delivery % unavailable (NSE archive not reachable).');
  return {
    data: {
      symbol,
      name,
      sector: sectors[symbol] ?? null,
      quote: qr?.quotes[symbol] ?? null,
      candles: candles.slice(-260),
      tech: candles.length >= 2 ? analyze(candles) : null,
      delivery: delivery?.rows[symbol] ?? null,
      lotSize: lots[symbol] ?? null,
      inFnoBan: ban.includes(symbol),
      news,
    },
    source: [qr?.source, daily?.source].filter(Boolean).join(' | ') || 'none',
    stale: Boolean(qr?.stale || daily?.stale),
    notes,
  };
}
