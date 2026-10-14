// Owner's data: trades, holdings, watchlist, alerts, settings. Every mutation
// is a compare-and-set read-modify-write (see kv.update).

import { randomUUID } from 'node:crypto';
import { contractId, parseContractId, type ContractWatch } from '../../shared/contract';
import { DEFAULT_WATCHLIST } from '../../shared/universe';
import { DEFAULT_SETTINGS, type Alert, type AlertEvent, type Holding, type Settings, type Trade, type WatchItem } from '../../shared/types';
import type { Ctx } from '../config';
import { read, update } from '../kv';
import { alertInput, contractWatchInput, holdingInput, settingsInput, tradeInput, watchInput } from '../schemas';
import type { z } from 'zod';

export const KEYS = {
  trades: 'data/trades',
  holdings: 'data/holdings',
  watchlist: 'data/watchlist',
  alerts: 'data/alerts',
  events: 'data/alert-events',
  settings: 'data/settings',
  contracts: 'data/contracts',
  wbAlerts: 'data/wb-alerts',
  wbSeen: 'data/wb-alert-seen',
  riskNotified: 'data/risk-notified',
  digestSent: 'data/digest-sent',
  telegramLog: 'data/telegram-log',
  scheduler: 'data/scheduler-state',
} as const;

export class NotFound extends Error {
  constructor(what: string) {
    super(`${what} not found`);
    this.name = 'NotFound';
  }
}
export class LimitError extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = 'LimitError';
  }
}

const iso = (ctx: Ctx) => ctx.now().toISOString();

// ---- Settings ---------------------------------------------------------------------

export async function getSettings(ctx: Ctx): Promise<Settings> {
  return { ...DEFAULT_SETTINGS, ...(await read<Partial<Settings>>(ctx.kv, KEYS.settings, {})) };
}

export async function saveSettings(ctx: Ctx, patch: z.infer<typeof settingsInput>): Promise<Settings> {
  const next = await update<Partial<Settings>>(ctx.kv, KEYS.settings, {}, (cur) => ({ ...cur, ...patch }));
  return { ...DEFAULT_SETTINGS, ...next };
}

// ---- Trades -----------------------------------------------------------------------

export async function listTrades(ctx: Ctx): Promise<Trade[]> {
  const t = await read<Trade[]>(ctx.kv, KEYS.trades, []);
  return t.sort((a, b) => Date.parse(b.entryAt) - Date.parse(a.entryAt));
}

export async function createTrade(ctx: Ctx, input: z.infer<typeof tradeInput>): Promise<Trade> {
  const trade: Trade = { id: randomUUID(), ...input, createdAt: iso(ctx), updatedAt: iso(ctx) };
  await update<Trade[]>(ctx.kv, KEYS.trades, [], (cur) => {
    if (cur.length >= 20000) throw new LimitError('Trade limit (20,000) reached. Export and archive older trades.');
    return [...cur, trade];
  });
  return trade;
}

export async function updateTrade(ctx: Ctx, id: string, input: z.infer<typeof tradeInput>): Promise<Trade> {
  let out: Trade | null = null;
  await update<Trade[]>(ctx.kv, KEYS.trades, [], (cur) => {
    const i = cur.findIndex((t) => t.id === id);
    if (i < 0) throw new NotFound('Trade');
    out = { ...cur[i]!, ...input, id, updatedAt: iso(ctx) };
    const next = [...cur];
    next[i] = out;
    return next;
  });
  return out!;
}

export async function deleteTrade(ctx: Ctx, id: string): Promise<void> {
  await update<Trade[]>(ctx.kv, KEYS.trades, [], (cur) => {
    if (!cur.some((t) => t.id === id)) throw new NotFound('Trade');
    return cur.filter((t) => t.id !== id);
  });
}

// ---- Holdings ----------------------------------------------------------------------

export async function listHoldings(ctx: Ctx): Promise<Holding[]> {
  return read<Holding[]>(ctx.kv, KEYS.holdings, []);
}

export async function createHoldings(ctx: Ctx, inputs: z.infer<typeof holdingInput>[]): Promise<Holding[]> {
  const created = inputs.map((i) => ({ id: randomUUID(), ...i }));
  await update<Holding[]>(ctx.kv, KEYS.holdings, [], (cur) => {
    if (cur.length + created.length > 1000) throw new LimitError('Holding limit (1,000 lots) reached.');
    return [...cur, ...created];
  });
  return created;
}

export async function updateHolding(ctx: Ctx, id: string, input: z.infer<typeof holdingInput>): Promise<Holding> {
  let out: Holding | null = null;
  await update<Holding[]>(ctx.kv, KEYS.holdings, [], (cur) => {
    const i = cur.findIndex((h) => h.id === id);
    if (i < 0) throw new NotFound('Holding');
    out = { id, ...input };
    const next = [...cur];
    next[i] = out;
    return next;
  });
  return out!;
}

export async function deleteHolding(ctx: Ctx, id: string): Promise<void> {
  await update<Holding[]>(ctx.kv, KEYS.holdings, [], (cur) => {
    if (!cur.some((h) => h.id === id)) throw new NotFound('Holding');
    return cur.filter((h) => h.id !== id);
  });
}

export async function replaceHoldings(ctx: Ctx, inputs: z.infer<typeof holdingInput>[]): Promise<Holding[]> {
  const created = inputs.map((i) => ({ id: randomUUID(), ...i }));
  await update<Holding[]>(ctx.kv, KEYS.holdings, [], () => created);
  return created;
}

// ---- Watchlist ----------------------------------------------------------------------

export async function listWatchlist(ctx: Ctx): Promise<WatchItem[]> {
  const cur = await ctx.kv.get<WatchItem[]>(KEYS.watchlist);
  if (cur.value) return cur.value;
  const at = iso(ctx);
  return DEFAULT_WATCHLIST.map((symbol) => ({ symbol, note: '', support: null, resistance: null, addedAt: at }));
}

export async function upsertWatch(ctx: Ctx, input: z.infer<typeof watchInput>): Promise<WatchItem> {
  const seed = await listWatchlist(ctx);
  let out: WatchItem | null = null;
  await update<WatchItem[]>(ctx.kv, KEYS.watchlist, seed, (cur) => {
    const i = cur.findIndex((w) => w.symbol === input.symbol);
    if (i >= 0) {
      out = { ...cur[i]!, ...input };
      const next = [...cur];
      next[i] = out;
      return next;
    }
    if (cur.length >= 100) throw new LimitError('Watchlist limit (100) reached.');
    out = { ...input, addedAt: iso(ctx) };
    return [...cur, out];
  });
  return out!;
}

export async function removeWatch(ctx: Ctx, symbol: string): Promise<void> {
  const seed = await listWatchlist(ctx);
  await update<WatchItem[]>(ctx.kv, KEYS.watchlist, seed, (cur) => cur.filter((w) => w.symbol !== symbol));
}

// ---- Tracked option contracts ---------------------------------------------------------

export const MAX_CONTRACTS = 50;

export async function listContracts(ctx: Ctx): Promise<ContractWatch[]> {
  return read<ContractWatch[]>(ctx.kv, KEYS.contracts, []);
}

/** Idempotent: adding a contract that is already tracked returns the existing row and duplicate=true. */
export async function addContract(ctx: Ctx, input: z.infer<typeof contractWatchInput>): Promise<{ item: ContractWatch; duplicate: boolean }> {
  const ref = parseContractId(input.id)!;
  const id = contractId(ref);
  let out: ContractWatch | null = null;
  let duplicate = false;
  await update<ContractWatch[]>(ctx.kv, KEYS.contracts, [], (cur) => {
    const hit = cur.find((c) => c.id === id);
    if (hit) { duplicate = true; out = hit; return cur; }
    if (cur.length >= MAX_CONTRACTS) throw new LimitError(`Tracked-contract limit (${MAX_CONTRACTS}) reached.`);
    out = { id, ref, note: input.note, addedAt: iso(ctx) };
    return [...cur, out];
  });
  return { item: out!, duplicate };
}

export async function removeContract(ctx: Ctx, id: string): Promise<void> {
  await update<ContractWatch[]>(ctx.kv, KEYS.contracts, [], (cur) => cur.filter((c) => c.id !== id));
}

// ---- Alerts --------------------------------------------------------------------------

export async function listAlerts(ctx: Ctx): Promise<Alert[]> {
  return read<Alert[]>(ctx.kv, KEYS.alerts, []);
}

export async function createAlert(ctx: Ctx, input: z.infer<typeof alertInput>): Promise<Alert> {
  const a: Alert = { id: randomUUID(), ...input, lastTriggeredAt: null, createdAt: iso(ctx) };
  await update<Alert[]>(ctx.kv, KEYS.alerts, [], (cur) => {
    if (cur.length >= 100) throw new LimitError('Alert limit (100) reached.');
    return [...cur, a];
  });
  return a;
}

export async function updateAlert(ctx: Ctx, id: string, input: z.infer<typeof alertInput>): Promise<Alert> {
  let out: Alert | null = null;
  await update<Alert[]>(ctx.kv, KEYS.alerts, [], (cur) => {
    const i = cur.findIndex((a) => a.id === id);
    if (i < 0) throw new NotFound('Alert');
    // editing the condition re-arms the alert
    const prev = cur[i]!;
    const changed = prev.type !== input.type || prev.symbol !== input.symbol || prev.threshold !== input.threshold;
    out = { ...prev, ...input, id, lastTriggeredAt: changed ? null : prev.lastTriggeredAt };
    const next = [...cur];
    next[i] = out;
    return next;
  });
  return out!;
}

export async function deleteAlert(ctx: Ctx, id: string): Promise<void> {
  await update<Alert[]>(ctx.kv, KEYS.alerts, [], (cur) => {
    if (!cur.some((a) => a.id === id)) throw new NotFound('Alert');
    return cur.filter((a) => a.id !== id);
  });
}

export async function listEvents(ctx: Ctx): Promise<AlertEvent[]> {
  return read<AlertEvent[]>(ctx.kv, KEYS.events, []);
}

export async function appendEvents(ctx: Ctx, events: AlertEvent[]): Promise<void> {
  if (events.length === 0) return;
  await update<AlertEvent[]>(ctx.kv, KEYS.events, [], (cur) => [...events, ...cur].slice(0, 200));
}
