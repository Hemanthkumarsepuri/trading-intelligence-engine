// Quote routing: Upstox (official) when configured and resolvable, Yahoo otherwise,
// per-instrument fallback so one bad symbol never blanks a screen.

import { isLiveSession } from '../../shared/india';
import type { Candle, Quote } from '../../shared/types';
import { toYahooSymbol, yahooDaily, yahooQuotes } from '../providers/yahoo';
import { upstoxDaily, upstoxQuotes } from '../providers/upstox';
import { cached } from '../cache';
import type { Ctx } from '../config';
import { fixtureDaily, fixtureQuote } from '../fixtures';
import { isinFor } from './symbols';
import { getLiveHolidays } from './symbols';

export interface Target {
  /** id we key the result by (NSE symbol or index id) */
  id: string;
  yahoo: string;
  /** Upstox instrument_key if already known (indices) */
  upstoxKey?: string;
  /** NSE equity symbol, used to resolve an ISIN for Upstox */
  equity?: string;
}

export interface QuoteResult {
  quotes: Record<string, Quote>;
  notes: string[];
  stale: boolean;
  source: string;
}

export async function ttlFor(ctx: Ctx, liveSec: number, closedSec: number): Promise<number> {
  const live = isLiveSession(ctx.now(), await getLiveHolidays(ctx));
  return live ? liveSec : closedSec;
}

async function fetchQuotes(ctx: Ctx, targets: Target[]): Promise<QuoteResult> {
  const now = ctx.now();
  const notes: string[] = [];
  const quotes: Record<string, Quote> = {};

  if (ctx.cfg.offline) {
    for (const t of targets) quotes[t.id] = fixtureQuote(t.id, t.yahoo, now);
    return { quotes, notes: ['DEMO DATA: offline mode, values are synthetic.'], stale: false, source: 'fixture' };
  }

  let remaining = targets;
  if (ctx.cfg.upstoxToken) {
    const keyed: { t: Target; key: string }[] = [];
    for (const t of targets) {
      const key = t.upstoxKey ?? (t.equity ? await isinFor(ctx, t.equity).then((i) => (i ? `NSE_EQ|${i}` : null)) : null);
      if (key) keyed.push({ t, key });
    }
    if (keyed.length > 0) {
      try {
        const res = await upstoxQuotes(ctx.cfg.upstoxToken, keyed.map((k) => k.key), now, { fetchImpl: ctx.fetchImpl });
        for (const { t, key } of keyed) {
          const q = res[key];
          if (q) quotes[t.id] = { ...q, symbol: t.id };
        }
        remaining = targets.filter((t) => !quotes[t.id]);
      } catch (e) {
        notes.push(`Upstox quotes failed (${e instanceof Error ? e.message : 'error'}); using Yahoo.`);
      }
    }
  }

  if (remaining.length > 0) {
    const y = await yahooQuotes(remaining.map((t) => ({ id: t.id, yahoo: t.yahoo })), now, { fetchImpl: ctx.fetchImpl });
    Object.assign(quotes, y.ok);
    for (const f of y.failed) notes.push(`${f.id}: ${f.reason}`);
  }

  if (Object.keys(quotes).length === 0) throw new Error(`No quotes available. ${notes.join(' | ')}`);
  const srcs = new Set(Object.values(quotes).map((q) => q.source));
  return { quotes, notes, stale: false, source: [...srcs].join('+') };
}

export async function getQuotes(ctx: Ctx, group: string, targets: Target[], liveSec = 20, closedSec = 600): Promise<QuoteResult> {
  if (targets.length === 0) return { quotes: {}, notes: [], stale: false, source: 'none' };
  const ttl = await ttlFor(ctx, liveSec, closedSec);
  const ids = targets.map((t) => t.id).sort().join(',');
  const key = `quotes/${group}/${ids.length > 80 ? hashStr(ids) : ids}`;
  const c = await cached(ctx.kv, key, ttl, () => fetchQuotes(ctx, targets), { now: () => ctx.now().getTime(), maxStaleSec: 3 * 86400 });
  return { ...c.value, stale: c.stale, notes: c.stale ? [...c.value.notes, `Showing last good data from ${c.savedAt} (refresh failed: ${c.error ?? 'unknown'}).`] : c.value.notes };
}

export function equityTarget(symbol: string): Target {
  return { id: symbol, yahoo: toYahooSymbol(symbol), equity: symbol };
}

function hashStr(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

/** ~1y of daily candles for indicators and charts. */
export async function getDaily(ctx: Ctx, symbol: string, opts: { upstoxKey?: string; yahoo?: string } = {}): Promise<{ candles: Candle[]; stale: boolean; source: string; error?: string }> {
  const now = ctx.now();
  const ysym = opts.yahoo ?? toYahooSymbol(symbol);
  if (ctx.cfg.offline) return { candles: fixtureDaily(ysym, now), stale: false, source: 'fixture' };
  const ttl = await ttlFor(ctx, 300, 6 * 3600);
  const c = await cached(
    ctx.kv,
    `daily/${symbol}`,
    ttl,
    async () => {
      if (ctx.cfg.upstoxToken) {
        const key = opts.upstoxKey ?? (await isinFor(ctx, symbol).then((i) => (i ? `NSE_EQ|${i}` : null)));
        if (key) {
          try {
            const to = now.toISOString().slice(0, 10);
            const from = new Date(now.getTime() - 400 * 86400000).toISOString().slice(0, 10);
            const cs = await upstoxDaily(ctx.cfg.upstoxToken, key, from, to, { fetchImpl: ctx.fetchImpl });
            if (cs.length >= 30) return { candles: cs, source: 'upstox' };
          } catch {
            /* fall through to Yahoo */
          }
        }
      }
      return { candles: await yahooDaily(ysym, '1y', { fetchImpl: ctx.fetchImpl }), source: 'yahoo' };
    },
    { now: () => now.getTime(), maxStaleSec: 5 * 86400 },
  );
  return { candles: c.value.candles, stale: c.stale, source: c.value.source, error: c.error };
}
