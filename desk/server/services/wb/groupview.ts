// Read models for custom groups and the comparison screen, plus evaluation of user alert rules.

import { contractId, parseContractId, type ContractWatch } from '../../../shared/contract';
import type { ContractGroup, GroupItem } from '../../../shared/groups';
import { evaluateRule, type ContractRule } from '../../../shared/wbrules';
import type { DataState } from '../../../shared/quality';
import type { WbAlert } from '../../../shared/wbalerts';
import type { Timeframe } from '../../../shared/candles';
import type { Ctx } from '../../config';
import { read, update } from '../../kv';
import { getSession } from '../market';
import { listContracts } from '../userdata';
import { validateItems, type TrackedRow } from './contracts';
import { loadTf, resolveInstrument, stateFor } from './data';
import { listGroups, listRules, markRulesTriggered } from './groups';

const asWatch = (it: GroupItem): ContractWatch | null => {
  const ref = parseContractId(it.id);
  return ref ? { id: contractId(ref), ref, note: it.note, addedAt: it.addedAt } : null;
};

/** Every distinct tracked contract: the ungrouped list plus every group's items. */
export async function allTracked(ctx: Ctx): Promise<ContractWatch[]> {
  const [flat, groups] = await Promise.all([listContracts(ctx), listGroups(ctx)]);
  const map = new Map<string, ContractWatch>(flat.map((c) => [c.id, c]));
  for (const g of groups) for (const it of g.items) {
    const w = asWatch(it);
    if (w && !map.has(w.id)) map.set(w.id, w);
  }
  return [...map.values()];
}

export interface GroupView extends Omit<ContractGroup, 'items'> {
  items: { item: GroupItem; row: TrackedRow }[];
  expiredCount: number;
}

export async function groupsView(ctx: Ctx): Promise<{ groups: GroupView[]; ungrouped: TrackedRow[]; notes: string[] }> {
  const [groups, flat] = await Promise.all([listGroups(ctx), listContracts(ctx)]);
  const all = new Map<string, ContractWatch>(flat.map((c) => [c.id, c]));
  for (const g of groups) for (const it of g.items) {
    const w = asWatch(it);
    if (w && !all.has(w.id)) all.set(w.id, w);
  }
  const rows = await validateItems(ctx, [...all.values()]);
  const out: GroupView[] = groups.map((g) => {
    const items = g.items.map((item) => ({ item, row: rows.get(item.id)! }));
    return { ...g, items, expiredCount: items.filter((x) => x.row.problem === 'This contract has expired.').length };
  });
  return { groups: out, ungrouped: flat.map((c) => rows.get(c.id)!), notes: [] };
}

export const MAX_COMPARE = 4;

export async function compareView(ctx: Ctx, ids: string[]): Promise<{ rows: TrackedRow[]; asOf: string; notes: string[] }> {
  const items: ContractWatch[] = [];
  const notes: string[] = [];
  for (const raw of [...new Set(ids)].slice(0, MAX_COMPARE)) {
    const ref = parseContractId(raw);
    if (!ref) { notes.push(`Ignored an invalid contract id.`); continue; }
    items.push({ id: contractId(ref), ref, note: '', addedAt: ctx.now().toISOString() });
  }
  if (ids.length > MAX_COMPARE) notes.push(`Only the first ${MAX_COMPARE} contracts are compared.`);
  const rows = await validateItems(ctx, items);
  return { rows: items.map((i) => rows.get(i.id)!), asOf: ctx.now().toISOString(), notes };
}

// ---- rule evaluation -----------------------------------------------------------------------------------------------------------

interface Prev {
  premium: number | null;
  spot: number | null;
}

export async function evaluateRules(ctx: Ctx): Promise<{ alerts: WbAlert[]; withheld: { ruleId: string; contractId: string; reason: string }[]; evaluated: number }> {
  const rules = (await listRules(ctx)).filter((r) => r.enabled);
  if (rules.length === 0) return { alerts: [], withheld: [], evaluated: 0 };
  const now = ctx.now();
  const session = await getSession(ctx);
  const marketOpen = session.phase === 'OPEN' || session.phase === 'SPECIAL';
  const items: ContractWatch[] = [];
  for (const id of new Set(rules.map((r) => r.contractId))) {
    const ref = parseContractId(id);
    if (ref) items.push({ id, ref, note: '', addedAt: now.toISOString() });
  }
  const rows = await validateItems(ctx, items);
  const candleCache = new Map<string, Promise<{ candles: import('../../../shared/candles').TfCandle[] | null; freshness: DataState }>>();
  const candlesFor = (underlying: string, tf: Timeframe) => {
    const key = `${underlying}|${tf}`;
    if (!candleCache.has(key)) {
      candleCache.set(key, (async () => {
        const inst = await resolveInstrument(ctx, underlying);
        if (!inst) return { candles: null, freshness: 'UNKNOWN' as DataState };
        const d = await loadTf(ctx, inst, tf);
        if (!d.availability.available) return { candles: null, freshness: 'DATA_INSUFFICIENT' as DataState };
        const last = d.candles[d.candles.length - 1]!;
        const asOf = new Date((last.t + 60) * 1000).toISOString();
        const freshness: DataState = d.stale ? 'STALE' : marketOpen ? stateFor(ctx, d.source, asOf, true, 'intraday', true) : 'MARKET_CLOSED';
        return { candles: d.candles, freshness };
      })());
    }
    return candleCache.get(key)!;
  };

  const alerts: WbAlert[] = [];
  const withheld: { ruleId: string; contractId: string; reason: string }[] = [];
  const fired: string[] = [];
  const prevKey = (id: string) => `wb/rule-prev/${id}`;
  const seenPrev = new Set<string>();

  for (const rule of rules) {
    const row = rows.get(rule.contractId) ?? null;
    const v = row?.validation ?? null;
    const needCandles = rule.kind === 'CLOSE_BEYOND_LEVEL' || rule.kind === 'RETEST';
    const cf = needCandles && rule.params.tf ? await candlesFor(rule.contractId.split('|')[0]!, rule.params.tf) : { candles: null, freshness: 'UNKNOWN' as DataState };
    const prev = await read<Prev | null>(ctx.kv, prevKey(rule.contractId), null);
    const spotState: DataState = v ? (v.state === 'VALID' ? 'VALID' : v.state) : 'UNKNOWN';
    const res = evaluateRule({ rule, now, contract: v, spot: row?.spot ?? null, spotState, candles: cf.candles, underlyingFreshness: cf.freshness, previous: prev });
    if (res.alert) { alerts.push(res.alert); fired.push(rule.id); }
    else if (res.withheld) withheld.push({ ruleId: rule.id, contractId: rule.contractId, reason: res.withheld });
    seenPrev.add(rule.contractId);
  }
  for (const id of seenPrev) {
    const v = rows.get(id)?.validation;
    if (v && v.state === 'VALID') await update<Prev | null>(ctx.kv, prevKey(id), null, () => ({ premium: v.ltp.value, spot: rows.get(id)!.spot })).catch(() => undefined);
  }
  return { alerts, withheld, evaluated: rules.length };
}

export { markRulesTriggered };
export type { ContractRule };
