import { describe, expect, it } from 'vitest';
import { closedViews, computeStats, disciplineReport, equityCurve, evaluateViolations, groupStats, plannedRisk, positionSize, riskStatus, type ClosedView } from '../shared/journal';
import { S, trade } from './helpers';

const NOW = new Date('2026-10-12T08:00:00.000Z'); // 13:30 IST, Monday

const view = (net: number, over: Partial<ClosedView> = {}): ClosedView => ({
  trade: trade({ id: String(Math.random()) }), gross: net, charges: { brokerage: 0, stt: 0, exchange: 0, sebi: 0, stamp: 0, gst: 0, dp: 0, total: 0 },
  net, exitDate: '2026-10-12', entryDate: '2026-10-12', holdMinutes: 30, riskAmount: null, rMultiple: null, ...over,
});

describe('computeStats', () => {
  const vs = [view(100), view(-50), view(200), view(-100), view(-100), view(50)];
  const st = computeStats(vs, 100000);
  it('basic counts and rates', () => {
    expect(st.trades).toBe(6);
    expect(st.wins).toBe(3);
    expect(st.winRate).toBeCloseTo(0.5, 6);
    expect(st.netPnl).toBe(100);
  });
  it('averages, payoff, expectancy, profit factor', () => {
    expect(st.avgWin).toBeCloseTo(350 / 3, 6);
    expect(st.avgLoss).toBeCloseTo(250 / 3, 6);
    expect(st.payoffRatio).toBeCloseTo(350 / 250, 6);
    expect(st.expectancy).toBeCloseTo(0.5 * (350 / 3) - 0.5 * (250 / 3), 6);
    expect(st.profitFactor).toBeCloseTo(350 / 250, 6);
  });
  it('drawdown from the running equity peak', () => {
    // equity: 100100, 100050, 100250(peak), 100150, 100050, 100100 -> max dd 200
    expect(st.maxDrawdown).toBe(200);
    expect(st.maxDrawdownPct).toBeCloseTo((200 / 100250) * 100, 6);
  });
  it('streaks', () => {
    expect(st.longestLossStreak).toBe(2);
    expect(st.longestWinStreak).toBe(1);
    expect(st.currentStreak).toEqual({ kind: 'W', n: 1 });
  });
  it('handles an empty journal without NaN', () => {
    const e = computeStats([], 100000);
    expect(e.winRate).toBeNull();
    expect(e.expectancy).toBeNull();
    expect(e.maxDrawdown).toBe(0);
  });
  it('profit factor is Infinity with only winners', () => {
    expect(computeStats([view(10), view(20)], 1000).profitFactor).toBe(Infinity);
  });
});

describe('closedViews / R-multiples', () => {
  it('computes risk and R from the stop loss', () => {
    const t = trade({ id: 'r', entryPrice: 100, stopLoss: 98, qty: 100, exitPrice: 104, exitAt: '2026-10-12T06:00:00.000Z' });
    expect(plannedRisk(t)).toBe(200);
    const [v] = closedViews([t], S);
    expect(v!.rMultiple).toBeCloseTo(v!.net / 200, 6);
    expect(v!.rMultiple!).toBeGreaterThan(1.9);
  });
  it('rejects a stop on the wrong side as a risk figure', () => {
    expect(plannedRisk(trade({ id: 'w', entryPrice: 100, stopLoss: 105 }))).toBeNull();
    expect(plannedRisk(trade({ id: 'w2', side: 'SHORT', entryPrice: 100, stopLoss: 95 }))).toBeNull();
  });
  it('excludes open trades', () => expect(closedViews([trade({ id: 'o' })], S)).toHaveLength(0));
});

describe('equityCurve and groups', () => {
  it('aggregates per exit day', () => {
    const pts = equityCurve([view(100, { exitDate: '2026-10-01' }), view(-30, { exitDate: '2026-10-01' }), view(50, { exitDate: '2026-10-02' })], 1000);
    expect(pts).toEqual([
      { date: '2026-10-01', pnl: 70, cumulative: 70, equity: 1070 },
      { date: '2026-10-02', pnl: 50, cumulative: 120, equity: 1120 },
    ]);
  });
  it('groups by tag, supporting multi-tag trades', () => {
    const a = view(100, { trade: trade({ id: 'a', tags: ['breakout', 'fomo'] }) });
    const b = view(-40, { trade: trade({ id: 'b', tags: ['fomo'] }) });
    const g = groupStats([a, b], (v) => v.trade.tags);
    expect(g.find((x) => x.key === 'fomo')).toMatchObject({ trades: 2, net: 60 });
    expect(g.find((x) => x.key === 'breakout')).toMatchObject({ trades: 1, net: 100 });
  });
});

describe('violations', () => {
  it('flags a missing stop loss and oversized risk', () => {
    const noSl = trade({ id: 'n', stopLoss: null });
    const big = trade({ id: 'b', entryPrice: 100, stopLoss: 90, qty: 1000 }); // 10,000 risk > 5,000 limit
    const v = evaluateViolations([noSl, big], S);
    expect(v.get('n')).toContain('NO_STOP_LOSS');
    expect(v.get('b')).toContain('OVERSIZED_RISK');
  });
  it('flags overtrading beyond the daily cap', () => {
    const ts = Array.from({ length: 8 }, (_, i) => trade({ id: `t${i}`, stopLoss: 99.5, entryAt: `2026-10-12T0${3 + (i % 5)}:${10 + i}:00.000Z` }));
    const v = evaluateViolations(ts, { ...S, maxTradesPerDay: 6 });
    const over = ts.filter((t) => v.get(t.id)!.includes('OVERTRADING'));
    expect(over).toHaveLength(2);
  });
  it('flags entry after the daily loss limit and a revenge re-entry', () => {
    const loser = trade({ id: 'L', symbol: 'TCS', entryPrice: 100, exitPrice: 89, qty: 1000, stopLoss: 99, entryAt: '2026-10-12T03:50:00.000Z', exitAt: '2026-10-12T04:00:00.000Z' });
    const next = trade({ id: 'N', symbol: 'TCS', stopLoss: 99.5, entryAt: '2026-10-12T04:05:00.000Z' });
    const v = evaluateViolations([loser, next], S);
    expect(v.get('N')).toContain('TRADED_AFTER_LOSS_LIMIT');
    expect(v.get('N')).toContain('REVENGE_REENTRY');
  });
  it('flags heated entries and ignored plans', () => {
    const t = trade({ id: 'h', stopLoss: 99.9, emotion: 5, planFollowed: false });
    expect(evaluateViolations([t], S).get('h')).toEqual(expect.arrayContaining(['HEATED_ENTRY', 'PLAN_NOT_FOLLOWED']));
  });
  it('discipline score is the share of clean trades', () => {
    const clean = trade({ id: 'c', stopLoss: 99.9, entryAt: '2026-10-09T04:00:00.000Z' });
    const dirty = trade({ id: 'd', stopLoss: null, entryAt: '2026-10-09T05:00:00.000Z' });
    const r = disciplineReport([clean, dirty], S, NOW);
    expect(r.score).toBe(50);
    expect(r.byViolation[0]!.code).toBe('NO_STOP_LOSS');
  });
});

describe('riskStatus', () => {
  it('is OK with no activity', () => expect(riskStatus([], S, NOW).state).toBe('OK'));
  it('breaches when realized loss reaches the daily limit', () => {
    const t = trade({ id: 'x', entryPrice: 100, exitPrice: 89, qty: 1000, entryAt: '2026-10-12T04:00:00.000Z', exitAt: '2026-10-12T05:00:00.000Z' });
    const r = riskStatus([t], S, NOW);
    expect(r.state).toBe('BREACHED');
    expect(r.lossLimitUsedPct).toBeGreaterThanOrEqual(100);
    expect(r.messages[0]).toMatch(/loss limit/i);
  });
  it('warns at 70% usage', () => {
    const t = trade({ id: 'y', entryPrice: 100, exitPrice: 92.5, qty: 1000, entryAt: '2026-10-12T04:00:00.000Z', exitAt: '2026-10-12T05:00:00.000Z' });
    expect(riskStatus([t], S, NOW).state).toBe('WARN');
  });
  it('ignores yesterday\'s losses', () => {
    const t = trade({ id: 'z', entryPrice: 100, exitPrice: 80, qty: 1000, entryAt: '2026-10-09T04:00:00.000Z', exitAt: '2026-10-09T05:00:00.000Z' });
    expect(riskStatus([t], S, NOW).state).toBe('OK');
  });
  it('breaches on max trades', () => {
    const ts = Array.from({ length: 6 }, (_, i) => trade({ id: `m${i}`, entryAt: '2026-10-12T04:00:00.000Z', exitPrice: 100, exitAt: '2026-10-12T04:30:00.000Z' }));
    expect(riskStatus(ts, S, NOW).state).toBe('BREACHED');
  });
});

describe('positionSize', () => {
  it('sizes to the risk budget', () => {
    const r = positionSize({ capital: 500000, riskPct: 1, entry: 100, stop: 98 });
    expect(r.riskAmount).toBe(5000);
    expect(r.qty).toBe(2500);
    expect(r.capitalRequired).toBe(250000);
  });
  it('rounds down to whole lots and warns when one lot is too big', () => {
    expect(positionSize({ capital: 500000, riskPct: 1, entry: 100, stop: 98, lotSize: 75 })).toMatchObject({ lots: 33, qty: 2475 });
    const big = positionSize({ capital: 100000, riskPct: 0.5, entry: 200, stop: 100, lotSize: 75 });
    expect(big.lots).toBe(0);
    expect(big.warnings.join(' ')).toMatch(/One lot/);
  });
  it('refuses a zero-distance stop', () => expect(positionSize({ capital: 1000, riskPct: 1, entry: 5, stop: 5 }).qty).toBe(0));
});
