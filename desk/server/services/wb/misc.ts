// Market-context strip, data-health report and workbench alerts.

import { type TfCandle } from '../../../shared/candles';
import { computeLevels, cumulativeRvol, gapRead } from '../../../shared/levels';
import { breadthReading, vixRegime } from '../../../shared/market';
import { MAX_AGE_SEC, missing, type DataState, type Sourced } from '../../../shared/quality';
import type { SessionState } from '../../../shared/types';
import { dedupeAlerts, deriveAlerts, type WbAlert } from '../../../shared/wbalerts';
import type { Ctx } from '../../config';
import { mapLimit } from '../../http';
import { read, update } from '../../kv';
import { getOverview, getSectors, getSession } from '../market';
import { KEYS, listWatchlist } from '../userdata';
import { analyzeSeries } from './analysis';
import { fnoStocks, loadTf, provOf, providerStats, resolveInstrument, stateFor, type ProviderStat } from './data';
import { upcomingFor } from './events';
import { trackedView } from './contracts';

// ---- market context ------------------------------------------------------------------------------------------------------------

export interface ContextView {
  session: SessionState;
  nifty: IndexRead;
  banknifty: IndexRead;
  vix: { value: Sourced<number>; label: string | null; detail: string | null };
  breadth: { advances: number | null; declines: number | null; label: string | null; state: DataState; note: string };
  sectors: { leaders: { id: string; name: string; changePct: number; relPct: number | null }[]; laggards: { id: string; name: string; changePct: number; relPct: number | null }[]; state: DataState; note: string };
  niftyOpening: { gapPct: Sourced<number>; gapKind: string | null; openingRange: { high: number | null; low: number | null }; note: string };
  regimeNote: string;
  notes: string[];
}
interface IndexRead {
  id: string;
  price: Sourced<number>;
  changePct: Sourced<number>;
}

export async function getContext(ctx: Ctx): Promise<ContextView> {
  const session = await getSession(ctx);
  const marketOpen = session.phase === 'OPEN' || session.phase === 'SPECIAL';
  const [ov, sec] = await Promise.all([getOverview(ctx).catch(() => null), getSectors(ctx).catch(() => null)]);
  const read2 = (id: string): IndexRead => {
    const q = ov?.data.indices.find((i) => i.id === id)?.quote ?? null;
    const st = q ? stateFor(ctx, q.source, q.marketTime, true, 'quote', marketOpen) : 'UNKNOWN';
    const mk = (v: number | null): Sourced<number> => (q && v !== null ? { value: v, state: st, prov: provOf(q.source, q.marketTime, q.fetchedAt) } : missing(`${id} unavailable.`));
    return { id, price: mk(q?.price ?? null), changePct: mk(q?.changePct ?? null) };
  };
  const vq = ov?.data.vix ?? null;
  const vst: DataState = vq ? stateFor(ctx, vq.quote.source, vq.quote.marketTime, true, 'quote', marketOpen) : 'UNKNOWN';
  const b = ov?.data.breadth ?? null;
  const rows = sec?.data.sectors ?? [];

  let opening: ContextView['niftyOpening'] = { gapPct: missing('NIFTY intraday history unavailable.'), gapKind: null, openingRange: { high: null, low: null }, note: 'Not available.' };
  try {
    const inst = await resolveInstrument(ctx, 'NIFTY');
    if (inst) {
      const d = await loadTf(ctx, inst, '15m');
      if (d.availability.available) {
        const g = gapRead(d.candles);
        const lv = computeLevels(d.candles, '15m');
        opening = {
          gapPct: g.gapPct !== null ? { value: g.gapPct, state: d.stale ? 'STALE' : 'VALID', prov: provOf(d.source, null, d.fetchedAt ?? ctx.now().toISOString()) } : missing(g.note),
          gapKind: g.kind,
          openingRange: { high: lv.find((l) => l.method === 'OPENING_RANGE_HIGH')?.price ?? null, low: lv.find((l) => l.method === 'OPENING_RANGE_LOW')?.price ?? null },
          note: g.note,
        };
      } else opening.note = d.availability.reason ?? 'Not available.';
    }
  } catch {
    /* leave as unavailable */
  }

  return {
    session,
    nifty: read2('NIFTY'),
    banknifty: read2('BANKNIFTY'),
    vix: { value: vq ? { value: vq.quote.price, state: vst, prov: provOf(vq.quote.source, vq.quote.marketTime, vq.quote.fetchedAt) } : missing('India VIX unavailable.'), label: vq?.reading.label ?? (vq ? vixRegime(vq.quote.price).label : null), detail: vq?.reading.detail ?? null },
    breadth: b ? { advances: b.advances, declines: b.declines, label: breadthReading(b.advances, b.declines).label, state: marketOpen || ctx.cfg.offline ? 'VALID' : 'MARKET_CLOSED', note: 'NSE advances/declines for the index universe.' } : { advances: null, declines: null, label: null, state: 'UNKNOWN', note: 'Breadth unavailable: the NSE API is not reachable from this host.' },
    sectors: rows.length ? { leaders: rows.slice(0, 3).map((r) => ({ id: r.id, name: r.name, changePct: r.quote.changePct, relPct: r.relative })), laggards: rows.slice(-3).reverse().map((r) => ({ id: r.id, name: r.name, changePct: r.quote.changePct, relPct: r.relative })), state: 'VALID', note: 'Sector indices ranked by day change.' } : { leaders: [], laggards: [], state: 'UNKNOWN', note: 'Sector indices unavailable.' },
    niftyOpening: opening,
    regimeNote: 'No bullish or bearish regime is inferred. Agreement or disagreement with the index is shown beside each stock as evidence, not as a rule.',
    notes: [...(ov?.notes ?? []), ...(sec?.notes ?? [])],
  };
}

// ---- data health -------------------------------------------------------------------------------------------------------------------

export interface HealthView {
  now: string;
  session: SessionState;
  providers: (ProviderStat & { configured: boolean })[];
  freshness: { quoteSec: number; optionChainSec: number; intradayCandleSec: number; delayedFeedSec: number };
  entitlements: string[];
  universe: { fnoStocks: number; indices: number; source: string };
  cache: string[];
  limitations: string[];
}

export async function getHealth(ctx: Ctx): Promise<HealthView> {
  const session = await getSession(ctx);
  const fno = await fnoStocks(ctx).catch(() => ({}));
  const upstox = Boolean(ctx.cfg.upstoxToken);
  const providers = providerStats().map((p) => ({ ...p, configured: p.id.startsWith('upstox') ? upstox : true }));
  return {
    now: ctx.now().toISOString(),
    session,
    providers,
    freshness: { quoteSec: MAX_AGE_SEC.quote, optionChainSec: MAX_AGE_SEC.optionChain, intradayCandleSec: MAX_AGE_SEC.intradayCandle, delayedFeedSec: MAX_AGE_SEC.delayedFeed },
    entitlements: [
      'Yahoo Finance chart API: unofficial and unauthenticated. Intraday bars are delayed by minutes; there is no entitlement or SLA, and commercial/redistribution use is not licensed. Always labelled DELAYED.',
      'NSE website JSON (option chain, announcements, event calendar): public pages read for personal research. NSE blocks some cloud hosts and can change formats; nothing is cached longer than its stated limit and failures are shown, not hidden.',
      upstox ? 'Upstox Analytics token: configured (official, read-only). Real-time entitlement of the token is not verified by the app.' : 'Upstox Analytics token: not configured. Set UPSTOX_ANALYTICS_TOKEN for official option chains, greeks and option-contract candles.',
      'No broker order API is configured or callable from this app.',
    ],
    universe: { fnoStocks: Object.keys(fno).length, indices: 4, source: ctx.cfg.offline ? 'demo' : 'NSE lot-size file (fo_mktlots)' },
    cache: ['Quotes: 20 s while the market is open, 10 min when closed.', 'Intraday candles: 45 s open, 3 h closed.', 'Option chains: 20 s open, 15 min closed.', 'Announcements: 10 min open, 1 h closed. Event calendar: 3 h.', 'Scanner results: 2 min open, 15 min closed.'],
    limitations: [
      'Provider statistics are held in memory and reset when the server restarts.',
      'Option-contract candles need an Upstox token; without one the option chart shows only snapshots this app observed itself, labelled as such.',
      'Greeks are provider-supplied when available, otherwise calculated with Black-Scholes (r = 6.5%, no dividends) and labelled.',
    ],
  };
}

// ---- workbench alerts ---------------------------------------------------------------------------------------------------------------

export interface WbAlertState {
  alerts: WbAlert[];
}

export async function listWbAlerts(ctx: Ctx): Promise<WbAlert[]> {
  return read<WbAlert[]>(ctx.kv, KEYS.wbAlerts, []);
}

export async function evaluateWbAlerts(ctx: Ctx): Promise<{ created: WbAlert[]; evaluated: { underlyings: number; contracts: number }; skipped: string[] }> {
  const now = ctx.now();
  const session = await getSession(ctx);
  const marketOpen = session.phase === 'OPEN' || session.phase === 'SPECIAL';
  const skipped: string[] = [];
  const tracked = await trackedView(ctx);
  const fno = await fnoStocks(ctx);
  const watch = (await listWatchlist(ctx)).map((w) => w.symbol).filter((s) => s in fno);
  const syms = [...new Set([...tracked.rows.map((r) => r.item.ref.underlying), ...watch])].slice(0, 10);
  const candidates: WbAlert[] = [];
  const prevKey = (id: string) => `wb/prev/${id}`;
  let contractsN = 0;

  await mapLimit(syms, 2, async (sym) => {
    const inst = await resolveInstrument(ctx, sym);
    if (!inst) { skipped.push(`${sym}: unknown underlying`); return; }
    const d = await loadTf(ctx, inst, '15m');
    if (!d.availability.available) { skipped.push(`${sym}: ${d.availability.reason ?? 'no 15-minute data'}`); return; }
    const last = d.candles[d.candles.length - 1];
    const asOfIso = last ? new Date((last.t + 900) * 1000).toISOString() : null;
    const freshness: DataState = d.stale ? 'STALE' : marketOpen ? stateFor(ctx, d.source, asOfIso, true, 'intraday', true) : 'MARKET_CLOSED';
    const a = analyzeSeries(d, freshness, null, null);
    const rv = cumulativeRvol(d.candles as TfCandle[]);
    const upcoming = inst.kind === 'STOCK' ? await upcomingFor(ctx, sym) : [];
    candidates.push(...deriveAlerts({ symbol: sym, freshness, asOf: asOfIso, now, setup: a.structure.primary, rvol: rv, contract: null, previous: null, upcoming }));
    for (const row of tracked.rows.filter((r) => r.item.ref.underlying === sym && r.validation)) {
      contractsN += 1;
      const v = row.validation!;
      const prev = await read<{ spreadPct: number | null; liquidity: string | null } | null>(ctx.kv, prevKey(v.id), null);
      candidates.push(...deriveAlerts({ symbol: sym, freshness, asOf: v.ltp.prov?.asOf ?? null, now, setup: null, rvol: null, contract: v, previous: prev, upcoming: [] }).filter((x) => x.contractId !== null));
      await update(ctx.kv, prevKey(v.id), null as unknown, () => ({ spreadPct: v.spreadPct.value, liquidity: v.liquidity.cls })).catch(() => undefined);
    }
  });

  const seen = await read<Record<string, string>>(ctx.kv, KEYS.wbSeen, {});
  const fresh = dedupeAlerts(candidates, seen, now);
  if (fresh.length > 0) {
    const cutoff = now.getTime() - 2 * 86_400_000;
    await update<Record<string, string>>(ctx.kv, KEYS.wbSeen, {}, (cur) => {
      const next: Record<string, string> = {};
      for (const [k, v] of Object.entries(cur)) if (Date.parse(v) >= cutoff) next[k] = v;
      for (const a of fresh) next[a.key] = a.createdAt;
      return next;
    });
    await update<WbAlert[]>(ctx.kv, KEYS.wbAlerts, [], (cur) => [...fresh, ...cur].slice(0, 100));
  }
  return { created: fresh, evaluated: { underlyings: syms.length, contracts: contractsN }, skipped };
}
