// Contract search, detail, explorer chain, tracked contracts and the option chart.

import { contractId, expiryMatches, parseContractId, parseSearch, validateContract, type ContractRef, type ContractValidation, type ContractWatch, type OptType } from '../../../shared/contract';
import { istDateStr } from '../../../shared/india';
import type { ChainRow, NormalizedChain } from '../../../shared/optionchain';
import { missing, reconcile, type DataState, type Provenance, type Sourced } from '../../../shared/quality';
import { FALLBACK_SYMBOLS, OPTION_UNDERLYINGS } from '../../../shared/universe';
import type { TfCandle, Timeframe } from '../../../shared/candles';
import type { Ctx } from '../../config';
import { upstoxIntradayMinutes } from '../../providers/upstox';
import { mapLimit } from '../../http';
import { read, update } from '../../kv';
import { getSession } from '../market';
import { getSymbolMaster } from '../symbols';
import { listContracts } from '../userdata';
import { defaultExpiry, fnoStocks, isLive, latencyOf, loadChain, loadExpiries, loadQuote, provOf, resolveInstrument, stateFor, type ChainSet, type Instrument } from './data';

// ---- search ---------------------------------------------------------------------------------------------------------------

export interface SearchHit {
  id: string;
  name: string;
  kind: 'INDEX' | 'STOCK';
  fno: boolean;
  lotSize: number | null;
}
export interface ContractHit {
  id: string;
  ref: ContractRef;
  label: string;
  ltp: number | null;
  state: DataState;
}
export interface SearchResult {
  query: string;
  parsed: ReturnType<typeof parseSearch>;
  groups: { type: 'INDEX' | 'STOCK_FNO' | 'STOCK_OTHER'; title: string; items: SearchHit[] }[];
  contracts: ContractHit[];
  notes: string[];
}

export const MAX_QUERY = 60;

export function labelOf(r: ContractRef): string {
  const [y, m, d] = r.expiry.split('-');
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][Number(m) - 1]!;
  return `${r.underlying} ${d} ${mon} ${y} ${r.strike} ${r.type}`;
}

export async function searchWorkbench(ctx: Ctx, rawQuery: string): Promise<SearchResult> {
  const query = rawQuery.trim().slice(0, MAX_QUERY);
  const parsed = parseSearch(query);
  const notes: string[] = [];
  const empty: SearchResult = { query, parsed, groups: [], contracts: [], notes };
  const text = parsed.text;
  if (text.length < 1) return empty;

  const [master, fno] = await Promise.all([getSymbolMaster(ctx), fnoStocks(ctx)]);
  const hits: (SearchHit & { rank: number })[] = [];
  for (const u of OPTION_UNDERLYINGS) {
    const rank = u.id === text ? 0 : u.id.startsWith(text) ? 1 : u.id.includes(text) || u.name.toUpperCase().includes(text) ? 3 : -1;
    if (rank >= 0) hits.push({ id: u.id, name: u.name, kind: 'INDEX', fno: true, lotSize: null, rank });
  }
  const rows = master.rows.length > 0 ? master.rows : FALLBACK_SYMBOLS.map((s) => ({ ...s, isin: '' }));
  for (const r of rows) {
    const sym = r.symbol;
    const name = r.name.toUpperCase();
    const rank = sym === text ? 0 : sym.startsWith(text) ? 1 : name.startsWith(text) ? 2 : sym.includes(text) || name.includes(text) ? 3 : -1;
    if (rank < 0) continue;
    hits.push({ id: sym, name: r.name, kind: 'STOCK', fno: sym in fno, lotSize: fno[sym] ?? null, rank: rank + (sym in fno ? 0 : 0.5) });
    if (hits.length > 300) break;
  }
  hits.sort((a, b) => a.rank - b.rank || a.id.localeCompare(b.id));
  const top = hits.slice(0, 25).map(({ rank: _r, ...h }) => h);
  const groups: SearchResult['groups'] = [];
  const ix = top.filter((h) => h.kind === 'INDEX');
  const sf = top.filter((h) => h.kind === 'STOCK' && h.fno);
  const so = top.filter((h) => h.kind === 'STOCK' && !h.fno);
  if (ix.length) groups.push({ type: 'INDEX', title: 'Indices', items: ix });
  if (sf.length) groups.push({ type: 'STOCK_FNO', title: 'F&O stocks', items: sf });
  if (so.length) groups.push({ type: 'STOCK_OTHER', title: 'Other NSE stocks (no listed options)', items: so });
  if (master.source === 'fallback') notes.push('The full NSE symbol list is unavailable; searching a built-in list of large caps only.');

  // contract lookup: only when the query names a strike, type or expiry and resolves to ONE underlying exactly
  const wantContract = parsed.strike !== null || parsed.type !== null || parsed.expiry !== null;
  const exact = top.filter((h) => h.id === text);
  const target = exact[0] ?? (top.length === 1 ? top[0] : undefined);
  if (wantContract && target && target.fno) {
    try {
      const inst = await resolveInstrument(ctx, target.id);
      if (inst) {
        const ex = await loadExpiries(ctx, inst);
        const wantExp = parsed.expiry ? ex.expiries.filter((e) => expiryMatches(e, parsed.expiry!)) : ex.expiries.slice(0, 1);
        for (const e of wantExp.slice(0, 2)) {
          const set = await loadChain(ctx, inst, e);
          const strikes = set.chain.rows.map((r) => r.strike);
          const chosen = parsed.strike !== null ? nearestStrikes(strikes, parsed.strike, 3) : strikes.length ? nearestStrikes(strikes, set.chain.spot ?? strikes[Math.floor(strikes.length / 2)]!, 3) : [];
          const types: OptType[] = parsed.type ? [parsed.type] : ['CE', 'PE'];
          for (const row of set.chain.rows.filter((r) => chosen.includes(r.strike))) {
            for (const type of types) {
              const ref: ContractRef = { underlying: inst.id, expiry: e, type, strike: row.strike };
              const leg = type === 'CE' ? row.ce : row.pe;
              contractsAdd(empty.contracts, ref, leg.ltp);
            }
          }
        }
        if (parsed.expiry && wantExp.length === 0) notes.push(`${inst.id} has no listed expiry matching that date. Listed: ${ex.expiries.slice(0, 4).join(', ')}.`);
      }
    } catch (e) {
      notes.push(`Contract lookup unavailable: ${e instanceof Error ? e.message : 'error'}`);
    }
  } else if (wantContract && target && !target.fno) {
    notes.push(`${target.id} has no listed option contracts (not in the F&O segment).`);
  } else if (wantContract && !target) {
    notes.push('Type the full underlying symbol first (for example "RELIANCE 1250 CE") to look up a contract.');
  }
  empty.groups = groups;
  empty.contracts = empty.contracts.slice(0, 12);
  return empty;
}

function contractsAdd(list: ContractHit[], ref: ContractRef, ltp: number | null): void {
  const id = contractId(ref);
  if (list.some((c) => c.id === id)) return;
  list.push({ id, ref, label: labelOf(ref), ltp: ltp !== null && ltp > 0 ? ltp : null, state: 'UNKNOWN' });
}

export function nearestStrikes(strikes: number[], target: number, n: number): number[] {
  return [...strikes].sort((a, b) => Math.abs(a - target) - Math.abs(b - target) || a - b).slice(0, n).sort((a, b) => a - b);
}

// ---- chain explorer ----------------------------------------------------------------------------------------------------------------

export interface ExplorerRow {
  strike: number;
  moneyness: { ce: 'ITM' | 'ATM' | 'OTM' | 'UNKNOWN'; pe: 'ITM' | 'ATM' | 'OTM' | 'UNKNOWN' };
  ce: ContractValidation;
  pe: ContractValidation;
}
export interface ExplorerView {
  underlying: { id: string; name: string; kind: 'INDEX' | 'STOCK' };
  expiry: string;
  expiries: string[];
  expiryReason: string | null;
  lotSize: Sourced<number>;
  spot: Sourced<number>;
  atmStrike: number | null;
  rows: ExplorerRow[];
  totalStrikes: number;
  window: { each: number };
  provenance: Provenance;
  state: DataState;
  notes: string[];
}

function chainProv(set: ChainSet): Provenance {
  return provOf(set.chain.source, set.chain.asOf ?? null, set.chain.fetchedAt);
}

export async function explorerView(ctx: Ctx, inst: Instrument, expiryAsked: string | null, each = 12): Promise<ExplorerView> {
  const session = await getSession(ctx);
  const marketOpen = session.phase === 'OPEN' || session.phase === 'SPECIAL';
  const ex = await loadExpiries(ctx, inst);
  const today = istDateStr(ctx.now());
  let expiry = expiryAsked;
  let expiryReason: string | null = null;
  if (expiry) {
    if (!ex.expiries.includes(expiry)) throw new ExplorerError('BAD_EXPIRY', `${expiry} is not a listed expiry for ${inst.id}. Listed: ${ex.expiries.slice(0, 6).join(', ') || 'none'}.`);
  } else {
    const d = defaultExpiry(ex.expiries, today);
    expiry = d.expiry;
    expiryReason = d.reason;
  }
  if (!expiry) throw new ExplorerError('NO_EXPIRY', 'No upcoming expiry is listed.');
  const set = await loadChain(ctx, inst, expiry);
  const chain = set.chain;
  const lot = ex.lotSize ?? inst.lotSize;
  const prov = chainProv(set);
  const state = stateFor(ctx, chain.source, chain.asOf ?? null, chain.rows.length > 0, 'chain', marketOpen);
  const spot = chain.spot;
  const sorted = [...chain.rows].sort((a, b) => a.strike - b.strike);
  let ai = 0;
  if (spot !== null) sorted.forEach((r, i) => { if (Math.abs(r.strike - spot) < Math.abs(sorted[ai]!.strike - spot)) ai = i; });
  else ai = Math.floor(sorted.length / 2);
  const win = sorted.slice(Math.max(0, ai - each), ai + each + 1);
  const latency = latencyOf(chain.source);
  const v = (ref: ContractRef, leg: ChainRow['ce']) => validateContract({ ref, leg, lotSize: lot, spot, asOf: chain.asOf ?? null, fetchedAt: chain.fetchedAt, source: chain.source, latency, marketOpen, now: ctx.now() });
  const money = (type: OptType, k: number): 'ITM' | 'ATM' | 'OTM' | 'UNKNOWN' => (spot === null ? 'UNKNOWN' : k === sorted[ai]!.strike ? 'ATM' : (type === 'CE' ? k < spot : k > spot) ? 'ITM' : 'OTM');
  const rows: ExplorerRow[] = win.map((r) => ({
    strike: r.strike,
    moneyness: { ce: money('CE', r.strike), pe: money('PE', r.strike) },
    ce: v({ underlying: inst.id, expiry, type: 'CE', strike: r.strike }, r.ce),
    pe: v({ underlying: inst.id, expiry, type: 'PE', strike: r.strike }, r.pe),
  }));
  return {
    underlying: { id: inst.id, name: inst.name, kind: inst.kind },
    expiry,
    expiries: ex.expiries,
    expiryReason,
    lotSize: lot !== null ? { value: lot, state: 'VALID', prov: provOf(ex.source, null, ctx.now().toISOString()) } : missing('Official lot size unavailable from the provider.'),
    spot: spot !== null ? { value: spot, state, prov } : missing('The chain did not include an underlying price.'),
    atmStrike: spot !== null ? sorted[ai]?.strike ?? null : null,
    rows,
    totalStrikes: sorted.length,
    window: { each },
    provenance: prov,
    state,
    notes: [...set.notes, ...(ex.stale ? ['Expiry list is the last good copy.'] : [])],
  };
}

export class ExplorerError extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

// ---- single contract ------------------------------------------------------------------------------------------------------------------

export interface ObservationPoint {
  t: number;
  ltp: number;
  spot: number | null;
}

export interface ContractView {
  id: string;
  label: string;
  validation: ContractValidation;
  underlying: { id: string; name: string; spot: Sourced<number>; independent: Sourced<number>; crossCheck: { state: DataState; note: string } };
  chain: { source: string; asOf: string | null; fetchedAt: string; stale: boolean };
  notes: string[];
}

const OBS_MIN_GAP_SEC = 30;
const OBS_MAX = 500;

async function observe(ctx: Ctx, id: string, ltp: number | null, spot: number | null, valid: boolean): Promise<void> {
  if (!valid || ltp === null || !(ltp > 0)) return;
  const t = Math.floor(ctx.now().getTime() / 1000);
  await update<ObservationPoint[]>(ctx.kv, `wb/obs/${id}`, [], (cur) => {
    const last = cur[cur.length - 1];
    if (last && t - last.t < OBS_MIN_GAP_SEC) return cur;
    return [...cur, { t, ltp, spot }].slice(-OBS_MAX);
  }).catch(() => undefined);
}

export async function contractView(ctx: Ctx, id: string): Promise<ContractView> {
  const ref = parseContractId(id);
  if (!ref) throw new ExplorerError('BAD_CONTRACT', 'Not a valid contract id.');
  const inst = await resolveInstrument(ctx, ref.underlying);
  if (!inst) throw new ExplorerError('UNKNOWN_UNDERLYING', `${ref.underlying} is not a known underlying.`);
  const session = await getSession(ctx);
  const marketOpen = session.phase === 'OPEN' || session.phase === 'SPECIAL';
  const ex = await loadExpiries(ctx, inst);
  if (!ex.expiries.includes(ref.expiry)) throw new ExplorerError('UNSUPPORTED_CONTRACT', `${ref.expiry} is not a listed expiry for ${inst.id}${ref.expiry < istDateStr(ctx.now()) ? ' (it has expired)' : ''}.`);
  const set = await loadChain(ctx, inst, ref.expiry);
  const row = set.chain.rows.find((r) => Math.abs(r.strike - ref.strike) < 0.001);
  if (!row) throw new ExplorerError('UNSUPPORTED_CONTRACT', `Strike ${ref.strike} is not listed for ${inst.id} ${ref.expiry}.`);
  const chain = set.chain;
  const lot = ex.lotSize ?? inst.lotSize;
  const validation = validateContract({ ref, leg: ref.type === 'CE' ? row.ce : row.pe, lotSize: lot, spot: chain.spot, asOf: chain.asOf ?? null, fetchedAt: chain.fetchedAt, source: chain.source, latency: latencyOf(chain.source), marketOpen, now: ctx.now() });

  const q = await loadQuote(ctx, inst);
  const prov = provOf(chain.source, chain.asOf ?? null, chain.fetchedAt);
  const cState = stateFor(ctx, chain.source, chain.asOf ?? null, chain.spot !== null, 'chain', marketOpen);
  const spot: Sourced<number> = chain.spot !== null ? { value: chain.spot, state: cState, prov } : missing('The chain did not include an underlying price.');
  const indep: Sourced<number> = q.quote ? { value: q.quote.price, state: stateFor(ctx, q.quote.source, q.quote.marketTime, true, 'quote', marketOpen), prov: provOf(q.quote.source, q.quote.marketTime, q.quote.fetchedAt) } : missing(q.error ?? 'No independent quote.');
  // a delayed independent feed is allowed a wider tolerance than two simultaneous snapshots
  const rec = reconcile(spot.value, indep.value, indep.prov?.latency === 'DELAYED' ? 1.5 : 0.5, [chain.source, q.quote?.source ?? 'quote']);
  const notes = [...set.notes];
  if (rec.state === 'CONFLICT') notes.push(rec.note);
  await observe(ctx, id, validation.ltp.value, chain.spot, validation.state === 'VALID');
  return {
    id,
    label: labelOf(ref),
    validation,
    underlying: { id: inst.id, name: inst.name, spot, independent: indep, crossCheck: { state: rec.state, note: rec.note } },
    chain: { source: chain.source, asOf: chain.asOf ?? null, fetchedAt: chain.fetchedAt, stale: set.stale },
    notes,
  };
}

export async function observations(ctx: Ctx, id: string): Promise<ObservationPoint[]> {
  return read<ObservationPoint[]>(ctx.kv, `wb/obs/${id}`, []);
}

// ---- tracked contracts ----------------------------------------------------------------------------------------------------------------------

export interface TrackedRow {
  item: ContractWatch;
  label: string;
  validation: ContractValidation | null;
  spot: number | null;
  problem: string | null;
}

export async function trackedView(ctx: Ctx): Promise<{ rows: TrackedRow[]; notes: string[] }> {
  const items = await listContracts(ctx);
  const session = await getSession(ctx);
  const marketOpen = session.phase === 'OPEN' || session.phase === 'SPECIAL';
  const notes: string[] = [];
  const groups = new Map<string, ContractWatch[]>();
  for (const it of items) groups.set(`${it.ref.underlying}|${it.ref.expiry}`, [...(groups.get(`${it.ref.underlying}|${it.ref.expiry}`) ?? []), it]);
  const out = new Map<string, TrackedRow>();
  const today = istDateStr(ctx.now());
  await mapLimit([...groups.entries()], 3, async ([, list]) => {
    const first = list[0]!;
    const label = (i: ContractWatch) => labelOf(i.ref);
    if (first.ref.expiry < today) {
      for (const it of list) out.set(it.id, { item: it, label: label(it), validation: null, spot: null, problem: 'This contract has expired.' });
      return;
    }
    try {
      const inst = await resolveInstrument(ctx, first.ref.underlying);
      if (!inst) throw new Error('unknown underlying');
      const [ex, set] = await Promise.all([loadExpiries(ctx, inst), loadChain(ctx, inst, first.ref.expiry)]);
      for (const it of list) {
        const row = set.chain.rows.find((r) => Math.abs(r.strike - it.ref.strike) < 0.001);
        if (!row) { out.set(it.id, { item: it, label: label(it), validation: null, spot: set.chain.spot, problem: `Strike ${it.ref.strike} is no longer listed.` }); continue; }
        const v = validateContract({ ref: it.ref, leg: it.ref.type === 'CE' ? row.ce : row.pe, lotSize: ex.lotSize ?? inst.lotSize, spot: set.chain.spot, asOf: set.chain.asOf ?? null, fetchedAt: set.chain.fetchedAt, source: set.chain.source, latency: latencyOf(set.chain.source), marketOpen, now: ctx.now() });
        out.set(it.id, { item: it, label: label(it), validation: v, spot: set.chain.spot, problem: null });
        await observe(ctx, it.id, v.ltp.value, set.chain.spot, v.state === 'VALID');
      }
    } catch (e) {
      for (const it of list) out.set(it.id, { item: it, label: label(it), validation: null, spot: null, problem: `Live data unavailable: ${e instanceof Error ? e.message : 'error'}` });
    }
  });
  const rows = items.map((it) => out.get(it.id) ?? { item: it, label: labelOf(it.ref), validation: null, spot: null, problem: 'Not evaluated.' });
  if (items.length === 0) notes.push('No contracts tracked yet.');
  return { rows, notes };
}

/** Verify a contract exists in the live chain before it is stored (never track a contract that is not listed). */
export async function assertListed(ctx: Ctx, ref: ContractRef): Promise<{ lotSize: number | null }> {
  const inst = await resolveInstrument(ctx, ref.underlying);
  if (!inst) throw new ExplorerError('UNKNOWN_UNDERLYING', `${ref.underlying} is not a known underlying.`);
  if (!inst.fno) throw new ExplorerError('UNSUPPORTED_CONTRACT', `${inst.id} is not in the F&O segment.`);
  const ex = await loadExpiries(ctx, inst);
  if (!ex.expiries.includes(ref.expiry)) throw new ExplorerError('UNSUPPORTED_CONTRACT', `${ref.expiry} is not a listed expiry for ${inst.id}.`);
  const set = await loadChain(ctx, inst, ref.expiry);
  if (!set.chain.rows.some((r) => Math.abs(r.strike - ref.strike) < 0.001)) throw new ExplorerError('UNSUPPORTED_CONTRACT', `Strike ${ref.strike} is not listed for ${inst.id} ${ref.expiry}.`);
  return { lotSize: ex.lotSize ?? inst.lotSize };
}

export { isLive };
export type { NormalizedChain };

// ---- option chart -----------------------------------------------------------------------------------------------------------------------------

export interface ContractChart {
  kind: 'CANDLES' | 'OBSERVED' | 'UNSUPPORTED';
  tf: Timeframe;
  candles: TfCandle[];
  points: ObservationPoint[];
  source: string | null;
  state: DataState;
  note: string;
}

const MIN_OF: Record<Exclude<Timeframe, '1d'>, 1 | 3 | 5 | 15 | 30 | 60> = { '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30, '1h': 60 };

/**
 * Exchange-style option candles come only from Upstox (needs a token). Without it the chart shows the
 * premium snapshots this app itself observed, which are labelled as such and are not candles.
 */
export async function contractChart(ctx: Ctx, id: string, tf: Timeframe): Promise<ContractChart> {
  const view = await contractView(ctx, id);
  const points = await observations(ctx, id);
  const key = (await keyFor(ctx, id)) ?? null;
  if (tf === '1d') return { kind: points.length ? 'OBSERVED' : 'UNSUPPORTED', tf, candles: [], points, source: null, state: points.length ? 'DATA_INSUFFICIENT' : 'UNSUPPORTED', note: 'Daily option candles are not offered: individual contracts have short, irregular histories. Showing observed snapshots only.' };
  if (ctx.cfg.upstoxToken && key && key.includes('|') && !ctx.cfg.offline) {
    try {
      const raw = await upstoxIntradayMinutes(ctx.cfg.upstoxToken, key, MIN_OF[tf], { fetchImpl: ctx.fetchImpl });
      const nowSec = ctx.now().getTime() / 1000;
      const step = MIN_OF[tf] * 60;
      const candles: TfCandle[] = raw.map((c) => ({ ...c, complete: c.t + step <= nowSec, have: 1, want: 1 }));
      return { kind: 'CANDLES', tf, candles, points, source: 'upstox', state: candles.length ? view.validation.state : 'DATA_INSUFFICIENT', note: candles.length ? 'Candles from Upstox for this contract.' : 'Upstox returned no candles for this contract today.' };
    } catch (e) {
      return { kind: points.length ? 'OBSERVED' : 'UNSUPPORTED', tf, candles: [], points, source: null, state: 'UNKNOWN', note: `Option candles unavailable (${e instanceof Error ? e.message.slice(0, 120) : 'error'}). Showing snapshots this app observed.` };
    }
  }
  return {
    kind: points.length ? 'OBSERVED' : 'UNSUPPORTED',
    tf,
    candles: [],
    points,
    source: null,
    state: points.length ? 'DATA_INSUFFICIENT' : 'UNSUPPORTED',
    note: points.length
      ? 'No option candle provider is configured. These are premium snapshots observed by this app each time the contract is loaded (at most one per 30 s); they are not exchange candles and have gaps.'
      : 'No option candle provider is configured (needs UPSTOX_ANALYTICS_TOKEN) and no snapshots have been observed yet. Keep the contract tracked and reopen it to build a snapshot series.',
  };
}

async function keyFor(ctx: Ctx, id: string): Promise<string | null> {
  const ref = parseContractId(id);
  if (!ref) return null;
  const inst = await resolveInstrument(ctx, ref.underlying);
  if (!inst) return null;
  const set = await loadChain(ctx, inst, ref.expiry);
  const row = set.chain.rows.find((r) => Math.abs(r.strike - ref.strike) < 0.001);
  return (ref.type === 'CE' ? row?.ce.instrumentKey : row?.pe.instrumentKey) ?? null;
}
