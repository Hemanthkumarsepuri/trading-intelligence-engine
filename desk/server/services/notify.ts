// Telegram delivery: one place that sends, records the outcome, and retries
// undelivered alert notifications without ever sending one twice in normal
// operation.
//
// Exactly-once is not achievable against Telegram (no idempotency key). The
// guarantee here is: an alert fires once (CAS claim in alertsRunner); its
// notification is attempted at most MAX_ATTEMPTS times; each attempt is
// leased with a CAS write *before* sending, so overlapping runs never send the
// same event concurrently. A crash after Telegram accepted a message but
// before the success was recorded can cause one duplicate after the lease
// expires; that window is the time of a single HTTP call.

import { escapeHtml, sendTelegram, TelegramError } from '../providers/telegram';
import type { AlertEvent, TelegramLog } from '../../shared/types';
import type { Ctx } from '../config';
import { read, update } from '../kv';
import { KEYS } from './userdata';

export const MAX_ATTEMPTS = 3;
const RETRY_WINDOW_MS = 30 * 60_000;
const LEASE_MS = 5 * 60_000;
const MAX_FAILURES_KEPT = 20;

let spacingMs = 1100; // Telegram: about one message per second per chat
export function setSendSpacingMs(ms: number): void {
  spacingMs = ms;
}
let lastSendAt = 0;
async function pace(): Promise<void> {
  // clamped: a wall-clock jump backwards must never stall delivery
  const wait = Math.min(spacingMs, lastSendAt + spacingMs - Date.now());
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastSendAt = Date.now();
}

const EMPTY_LOG: TelegramLog = { lastSuccessAt: null, lastSuccessKind: null, lastTestAt: null, failures: [] };

export async function telegramLog(ctx: Ctx): Promise<TelegramLog> {
  return { ...EMPTY_LOG, ...(await read<Partial<TelegramLog>>(ctx.kv, KEYS.telegramLog, {})) };
}

export interface NotifyResult {
  delivered: boolean;
  error?: string;
  category?: string;
  retryAfterSec?: number | null;
}

/** Send one Telegram message and record the outcome. Never throws. */
export async function notify(ctx: Ctx, html: string, kind = 'message'): Promise<NotifyResult> {
  if (!ctx.cfg.telegram) return { delivered: false, error: 'Telegram not configured', category: 'NOT_CONFIGURED' };
  const at = ctx.now().toISOString();
  try {
    await pace();
    await sendTelegram(ctx.cfg.telegram.token, ctx.cfg.telegram.chatId, html, ctx.fetchImpl);
    await update<TelegramLog>(ctx.kv, KEYS.telegramLog, EMPTY_LOG, (cur) => ({ ...EMPTY_LOG, ...cur, lastSuccessAt: at, lastSuccessKind: kind })).catch(() => undefined);
    return { delivered: true };
  } catch (e) {
    const category = e instanceof TelegramError ? e.kind : 'UNKNOWN';
    const message = e instanceof Error ? e.message : 'send failed';
    const retryAfterSec = e instanceof TelegramError ? e.retryAfterSec : null;
    await update<TelegramLog>(ctx.kv, KEYS.telegramLog, EMPTY_LOG, (cur) => ({
      ...EMPTY_LOG,
      ...cur,
      failures: [{ at, kind, category, message }, ...(cur.failures ?? [])].slice(0, MAX_FAILURES_KEPT),
    })).catch(() => undefined);
    return { delivered: false, error: message, category, retryAfterSec };
  }
}

/** Record a failure that happened before any send (e.g. token rejected by getMe). */
export async function notifyFailureOnly(ctx: Ctx, kind: string, res: NotifyResult): Promise<void> {
  const at = ctx.now().toISOString();
  await update<TelegramLog>(ctx.kv, KEYS.telegramLog, EMPTY_LOG, (cur) => ({
    ...EMPTY_LOG,
    ...cur,
    failures: [{ at, kind, category: res.category ?? 'UNKNOWN', message: res.error ?? 'failed' }, ...(cur.failures ?? [])].slice(0, MAX_FAILURES_KEPT),
  })).catch(() => undefined);
}

export function alertHtml(message: string, note?: string): string {
  return `<b>Alert</b>\n${escapeHtml(message)}${note ? `\n<i>${escapeHtml(note)}</i>` : ''}`;
}

/** Bookkeeping after a delivery attempt of a stored event. */
export async function recordAttempt(ctx: Ctx, eventId: string, res: NotifyResult): Promise<void> {
  const now = ctx.now();
  await update<AlertEvent[]>(ctx.kv, KEYS.events, [], (cur) =>
    cur.map((e) => {
      if (e.id !== eventId) return e;
      if (res.delivered) return { ...e, delivered: true, deliveredAt: now.toISOString(), lastError: null, nextAttemptAt: null };
      const backoffMs = Math.max(60_000, (res.retryAfterSec ?? 0) * 1000);
      return { ...e, delivered: false, lastError: res.error ?? 'failed', nextAttemptAt: new Date(now.getTime() + backoffMs).toISOString() };
    }),
  );
}

/**
 * Retry undelivered alert notifications: at most MAX_ATTEMPTS per event, only
 * within RETRY_WINDOW_MS of the alert, each attempt leased first via CAS.
 */
export async function retryUndelivered(ctx: Ctx): Promise<{ retried: number; delivered: number }> {
  if (!ctx.cfg.telegram) return { retried: 0, delivered: 0 };
  const now = ctx.now();
  const leased: AlertEvent[] = [];
  await update<AlertEvent[]>(ctx.kv, KEYS.events, [], (cur) => {
    leased.length = 0;
    return cur.map((e) => {
      const attempts = e.attempts ?? 1;
      const dueAt = e.nextAttemptAt ? Date.parse(e.nextAttemptAt) : 0;
      const eligible = !e.delivered && attempts < MAX_ATTEMPTS && now.getTime() - Date.parse(e.at) <= RETRY_WINDOW_MS && dueAt <= now.getTime();
      if (!eligible) return e;
      const next = { ...e, attempts: attempts + 1, nextAttemptAt: new Date(now.getTime() + LEASE_MS).toISOString() };
      leased.push(next);
      return next;
    });
  });
  let delivered = 0;
  for (const e of leased) {
    const res = await notify(ctx, alertHtml(e.message), 'alert-retry');
    if (res.delivered) delivered++;
    await recordAttempt(ctx, e.id, res);
  }
  return { retried: leased.length, delivered };
}
