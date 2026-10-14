// Persistence for named contract groups and user alert rules. Every mutation is a compare-and-set
// read-modify-write on the existing authenticated KV, like the rest of the user data.

import { randomUUID } from 'node:crypto';
import type { z } from 'zod';
import { MAX_GROUPS, MAX_ITEMS_PER_GROUP, normalizeGroupName, reorderBy, sameName, type ChartPrefs, type ContractGroup, type ContractIdentity, type GroupItem } from '../../../shared/groups';
import { contractId, parseContractId } from '../../../shared/contract';
import type { ContractRule } from '../../../shared/wbrules';
import type { Ctx } from '../../config';
import { read, update } from '../../kv';
import type { chartPrefsInput, groupInput, groupPatch, itemInput, itemPatch, ruleInput } from '../../schemas';
import { KEYS, LimitError, NotFound } from '../userdata';

export class DuplicateError extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = 'DuplicateError';
  }
}

const iso = (ctx: Ctx) => ctx.now().toISOString();
const prefsOf = (p: z.infer<typeof chartPrefsInput> | null | undefined): ChartPrefs | undefined => (p ? (p as ChartPrefs) : undefined);

export async function listGroups(ctx: Ctx): Promise<ContractGroup[]> {
  return read<ContractGroup[]>(ctx.kv, KEYS.groups, []);
}

export async function createGroup(ctx: Ctx, input: z.infer<typeof groupInput>): Promise<ContractGroup> {
  const g: ContractGroup = { id: randomUUID(), name: input.name, note: input.note, items: [], createdAt: iso(ctx) };
  await update<ContractGroup[]>(ctx.kv, KEYS.groups, [], (cur) => {
    if (cur.length >= MAX_GROUPS) throw new LimitError(`Group limit (${MAX_GROUPS}) reached.`);
    if (cur.some((x) => sameName(x.name, input.name))) throw new DuplicateError(`A group named "${input.name}" already exists.`);
    return [...cur, g];
  });
  return g;
}

export async function updateGroup(ctx: Ctx, id: string, patch: z.infer<typeof groupPatch>): Promise<ContractGroup> {
  let out: ContractGroup | null = null;
  await update<ContractGroup[]>(ctx.kv, KEYS.groups, [], (cur) => {
    const i = cur.findIndex((g) => g.id === id);
    if (i < 0) throw new NotFound('Group');
    if (patch.name !== undefined && cur.some((g) => g.id !== id && sameName(g.name, patch.name!))) throw new DuplicateError(`A group named "${patch.name}" already exists.`);
    const g = cur[i]!;
    const next: ContractGroup = { ...g, ...(patch.name !== undefined ? { name: normalizeGroupName(patch.name) } : {}), ...(patch.note !== undefined ? { note: patch.note } : {}) };
    if (patch.prefs !== undefined) {
      if (patch.prefs === null) delete next.prefs;
      else next.prefs = prefsOf(patch.prefs);
    }
    out = next;
    const copy = [...cur];
    copy[i] = next;
    return copy;
  });
  return out!;
}

export async function deleteGroup(ctx: Ctx, id: string): Promise<void> {
  await update<ContractGroup[]>(ctx.kv, KEYS.groups, [], (cur) => {
    if (!cur.some((g) => g.id === id)) throw new NotFound('Group');
    return cur.filter((g) => g.id !== id);
  });
}

export async function reorderGroups(ctx: Ctx, order: string[]): Promise<ContractGroup[]> {
  return update<ContractGroup[]>(ctx.kv, KEYS.groups, [], (cur) => reorderBy(cur, order));
}

/** Idempotent: a contract already in this group is reported, never added twice. */
export async function addGroupItem(ctx: Ctx, groupId: string, input: z.infer<typeof itemInput>, identity: ContractIdentity): Promise<{ item: GroupItem; duplicate: boolean }> {
  const ref = parseContractId(input.id)!;
  const id = contractId(ref);
  let out: GroupItem | null = null;
  let duplicate = false;
  await update<ContractGroup[]>(ctx.kv, KEYS.groups, [], (cur) => {
    const i = cur.findIndex((g) => g.id === groupId);
    if (i < 0) throw new NotFound('Group');
    const g = cur[i]!;
    const hit = g.items.find((x) => x.id === id);
    if (hit) { duplicate = true; out = hit; return cur; }
    if (g.items.length >= MAX_ITEMS_PER_GROUP) throw new LimitError(`A group holds at most ${MAX_ITEMS_PER_GROUP} contracts.`);
    out = { id, note: input.note, addedAt: iso(ctx), identity };
    const copy = [...cur];
    copy[i] = { ...g, items: [...g.items, out] };
    return copy;
  });
  return { item: out!, duplicate };
}

export async function updateGroupItem(ctx: Ctx, groupId: string, id: string, patch: z.infer<typeof itemPatch>): Promise<GroupItem> {
  let out: GroupItem | null = null;
  await update<ContractGroup[]>(ctx.kv, KEYS.groups, [], (cur) => {
    const gi = cur.findIndex((g) => g.id === groupId);
    if (gi < 0) throw new NotFound('Group');
    const g = cur[gi]!;
    const ii = g.items.findIndex((x) => x.id === id);
    if (ii < 0) throw new NotFound('Contract in group');
    const it: GroupItem = { ...g.items[ii]!, ...(patch.note !== undefined ? { note: patch.note } : {}) };
    if (patch.prefs !== undefined) {
      if (patch.prefs === null) delete it.prefs;
      else it.prefs = prefsOf(patch.prefs);
    }
    out = it;
    const items = [...g.items];
    items[ii] = it;
    const copy = [...cur];
    copy[gi] = { ...g, items };
    return copy;
  });
  return out!;
}

export async function removeGroupItem(ctx: Ctx, groupId: string, id: string): Promise<void> {
  await update<ContractGroup[]>(ctx.kv, KEYS.groups, [], (cur) => {
    const gi = cur.findIndex((g) => g.id === groupId);
    if (gi < 0) throw new NotFound('Group');
    const copy = [...cur];
    copy[gi] = { ...cur[gi]!, items: cur[gi]!.items.filter((x) => x.id !== id) };
    return copy;
  });
}

export async function reorderGroupItems(ctx: Ctx, groupId: string, order: string[]): Promise<ContractGroup> {
  let out: ContractGroup | null = null;
  await update<ContractGroup[]>(ctx.kv, KEYS.groups, [], (cur) => {
    const gi = cur.findIndex((g) => g.id === groupId);
    if (gi < 0) throw new NotFound('Group');
    out = { ...cur[gi]!, items: reorderBy(cur[gi]!.items, order) };
    const copy = [...cur];
    copy[gi] = out;
    return copy;
  });
  return out!;
}

// ---- rules ---------------------------------------------------------------------------------------------------------------

export const MAX_RULES = 100;

export async function listRules(ctx: Ctx): Promise<ContractRule[]> {
  return read<ContractRule[]>(ctx.kv, KEYS.rules, []);
}

const sameRule = (a: Pick<ContractRule, 'contractId' | 'kind' | 'params'>, b: Pick<ContractRule, 'contractId' | 'kind' | 'params'>) => a.contractId === b.contractId && a.kind === b.kind && JSON.stringify(a.params) === JSON.stringify(b.params);

export async function createRule(ctx: Ctx, input: z.infer<typeof ruleInput>): Promise<{ rule: ContractRule; duplicate: boolean }> {
  const cid = contractId(parseContractId(input.contractId)!);
  let out: ContractRule | null = null;
  let duplicate = false;
  await update<ContractRule[]>(ctx.kv, KEYS.rules, [], (cur) => {
    const hit = cur.find((r) => sameRule(r, { contractId: cid, kind: input.kind, params: input.params }));
    if (hit) { duplicate = true; out = hit; return cur; }
    if (cur.length >= MAX_RULES) throw new LimitError(`Rule limit (${MAX_RULES}) reached.`);
    out = { id: randomUUID(), contractId: cid, kind: input.kind, params: input.params, enabled: input.enabled, note: input.note, createdAt: iso(ctx), lastTriggeredAt: null };
    return [...cur, out];
  });
  return { rule: out!, duplicate };
}

export async function updateRule(ctx: Ctx, id: string, input: z.infer<typeof ruleInput>): Promise<ContractRule> {
  let out: ContractRule | null = null;
  await update<ContractRule[]>(ctx.kv, KEYS.rules, [], (cur) => {
    const i = cur.findIndex((r) => r.id === id);
    if (i < 0) throw new NotFound('Rule');
    const prev = cur[i]!;
    const cid = contractId(parseContractId(input.contractId)!);
    if (cur.some((r) => r.id !== id && sameRule(r, { contractId: cid, kind: input.kind, params: input.params }))) throw new DuplicateError('An identical rule already exists.');
    // editing the condition re-arms the rule
    const changed = !sameRule(prev, { contractId: cid, kind: input.kind, params: input.params });
    out = { ...prev, contractId: cid, kind: input.kind, params: input.params, enabled: input.enabled, note: input.note, lastTriggeredAt: changed ? null : prev.lastTriggeredAt };
    const copy = [...cur];
    copy[i] = out;
    return copy;
  });
  return out!;
}

export async function setRuleEnabled(ctx: Ctx, id: string, enabled: boolean): Promise<ContractRule> {
  let out: ContractRule | null = null;
  await update<ContractRule[]>(ctx.kv, KEYS.rules, [], (cur) => {
    const i = cur.findIndex((r) => r.id === id);
    if (i < 0) throw new NotFound('Rule');
    out = { ...cur[i]!, enabled };
    const copy = [...cur];
    copy[i] = out;
    return copy;
  });
  return out!;
}

export async function deleteRule(ctx: Ctx, id: string): Promise<void> {
  await update<ContractRule[]>(ctx.kv, KEYS.rules, [], (cur) => {
    if (!cur.some((r) => r.id === id)) throw new NotFound('Rule');
    return cur.filter((r) => r.id !== id);
  });
}

export async function markRulesTriggered(ctx: Ctx, ids: string[]): Promise<void> {
  if (ids.length === 0) return;
  const at = iso(ctx);
  await update<ContractRule[]>(ctx.kv, KEYS.rules, [], (cur) => cur.map((r) => (ids.includes(r.id) ? { ...r, lastTriggeredAt: at } : r)));
}
