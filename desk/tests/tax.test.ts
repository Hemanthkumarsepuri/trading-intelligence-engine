import { describe, expect, it } from 'vitest';
import { tradeEconomics } from '../shared/charges';
import { summarizeTax } from '../shared/tax';
import { S, trade } from './helpers';

const delivery = (id: string, entry: string, exit: string, p0: number, p1: number, qty = 1000) =>
  trade({ id, segment: 'EQ_DELIVERY', qty, entryPrice: p0, exitPrice: p1, entryAt: `${entry}T04:00:00.000Z`, exitAt: `${exit}T06:00:00.000Z` });

const gainExStt = (t: ReturnType<typeof trade>) => { const e = tradeEconomics(t, S); return e.gross - (e.charges.total - e.charges.stt); };

describe('capital gains', () => {
  it('long-term gain: 12.5% above the INR 1.25 lakh exemption, plus 4% cess', () => {
    const t = delivery('l', '2024-01-10', '2026-06-10', 1000, 1300); // ~3 lakh
    const r = summarizeTax([t], S, '2026-27');
    const g = gainExStt(t);
    expect(r.ltcgGross).toBeCloseTo(g, -1);
    expect(r.ltcgTaxable).toBeCloseTo(g - 125000, -1);
    expect(r.capitalGainsTax).toBeCloseTo((g - 125000) * 0.125 * 1.04, -1);
    expect(r.stcgTaxable).toBe(0);
  });
  it('short-term gain taxed at 20%', () => {
    const t = delivery('s', '2026-05-01', '2026-06-10', 1000, 1100); // 1 lakh
    const r = summarizeTax([t], S, '2026-27');
    expect(r.stcgTaxable).toBeCloseTo(gainExStt(t), -1);
    expect(r.capitalGainsTax).toBeCloseTo(gainExStt(t) * 0.2 * 1.04, -1);
  });
  it('short-term loss sets off against long-term gain', () => {
    const lt = delivery('l', '2024-01-10', '2026-06-10', 1000, 1300);
    const st = delivery('s', '2026-05-01', '2026-06-10', 1000, 950); // -50k
    const r = summarizeTax([lt, st], S, '2026-27');
    expect(r.stclCarryForward).toBe(0);
    expect(r.ltcgNet).toBeCloseTo(gainExStt(lt) + gainExStt(st), -1);
  });
  it('long-term loss does NOT set off against short-term gain and is carried forward', () => {
    const lt = delivery('l', '2024-01-10', '2026-06-10', 1000, 800); // -2 lakh LT
    const st = delivery('s', '2026-05-01', '2026-06-10', 1000, 1100);
    const r = summarizeTax([lt, st], S, '2026-27');
    expect(r.ltclCarryForward).toBeGreaterThan(190000);
    expect(r.stcgTaxable).toBeGreaterThan(90000);
  });
  it('ignores trades from another financial year', () => {
    const t = delivery('x', '2025-05-01', '2026-01-10', 1000, 1100);
    expect(summarizeTax([t], S, '2026-27').stcgGross).toBe(0);
    expect(summarizeTax([t], S, '2025-26').stcgGross).toBeGreaterThan(0);
  });
});

describe('business income', () => {
  it('F&O is non-speculative and builds turnover from absolute results', () => {
    const win = trade({ id: 'f1', segment: 'FUT', qty: 75, entryPrice: 25000, exitPrice: 25100, exitAt: '2026-08-01T06:00:00.000Z', entryAt: '2026-08-01T04:00:00.000Z' });
    const loss = trade({ id: 'f2', segment: 'FUT', qty: 75, entryPrice: 25000, exitPrice: 24950, exitAt: '2026-08-02T06:00:00.000Z', entryAt: '2026-08-02T04:00:00.000Z' });
    const r = summarizeTax([win, loss], S, '2026-27');
    expect(r.nonSpeculative.trades).toBe(2);
    expect(r.nonSpeculative.futuresTurnover).toBe(7500 + 3750);
    expect(r.speculative.trades).toBe(0);
  });
  it('intraday equity is speculative', () => {
    const t = trade({ id: 'i', entryPrice: 100, exitPrice: 102, exitAt: '2026-08-01T06:00:00.000Z' });
    expect(summarizeTax([t], S, '2026-27').speculative.trades).toBe(1);
  });
  it('option turnover includes premium received on the sell leg', () => {
    const t = trade({ id: 'o', segment: 'OPT', side: 'SHORT', qty: 75, entryPrice: 100, exitPrice: 60, exitAt: '2026-08-01T06:00:00.000Z' });
    const r = summarizeTax([t], S, '2026-27');
    expect(r.nonSpeculative.optionsTurnover).toBe(40 * 75 + 100 * 75);
  });
});
