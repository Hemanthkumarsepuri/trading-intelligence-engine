// Scheduled alert evaluation. Runs every few minutes during market hours.
//
// Correctness rules (each one is a bug class from the TIRE audit):
//  - Fire only from fresh data: stale quotes are skipped and reported.
//  - Exactly-once delivery: a fired alert is "claimed" with a compare-and-set
//    write BEFORE anything is sent, so two overlapping runs cannot double-send.
//  - Notification failure never loses the event: it is logged with delivered=false.
//  - Live-price alerts only evaluate while the market is open.

import { randomUUID } from 'node:crypto';
import { evaluateAlert, needsHistory, shouldConsider } from '../../shared/alerts';
import { isLiveSession, istDateStr } from '../../shared/india';
import { analyze, type TechSnapshot } from '../../shared/indicators';
import { riskStatus } from '../../shared/journal';
import { MAIN_INDICES, VIX_DEF } from '../../shared/universe';
import type { Alert, AlertEvent, Quote } from '../../shared/types';
import type { Ctx } from '../config';
import { mapLimit } from '../http';
import { escapeHtml } from '../providers/telegram';
import { fmtIstTime } from '../../shared/format';
import { alertHtml, notify, recordAttempt, retryUndelivered } from './notify';
import { read, update } from '../kv';
import { getBan, getSession } from './market';
import { equityTarget, getDaily, getQuotes, type Target } from './quotes';
import { getLiveHolidays } from './symbols';
import { appendEvents, getSettings, KEYS, listAlerts, listTrades } from './userdata';

export interface TickReport {
  ranAt: string;
  skippedReason?: string;
  evaluated: number;
  fired: string[];
  skipped: { alertId: string; symbol: string; why: string }[];
  notified: number;
  notifyFailures: string[];
  /** earlier undelivered notifications re-attempted in this run */
  retried?: number;
  risk?: { state: string; notified: boolean };
}

// A quote whose exchange timestamp is older than this during market hours is
// treated as stale: covers delayed feeds (e.g. a 15-minute-delayed print) and
// thinly traded symbols. Such alerts are skipped and reported, never fired.
const MAX_QUOTE_AGE_MIN = 5;
const LIVE_ONLY = new Set(['PRICE_ABOVE', 'PRICE_BELOW', 'DAY_CHANGE_ABOVE', 'DAY_CHANGE_BELOW', 'VIX_ABOVE']);

function targetFor(symbol: string): Target {
  const idx = MAIN_INDICES.find((i) => i.id === symbol);
  return idx ? { id: idx.id, yahoo: idx.yahoo, upstoxKey: idx.upstox } : equityTarget(symbol);
}

export async function runAlertsTick(ctx: Ctx): Promise<TickReport> {
  const now = ctx.now();
  const report: TickReport = { ranAt: now.toISOString(), evaluated: 0, fired: [], skipped: [], notified: 0, notifyFailures: [] };

  const session = await getSession(ctx);
  if (!session.isTradingDay) {
    report.skippedReason = `Not a trading day (${session.reason}).`;
    return report;
  }
  const holidays = await getLiveHolidays(ctx);
  const live = isLiveSession(now, holidays);

  // ---- alerts --------------------------------------------------------------------
  const alerts = (await listAlerts(ctx)).filter((a) => shouldConsider(a, now).ok);
  const due: Alert[] = [];
  for (const a of alerts) {
    if (LIVE_ONLY.has(a.type) && !live) {
      report.skipped.push({ alertId: a.id, symbol: a.symbol, why: 'market not open' });
      continue;
    }
    due.push(a);
  }

  if (due.length > 0) {
    const quoteTargets = new Map<string, Target>();
    for (const a of due) if (a.type !== 'VIX_ABOVE' && a.type !== 'FNO_BAN') quoteTargets.set(a.symbol, targetFor(a.symbol));
    const wantsVix = due.some((a) => a.type === 'VIX_ABOVE');
    if (wantsVix) quoteTargets.set(VIX_DEF.id, { id: VIX_DEF.id, yahoo: VIX_DEF.yahoo, upstoxKey: VIX_DEF.upstox });

    const quotes: Record<string, Quote | undefined> = {};
    const priceNeeded = due.filter((a) => !needsHistory(a) && a.type !== 'FNO_BAN' && a.type !== 'VIX_ABOVE').map((a) => a.symbol);
    const targets = [...quoteTargets.values()].filter((t) => priceNeeded.includes(t.id) || t.id === VIX_DEF.id);
    if (targets.length > 0) {
      try {
        const qr = await getQuotes(ctx, 'alerts', targets, 10, 300);
        Object.assign(quotes, qr.quotes);
      } catch (e) {
        report.skippedReason = `Quotes unavailable: ${e instanceof Error ? e.message : 'error'}`;
      }
    }

    const tech: Record<string, TechSnapshot | null> = {};
    const techStale = new Set<string>();
    const histSyms = [...new Set(due.filter(needsHistory).map((a) => a.symbol))];
    await mapLimit(histSyms, 4, async (s) => {
      const d = await getDaily(ctx, s).catch(() => null);
      tech[s] = d && d.candles.length >= 2 ? analyze(d.candles) : null;
      if (d?.stale) techStale.add(s);
    });

    // The ban list only counts when it is fresh and dated for today's session.
    let banned: Set<string> | null = new Set<string>();
    if (due.some((a) => a.type === 'FNO_BAN')) {
      const b = await getBan(ctx).catch(() => null);
      banned = b && !b.stale && b.data.asOf === istDateStr(now) ? new Set(b.data.symbols) : null;
    }

    const fired: { alert: Alert; message: string }[] = [];
    for (const a of due) {
      report.evaluated++;
      const ev = evaluateAlert(a, { quotes, tech, techStale, vix: quotes[VIX_DEF.id] ?? null, banned, now, maxQuoteAgeMin: MAX_QUOTE_AGE_MIN, marketLive: live });
      if (ev.fired) fired.push({ alert: a, message: ev.message });
      else if (ev.skipped) report.skipped.push({ alertId: a.id, symbol: a.symbol, why: ev.skipped });
    }

    if (fired.length > 0) {
      // 1) Claim (CAS): only alerts this run moved from armed to fired continue.
      const claimed = new Set<string>();
      await update<Alert[]>(ctx.kv, KEYS.alerts, [], (cur) => {
        claimed.clear();
        return cur.map((a) => {
          const f = fired.find((x) => x.alert.id === a.id);
          if (!f || !shouldConsider(a, now).ok) return a;
          claimed.add(a.id);
          return { ...a, lastTriggeredAt: now.toISOString() };
        });
      });

      // 2) Outbox: persist the event (leased for the first attempt) before sending,
      //    so a crash can never lose a fired alert.
      const lease = new Date(now.getTime() + 5 * 60_000).toISOString();
      const events: (AlertEvent & { note: string })[] = fired
        .filter((f) => claimed.has(f.alert.id))
        .map((f) => ({ id: randomUUID(), alertId: f.alert.id, at: now.toISOString(), message: f.message, delivered: false, attempts: 1, nextAttemptAt: lease, lastError: null, note: f.alert.note }));
      await appendEvents(ctx, events.map(({ note: _n, ...e }) => e));

      // 3) Send, then record the outcome. Failures are retried by retryUndelivered().
      for (const e of events) {
        const res = await notify(ctx, alertHtml(e.message, e.note), 'alert');
        await recordAttempt(ctx, e.id, res);
        if (res.delivered) report.notified++;
        else if (res.error) report.notifyFailures.push(`${e.message.split(' ')[0]}: ${res.error}`);
        report.fired.push(e.alertId);
      }
    }
  }

  // ---- retries of earlier undelivered notifications -----------------------------------
  try {
    const r = await retryUndelivered(ctx);
    report.retried = r.retried;
  } catch (e) {
    report.notifyFailures.push(`retry pass failed: ${e instanceof Error ? e.message : 'error'}`);
  }

  // ---- risk guard ------------------------------------------------------------------
  try {
    const [settings, trades] = await Promise.all([getSettings(ctx), listTrades(ctx)]);
    const rs = riskStatus(trades, settings, now);
    if (rs.state !== 'OK') {
      const rank = { OK: 0, WARN: 1, BREACHED: 2 } as const;
      let send = false;
      await update<{ date: string; state: keyof typeof rank }>(ctx.kv, KEYS.riskNotified, { date: '', state: 'OK' }, (cur) => {
        const sameDay = cur.date === istDateStr(now);
        send = !sameDay || rank[rs.state] > rank[cur.state];
        return send ? { date: istDateStr(now), state: rs.state } : cur;
      });
      if (send) {
        const ev: AlertEvent = { id: randomUUID(), alertId: 'risk-guard', at: now.toISOString(), message: `Risk guard ${rs.state}: ${rs.messages.join(' ')} (${fmtIstTime(now)} IST)`, delivered: false, attempts: 1, nextAttemptAt: new Date(now.getTime() + 5 * 60_000).toISOString(), lastError: null };
        await appendEvents(ctx, [ev]);
        const res = await notify(ctx, `<b>Risk guard: ${rs.state}</b>\n${rs.messages.map(escapeHtml).join('\n')}\n<i>${fmtIstTime(now)} IST</i>`, 'risk-guard');
        await recordAttempt(ctx, ev.id, res);
        report.risk = { state: rs.state, notified: res.delivered };
        if (!res.delivered && res.error) report.notifyFailures.push(`risk guard: ${res.error}`);
      } else {
        report.risk = { state: rs.state, notified: false };
      }
    }
  } catch (e) {
    report.notifyFailures.push(`risk guard check failed: ${e instanceof Error ? e.message : 'error'}`);
  }

  await ctx.kv.put('data/last-tick', report, '*').catch(() => undefined);
  return report;
}

export async function lastTick(ctx: Ctx): Promise<TickReport | null> {
  return read<TickReport | null>(ctx.kv, 'data/last-tick', null);
}
