// Trading-journal analytics and the personal risk guard. Pure functions.

import { tradeEconomics, type ChargeBreakdown } from './charges';
import { istDateStr } from './india';
import type { Settings, Trade } from './types';

export interface ClosedView {
  trade: Trade;
  gross: number;
  charges: ChargeBreakdown;
  net: number;
  /** IST date of exit, YYYY-MM-DD */
  exitDate: string;
  entryDate: string;
  holdMinutes: number;
  riskAmount: number | null;
  /** net P&L divided by planned risk; null without a stop loss */
  rMultiple: number | null;
}

export function plannedRisk(t: Trade): number | null {
  if (t.stopLoss === null) return null;
  const perUnit = t.side === 'LONG' ? t.entryPrice - t.stopLoss : t.stopLoss - t.entryPrice;
  if (!(perUnit > 0)) return null; // stop on the wrong side of entry is not a risk figure
  return perUnit * t.qty;
}

export function closedViews(trades: Trade[], s: Settings): ClosedView[] {
  const out: ClosedView[] = [];
  for (const t of trades) {
    if (t.exitPrice === null || t.exitAt === null) continue;
    const eco = tradeEconomics(t, s);
    const risk = plannedRisk(t);
    out.push({
      trade: t,
      gross: eco.gross,
      charges: eco.charges,
      net: eco.net,
      exitDate: istDateStr(new Date(t.exitAt)),
      entryDate: istDateStr(new Date(t.entryAt)),
      holdMinutes: Math.max(0, (Date.parse(t.exitAt) - Date.parse(t.entryAt)) / 60000),
      riskAmount: risk,
      rMultiple: risk !== null && risk > 0 ? eco.net / risk : null,
    });
  }
  return out.sort((a, b) => Date.parse(a.trade.exitAt!) - Date.parse(b.trade.exitAt!));
}

export interface JournalStats {
  trades: number;
  wins: number;
  losses: number;
  winRate: number | null;
  grossPnl: number;
  netPnl: number;
  totalCharges: number;
  /** charges as % of gross profit - how much of your edge the market's toll takes */
  chargesPctOfGrossProfit: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  payoffRatio: number | null;
  expectancy: number | null;
  profitFactor: number | null;
  avgR: number | null;
  maxDrawdown: number;
  maxDrawdownPct: number | null;
  longestWinStreak: number;
  longestLossStreak: number;
  currentStreak: { kind: 'W' | 'L' | null; n: number };
  best: number | null;
  worst: number | null;
  avgHoldMinutes: number | null;
}

export function computeStats(views: ClosedView[], capital: number): JournalStats {
  const n = views.length;
  const wins = views.filter((v) => v.net > 0);
  const losses = views.filter((v) => v.net < 0);
  const gross = views.reduce((a, v) => a + v.gross, 0);
  const net = views.reduce((a, v) => a + v.net, 0);
  const charges = views.reduce((a, v) => a + v.charges.total, 0);
  const grossProfit = wins.reduce((a, v) => a + v.net, 0);
  const grossLoss = Math.abs(losses.reduce((a, v) => a + v.net, 0));
  const avgWin = wins.length ? grossProfit / wins.length : null;
  const avgLoss = losses.length ? grossLoss / losses.length : null;
  const winRate = n ? wins.length / n : null;

  let expectancy: number | null = null;
  if (n && winRate !== null) expectancy = winRate * (avgWin ?? 0) - (1 - winRate) * (avgLoss ?? 0);

  // equity curve drawdown
  let equity = capital;
  let peak = capital;
  let maxDD = 0;
  let maxDDPct: number | null = null;
  for (const v of views) {
    equity += v.net;
    if (equity > peak) peak = equity;
    const dd = peak - equity;
    if (dd > maxDD) {
      maxDD = dd;
      maxDDPct = peak > 0 ? (dd / peak) * 100 : null;
    }
  }

  let curW = 0;
  let curL = 0;
  let longW = 0;
  let longL = 0;
  for (const v of views) {
    if (v.net > 0) { curW++; curL = 0; } else if (v.net < 0) { curL++; curW = 0; } else { curW = 0; curL = 0; }
    longW = Math.max(longW, curW);
    longL = Math.max(longL, curL);
  }
  const currentStreak = curW > 0 ? { kind: 'W' as const, n: curW } : curL > 0 ? { kind: 'L' as const, n: curL } : { kind: null, n: 0 };

  const rs = views.map((v) => v.rMultiple).filter((x): x is number => x !== null);
  const nets = views.map((v) => v.net);
  return {
    trades: n,
    wins: wins.length,
    losses: losses.length,
    winRate,
    grossPnl: gross,
    netPnl: net,
    totalCharges: charges,
    chargesPctOfGrossProfit: grossProfit > 0 ? (charges / grossProfit) * 100 : null,
    avgWin,
    avgLoss,
    payoffRatio: avgWin !== null && avgLoss !== null && avgLoss > 0 ? avgWin / avgLoss : null,
    expectancy,
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : null,
    avgR: rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null,
    maxDrawdown: maxDD,
    maxDrawdownPct: maxDDPct,
    longestWinStreak: longW,
    longestLossStreak: longL,
    currentStreak,
    best: nets.length ? Math.max(...nets) : null,
    worst: nets.length ? Math.min(...nets) : null,
    avgHoldMinutes: n ? views.reduce((a, v) => a + v.holdMinutes, 0) / n : null,
  };
}

export interface EquityPoint { date: string; pnl: number; equity: number; cumulative: number }

/** Daily realized equity curve, one point per exit date. */
export function equityCurve(views: ClosedView[], capital: number): EquityPoint[] {
  const byDay = new Map<string, number>();
  for (const v of views) byDay.set(v.exitDate, (byDay.get(v.exitDate) ?? 0) + v.net);
  const days = [...byDay.keys()].sort();
  let cum = 0;
  return days.map((d) => {
    const pnl = byDay.get(d)!;
    cum += pnl;
    return { date: d, pnl, cumulative: cum, equity: capital + cum };
  });
}

export interface GroupStat { key: string; trades: number; net: number; winRate: number; avgNet: number }

export function groupStats(views: ClosedView[], keyOf: (v: ClosedView) => string | string[]): GroupStat[] {
  const m = new Map<string, ClosedView[]>();
  for (const v of views) {
    const keys = keyOf(v);
    for (const k of Array.isArray(keys) ? keys : [keys]) {
      if (!k) continue;
      const arr = m.get(k) ?? [];
      arr.push(v);
      m.set(k, arr);
    }
  }
  return [...m.entries()]
    .map(([key, vs]) => {
      const net = vs.reduce((a, v) => a + v.net, 0);
      return { key, trades: vs.length, net, winRate: vs.filter((v) => v.net > 0).length / vs.length, avgNet: net / vs.length };
    })
    .sort((a, b) => b.net - a.net);
}

export function weekdayOf(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number) as [number, number, number];
  return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][new Date(Date.UTC(y, m - 1, d)).getUTCDay()]!;
}

// ---- Behaviour / discipline -----------------------------------------------

export type ViolationCode =
  | 'NO_STOP_LOSS'
  | 'OVERSIZED_RISK'
  | 'TRADED_AFTER_LOSS_LIMIT'
  | 'OVERTRADING'
  | 'PLAN_NOT_FOLLOWED'
  | 'HEATED_ENTRY'
  | 'REVENGE_REENTRY';

export const VIOLATION_LABEL: Record<ViolationCode, string> = {
  NO_STOP_LOSS: 'No stop loss defined',
  OVERSIZED_RISK: 'Risk above per-trade limit',
  TRADED_AFTER_LOSS_LIMIT: 'Entered after daily loss limit',
  OVERTRADING: 'Beyond max trades for the day',
  PLAN_NOT_FOLLOWED: 'Plan not followed',
  HEATED_ENTRY: 'Entered while emotionally heated',
  REVENGE_REENTRY: 'Re-entered within 15 min of a loss',
};

export function evaluateViolations(trades: Trade[], s: Settings): Map<string, ViolationCode[]> {
  const result = new Map<string, ViolationCode[]>();
  const sorted = [...trades].sort((a, b) => Date.parse(a.entryAt) - Date.parse(b.entryAt));
  const lossLimit = (s.capital * s.maxDailyLossPct) / 100;
  const riskLimit = (s.capital * s.riskPerTradePct) / 100;
  const perDayCount = new Map<string, number>();

  for (const t of sorted) {
    const v: ViolationCode[] = [];
    const day = istDateStr(new Date(t.entryAt));
    const entryMs = Date.parse(t.entryAt);

    if (t.stopLoss === null) v.push('NO_STOP_LOSS');
    else {
      const risk = plannedRisk(t);
      if (risk !== null && risk > riskLimit * 1.05) v.push('OVERSIZED_RISK');
    }

    const n = (perDayCount.get(day) ?? 0) + 1;
    perDayCount.set(day, n);
    if (n > s.maxTradesPerDay) v.push('OVERTRADING');

    // realized loss of trades that had already exited when this one was entered
    let realized = 0;
    let lastLossExit: { ms: number; symbol: string } | null = null;
    for (const o of trades) {
      if (o.id === t.id || o.exitAt === null || o.exitPrice === null) continue;
      const exitMs = Date.parse(o.exitAt);
      if (exitMs > entryMs) continue;
      if (istDateStr(new Date(o.exitAt)) === day) realized += tradeEconomics(o, s).net;
      if (o.symbol === t.symbol && entryMs - exitMs <= 15 * 60000 && tradeEconomics(o, s).net < 0) {
        if (!lastLossExit || exitMs > lastLossExit.ms) lastLossExit = { ms: exitMs, symbol: o.symbol };
      }
    }
    if (realized <= -lossLimit) v.push('TRADED_AFTER_LOSS_LIMIT');
    if (lastLossExit) v.push('REVENGE_REENTRY');

    if (t.planFollowed === false) v.push('PLAN_NOT_FOLLOWED');
    if (t.emotion !== null && t.emotion >= 4) v.push('HEATED_ENTRY');

    result.set(t.id, v);
  }
  return result;
}

export interface DisciplineReport {
  windowDays: number;
  trades: number;
  cleanTrades: number;
  score: number | null;
  byViolation: { code: ViolationCode; label: string; count: number }[];
  /** net P&L of clean trades vs trades with at least one violation */
  cleanNet: number;
  violatedNet: number;
}

export function disciplineReport(trades: Trade[], s: Settings, now: Date, windowDays = 30): DisciplineReport {
  const cutoff = now.getTime() - windowDays * 86400000;
  const inWindow = trades.filter((t) => Date.parse(t.entryAt) >= cutoff);
  const viol = evaluateViolations(trades, s);
  const counts = new Map<ViolationCode, number>();
  let clean = 0;
  let cleanNet = 0;
  let violatedNet = 0;
  for (const t of inWindow) {
    const vs = viol.get(t.id) ?? [];
    const net = t.exitPrice !== null ? tradeEconomics(t, s).net : 0;
    if (vs.length === 0) { clean++; cleanNet += net; } else violatedNet += net;
    for (const c of new Set(vs)) counts.set(c, (counts.get(c) ?? 0) + 1);
  }
  return {
    windowDays,
    trades: inWindow.length,
    cleanTrades: clean,
    score: inWindow.length ? Math.round((clean / inWindow.length) * 100) : null,
    byViolation: [...counts.entries()].map(([code, count]) => ({ code, label: VIOLATION_LABEL[code], count })).sort((a, b) => b.count - a.count),
    cleanNet,
    violatedNet,
  };
}

// ---- Today's risk guard ----------------------------------------------------

export interface RiskStatus {
  date: string;
  state: 'OK' | 'WARN' | 'BREACHED';
  realizedToday: number;
  dailyLossLimit: number;
  lossLimitUsedPct: number;
  tradesToday: number;
  maxTrades: number;
  openPositions: number;
  maxOpenPositions: number;
  messages: string[];
}

export function riskStatus(trades: Trade[], s: Settings, now: Date): RiskStatus {
  const today = istDateStr(now);
  let realized = 0;
  let entriesToday = 0;
  let open = 0;
  for (const t of trades) {
    if (istDateStr(new Date(t.entryAt)) === today) entriesToday++;
    if (t.exitPrice === null) open++;
    else if (t.exitAt && istDateStr(new Date(t.exitAt)) === today) realized += tradeEconomics(t, s).net;
  }
  const limit = (s.capital * s.maxDailyLossPct) / 100;
  const used = limit > 0 ? Math.max(0, -realized / limit) * 100 : 0;
  const messages: string[] = [];
  let state: RiskStatus['state'] = 'OK';

  if (used >= 100) { state = 'BREACHED'; messages.push('Daily loss limit reached. Stop trading for the day.'); }
  else if (used >= 70) { state = 'WARN'; messages.push(`${used.toFixed(0)}% of the daily loss limit is used.`); }
  if (entriesToday >= s.maxTradesPerDay) {
    state = 'BREACHED';
    messages.push(`Max trades for the day (${s.maxTradesPerDay}) reached.`);
  } else if (entriesToday === s.maxTradesPerDay - 1 && state === 'OK') {
    state = 'WARN';
    messages.push('One trade left before your daily trade cap.');
  }
  if (open > s.maxOpenPositions) {
    state = 'BREACHED';
    messages.push(`Open positions (${open}) exceed your limit of ${s.maxOpenPositions}.`);
  }
  if (messages.length === 0) messages.push('Within all limits.');

  return {
    date: today,
    state,
    realizedToday: realized,
    dailyLossLimit: limit,
    lossLimitUsedPct: used,
    tradesToday: entriesToday,
    maxTrades: s.maxTradesPerDay,
    openPositions: open,
    maxOpenPositions: s.maxOpenPositions,
    messages,
  };
}

// ---- Position sizing --------------------------------------------------------

export interface SizingResult {
  riskAmount: number;
  perUnitRisk: number;
  rawQty: number;
  qty: number;
  lots: number | null;
  capitalRequired: number;
  capitalPct: number | null;
  warnings: string[];
}

export function positionSize(args: {
  capital: number;
  riskPct: number;
  entry: number;
  stop: number;
  lotSize?: number | null;
}): SizingResult {
  const warnings: string[] = [];
  const riskAmount = (args.capital * args.riskPct) / 100;
  const perUnit = Math.abs(args.entry - args.stop);
  if (!(perUnit > 0)) {
    return { riskAmount, perUnitRisk: 0, rawQty: 0, qty: 0, lots: null, capitalRequired: 0, capitalPct: null, warnings: ['Entry and stop loss must differ.'] };
  }
  const rawQty = riskAmount / perUnit;
  let qty = Math.floor(rawQty);
  let lots: number | null = null;
  if (args.lotSize && args.lotSize > 0) {
    lots = Math.floor(rawQty / args.lotSize);
    qty = lots * args.lotSize;
    if (lots === 0) warnings.push('One lot already risks more than your per-trade limit. Skip, or widen risk knowingly.');
  }
  const capitalRequired = qty * args.entry;
  const capitalPct = args.capital > 0 ? (capitalRequired / args.capital) * 100 : null;
  if (capitalPct !== null && capitalPct > 100) warnings.push('Notional exceeds capital; check margin or use a smaller size.');
  return { riskAmount, perUnitRisk: perUnit, rawQty, qty, lots, capitalRequired, capitalPct, warnings };
}
