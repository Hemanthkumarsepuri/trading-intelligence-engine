import { describe, expect, it } from 'vitest';
import { analyze, atr, classicPivots, ema, rsi, sma, swingLevels } from '../shared/indicators';
import { candles } from './helpers';

describe('moving averages and oscillators', () => {
  it('sma', () => expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]));
  it('ema seeds with the SMA', () => {
    const e = ema([1, 2, 3, 4, 5], 3);
    expect(e[2]).toBe(2);
    expect(e[3]).toBeCloseTo(3, 6); // k=0.5: 4*.5+2*.5
    expect(e[4]).toBeCloseTo(4, 6);
  });
  it('rsi is 100 on a monotonically rising series and 0 on a falling one', () => {
    const up = Array.from({ length: 30 }, (_, i) => 100 + i);
    const dn = Array.from({ length: 30 }, (_, i) => 100 - i);
    expect(rsi(up).at(-1)).toBe(100);
    expect(rsi(dn).at(-1)).toBeCloseTo(0, 6);
  });
  it('rsi is near 50 for alternating moves', () => {
    const alt = Array.from({ length: 60 }, (_, i) => 100 + (i % 2));
    expect(rsi(alt).at(-1)!).toBeGreaterThan(40);
    expect(rsi(alt).at(-1)!).toBeLessThan(60);
  });
  it('atr of constant 2-point ranges is 2', () => {
    expect(atr(candles(30, () => ({ h: 101, l: 99, c: 100 }))).at(-1)).toBeCloseTo(2, 6);
  });
});

describe('levels', () => {
  it('classic pivots', () => {
    expect(classicPivots(110, 90, 100)).toEqual({ pivot: 100, r1: 110, s1: 90, r2: 120, s2: 80 });
  });
  it('finds swing high and low', () => {
    const cs = candles(14, (i) => (i === 5 ? { h: 120, c: 110 } : i === 8 ? { l: 80, c: 90 } : {}));
    const sw = swingLevels(cs, 3);
    expect(sw.highs).toContain(120);
    expect(sw.lows).toContain(80);
  });
});

describe('analyze', () => {
  it('flags a 20-day breakout only when volume confirms', () => {
    const base = candles(40);
    const withVol = [...base, { t: base.at(-1)!.t + 86400, o: 101, h: 112, l: 101, c: 111, v: 3000 }];
    const noVol = [...base, { t: base.at(-1)!.t + 86400, o: 101, h: 112, l: 101, c: 111, v: 1000 }];
    expect(analyze(withVol)!.observations).toContain('BREAKOUT_20D_WITH_VOLUME');
    expect(analyze(withVol)!.observations).toContain('VOLUME_SURGE');
    expect(analyze(noVol)!.observations).not.toContain('BREAKOUT_20D_WITH_VOLUME');
  });
  it('measures the breakout against prior bars, never against itself', () => {
    const base = candles(40);
    const flat = [...base, { ...base.at(-1)!, t: base.at(-1)!.t + 86400, v: 5000 }];
    expect(analyze(flat)!.observations).not.toContain('BREAKOUT_20D_WITH_VOLUME');
  });
  it('detects gap up and near-52w-high', () => {
    const cs = candles(70, (i) => ({ c: 100 + i * 0.1, h: 100.5 + i * 0.1, l: 99.5 + i * 0.1, o: 100 + i * 0.1 }));
    const last = cs.at(-1)!;
    cs.push({ t: last.t + 86400, o: last.c * 1.02, h: last.c * 1.03, l: last.c * 1.015, c: last.c * 1.025, v: 1000 });
    const a = analyze(cs)!;
    expect(a.observations).toContain('GAP_UP');
    expect(a.observations).toContain('NEAR_52W_HIGH');
    expect(a.pos52w).toBeGreaterThan(90);
  });
  it('returns null on too little data and never throws on short series', () => {
    expect(analyze([])).toBeNull();
    expect(analyze(candles(3))).not.toBeNull();
  });
});
