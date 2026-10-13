import { describe, expect, it } from 'vitest';
import { breakEvenPoints, legCharges, tradeEconomics } from '../shared/charges';
import { S, trade } from './helpers';

describe('legCharges', () => {
  it('delivery buy of INR 1,00,000: STT 0.1%, stamp 0.015%, no brokerage', () => {
    const c = legCharges('EQ_DELIVERY', 'BUY', 1000, 100, S);
    expect(c.brokerage).toBe(0);
    expect(c.stt).toBe(100);
    expect(c.stamp).toBe(15);
    expect(c.exchange).toBeCloseTo(3.07, 2);
    expect(c.dp).toBe(0);
    expect(c.total).toBeCloseTo(118.74, 2);
  });
  it('delivery sell adds the DP charge and no stamp duty', () => {
    const c = legCharges('EQ_DELIVERY', 'SELL', 1000, 100, S);
    expect(c.dp).toBe(15.34);
    expect(c.stamp).toBe(0);
    expect(c.stt).toBe(100);
  });
  it('options sell uses 0.15% STT on premium (Budget 2026) and flat brokerage', () => {
    const c = legCharges('OPT', 'SELL', 100, 75, S);
    expect(c.brokerage).toBe(20);
    expect(c.stt).toBeCloseTo(11.25, 2);
    expect(c.exchange).toBeCloseTo(2.66, 2);
    expect(c.total).toBeCloseTo(38.0, 1);
  });
  it('options buy has no STT', () => {
    expect(legCharges('OPT', 'BUY', 100, 75, S).stt).toBe(0);
  });
  it('futures sell uses 0.05% STT', () => {
    const c = legCharges('FUT', 'SELL', 25000, 75, S); // turnover 18,75,000
    expect(c.stt).toBeCloseTo(937.5, 2);
    expect(c.brokerage).toBe(20); // capped
  });
  it('intraday brokerage is the lower of 0.03% and the cap', () => {
    expect(legCharges('EQ_INTRADAY', 'BUY', 100, 100, S).brokerage).toBe(3); // 0.03% of 10,000
    expect(legCharges('EQ_INTRADAY', 'BUY', 1000, 1000, S).brokerage).toBe(20);
  });
});

describe('tradeEconomics', () => {
  it('long: net = gross - round-trip charges', () => {
    const t = trade({ id: 'a', entryPrice: 100, exitPrice: 105, qty: 200, exitAt: '2026-10-12T06:00:00.000Z' });
    const e = tradeEconomics(t, S);
    expect(e.gross).toBe(1000);
    const expected = legCharges('EQ_INTRADAY', 'BUY', 100, 200, S).total + legCharges('EQ_INTRADAY', 'SELL', 105, 200, S).total;
    expect(e.charges.total).toBeCloseTo(expected, 2);
    expect(e.net).toBeCloseTo(1000 - expected, 2);
    expect(e.closed).toBe(true);
  });
  it('short: profit when price falls, STT lands on the entry (sell) leg', () => {
    const t = trade({ id: 'b', side: 'SHORT', entryPrice: 100, exitPrice: 95, qty: 100, exitAt: '2026-10-12T06:00:00.000Z' });
    const e = tradeEconomics(t, S);
    expect(e.gross).toBe(500);
    expect(e.charges.stt).toBeCloseTo(2.5, 2); // 0.025% of 10,000 on the sell (entry)
  });
  it('open trade reports entry-leg charges only and zero P&L', () => {
    const e = tradeEconomics(trade({ id: 'c' }), S);
    expect(e.closed).toBe(false);
    expect(e.net).toBe(0);
    expect(e.charges.total).toBeGreaterThan(0);
  });
  it('break-even is positive and small relative to price', () => {
    const be = breakEvenPoints('OPT', 'LONG', 100, 75, S);
    expect(be).toBeGreaterThan(0.4);
    expect(be).toBeLessThan(2);
  });
});
