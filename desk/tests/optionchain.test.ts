import { describe, expect, it } from 'vitest';
import { analyzeChain, buildup, inferStrikeStep, maxPain, type ChainRow, type NormalizedChain, type OptionLeg } from '../shared/optionchain';

const leg = (o: Partial<OptionLeg> = {}): OptionLeg => ({ ltp: null, oi: null, prevOi: null, volume: null, iv: null, bid: null, ask: null, ...o });
const row = (strike: number, ceOi: number, peOi: number, extra: { ce?: Partial<OptionLeg>; pe?: Partial<OptionLeg> } = {}): ChainRow => ({
  strike, ce: leg({ oi: ceOi, ...extra.ce }), pe: leg({ oi: peOi, ...extra.pe }),
});

describe('max pain', () => {
  it('picks the strike with the least aggregate payout', () => {
    expect(maxPain([row(100, 100, 50), row(110, 200, 200), row(120, 50, 100)])).toBe(110);
  });
  it('is null with no open interest', () => expect(maxPain([row(100, 0, 0)])).toBeNull());
});

describe('strike step', () => {
  it('uses the most common gap', () => expect(inferStrikeStep([100, 150, 200, 250, 400])).toBe(50));
});

describe('analyzeChain', () => {
  const chain: NormalizedChain = {
    underlying: 'NIFTY', expiry: '2026-10-20', spot: 25012, source: 'fixture', fetchedAt: '2026-10-12T05:00:00Z',
    rows: [
      row(24900, 100000, 400000, { ce: { prevOi: 90000, ltp: 160 }, pe: { prevOi: 350000, ltp: 45, iv: 14 } }),
      row(25000, 300000, 300000, { ce: { prevOi: 250000, ltp: 90, iv: 12.5, volume: 1000 }, pe: { prevOi: 240000, ltp: 80, iv: 13.5, volume: 1500 } }),
      row(25100, 500000, 100000, { ce: { prevOi: 400000, ltp: 40, iv: 12 }, pe: { prevOi: 100000, ltp: 150 } }),
    ],
  };
  const a = analyzeChain(chain);
  it('computes PCR from OI', () => {
    expect(a.totalCallOi).toBe(900000);
    expect(a.totalPutOi).toBe(800000);
    expect(a.pcrOi).toBeCloseTo(800000 / 900000, 6);
    // volume is missing on two strikes: unknown, not zero
    expect(a.pcrVolume).toBeNull();
  });
  it('finds ATM, straddle and implied move', () => {
    expect(a.atmStrike).toBe(25000);
    expect(a.atmStraddle).toBe(170);
    expect(a.impliedMovePct).toBeCloseTo((170 / 25012) * 100, 6);
    expect(a.impliedRange).toEqual({ low: 25012 - 170, high: 25012 + 170 });
    expect(a.atmIv).toBeCloseTo(13, 6);
  });
  it('ranks call and put walls by OI', () => {
    expect(a.callWalls[0]).toMatchObject({ strike: 25100, oi: 500000 });
    expect(a.putWalls[0]).toMatchObject({ strike: 24900, oi: 400000 });
  });
  it('finds where fresh writing is concentrated', () => {
    expect(a.freshCallWriting[0]).toMatchObject({ strike: 25100, oiChange: 100000 });
    expect(a.freshPutWriting[0]).toMatchObject({ strike: 25000, oiChange: 60000 });
    expect(a.freshPutWriting[1]).toMatchObject({ strike: 24900, oiChange: 50000 });
  });
  it('does not invent an implied move when premiums are missing', () => {
    const closed = analyzeChain({ ...chain, rows: chain.rows.map((r) => ({ ...r, ce: { ...r.ce, ltp: null }, pe: { ...r.pe, ltp: null } })) });
    expect(closed.atmStraddle).toBeNull();
    expect(closed.impliedRange).toBeNull();
    expect(closed.notes.join(' ')).toMatch(/unavailable/);
  });
});

describe('buildup', () => {
  it('classifies the four price/OI quadrants', () => {
    expect(buildup(1, 5)).toBe('LONG_BUILDUP');
    expect(buildup(-1, 5)).toBe('SHORT_BUILDUP');
    expect(buildup(1, -5)).toBe('SHORT_COVERING');
    expect(buildup(-1, -5)).toBe('LONG_UNWINDING');
    expect(buildup(null, 5)).toBe('NEUTRAL');
    expect(buildup(0.01, 5)).toBe('NEUTRAL');
  });
});

describe('missing data is never treated as zero', () => {
  const base = (rows: ChainRow[]): NormalizedChain => ({ underlying: 'NIFTY', expiry: '2026-10-20', spot: 25000, source: 'fixture', fetchedAt: '2026-10-12T05:00:00Z', rows });
  it('withholds PCR, max pain and totals when any strike lacks OI', () => {
    const rows: ChainRow[] = [row(24900, 100000, 400000), row(25000, 300000, 300000), { strike: 25100, ce: leg({ oi: null }), pe: leg({ oi: 100000 }) }];
    const a = analyzeChain(base(rows));
    expect(a.strikesMissingOi).toBe(1);
    expect(a.totalCallOi).toBeNull();
    expect(a.pcrOi).toBeNull();
    expect(a.maxPain).toBeNull();
    expect(a.pcrRead.label).toBe('Unavailable');
    expect(a.notes.join(' ')).toMatch(/never treated as zero/);
    // walls rank only strikes with data; the missing strike is not ranked as an empty strike
    expect(a.callWalls.map((w) => w.strike)).toEqual([25000, 24900]);
  });
  it('reports OI change as unknown when previous-day OI is missing', () => {
    const a = analyzeChain(base([row(25000, 300000, 300000, { ce: { prevOi: 250000 } })]));
    expect(a.totalCallOiChange).toBe(50000);
    expect(a.totalPutOiChange).toBeNull();
    expect(a.freshPutWriting).toEqual([]);
  });
  it('computes volume PCR only when every strike reports volume', () => {
    const a = analyzeChain(base([row(25000, 1, 1, { ce: { volume: 200 }, pe: { volume: 100 } }), row(25050, 1, 1, { ce: { volume: 100 }, pe: { volume: 50 } })]));
    expect(a.pcrVolume).toBeCloseTo(0.5, 6);
  });
  it('keeps PCR wording descriptive, never directional', () => {
    const a = analyzeChain(base([row(25000, 100, 300)]));
    expect(a.pcrRead.label).not.toMatch(/bull|bear|support|buy|sell/i);
    expect(a.pcrRead.detail).toMatch(/not a directional signal/);
  });
});
