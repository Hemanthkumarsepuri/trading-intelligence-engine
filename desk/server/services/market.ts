import { breadthReading, flowLabel, fiiFuturesRead, vixRegime, type Reading } from '../../shared/market';
import { istDateStr, sessionState } from '../../shared/india';
import { GLOBAL_CUES, MAIN_INDICES, SECTOR_INDICES, VIX_DEF, type GlobalDef, type IndexDef } from '../../shared/universe';
import type { Quote, SessionState } from '../../shared/types';
import { cached } from '../cache';
import type { Ctx } from '../config';
import { fixtureFiiDii, fixtureIpos, fixtureNews, fixtureParticipantOi } from '../fixtures';
import { fetchBanList, fetchBreadth, fetchFiiDii, fetchParticipantOi, fiiPositioning, type Breadth, type FiiDiiDay } from '../providers/nse';
import { fetchNews, MARKET_FEEDS, symbolFeed, type NewsItem } from '../providers/news';
import { upstoxIpos, type IpoRow } from '../providers/upstox';
import { getQuotes, ttlFor, type Target } from './quotes';
import { getLiveHolidays } from './symbols';

export interface Svc<T> {
  data: T;
  source: string;
  stale: boolean;
  notes: string[];
}

const idx = (d: IndexDef): Target => ({ id: d.id, yahoo: d.yahoo, upstoxKey: d.upstox });
const glob = (d: GlobalDef): Target => ({ id: d.id, yahoo: d.yahoo });

export async function getSession(ctx: Ctx): Promise<SessionState> {
  return sessionState(ctx.now(), await getLiveHolidays(ctx));
}

export interface Overview {
  session: SessionState;
  indices: { id: string; name: string; quote: Quote | null }[];
  vix: { quote: Quote; reading: Reading } | null;
  globals: { id: string; name: string; group: string; why: string; quote: Quote | null }[];
  breadth: (Breadth & { label: string }) | null;
}

export async function getOverview(ctx: Ctx): Promise<Svc<Overview>> {
  const notes: string[] = [];
  const [ix, gl] = await Promise.all([
    getQuotes(ctx, 'indices', [...MAIN_INDICES.map(idx), idx(VIX_DEF)]).catch((e: Error) => { notes.push(`Indices unavailable: ${e.message}`); return null; }),
    getQuotes(ctx, 'globals', GLOBAL_CUES.map(glob), 60, 900).catch((e: Error) => { notes.push(`Global cues unavailable: ${e.message}`); return null; }),
  ]);
  if (ix) notes.push(...ix.notes);
  if (gl) notes.push(...gl.notes.filter((n) => !notes.includes(n)));

  let breadth: Overview['breadth'] = null;
  if (!ctx.cfg.offline) {
    try {
      const ttl = await ttlFor(ctx, 120, 3600);
      const b = await cached(ctx.kv, 'breadth', ttl, () => fetchBreadth({ fetchImpl: ctx.fetchImpl }), { now: () => ctx.now().getTime(), maxStaleSec: 86400 });
      breadth = { ...b.value, label: breadthReading(b.value.advances, b.value.declines).label };
    } catch {
      notes.push('Market breadth (advances/declines) unavailable: NSE API not reachable from this host.');
    }
  } else {
    breadth = { advances: 31, declines: 17, unchanged: 2, label: breadthReading(31, 17).label };
  }

  const vixQ = ix?.quotes[VIX_DEF.id] ?? null;
  const data: Overview = {
    session: await getSession(ctx),
    indices: MAIN_INDICES.map((d) => ({ id: d.id, name: d.name, quote: ix?.quotes[d.id] ?? null })),
    vix: vixQ ? { quote: vixQ, reading: vixRegime(vixQ.price) } : null,
    globals: GLOBAL_CUES.map((d) => ({ id: d.id, name: d.name, group: d.group, why: d.why, quote: gl?.quotes[d.id] ?? null })),
    breadth,
  };
  const stale = Boolean(ix?.stale || gl?.stale);
  if (ctx.cfg.offline) notes.unshift('DEMO DATA: offline mode, values are synthetic.');
  return { data, source: [ix?.source, gl?.source].filter(Boolean).join(' | ') || 'none', stale, notes: [...new Set(notes)] };
}

export interface SectorRow {
  id: string;
  name: string;
  quote: Quote;
  /** percentage points vs Nifty 50 */
  relative: number | null;
}

export async function getSectors(ctx: Ctx): Promise<Svc<{ sectors: SectorRow[]; niftyChangePct: number | null }>> {
  const r = await getQuotes(ctx, 'sectors', [idx(MAIN_INDICES[0]!), ...SECTOR_INDICES.map(idx)], 30, 900);
  const nifty = r.quotes[MAIN_INDICES[0]!.id]?.changePct ?? null;
  const notes = [...r.notes];
  const sectors: SectorRow[] = [];
  for (const d of SECTOR_INDICES) {
    const q = r.quotes[d.id];
    if (!q) { notes.push(`${d.name}: no data from provider`); continue; }
    sectors.push({ id: d.id, name: d.name, quote: q, relative: nifty !== null ? q.changePct - nifty : null });
  }
  sectors.sort((a, b) => b.quote.changePct - a.quote.changePct);
  return { data: { sectors, niftyChangePct: nifty }, source: r.source, stale: r.stale, notes };
}

export interface Flows {
  cash: (FiiDiiDay & { fiiLabel: string; diiLabel: string }) | null;
  positioning: (ReturnType<typeof fiiPositioning> & { read: Reading }) | null;
}

export async function getFlows(ctx: Ctx): Promise<Svc<Flows>> {
  const notes: string[] = [];
  const holidays = await getLiveHolidays(ctx);
  let cash: Flows['cash'] = null;
  let positioning: Flows['positioning'] = null;

  try {
    const day = ctx.cfg.offline
      ? fixtureFiiDii(ctx.now())
      : (await cached(ctx.kv, 'fiidii', await ttlFor(ctx, 1800, 6 * 3600), () => fetchFiiDii({ fetchImpl: ctx.fetchImpl }), { now: () => ctx.now().getTime(), maxStaleSec: 4 * 86400 })).value;
    cash = { ...day, fiiLabel: flowLabel(day.fii?.net ?? null), diiLabel: flowLabel(day.dii?.net ?? null) };
  } catch (e) {
    notes.push(`FII/DII cash flows unavailable: ${e instanceof Error ? e.message : 'error'}. NSE often blocks cloud hosts.`);
  }

  try {
    const oi = ctx.cfg.offline
      ? fixtureParticipantOi(ctx.now())
      : (await cached(ctx.kv, 'participant-oi', 3 * 3600, () => fetchParticipantOi(ctx.now(), holidays, { fetchImpl: ctx.fetchImpl }), { now: () => ctx.now().getTime(), maxStaleSec: 5 * 86400 })).value;
    const p = fiiPositioning(oi);
    positioning = { ...p, read: fiiFuturesRead(p.indexFutLongPct) };
  } catch (e) {
    notes.push(`FII derivatives positioning unavailable: ${e instanceof Error ? e.message : 'error'}.`);
  }

  if (ctx.cfg.offline) notes.unshift('DEMO DATA: offline mode, values are synthetic.');
  return { data: { cash, positioning }, source: ctx.cfg.offline ? 'fixture' : 'nse', stale: false, notes };
}

export async function getBan(ctx: Ctx): Promise<Svc<{ symbols: string[]; asOf: string | null }>> {
  if (ctx.cfg.offline) return { data: { symbols: ['DEMOBAN'], asOf: istDateStr(ctx.now()) }, source: 'fixture', stale: false, notes: ['DEMO DATA'] };
  const holidays = await getLiveHolidays(ctx);
  const c = await cached(ctx.kv, 'fno-ban', await ttlFor(ctx, 3600, 6 * 3600), () => fetchBanList(ctx.now(), holidays, { fetchImpl: ctx.fetchImpl }), { now: () => ctx.now().getTime(), maxStaleSec: 3 * 86400 });
  const notes = c.stale ? [`Showing last good list (refresh failed: ${c.error}).`] : [];
  const today = istDateStr(ctx.now());
  if (c.value.asOf === null) notes.push('The ban file does not state its trade date; treat it as unverified.');
  else if (c.value.asOf !== today) notes.push(`This list is for ${c.value.asOf}, not today (${today}).`);
  return { data: c.value, source: 'nse', stale: c.stale, notes };
}

export async function getNews(ctx: Ctx, symbol?: string, name?: string): Promise<Svc<{ items: NewsItem[] }>> {
  if (ctx.cfg.offline) return { data: { items: fixtureNews(ctx.now()) }, source: 'fixture', stale: false, notes: ['DEMO DATA'] };
  const feeds = symbol ? [symbolFeed(symbol, name)] : MARKET_FEEDS;
  const key = symbol ? `news/${symbol}` : 'news/market';
  const c = await cached(ctx.kv, key, symbol ? 900 : 300, () => fetchNews(feeds, ctx.now(), 40, { fetchImpl: ctx.fetchImpl }), { now: () => ctx.now().getTime(), maxStaleSec: 86400 });
  const notes: string[] = c.value.failed.map((f) => `${f.feed}: ${f.reason}`);
  if (c.stale) notes.push(`Showing cached headlines (refresh failed: ${c.error}).`);
  return { data: { items: c.value.items }, source: 'rss', stale: c.stale, notes };
}

export async function getIpos(ctx: Ctx): Promise<Svc<{ ipos: IpoRow[] }>> {
  if (ctx.cfg.offline) return { data: { ipos: fixtureIpos() }, source: 'fixture', stale: false, notes: ['DEMO DATA'] };
  if (!ctx.cfg.upstoxToken) return { data: { ipos: [] }, source: 'none', stale: false, notes: ['IPO calendar needs UPSTOX_ANALYTICS_TOKEN (free, read-only).'] };
  const token = ctx.cfg.upstoxToken;
  const c = await cached(
    ctx.kv,
    'ipos',
    3600,
    async () => {
      const [open, upcoming] = await Promise.all([upstoxIpos(token, 'open', { fetchImpl: ctx.fetchImpl }), upstoxIpos(token, 'upcoming', { fetchImpl: ctx.fetchImpl })]);
      return [...open, ...upcoming];
    },
    { now: () => ctx.now().getTime(), maxStaleSec: 3 * 86400 },
  );
  return { data: { ipos: c.value }, source: 'upstox', stale: c.stale, notes: c.stale ? [`Showing cached IPO list (refresh failed: ${c.error}).`] : [] };
}
