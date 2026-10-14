// Market scanner over the F&O universe. Two passes keep provider load bounded:
//   1. every symbol in the (capped) universe: daily + 15-minute data, structure, trend, relative volume;
//   2. only symbols that pass pass 1: scheduled events and the option chain.
// Nothing is invented to reach a target count; exclusions are reported with their reasons.

import type { Timeframe } from '../../../shared/candles';
import { validateContract, type ContractValidation, type OptType } from '../../../shared/contract';
import { istDateStr } from '../../../shared/india';
import { cumulativeRvol, type RelVol } from '../../../shared/levels';
import { DEFAULT_FILTERS, RANKING_RULE, evaluateCandidate, rankCandidates, selectStrikes, summarize, type Candidate, type Exclusion, type ScanFilters, type ScanInput, type ScanSummary, type TfAnalysis } from '../../../shared/scanner';
import { FALLBACK_SYMBOLS, OPTION_UNDERLYINGS } from '../../../shared/universe';
import type { DataState } from '../../../shared/quality';
import { cached } from '../../cache';
import type { Ctx } from '../../config';
import { mapLimit } from '../../http';
import { getBan, getSectors, getSession } from '../market';
import { getQuotes } from '../quotes';
import { analyzeSeries, toTfAnalysis } from './analysis';
import { defaultExpiry, fnoStocks, latencyOf, loadChain, loadExpiries, loadQuote, loadTf, resolveInstrument, stateFor, type Instrument, type TfData } from './data';
import { upcomingFor } from './events';

export interface ScanResponse {
  summary: ScanSummary;
  candidates: Candidate[];
  excluded: Exclusion[];
  filters: ScanFilters;
  ranking: string;
  asOf: string;
  session: { phase: string; label: string };
  limits: { universe: number; evaluated: number; cap: number; deadlineHit: boolean };
  notes: string[];
}

export const SCAN_CAP = 60;
const DEADLINE_MS = 50_000;
const SCAN_TFS: Timeframe[] = ['1d', '1h', '30m', '15m'];

function hashKey(o: unknown): string {
  const s = JSON.stringify(o);
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

async function universe(ctx: Ctx, filters: ScanFilters, cap: number): Promise<{ list: Instrument[]; total: number }> {
  const fno = await fnoStocks(ctx);
  const all = Object.keys(fno);
  const priority = FALLBACK_SYMBOLS.map((s) => s.symbol).filter((s) => s in fno);
  const rest = all.filter((s) => !priority.includes(s)).sort();
  const ordered = [...priority, ...rest];
  const ids: string[] = [];
  if (filters.segment !== 'STOCK') ids.push(...OPTION_UNDERLYINGS.map((u) => u.id));
  if (filters.segment !== 'INDEX') ids.push(...ordered);
  const list: Instrument[] = [];
  for (const id of ids.slice(0, cap)) {
    const inst = await resolveInstrument(ctx, id);
    if (inst) list.push(inst);
  }
  return { list, total: ids.length };
}

interface Pass1 {
  inst: Instrument;
  input: ScanInput;
}

async function pass1(ctx: Ctx, inst: Instrument, marketOpen: boolean, quote: Awaited<ReturnType<typeof loadQuote>>, sectorPct: Map<string, number | null>, benchPct: number | null, ban: Set<string>): Promise<Pass1> {
  const sets: Partial<Record<Timeframe, TfData>> = {};
  for (const tf of SCAN_TFS) sets[tf] = await loadTf(ctx, inst, tf);
  const q = quote.quote;
  const qState: DataState = q ? stateFor(ctx, q.source, q.marketTime, true, 'quote', marketOpen) : 'UNKNOWN';
  const anyStale = Object.values(sets).some((d) => d?.stale);
  const freshness: DataState = qState === 'VALID' && anyStale ? 'STALE' : qState;
  const dailyTrend = sets['1d']!.candles.length ? analyzeSeries(sets['1d']!, freshness, null, null).trend.trend : 'UNKNOWN';
  const tfs: Partial<Record<Timeframe, TfAnalysis>> = {};
  for (const tf of SCAN_TFS) {
    const d = sets[tf]!;
    if (!d.availability.available) continue;
    const a = analyzeSeries(d, freshness, null, tf === '1d' ? null : { tf: '1d', trend: dailyTrend });
    tfs[tf] = toTfAnalysis(a, d);
  }
  const d15 = sets['15m']!;
  const rvol: RelVol | null = d15.availability.available ? cumulativeRvol(d15.candles) : null;
  return {
    inst,
    input: {
      symbol: inst.id, name: inst.name, sector: inst.sector, isIndex: inst.kind === 'INDEX', lotSize: inst.lotSize, price: q?.price ?? null, changePct: q?.changePct ?? null,
      priceAsOf: q?.marketTime ?? null, freshness, tfs, rvol, sectorChangePct: sectorPct.get(inst.id) ?? null, benchChangePct: benchPct, upcoming: [], inFnoBan: ban.has(inst.id), contracts: [], contractNote: null,
    },
  };
}

async function contractsFor(ctx: Ctx, inst: Instrument, direction: 'UP' | 'DOWN', price: number | null, filters: ScanFilters, marketOpen: boolean): Promise<{ contracts: ContractValidation[] | null; note: string | null }> {
  try {
    const ex = await loadExpiries(ctx, inst);
    const today = istDateStr(ctx.now());
    const exp = filters.expiry && ex.expiries.includes(filters.expiry) ? filters.expiry : defaultExpiry(ex.expiries, today).expiry;
    if (!exp) return { contracts: null, note: 'No upcoming expiry is listed.' };
    const set = await loadChain(ctx, inst, exp, price);
    const spot = set.chain.spot ?? price;
    if (spot === null) return { contracts: null, note: 'No underlying price to choose strikes from.' };
    const pick = selectStrikes(set.chain.rows.map((r) => r.strike), spot, direction);
    if (!pick) return { contracts: null, note: 'The chain listed no strikes.' };
    const types: OptType[] = filters.optionType !== 'ANY' ? [filters.optionType] : [direction === 'UP' ? 'CE' : 'PE'];
    const out: ContractValidation[] = [];
    for (const type of types) {
      for (const strike of [pick.atm, pick.otm].filter((x): x is number => x !== null)) {
        const row = set.chain.rows.find((r) => r.strike === strike);
        if (!row) continue;
        out.push(validateContract({ ref: { underlying: inst.id, expiry: exp, type, strike }, leg: type === 'CE' ? row.ce : row.pe, lotSize: ex.lotSize ?? inst.lotSize, spot, asOf: set.chain.asOf ?? null, fetchedAt: set.chain.fetchedAt, source: set.chain.source, latency: latencyOf(set.chain.source), marketOpen, now: ctx.now() }));
      }
    }
    return { contracts: out, note: `${pick.criteria} Expiry ${exp}.` };
  } catch (e) {
    return { contracts: null, note: `Option chain unavailable: ${e instanceof Error ? e.message.slice(0, 160) : 'error'}` };
  }
}

export async function runScan(ctx: Ctx, filtersIn: Partial<ScanFilters> = {}, capIn = 40): Promise<ScanResponse> {
  const filters: ScanFilters = { ...DEFAULT_FILTERS, ...filtersIn, allowStale: false };
  const cap = Math.max(5, Math.min(SCAN_CAP, Math.floor(capIn)));
  const session = await getSession(ctx);
  const key = `wb/scan/${hashKey({ filters, cap })}`;
  const c = await cached(ctx.kv, key, session.phase === 'OPEN' ? 120 : 900, () => scanOnce(ctx, filters, cap), { now: () => ctx.now().getTime(), maxStaleSec: 3600 });
  return c.stale ? { ...c.value, notes: [...c.value.notes, `Showing the previous scan (${c.savedAt}); the refresh failed: ${c.error}`] } : c.value;
}

async function scanOnce(ctx: Ctx, filters: ScanFilters, cap: number): Promise<ScanResponse> {
  const t0 = Date.now();
  const session = await getSession(ctx);
  const marketOpen = session.phase === 'OPEN' || session.phase === 'SPECIAL';
  const notes: string[] = [];
  const { list, total } = await universe(ctx, filters, cap);
  if (total > list.length) notes.push(`Scanned ${list.length} of ${total} symbols (cap ${cap}) to stay inside provider rate limits; large, liquid names are scanned first.`);

  const [sectors, ban, quotes] = await Promise.all([
    getSectors(ctx).catch(() => null),
    getBan(ctx).then((b) => new Set(b.data.symbols)).catch(() => new Set<string>()),
    ctx.cfg.offline ? Promise.resolve(null) : getQuotes(ctx, 'wb-scan', list.map((i) => (i.kind === 'INDEX' ? { id: i.id, yahoo: i.yahoo, upstoxKey: i.upstoxKey ?? undefined } : { id: i.id, yahoo: i.yahoo, equity: i.id })), 20, 600).catch((e: Error) => { notes.push(`Batch quotes failed: ${e.message}`); return null; }),
  ]);
  const secPct = new Map<string, number | null>();
  const benchPct = sectors?.data.niftyChangePct ?? null;
  const SECTOR_ID: Record<string, string> = { 'Information Technology': 'IT', 'Financial Services': 'FINSERV', 'Automobile and Auto Components': 'AUTO', 'Fast Moving Consumer Goods': 'FMCG', Healthcare: 'PHARMA', 'Metals & Mining': 'METAL', 'Oil Gas & Consumable Fuels': 'ENERGY', Realty: 'REALTY', 'Media Entertainment & Publication': 'MEDIA', Construction: 'INFRA' };
  for (const i of list) {
    const sid = i.sector ? SECTOR_ID[i.sector] : undefined;
    secPct.set(i.id, sid ? sectors?.data.sectors.find((s) => s.id === sid)?.quote.changePct ?? null : null);
  }

  let deadlineHit = false;
  const done: Pass1[] = [];
  await mapLimit(list, 5, async (inst) => {
    if (Date.now() - t0 > DEADLINE_MS) { deadlineHit = true; return; }
    const preQ = quotes?.quotes[inst.id];
    const quote = preQ ? { quote: preQ, stale: quotes!.stale } : await loadQuote(ctx, inst);
    done.push(await pass1(ctx, inst, marketOpen, quote, secPct, benchPct, ban));
  });
  if (deadlineHit) notes.push(`The scan time budget (${DEADLINE_MS / 1000}s) ran out; the remaining symbols were not evaluated. Run it again to continue from the cache.`);

  const excluded: Exclusion[] = [];
  const pre: { p: Pass1; cand: Candidate }[] = [];
  for (const p of done) {
    const r = evaluateCandidate(p.input, { ...filters, requireOptions: false });
    if ('excluded' in r) excluded.push(r.excluded);
    else pre.push({ p, cand: r.candidate });
  }

  // pass 2 only for what survived pass 1, best first, bounded
  const ranked = rankCandidates(pre.map((x) => x.cand));
  const second = ranked.slice(0, 15);
  const byId = new Map(pre.map((x) => [x.p.inst.id, x.p]));
  for (const c of ranked.slice(15)) excluded.push({ symbol: c.symbol, reason: 'ranked below the option-chain budget (top 15 are checked for contracts)', audit: c.audit });

  const finals: Candidate[] = [];
  await mapLimit(second, 2, async (c) => {
    const p = byId.get(c.symbol)!;
    const [upcoming, ct] = await Promise.all([p.inst.kind === 'STOCK' ? upcomingFor(ctx, p.inst.id) : Promise.resolve([]), contractsFor(ctx, p.inst, c.setup.direction, p.input.price, filters, marketOpen)]);
    const input: ScanInput = { ...p.input, upcoming, contracts: ct.contracts, contractNote: ct.note };
    const r = evaluateCandidate(input, filters);
    if ('excluded' in r) excluded.push(r.excluded);
    else finals.push(r.candidate);
  });

  const out = rankCandidates(finals);
  const summary = summarize(out, excluded, total, done.length, 5);
  return {
    summary,
    candidates: out,
    excluded: excluded.slice(0, 200),
    filters,
    ranking: RANKING_RULE,
    asOf: ctx.now().toISOString(),
    session: { phase: session.phase, label: session.label },
    limits: { universe: total, evaluated: done.length, cap, deadlineHit },
    notes: [...notes, ...(ctx.cfg.offline ? ['DEMO DATA: synthetic prices, volumes and option chains; the candidates below are not real.'] : [])],
  };
}
