import { describe, expect, it } from 'vitest';
import { parseHoldingsCsv, summarizePortfolio, viewHolding } from '../shared/portfolio';
import { quote } from './helpers';

const TODAY = '2026-10-12';
const h = (id: string, symbol: string, qty: number, avgPrice: number, buyDate: string) => ({ id, symbol, qty, avgPrice, buyDate, notes: '' });

describe('viewHolding', () => {
  it('values at live price and computes day P&L from the previous close', () => {
    const v = viewHolding(h('1', 'TCS', 10, 3000, '2024-01-01'), quote('TCS', 3300, 3200, '2026-10-12T05:00:00Z'), 'Information Technology', TODAY);
    expect(v.value).toBe(33000);
    expect(v.pnl).toBe(3000);
    expect(v.pnlPct).toBeCloseTo(10, 6);
    expect(v.dayPnl).toBe(1000);
    expect(v.longTerm).toBe(true);
    expect(v.sector).toBe('Information Technology');
  });
  it('counts days until a holding turns long term', () => {
    const v = viewHolding(h('2', 'INFY', 5, 1500, '2025-10-20'), undefined, undefined, TODAY);
    expect(v.longTerm).toBe(false);
    expect(v.daysToLongTerm).toBe(9); // long term from 2026-10-21
    expect(v.value).toBeNull();
  });
  it('leaves tax class unknown when the buy date is missing', () => {
    expect(viewHolding(h('3', 'ITC', 1, 1, ''), undefined, undefined, TODAY).longTerm).toBeNull();
  });
});

describe('summarizePortfolio', () => {
  const views = [
    viewHolding(h('1', 'TCS', 10, 3000, '2024-01-01'), quote('TCS', 3300, 3200, 'x'), 'IT', TODAY),
    viewHolding(h('2', 'INFY', 100, 1500, '2025-10-20'), quote('INFY', 1400, 1450, 'x'), 'IT', TODAY),
    viewHolding(h('3', 'HDFCBANK', 10, 1000, '2026-01-01'), undefined, 'Financial Services', TODAY),
  ];
  const s = summarizePortfolio(views);
  it('totals and day P&L', () => {
    expect(s.invested).toBe(30000 + 150000 + 10000);
    expect(s.value).toBe(33000 + 140000 + 10000); // unpriced holding at cost
    expect(s.dayPnl).toBe(1000 - 5000);
    expect(s.unpriced).toBe(1);
  });
  it('splits unrealized gains by tax class', () => {
    expect(s.unrealizedLtcg).toBe(3000);
    expect(s.unrealizedStcg).toBe(-10000);
    expect(s.ltcgHeadroom).toBe(125000 - 3000);
  });
  it('warns about concentration and missing prices', () => {
    expect(s.warnings.join(' ')).toMatch(/INFY is 7\d% of the portfolio/);
    expect(s.warnings.join(' ')).toMatch(/no live price/);
  });
  it('sector allocation sums to ~100%', () => {
    expect(s.bySector.reduce((a, x) => a + x.pct, 0)).toBeCloseTo(100, 6);
  });
});

describe('parseHoldingsCsv', () => {
  it('reads a Zerodha holdings export', () => {
    const csv = 'Instrument,Qty.,Avg. cost,LTP,Invested,Cur. val,P&L,Net chg.,Day chg.\nRELIANCE,10,"1,250.50",1300,12505,13000,495,3.9,0.5\nTCS-EQ,5,3000,3100,15000,15500,500,3.3,0.2\n';
    const r = parseHoldingsCsv(csv);
    expect(r.errors).toEqual([]);
    expect(r.rows).toEqual([
      { symbol: 'RELIANCE', qty: 10, avgPrice: 1250.5, buyDate: '' },
      { symbol: 'TCS', qty: 5, avgPrice: 3000, buyDate: '' },
    ]);
  });
  it('reads dd-mm-yyyy dates and skips bad rows with a message', () => {
    const csv = 'symbol,quantity,avg price,buy date\nINFY,10,1500,15-03-2024\nBAD,0,100,\n';
    const r = parseHoldingsCsv(csv);
    expect(r.rows).toEqual([{ symbol: 'INFY', qty: 10, avgPrice: 1500, buyDate: '2024-03-15' }]);
    expect(r.errors[0]).toMatch(/BAD/);
  });
  it('explains an unrecognised layout', () => {
    expect(parseHoldingsCsv('a,b\n1,2\n').errors[0]).toMatch(/Could not find columns/);
  });
});
