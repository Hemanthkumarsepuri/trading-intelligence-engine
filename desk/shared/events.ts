// News / announcement / scheduled-event normalisation and chart correlation.
// Nothing here claims causation: correlation yields "possible context" only, and only when the
// event's own timestamp is reliable.

import { istDateStr } from './india';
import { parseNseTimestamp } from './quality';
import type { TfCandle } from './candles';

export type EventType = 'ANNOUNCEMENT' | 'EARNINGS' | 'BOARD_MEETING' | 'CORPORATE_ACTION' | 'NEWS' | 'MACRO';
export type Verification = 'CONFIRMED_EXCHANGE' | 'REPORTED' | 'UNVERIFIED';

export interface MarketEvent {
  id: string;
  headline: string;
  /** who published it (exchange, company, or the news outlet) */
  publisher: string;
  /** original publication time, ISO UTC; null when the source gave none or it could not be parsed */
  publishedAt: string | null;
  /** zone the source states its times in (NSE: IST). The ISO field above is already converted to UTC. */
  sourceZone: 'Asia/Kolkata' | 'UTC' | 'unknown';
  /** true only if publishedAt came from the source itself and parsed cleanly */
  timestampReliable: boolean;
  /** for scheduled events (results, board meetings): the day it is scheduled (YYYY-MM-DD, IST) */
  scheduledFor: string | null;
  link: string | null;
  symbols: string[];
  sectors: string[];
  type: EventType;
  verification: Verification;
  retrievedAt: string;
  /** source delay or freshness limitation, shown with the item */
  limitation: string | null;
}

const ALLOWED_LINK_HOSTS = /^https:\/\/([a-z0-9-]+\.)*(nseindia\.com|bseindia\.com)\//i;

/** Only http(s) links are ever rendered; exchange attachments are limited to NSE/BSE hosts. */
export function safeLink(u: unknown, exchangeOnly = false): string | null {
  if (typeof u !== 'string') return null;
  try {
    const url = new URL(u);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return null;
    if (exchangeOnly && !ALLOWED_LINK_HOSTS.test(u)) return null;
    return url.toString();
  } catch {
    return null;
  }
}

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);
const clean = (s: unknown) => (typeof s === 'string' ? s.replace(/\s+/g, ' ').trim() : '');

export interface RawNseAnnouncement {
  symbol?: string;
  desc?: string;
  attchmntText?: string;
  attchmntFile?: string;
  an_dt?: string;
  exchdisstime?: string;
  sort_date?: string;
  seq_id?: string;
  sm_name?: string;
}

export function normalizeNseAnnouncement(r: RawNseAnnouncement, symbol: string, retrievedAt: string): MarketEvent | null {
  const desc = clean(r.desc);
  const text = clean(r.attchmntText);
  const headline = desc ? (text ? `${desc}: ${clip(text, 160)}` : desc) : clip(text, 200);
  if (!headline) return null;
  const publishedAt = parseNseTimestamp(r.an_dt) ?? parseNseTimestamp(r.exchdisstime);
  const sym = clean(r.symbol).toUpperCase() || symbol;
  return {
    id: `nse-ann-${r.seq_id ?? `${sym}-${publishedAt ?? headline.slice(0, 20)}`}`,
    headline,
    publisher: 'NSE corporate announcement',
    publishedAt,
    sourceZone: 'Asia/Kolkata',
    timestampReliable: publishedAt !== null,
    scheduledFor: null,
    link: safeLink(r.attchmntFile, true),
    symbols: [sym],
    sectors: [],
    type: /result|financial/i.test(desc) ? 'EARNINGS' : /dividend|bonus|split|buy.?back|rights/i.test(desc) ? 'CORPORATE_ACTION' : 'ANNOUNCEMENT',
    verification: 'CONFIRMED_EXCHANGE',
    retrievedAt,
    limitation: 'Read from the NSE website feed, which can lag the live filing; the time shown is the exchange broadcast time.',
  };
}

export interface RawNseEvent {
  symbol?: string;
  company?: string;
  purpose?: string;
  bm_desc?: string;
  date?: string;
}

export function normalizeNseEventCalendar(r: RawNseEvent, symbol: string, retrievedAt: string): MarketEvent | null {
  const purpose = clean(r.purpose);
  const iso = parseNseTimestamp(r.date);
  if (!purpose || !iso) return null;
  const day = istDateStr(new Date(iso));
  const sym = clean(r.symbol).toUpperCase() || symbol;
  return {
    id: `nse-evt-${sym}-${day}-${purpose.slice(0, 24).replace(/\W+/g, '-').toLowerCase()}`,
    headline: `${sym}: ${purpose}${r.bm_desc ? ` (${clip(clean(r.bm_desc), 140)})` : ''}`,
    publisher: 'NSE event calendar',
    publishedAt: null,
    sourceZone: 'Asia/Kolkata',
    timestampReliable: false, // a scheduled date, not a publication time
    scheduledFor: day,
    link: null,
    symbols: [sym],
    sectors: [],
    type: /result/i.test(purpose) ? 'EARNINGS' : /board/i.test(purpose) ? 'BOARD_MEETING' : /dividend|bonus|split|buy.?back|rights/i.test(purpose) ? 'CORPORATE_ACTION' : 'ANNOUNCEMENT',
    verification: 'CONFIRMED_EXCHANGE',
    retrievedAt,
    limitation: 'Scheduled date only (no time of day); dates can be revised by the company.',
  };
}

export interface RawNews {
  id: string;
  title: string;
  link: string;
  source: string;
  publishedAt: string | null;
}

export function normalizeNews(n: RawNews, symbol: string | null, retrievedAt: string): MarketEvent {
  const t = n.publishedAt ? Date.parse(n.publishedAt) : NaN;
  const ok = !Number.isNaN(t);
  return {
    id: `news-${n.id}`,
    headline: clip(clean(n.title), 220),
    publisher: n.source || 'Unknown publisher',
    publishedAt: ok ? new Date(t).toISOString() : null,
    sourceZone: ok ? 'UTC' : 'unknown',
    timestampReliable: ok,
    scheduledFor: null,
    link: safeLink(n.link),
    symbols: symbol ? [symbol] : [],
    sectors: [],
    type: 'NEWS',
    verification: 'REPORTED',
    retrievedAt,
    limitation: 'Headline from an RSS aggregator; the outlet is the origin, not the feed. Not independently verified.',
  };
}

/** Same headline for the same symbol counts once; exchange-confirmed items win over reports. */
export function dedupeEvents(events: MarketEvent[]): MarketEvent[] {
  const rank = (e: MarketEvent) => (e.verification === 'CONFIRMED_EXCHANGE' ? 2 : e.verification === 'REPORTED' ? 1 : 0);
  const map = new Map<string, MarketEvent>();
  for (const e of events) {
    const key = `${e.symbols.join(',')}|${e.headline.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().slice(0, 80)}`;
    const cur = map.get(key);
    if (!cur || rank(e) > rank(cur)) map.set(key, e);
  }
  return [...map.values()];
}

export function sortEvents(events: MarketEvent[]): MarketEvent[] {
  const key = (e: MarketEvent) => (e.publishedAt ? Date.parse(e.publishedAt) : e.scheduledFor ? Date.parse(`${e.scheduledFor}T00:00:00Z`) : 0);
  return [...events].sort((a, b) => key(b) - key(a));
}

export interface ChartMarker {
  /** bucket start (epoch s) of the candle the event falls into */
  t: number;
  id: string;
  headline: string;
  type: EventType;
  /** published outside the candle series' session hours, so it is pinned to the next bar */
  afterHours: boolean;
}

/**
 * Place events with a reliable publication time on the candle series. An event is pinned to the bar that
 * contains it, or to the next bar if it fell between sessions. Events outside the series are dropped.
 */
export function chartMarkers(events: MarketEvent[], candles: TfCandle[], barSec: number): ChartMarker[] {
  if (candles.length === 0) return [];
  const out: ChartMarker[] = [];
  const first = candles[0]!.t;
  const lastEnd = candles[candles.length - 1]!.t + barSec;
  for (const e of events) {
    if (!e.timestampReliable || !e.publishedAt) continue;
    const ts = Math.floor(Date.parse(e.publishedAt) / 1000);
    if (ts < first || ts >= lastEnd) continue;
    const inBar = candles.find((c) => ts >= c.t && ts < c.t + barSec);
    if (inBar) out.push({ t: inBar.t, id: e.id, headline: e.headline, type: e.type, afterHours: false });
    else {
      const next = candles.find((c) => c.t > ts);
      if (next) out.push({ t: next.t, id: e.id, headline: e.headline, type: e.type, afterHours: true });
    }
  }
  return out;
}

/** Reliable events published in [fromT - lookbackSec, toT]: wording is "possible context", never "cause". */
export function possibleContext(events: MarketEvent[], fromT: number, toT: number, lookbackSec = 3 * 3600): { events: MarketEvent[]; statement: string } {
  const hit = events.filter((e) => {
    if (!e.timestampReliable || !e.publishedAt) return false;
    const ts = Date.parse(e.publishedAt) / 1000;
    return ts >= fromT - lookbackSec && ts <= toT;
  });
  return {
    events: hit,
    statement: hit.length === 0
      ? 'No timestamped event found around this move. This does not mean none occurred.'
      : `${hit.length} timestamped item${hit.length > 1 ? 's' : ''} appeared around this move. They are possible context only; timing alone does not show that any of them moved the price.`,
  };
}

export interface Upcoming {
  kind: 'EARNINGS' | 'BOARD_MEETING' | 'CORPORATE_ACTION';
  daysAway: number;
  event: MarketEvent;
}

/** Scheduled events inside the next `days` days (IST calendar days). */
export function upcomingEvents(events: MarketEvent[], now: Date, days = 7): Upcoming[] {
  const today = istDateStr(now);
  const t0 = Date.parse(`${today}T00:00:00Z`);
  const out: Upcoming[] = [];
  for (const e of events) {
    if (!e.scheduledFor || (e.type !== 'EARNINGS' && e.type !== 'BOARD_MEETING' && e.type !== 'CORPORATE_ACTION')) continue;
    const d = Math.round((Date.parse(`${e.scheduledFor}T00:00:00Z`) - t0) / 86_400_000);
    if (d >= 0 && d <= days) out.push({ kind: e.type, daysAway: d, event: e });
  }
  return out.sort((a, b) => a.daysAway - b.daysAway);
}
