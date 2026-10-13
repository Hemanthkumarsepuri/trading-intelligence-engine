// Export, import and scheduled backups.
//
// Import is all-or-nothing: every row is validated first, the current data is
// snapshotted to a backup key, and only then is anything replaced.

import { randomUUID } from 'node:crypto';
import { istDateStr } from '../../shared/india';
import { DEFAULT_SETTINGS, type Alert, type Holding, type Settings, type Trade, type WatchItem } from '../../shared/types';
import type { Ctx } from '../config';
import { read, update } from '../kv';
import { alertInput, backupInput, holdingInput, settingsInput, tradeInput, watchInput } from '../schemas';
import { KEYS, getSettings, listAlerts, listHoldings, listTrades, listWatchlist } from './userdata';

export interface BackupFile {
  version: 1;
  app: 'tire-desk';
  exportedAt: string;
  trades: Trade[];
  holdings: Holding[];
  watchlist: WatchItem[];
  alerts: Alert[];
  settings: Settings;
}

export async function exportAll(ctx: Ctx): Promise<BackupFile> {
  const [trades, holdings, watchlist, alerts, settings] = await Promise.all([listTrades(ctx), listHoldings(ctx), listWatchlist(ctx), listAlerts(ctx), getSettings(ctx)]);
  return { version: 1, app: 'tire-desk', exportedAt: ctx.now().toISOString(), trades, holdings, watchlist, alerts, settings };
}

export class ImportError extends Error {
  constructor(public problems: string[]) {
    super(`Import rejected: ${problems.length} problem(s). Nothing was changed.`);
    this.name = 'ImportError';
  }
}

const idOf = (row: Record<string, unknown>): string => (typeof row.id === 'string' && /^[\w-]{8,64}$/.test(row.id) ? row.id : randomUUID());
const strOf = (row: Record<string, unknown>, k: string, fb: string): string => (typeof row[k] === 'string' && !Number.isNaN(Date.parse(row[k] as string)) ? new Date(row[k] as string).toISOString() : fb);

interface Validated {
  trades: Trade[];
  holdings: Holding[];
  watchlist: WatchItem[];
  alerts: Alert[];
  settings: Partial<Settings> | null;
}

/** Validate a backup file completely without writing anything. Throws ImportError listing problems. */
export function validateBackup(raw: unknown, nowIso: string): Validated {
  const parsed = backupInput.safeParse(raw);
  if (!parsed.success) throw new ImportError(parsed.error.issues.slice(0, 10).map((i) => `${i.path.join('.')}: ${i.message}`));
  const b = parsed.data;
  const problems: string[] = [];
  const now = nowIso;

  const trades: Trade[] = [];
  b.trades.forEach((row, i) => {
    const r = tradeInput.safeParse(row);
    if (!r.success) return void problems.push(`trades[${i}]: ${r.error.issues[0]?.path.join('.')} ${r.error.issues[0]?.message}`);
    trades.push({ id: idOf(row), ...r.data, createdAt: strOf(row, 'createdAt', now), updatedAt: strOf(row, 'updatedAt', now) });
  });
  const holdings: Holding[] = [];
  b.holdings.forEach((row, i) => {
    const r = holdingInput.safeParse(row);
    if (!r.success) return void problems.push(`holdings[${i}]: ${r.error.issues[0]?.path.join('.')} ${r.error.issues[0]?.message}`);
    holdings.push({ id: idOf(row), ...r.data });
  });
  const watchlist: WatchItem[] = [];
  b.watchlist.forEach((row, i) => {
    const r = watchInput.safeParse(row);
    if (!r.success) return void problems.push(`watchlist[${i}]: ${r.error.issues[0]?.path.join('.')} ${r.error.issues[0]?.message}`);
    watchlist.push({ ...r.data, addedAt: strOf(row, 'addedAt', now) });
  });
  const alerts: Alert[] = [];
  b.alerts.forEach((row, i) => {
    const r = alertInput.safeParse(row);
    if (!r.success) return void problems.push(`alerts[${i}]: ${r.error.issues[0]?.path.join('.')} ${r.error.issues[0]?.message}`);
    alerts.push({ id: idOf(row), ...r.data, lastTriggeredAt: typeof row.lastTriggeredAt === 'string' ? row.lastTriggeredAt : null, createdAt: strOf(row, 'createdAt', now) });
  });
  const s = settingsInput.safeParse(b.settings);
  if (!s.success) problems.push(`settings: ${s.error.issues[0]?.path.join('.')} ${s.error.issues[0]?.message}`);

  const dup = (ids: string[], what: string) => {
    if (new Set(ids).size !== ids.length) problems.push(`${what}: duplicate ids`);
  };
  dup(trades.map((t) => t.id), 'trades');
  dup(holdings.map((h) => h.id), 'holdings');
  dup(alerts.map((a) => a.id), 'alerts');
  if (new Set(watchlist.map((w) => w.symbol)).size !== watchlist.length) problems.push('watchlist: duplicate symbols');
  if (problems.length > 0) throw new ImportError(problems.slice(0, 12));
  return { trades, holdings, watchlist, alerts, settings: s.success ? s.data : null };
}

export async function importAll(ctx: Ctx, raw: unknown): Promise<{ trades: number; holdings: number; watchlist: number; alerts: number; backupKey: string }> {
  const now = ctx.now().toISOString();
  const { trades, holdings, watchlist, alerts, settings } = validateBackup(raw, now);
  const backupKey = await snapshot(ctx, `pre-import-${now.replace(/[:.]/g, '-')}`);

  await update<Trade[]>(ctx.kv, KEYS.trades, [], () => trades);
  await update<Holding[]>(ctx.kv, KEYS.holdings, [], () => holdings);
  await update<WatchItem[]>(ctx.kv, KEYS.watchlist, [], () => watchlist);
  await update<Alert[]>(ctx.kv, KEYS.alerts, [], () => alerts);
  if (settings) await update<Partial<Settings>>(ctx.kv, KEYS.settings, {}, () => ({ ...DEFAULT_SETTINGS, ...settings }));
  return { trades: trades.length, holdings: holdings.length, watchlist: watchlist.length, alerts: alerts.length, backupKey };
}

async function snapshot(ctx: Ctx, name: string): Promise<string> {
  const key = `backup/${name}`;
  await ctx.kv.put(key, await exportAll(ctx), '*');
  return key;
}

/** Read a stored snapshot back and validate it end to end, as a restore would. Writes nothing. */
export async function verifyBackup(ctx: Ctx, key: string): Promise<{ key: string; ok: boolean; exportedAt: string | null; counts: Record<string, number>; problems: string[] }> {
  const b = await readBackup(ctx, key);
  if (!b) return { key, ok: false, exportedAt: null, counts: {}, problems: ['Snapshot not found or unreadable.'] };
  try {
    const v = validateBackup({ ...b, confirm: true }, ctx.now().toISOString());
    return { key, ok: true, exportedAt: b.exportedAt, counts: { trades: v.trades.length, holdings: v.holdings.length, watchlist: v.watchlist.length, alerts: v.alerts.length }, problems: [] };
  } catch (e) {
    return { key, ok: false, exportedAt: b.exportedAt, counts: {}, problems: e instanceof ImportError ? e.problems : [e instanceof Error ? e.message : 'invalid'] };
  }
}

/** Restore a stored snapshot (the current data is snapshotted first by importAll). */
export async function restoreBackup(ctx: Ctx, key: string): Promise<Awaited<ReturnType<typeof importAll>>> {
  const b = await readBackup(ctx, key);
  if (!b) throw new ImportError(['Snapshot not found or unreadable.']);
  return importAll(ctx, { ...b, confirm: true });
}

const KEEP_DAILY = 14;
const KEEP_PRE_IMPORT = 5;

/** Daily snapshot with retention. Idempotent per IST day. */
export async function runBackup(ctx: Ctx): Promise<{ key: string; kept: number; removed: number }> {
  const day = istDateStr(ctx.now());
  const key = await snapshot(ctx, `daily-${day}`);

  const keys = await ctx.kv.list('backup/');
  const daily = keys.filter((k) => k.startsWith('backup/daily-')).sort();
  const pre = keys.filter((k) => k.startsWith('backup/pre-import-')).sort();
  const drop = [...daily.slice(0, Math.max(0, daily.length - KEEP_DAILY)), ...pre.slice(0, Math.max(0, pre.length - KEEP_PRE_IMPORT))];
  for (const k of drop) await ctx.kv.del(k);
  return { key, kept: daily.length + pre.length - drop.length, removed: drop.length };
}

export async function listBackups(ctx: Ctx): Promise<string[]> {
  return (await ctx.kv.list('backup/')).sort().reverse();
}

export async function readBackup(ctx: Ctx, key: string): Promise<BackupFile | null> {
  if (!/^backup\/[\w.-]+$/.test(key)) return null;
  return read<BackupFile | null>(ctx.kv, key, null);
}
