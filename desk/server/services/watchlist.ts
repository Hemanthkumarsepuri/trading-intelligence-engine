// Watchlist view: live quotes plus a technical snapshot per symbol, bounded by a
// deadline so a slow upstream can never make the function time out.

import { analyze, type TechSnapshot } from '../../shared/indicators';
import type { Quote, WatchItem } from '../../shared/types';
import type { Ctx } from '../config';
import { mapLimit } from '../http';
import { equityTarget, getDaily, getQuotes } from './quotes';
import { getBan, type Svc } from './market';
import { getSectorMap } from './symbols';
import { listWatchlist } from './userdata';

export interface WatchRow {
  item: WatchItem;
  quote: Quote | null;
  tech: TechSnapshot | null;
  sector: string | null;
  inFnoBan: boolean;
  /** why a column is empty, so the UI never shows an unexplained blank */
  gaps: string[];
}

/** Resolve `p` or `fallback` after `ms`, whichever is first. */
export async function withDeadline<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  let t: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<T>((resolve) => {
    t = setTimeout(() => resolve(fallback), ms);
  });
  try {
    return await Promise.race([p, timeout]);
  } finally {
    if (t) clearTimeout(t);
  }
}

export async function getWatchlistView(ctx: Ctx, opts: { deadlineMs?: number } = {}): Promise<Svc<{ rows: WatchRow[] }>> {
  const items = await listWatchlist(ctx);
  const notes: string[] = [];
  if (items.length === 0) return { data: { rows: [] }, source: 'none', stale: false, notes };

  const deadline = opts.deadlineMs ?? 6000;
  const [qr, sectors, ban] = await Promise.all([
    getQuotes(ctx, 'watch', items.map((i) => equityTarget(i.symbol)), 15, 600).catch((e: Error) => {
      notes.push(`Quotes unavailable: ${e.message}`);
      return null;
    }),
    getSectorMap(ctx),
    getBan(ctx).then((b) => new Set(b.data.symbols)).catch(() => new Set<string>()),
  ]);
  if (qr) notes.push(...qr.notes);

  // Daily history is cached for hours, so only a cold cache is slow; the deadline
  // bounds that case and the symbols that missed it say so.
  const techs = new Map<string, TechSnapshot | null>();
  const timedOut = new Set<string>();
  const start = Date.now();
  await withDeadline(
    mapLimit(items, 5, async (it) => {
      if (Date.now() - start > deadline) {
        timedOut.add(it.symbol);
        return;
      }
      const d = await getDaily(ctx, it.symbol).catch(() => null);
      techs.set(it.symbol, d && d.candles.length >= 2 ? analyze(d.candles) : null);
    }),
    deadline,
    [],
  );
  for (const it of items) if (!techs.has(it.symbol)) timedOut.add(it.symbol);

  const rows: WatchRow[] = items.map((item) => {
    const quote = qr?.quotes[item.symbol] ?? null;
    const tech = techs.get(item.symbol) ?? null;
    const gaps: string[] = [];
    if (!quote) gaps.push('no quote');
    if (!tech) gaps.push(timedOut.has(item.symbol) ? 'history still loading, refresh in a moment' : 'no daily history');
    return { item, quote, tech, sector: sectors[item.symbol] ?? null, inFnoBan: ban.has(item.symbol), gaps };
  });
  if (timedOut.size > 0) notes.push(`${timedOut.size} symbol(s) have no technicals yet (history loads in the background and appears on the next refresh).`);

  return { data: { rows }, source: qr?.source ?? 'none', stale: Boolean(qr?.stale), notes: [...new Set(notes)] };
}
