// Alert evaluation - pure and fully testable. The scheduled function supplies
// fresh data; this module decides what fires and why something was skipped.

import { fmtIstTime } from './format';
import { ageMinutes, istDateStr } from './india';
import type { TechSnapshot } from './indicators';
import type { Alert, AlertType, Quote } from './types';

export const ALERT_LABEL: Record<AlertType, string> = {
  PRICE_ABOVE: 'Price rises above',
  PRICE_BELOW: 'Price falls below',
  DAY_CHANGE_ABOVE: 'Day change above (%)',
  DAY_CHANGE_BELOW: 'Day change below (%)',
  RSI_ABOVE: 'Daily RSI above',
  RSI_BELOW: 'Daily RSI below',
  VOLUME_SPIKE: 'Volume vs 20-day avg (x)',
  VIX_ABOVE: 'India VIX above',
  FNO_BAN: 'Enters F&O ban list',
};

export interface AlertContext {
  quotes: Record<string, Quote | undefined>;
  tech: Record<string, TechSnapshot | null | undefined>;
  /** symbols whose daily history came from a stale cache (refresh failed) */
  techStale?: ReadonlySet<string>;
  /** India VIX quote, freshness-gated like any other quote */
  vix: Quote | null;
  /** today's F&O ban list; null when unavailable or not verifiably for today */
  banned: ReadonlySet<string> | null;
  now: Date;
  /** max acceptable age of a quote's exchange timestamp while the market is live */
  maxQuoteAgeMin: number;
  marketLive: boolean;
}

export type Evaluation =
  | { fired: true; message: string }
  | { fired: false; skipped?: string };

export function shouldConsider(a: Alert, now: Date): { ok: boolean; why?: string } {
  if (!a.enabled) return { ok: false, why: 'disabled' };
  if (a.repeat === 'once' && a.lastTriggeredAt) return { ok: false, why: 'already fired (once)' };
  if (a.repeat === 'daily' && a.lastTriggeredAt && istDateStr(new Date(a.lastTriggeredAt)) === istDateStr(now)) {
    return { ok: false, why: 'already fired today' };
  }
  return { ok: true };
}

/** Freshness gate shared by every quote-based alert: never act on a stale print while the market is live. */
function staleReason(q: Quote, ctx: AlertContext): string | null {
  if (!ctx.marketLive) return null;
  const age = ageMinutes(q.marketTime, ctx.now);
  return age > ctx.maxQuoteAgeMin ? `stale quote (${Number.isFinite(age) ? `${Math.round(age)} min old` : 'no exchange timestamp'})` : null;
}

const at = (q: Quote): string => (q.marketTime ? ` (traded ${fmtIstTime(q.marketTime)} IST)` : '');

export function evaluateAlert(a: Alert, ctx: AlertContext): Evaluation {
  const gate = shouldConsider(a, ctx.now);
  if (!gate.ok) return { fired: false, skipped: gate.why };

  const sym = a.symbol.toUpperCase();

  if (a.type === 'FNO_BAN') {
    if (ctx.banned === null) return { fired: false, skipped: 'ban list unavailable or not dated for today' };
    return ctx.banned.has(sym) ? { fired: true, message: `${sym} is in today's F&O ban list. No fresh positions allowed.` } : { fired: false };
  }

  if (a.type === 'VIX_ABOVE') {
    if (!ctx.vix) return { fired: false, skipped: 'VIX unavailable' };
    const stale = staleReason(ctx.vix, ctx);
    if (stale) return { fired: false, skipped: stale };
    return ctx.vix.price > a.threshold ? { fired: true, message: `India VIX is ${ctx.vix.price.toFixed(2)}, above your ${a.threshold} alert${at(ctx.vix)}.` } : { fired: false };
  }

  if (a.type === 'RSI_ABOVE' || a.type === 'RSI_BELOW' || a.type === 'VOLUME_SPIKE') {
    const t = ctx.tech[sym];
    if (!t) return { fired: false, skipped: 'no daily history' };
    if (ctx.techStale?.has(sym)) return { fired: false, skipped: 'daily history is a stale cached copy' };
    if (a.type === 'RSI_ABOVE') return t.rsi14 !== null && t.rsi14 > a.threshold ? { fired: true, message: `${sym} daily RSI is ${t.rsi14.toFixed(1)}, above ${a.threshold}.` } : { fired: false };
    if (a.type === 'RSI_BELOW') return t.rsi14 !== null && t.rsi14 < a.threshold ? { fired: true, message: `${sym} daily RSI is ${t.rsi14.toFixed(1)}, below ${a.threshold}.` } : { fired: false };
    return t.volRatio20 !== null && t.volRatio20 >= a.threshold ? { fired: true, message: `${sym} volume is ${t.volRatio20.toFixed(1)}x its 20-day average.` } : { fired: false };
  }

  const q = ctx.quotes[sym];
  if (!q) return { fired: false, skipped: 'no quote' };
  const stale = staleReason(q, ctx);
  if (stale) return { fired: false, skipped: stale };

  switch (a.type) {
    case 'PRICE_ABOVE':
      return q.price > a.threshold ? { fired: true, message: `${sym} is at ${q.price.toFixed(2)}, above your ${a.threshold} level${at(q)}.` } : { fired: false };
    case 'PRICE_BELOW':
      return q.price < a.threshold ? { fired: true, message: `${sym} is at ${q.price.toFixed(2)}, below your ${a.threshold} level${at(q)}.` } : { fired: false };
    case 'DAY_CHANGE_ABOVE':
      return q.changePct > a.threshold ? { fired: true, message: `${sym} is up ${q.changePct.toFixed(2)}% today (alert at ${a.threshold}%)${at(q)}.` } : { fired: false };
    case 'DAY_CHANGE_BELOW':
      return q.changePct < a.threshold ? { fired: true, message: `${sym} is down ${Math.abs(q.changePct).toFixed(2)}% today (alert at ${a.threshold}%)${at(q)}.` } : { fired: false };
    default:
      return { fired: false };
  }
}

/** Which alert types need daily candles (so the scheduler only fetches what it must). */
export function needsHistory(a: Alert): boolean {
  return a.type === 'RSI_ABOVE' || a.type === 'RSI_BELOW' || a.type === 'VOLUME_SPIKE';
}
