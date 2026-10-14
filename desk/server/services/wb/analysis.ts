// Underlying analysis: quote validation, multi-timeframe structure, levels, breakout state.

import { evaluateStructure, type StructureResult } from '../../../shared/breakout';
import { TIMEFRAMES, type TfAvailability, type TfCandle, type Timeframe } from '../../../shared/candles';
import { istDateStr } from '../../../shared/india';
import { atr, lastOf } from '../../../shared/indicators';
import { compression, computeLevels, cumulativeRvol, gapRead, mtfAgreement, priceVolumeExpansion, trendRead, vwapRead, type Level, type MtfAgreement, type RelVol, type Trend, type TrendRead, type VwapRead } from '../../../shared/levels';
import { missing, type DataState, type Sourced } from '../../../shared/quality';
import type { SessionState } from '../../../shared/types';
import type { Ctx } from '../../config';
import { getSession, getSectors } from '../market';
import { isLive, loadIntraday, loadQuote, loadTf, provOf, stateFor, type Instrument, type TfData } from './data';
import type { TfAnalysis } from '../../../shared/scanner';

/** NSE industry label (from the Nifty 500 list) -> the sector index the Desk already quotes. */
const SECTOR_INDEX: Record<string, string> = {
  'Information Technology': 'IT',
  'Financial Services': 'FINSERV',
  'Automobile and Auto Components': 'AUTO',
  'Fast Moving Consumer Goods': 'FMCG',
  Healthcare: 'PHARMA',
  'Metals & Mining': 'METAL',
  'Oil Gas & Consumable Fuels': 'ENERGY',
  Realty: 'REALTY',
  'Media Entertainment & Publication': 'MEDIA',
  Construction: 'INFRA',
};

export interface SeriesAnalysis {
  tf: Timeframe;
  levels: Level[];
  structure: StructureResult;
  trend: TrendRead;
}

export function analyzeSeries(d: TfData, freshness: DataState, livePrice: number | null, htf: { tf: Timeframe; trend: Trend } | null): SeriesAnalysis {
  const levels = d.availability.available ? computeLevels(d.candles, d.tf) : [];
  const structure = d.availability.available ? evaluateStructure(d.candles, levels, d.tf, { freshness, livePrice, htfTrend: htf }) : { tf: d.tf, setups: [], primary: null };
  const trend = trendRead(d.candles);
  return { tf: d.tf, levels, structure, trend };
}

export const toTfAnalysis = (a: SeriesAnalysis, d: TfData): TfAnalysis => ({ structure: a.structure, trend: a.trend, barsCompleted: d.candles.filter((c) => c.complete).length });

export interface UnderlyingView {
  instrument: Instrument;
  session: SessionState;
  asOf: string;
  freshness: DataState;
  price: Sourced<number>;
  prevClose: Sourced<number>;
  change: Sourced<number>;
  changePct: Sourced<number>;
  dayHigh: Sourced<number>;
  dayLow: Sourced<number>;
  /** session traded volume in shares; indices report none */
  volume: Sourced<number>;
  rvol: RelVol;
  vwap: VwapRead;
  atr: Sourced<number>;
  tf: Timeframe;
  candles: TfCandle[];
  availability: TfAvailability;
  levels: Level[];
  structure: StructureResult;
  trend: TrendRead;
  htfTrend: { tf: Timeframe; trend: Trend } | null;
  compression: ReturnType<typeof compression>;
  gap: ReturnType<typeof gapRead>;
  expansion: ReturnType<typeof priceVolumeExpansion>;
  prevSession: { date: string; high: number; low: number; close: number } | null;
  sector: { name: string; indexId: string | null; changePct: Sourced<number>; relPct: number | null };
  benchmark: { id: 'NIFTY'; changePct: Sourced<number>; relPct: number | null };
  notes: string[];
}

const WINDOW: Record<Timeframe, number> = { '1m': 400, '3m': 300, '5m': 350, '15m': 400, '30m': 400, '1h': 300, '1d': 260 };

export async function getUnderlyingView(ctx: Ctx, inst: Instrument, tf: Timeframe): Promise<UnderlyingView> {
  const now = ctx.now();
  const notes: string[] = [];
  const session = await getSession(ctx);
  const marketOpen = session.phase === 'OPEN' || session.phase === 'SPECIAL';

  const [q, daily, sel, set15, sectors] = await Promise.all([
    loadQuote(ctx, inst),
    loadTf(ctx, inst, '1d'),
    tf === '1d' ? Promise.resolve(null) : loadTf(ctx, inst, tf),
    loadIntraday(ctx, inst, '15m').catch(() => null),
    getSectors(ctx).catch(() => null),
  ]);
  const main = tf === '1d' ? daily : sel!;
  const quote = q.quote;
  if (q.error) notes.push(`Quote unavailable: ${q.error}`);
  if (q.stale) notes.push('The quote is the last good copy; the refresh failed.');

  const qSource = quote?.source ?? 'none';
  const asOf = quote?.marketTime ?? null;
  const fetched = quote?.fetchedAt ?? now.toISOString();
  const qState: DataState = quote ? stateFor(ctx, qSource, asOf, true, 'quote', marketOpen) : 'UNKNOWN';
  const fld = (v: number | null | undefined, why: string): Sourced<number> => (quote && v != null && Number.isFinite(v) ? { value: v, state: qState, prov: provOf(qSource, asOf, fetched) } : missing(why));
  const price = fld(quote?.price, 'No quote from any provider.');

  // data freshness for structure: the quote's, capped by the candle series actually used
  let freshness: DataState = qState;
  if (main.stale) freshness = freshness === 'VALID' ? 'STALE' : freshness;
  if (!main.availability.available && freshness === 'VALID') freshness = 'DATA_INSUFFICIENT';

  const htfTrend = tf === '1d' ? null : { tf: '1d' as Timeframe, trend: trendRead(daily.candles).trend };
  const live = marketOpen && qState === 'VALID' && tf !== '1d' ? price.value : null;
  const a = analyzeSeries(main, freshness, live, htfTrend);

  // intraday-only reads come from the 15-minute series, which always carries enough sessions
  const intra15 = set15 ? (tf === '15m' ? main : await loadTf(ctx, inst, '15m')) : null;
  const rvol: RelVol = intra15 && intra15.availability.available ? cumulativeRvol(intra15.candles) : { value: null, baseline: null, samples: 0, method: 'NONE', note: 'Intraday history unavailable, so relative volume is not computed.' };
  const vwap: VwapRead = intra15 && intra15.availability.available && price.value != null ? vwapRead(intra15.candles, price.value) : { vwap: null, distPct: null, position: 'UNKNOWN', event: null, note: 'Intraday history unavailable.' };
  if (inst.kind === 'INDEX') notes.push('Indices carry no traded volume: VWAP and relative volume are unavailable and volume confirmation cannot be applied.');

  const dailyDone = daily.candles.filter((c) => c.complete);
  const lastDone = dailyDone[dailyDone.length - 1];
  const prevSession = lastDone ? { date: istDateStr(new Date(lastDone.t * 1000)), high: lastDone.h, low: lastDone.l, close: lastDone.c } : null;

  const atrV = main.availability.available ? lastOf(atr(main.candles.filter((c) => c.complete), 14)) : null;
  const atrField: Sourced<number> = atrV !== null ? { value: atrV, state: 'VALID', prov: provOf(main.source, null, main.fetchedAt ?? fetched, `ATR(14) on completed ${tf} bars`) } : missing('Not enough completed bars for ATR(14).', 'DATA_INSUFFICIENT');

  // sector and benchmark
  const secName = inst.sector;
  const secId = secName ? SECTOR_INDEX[secName] ?? null : null;
  const secRow = secId && sectors ? sectors.data.sectors.find((s) => s.id === secId) : null;
  const niftyPct = sectors?.data.niftyChangePct ?? null;
  const chg = quote?.changePct ?? null;
  const sectorField: Sourced<number> = secRow ? { value: secRow.quote.changePct, state: stateFor(ctx, secRow.quote.source, secRow.quote.marketTime, true, 'quote', marketOpen), prov: provOf(secRow.quote.source, secRow.quote.marketTime, secRow.quote.fetchedAt) } : missing(inst.kind === 'INDEX' ? 'Not applicable to an index.' : secName ? `No sector index is mapped for "${secName}".` : 'Sector unknown for this symbol.');
  const benchField: Sourced<number> = niftyPct !== null ? { value: niftyPct, state: qState, prov: provOf(sectors!.source, null, now.toISOString()) } : missing('Nifty 50 change unavailable.');

  const sel2 = main; // the timeframe the user asked for
  return {
    instrument: inst,
    session,
    asOf: now.toISOString(),
    freshness,
    price,
    prevClose: fld(quote?.prevClose, 'Previous close unavailable.'),
    change: fld(quote?.change, 'Change unavailable.'),
    changePct: fld(quote?.changePct, 'Change unavailable.'),
    dayHigh: fld(quote?.high, 'Session high unavailable.'),
    dayLow: fld(quote?.low, 'Session low unavailable.'),
    volume: inst.kind === 'INDEX' ? missing('Indices have no traded volume.', 'UNSUPPORTED') : fld(quote?.volume, 'Session volume unavailable.'),
    rvol,
    vwap,
    atr: atrField,
    tf,
    candles: sel2.candles.slice(-WINDOW[tf]),
    availability: sel2.availability,
    levels: a.levels,
    structure: a.structure,
    trend: a.trend,
    htfTrend,
    compression: compression(sel2.candles),
    gap: gapRead(sel2.candles),
    expansion: priceVolumeExpansion(sel2.candles),
    prevSession,
    sector: { name: secName ?? 'Unknown', indexId: secId, changePct: sectorField, relPct: sectorField.value !== null && chg !== null ? chg - sectorField.value : null },
    benchmark: { id: 'NIFTY', changePct: benchField, relPct: benchField.value !== null && chg !== null && inst.id !== 'NIFTY' ? chg - benchField.value : null },
    notes,
  };
}

export interface MtfRow {
  tf: Timeframe;
  availability: TfAvailability;
  trend: TrendRead;
  state: string | null;
  level: number | null;
  direction: 'UP' | 'DOWN' | null;
}

/** Availability, trend and best breakout state for every timeframe. Fetches each distinct source once. */
export async function getMtf(ctx: Ctx, inst: Instrument): Promise<{ rows: MtfRow[]; agreement: MtfAgreement; freshness: DataState }> {
  const session = await getSession(ctx);
  const marketOpen = session.phase === 'OPEN' || session.phase === 'SPECIAL';
  const q = await loadQuote(ctx, inst);
  const qState: DataState = q.quote ? stateFor(ctx, q.quote.source, q.quote.marketTime, true, 'quote', marketOpen) : 'UNKNOWN';
  const sets = await Promise.all(TIMEFRAMES.map((t) => loadTf(ctx, inst, t)));
  const daily = sets.find((s) => s.tf === '1d')!;
  const dailyTrend = trendRead(daily.candles).trend;
  const rows: MtfRow[] = sets.map((d) => {
    const a = analyzeSeries(d, d.stale && qState === 'VALID' ? 'STALE' : qState, null, d.tf === '1d' ? null : { tf: '1d', trend: dailyTrend });
    const p = a.structure.primary;
    return { tf: d.tf, availability: d.availability, trend: a.trend, state: p?.state ?? null, level: p?.level.price ?? null, direction: p?.direction ?? null };
  });
  const trends: Partial<Record<Timeframe, Trend>> = {};
  for (const r of rows) trends[r.tf] = r.availability.available ? r.trend.trend : 'UNKNOWN';
  return { rows, agreement: mtfAgreement(trends), freshness: qState };
}

export { isLive };
