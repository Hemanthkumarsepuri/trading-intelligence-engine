// Assembles the journal screen from pure analytics. No I/O beyond reading data.

import { financialYear } from '../../shared/india';
import {
  closedViews,
  computeStats,
  disciplineReport,
  equityCurve,
  evaluateViolations,
  groupStats,
  riskStatus,
  weekdayOf,
  type ClosedView,
  type DisciplineReport,
  type EquityPoint,
  type GroupStat,
  type JournalStats,
  type RiskStatus,
  type ViolationCode,
} from '../../shared/journal';
import { fiscalYearsWithTrades, summarizeTax, type TaxSummary } from '../../shared/tax';
import type { Trade } from '../../shared/types';
import type { Ctx } from '../config';
import { getSettings, listTrades } from './userdata';

export interface TradeRow {
  trade: Trade;
  net: number | null;
  gross: number | null;
  charges: number | null;
  rMultiple: number | null;
  violations: ViolationCode[];
}

export interface JournalData {
  rows: TradeRow[];
  stats: JournalStats;
  equity: EquityPoint[];
  groups: { setup: GroupStat[]; segment: GroupStat[]; weekday: GroupStat[]; tag: GroupStat[]; emotion: GroupStat[] };
  discipline: DisciplineReport;
  risk: RiskStatus;
  tax: TaxSummary;
  fy: string;
  fys: string[];
  capital: number;
}

export async function getJournal(ctx: Ctx, fyParam?: string): Promise<JournalData> {
  const now = ctx.now();
  const [trades, settings] = await Promise.all([listTrades(ctx), getSettings(ctx)]);
  const views = closedViews(trades, settings);
  const byId = new Map<string, ClosedView>(views.map((v) => [v.trade.id, v]));
  const violations = evaluateViolations(trades, settings);
  const fy = fyParam && /^\d{4}-\d{2}$/.test(fyParam) ? fyParam : financialYear(now.toISOString().slice(0, 10));

  const rows: TradeRow[] = trades.map((t) => {
    const v = byId.get(t.id);
    return { trade: t, net: v?.net ?? null, gross: v?.gross ?? null, charges: v?.charges.total ?? null, rMultiple: v?.rMultiple ?? null, violations: violations.get(t.id) ?? [] };
  });

  return {
    rows,
    stats: computeStats(views, settings.capital),
    equity: equityCurve(views, settings.capital),
    groups: {
      setup: groupStats(views, (v) => v.trade.setup || '(no setup)'),
      segment: groupStats(views, (v) => v.trade.segment),
      weekday: groupStats(views, (v) => weekdayOf(v.entryDate)),
      tag: groupStats(views, (v) => v.trade.tags),
      emotion: groupStats(views, (v) => (v.trade.emotion === null ? '' : String(v.trade.emotion))),
    },
    discipline: disciplineReport(trades, settings, now),
    risk: riskStatus(trades, settings, now),
    tax: summarizeTax(trades, settings, fy),
    fy,
    fys: fiscalYearsWithTrades(trades, now),
    capital: settings.capital,
  };
}
