// News and event context. Sources are limited and said to be: exchange filings and calendars from NSE,
// and public RSS headlines. There is no licensed macro calendar, so that panel reports UNSUPPORTED
// instead of inventing events.

import { dedupeEvents, normalizeNews, normalizeNseAnnouncement, normalizeNseEventCalendar, sortEvents, upcomingEvents, type MarketEvent, type RawNseAnnouncement, type RawNseEvent, type Upcoming } from '../../../shared/events';
import { cached } from '../../cache';
import type { Ctx } from '../../config';
import { fixtureAnnouncements, fixtureEventCalendar } from '../../fixtures-wb';
import { mapLimit } from '../../http';
import { fetchNseAnnouncements, fetchNseEventCalendar } from '../../providers/nse';
import { getNews } from '../market';
import { isLive, track } from './data';

export interface SourceStatus {
  id: string;
  label: string;
  state: 'OK' | 'PARTIAL' | 'UNAVAILABLE' | 'UNSUPPORTED';
  detail: string;
  retrievedAt: string | null;
}

export interface EventsView {
  symbols: string[];
  events: MarketEvent[];
  upcoming: Upcoming[];
  sources: SourceStatus[];
  notes: string[];
}

const MAX_SYMBOLS = 8;
const RECENT_DAYS = 14;

async function announcementsFor(ctx: Ctx, symbol: string): Promise<MarketEvent[]> {
  const at = ctx.now().toISOString();
  const raw: unknown[] = ctx.cfg.offline
    ? fixtureAnnouncements(symbol, ctx.now())
    : (await cached(ctx.kv, `wb/ann/${symbol}`, isLive(ctx) ? 600 : 3600, () => track('nse-announcements', () => fetchNseAnnouncements(symbol, { fetchImpl: ctx.fetchImpl }), ctx.now), { now: () => ctx.now().getTime(), maxStaleSec: 2 * 86400 })).value;
  const cutoff = ctx.now().getTime() - RECENT_DAYS * 86_400_000;
  const out: MarketEvent[] = [];
  for (const r of raw.slice(0, 60)) {
    const e = normalizeNseAnnouncement(r as RawNseAnnouncement, symbol, at);
    if (e && (!e.publishedAt || Date.parse(e.publishedAt) >= cutoff)) out.push(e);
  }
  return out.slice(0, 15);
}

async function calendarFor(ctx: Ctx, symbol: string): Promise<MarketEvent[]> {
  const at = ctx.now().toISOString();
  const raw: unknown[] = ctx.cfg.offline
    ? fixtureEventCalendar(symbol, ctx.now())
    : (await cached(ctx.kv, `wb/evt/${symbol}`, 3 * 3600, () => track('nse-events', () => fetchNseEventCalendar(symbol, { fetchImpl: ctx.fetchImpl }), ctx.now), { now: () => ctx.now().getTime(), maxStaleSec: 3 * 86400 })).value;
  const out: MarketEvent[] = [];
  for (const r of raw) {
    const e = normalizeNseEventCalendar(r as RawNseEvent, symbol, at);
    if (e) out.push(e);
  }
  return out;
}

export async function getEvents(ctx: Ctx, symbolsIn: string[], withNews = true): Promise<EventsView> {
  const symbols = [...new Set(symbolsIn.map((s) => s.toUpperCase()))].slice(0, MAX_SYMBOLS);
  const at = ctx.now().toISOString();
  const events: MarketEvent[] = [];
  const sources: SourceStatus[] = [];
  const notes: string[] = [];
  if (symbolsIn.length > MAX_SYMBOLS) notes.push(`Only the first ${MAX_SYMBOLS} symbols are loaded at once, to respect provider limits.`);

  const ann = await mapLimit(symbols, 2, (s) => announcementsFor(ctx, s));
  const cal = await mapLimit(symbols, 2, (s) => calendarFor(ctx, s));
  const status = (id: string, label: string, res: PromiseSettledResult<MarketEvent[]>[], what: string): void => {
    const failed = res.map((r, i) => (r.status === 'rejected' ? `${symbols[i]}: ${r.reason instanceof Error ? r.reason.message.slice(0, 120) : 'error'}` : null)).filter(Boolean) as string[];
    for (const r of res) if (r.status === 'fulfilled') events.push(...r.value);
    if (symbols.length === 0) sources.push({ id, label, state: 'OK', detail: 'No symbol selected.', retrievedAt: null });
    else if (failed.length === 0) sources.push({ id, label, state: 'OK', detail: ctx.cfg.offline ? `DEMO DATA: synthetic ${what}.` : `${what} loaded for ${symbols.length} symbol(s).`, retrievedAt: at });
    else if (failed.length < symbols.length) sources.push({ id, label, state: 'PARTIAL', detail: `Unavailable for: ${failed.join('; ')}`, retrievedAt: at });
    else sources.push({ id, label, state: 'UNAVAILABLE', detail: `${failed[0]}. NSE often blocks cloud hosts; nothing is substituted.`, retrievedAt: null });
  };
  status('nse-announcements', 'NSE corporate announcements', ann, 'filings');
  status('nse-events', 'NSE event calendar (results, board meetings)', cal, 'calendar entries');

  if (withNews) {
    const lists = await mapLimit(symbols.length ? symbols : [''], 2, async (s) => {
      const r = await track('rss-news', () => getNews(ctx, s || undefined), ctx.now);
      return r.data.items.slice(0, 12).map((n) => normalizeNews(n, s || null, at));
    });
    const bad = lists.filter((l) => l.status === 'rejected').length;
    for (const l of lists) if (l.status === 'fulfilled') events.push(...l.value);
    sources.push({ id: 'rss-news', label: 'Public RSS headlines', state: bad === 0 ? 'OK' : bad < lists.length ? 'PARTIAL' : 'UNAVAILABLE', detail: bad === 0 ? 'Headlines are reported by the outlet named on each item and are not independently verified.' : `${bad} of ${lists.length} feeds failed; nothing is substituted.`, retrievedAt: bad === lists.length ? null : at });
  }
  sources.push({ id: 'macro', label: 'Macro and sector event calendar', state: 'UNSUPPORTED', detail: 'No licensed macroeconomic calendar is configured. RBI, inflation and global events appear only if a headline covers them. Nothing is inferred.', retrievedAt: null });

  const sorted = sortEvents(dedupeEvents(events)).slice(0, 120);
  return { symbols, events: sorted, upcoming: upcomingEvents(sorted, ctx.now(), 10), sources, notes };
}

/** Scheduled events only (used by the scanner for event proximity). Never throws. */
export async function upcomingFor(ctx: Ctx, symbol: string): Promise<Upcoming[]> {
  try {
    return upcomingEvents(await calendarFor(ctx, symbol), ctx.now(), 10);
  } catch {
    return [];
  }
}
