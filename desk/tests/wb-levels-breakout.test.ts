import { describe, expect, it } from 'vitest';
import { evaluateBreakout, evaluateStructure, type BreakoutContext } from '../shared/breakout';
import { aggregate, type TfCandle } from '../shared/candles';
import { fromIst } from '../shared/india';
import { barRvol, compression, computeLevels, cumulativeRvol, gapRead, mtfAgreement, nearest, pivots, sessionVwap, trendRead, vwapRead, type Level } from '../shared/levels';
import { bars, epoch, sessionBars } from './wbhelpers';

const lvl = (price: number, kind: 'RESISTANCE' | 'SUPPORT' = 'RESISTANCE', touches = 3): Level => ({ id: `t:${kind}:${price}`, price, low: price, high: price, kind, method: 'SWING_CLUSTER', tf: '15m', touches, from: 0, to: 0, explain: 'test' });
const ok: BreakoutContext = { freshness: 'VALID' };

/** 40 quiet bars (range 2, ATR ~2) below a resistance at 110, then the tail supplied by `tail`. */
function series(tail: Partial<TfCandle>[]): TfCandle[] {
  const base = bars(40);
  return [...base, ...bars(tail.length, (i) => tail[i]!, base[base.length - 1]!.t + 900)];
}
const up = (c: number, v = 1000, extra: Partial<TfCandle> = {}): Partial<TfCandle> => ({ o: c - 0.5, h: c + 0.7, l: c - 0.8, c, v, ...extra });

describe('breakout lifecycle (up)', () => {
  it('NOT_READY when price is far from the level', () => {
    const s = evaluateBreakout(series([up(104)]), lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('NOT_READY');
  });

  it('APPROACHING_LEVEL within 0.5×ATR, without any completed close beyond', () => {
    const s = evaluateBreakout(series([up(109.3, 1000, { h: 109.8 })]), lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('APPROACHING_LEVEL');
    expect(s.distance).toBeGreaterThan(0);
  });

  it('a single live tick through the line is only POTENTIAL_BREAKOUT, never confirmed', () => {
    const s = evaluateBreakout(series([up(109)]), lvl(110), 'UP', '15m', { ...ok, livePrice: 111.5 });
    expect(s.state).toBe('POTENTIAL_BREAKOUT');
    expect(s.confirmation).toMatch(/completed 15 min close above/);
  });

  it('the forming candle crossing the level is POTENTIAL, and does not count as a closed candle', () => {
    const c = series([up(109)]);
    c.push({ t: c[c.length - 1]!.t + 900, o: 109, h: 111, l: 108.9, c: 110.8, v: 5000, complete: false, have: 1, want: 3 });
    const s = evaluateBreakout(c, lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('POTENTIAL_BREAKOUT');
    expect(s.closesBeyond).toBe(0);
  });

  it('one close beyond with high volume but no hold is CONFIRMATION_PENDING', () => {
    const s = evaluateBreakout(series([up(111, 3000)]), lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('CONFIRMATION_PENDING');
    expect(s.closesBeyond).toBe(1);
    expect(s.contradicting.join(' ')).toMatch(/required/);
  });

  it('two closes beyond on expanded volume is CONFIRMED with evidence listed', () => {
    const s = evaluateBreakout(series([up(111, 3000), up(111.6, 1200)]), lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('CONFIRMED');
    expect(s.closesBeyond).toBe(2);
    expect(s.rvol?.value).toBeGreaterThanOrEqual(1.5);
    expect(s.supporting.join(' ')).toMatch(/Breakout-bar volume/);
    expect(s.invalidation).toMatch(/FAILED/);
    expect(s.rules.length).toBeGreaterThan(3);
  });

  it('two closes beyond on ordinary volume is not confirmed', () => {
    const s = evaluateBreakout(series([up(111, 1000), up(111.6, 1000)]), lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('CONFIRMATION_PENDING');
    expect(s.contradicting.join(' ')).toMatch(/Breakout-bar volume 1\.0\d× .*< 1\.5/);
  });

  it('a held retest can substitute for a second consecutive close', () => {
    const s = evaluateBreakout(series([up(111, 3000), up(111.4, 1200), up(112.2, 1100, { l: 110.2 }), up(112)]), lvl(110), 'UP', '15m', ok);
    expect(['CONFIRMED']).toContain(s.state);
    expect(s.retestHeld).toBe(true);
  });

  it('unknown breakout-bar volume blocks confirmation and says why', () => {
    const s = evaluateBreakout(series([up(111, 3000, { v: null }), up(111.6, 1200)]), lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('CONFIRMATION_PENDING');
    expect(s.missing.join(' ')).toMatch(/volume ratio unavailable/i);
  });

  it('insufficient volume history blocks confirmation', () => {
    const c = bars(40, () => ({ v: null }));
    const t = [up(111, 3000), up(111.6, 1200)];
    const all = [...c, ...bars(2, (i) => t[i]!, c[39]!.t + 900)];
    const s = evaluateBreakout(all, lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('CONFIRMATION_PENDING');
  });

  it('STALE data never confirms: the verdict is STALE and explains itself', () => {
    const s = evaluateBreakout(series([up(111, 3000), up(111.6, 1200)]), lvl(110), 'UP', '15m', { freshness: 'STALE' });
    expect(s.state).toBe('STALE');
    expect(s.missing.join(' ')).toMatch(/older than the freshness limit/);
  });

  it('data with an unverifiable timestamp caps at CONFIRMATION_PENDING', () => {
    const s = evaluateBreakout(series([up(111, 3000), up(111.6, 1200)]), lvl(110), 'UP', '15m', { freshness: 'UNKNOWN' });
    expect(s.state).toBe('CONFIRMATION_PENDING');
  });

  it('closed-market data may still be CONFIRMED (the session is final)', () => {
    const s = evaluateBreakout(series([up(111, 3000), up(111.6, 1200)]), lvl(110), 'UP', '15m', { freshness: 'MARKET_CLOSED' });
    expect(s.state).toBe('CONFIRMED');
  });

  it('conflicting inputs assert no state', () => {
    const s = evaluateBreakout(series([up(111, 3000), up(111.6, 1200)]), lvl(110), 'UP', '15m', { freshness: 'CONFLICT' });
    expect(s.state).toBe('DATA_INSUFFICIENT');
  });

  it('too few bars is DATA_INSUFFICIENT', () => {
    expect(evaluateBreakout(bars(12), lvl(110), 'UP', '15m', ok).state).toBe('DATA_INSUFFICIENT');
  });

  it('a close back through the level shortly after the break is FAILED', () => {
    const s = evaluateBreakout(series([up(111, 3000), up(109.4, 1500)]), lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('FAILED');
  });

  it('a close more than 1×ATR on the wrong side after a break is INVALIDATED', () => {
    const s = evaluateBreakout(series([up(111, 3000), up(106.5, 1500)]), lvl(110), 'UP', '15m', ok);
    expect(s.state).toBe('INVALIDATED');
  });

  it('flags a thin opposing room and an opposing higher-timeframe trend', () => {
    const s = evaluateBreakout(series([up(111, 3000), up(111.6, 1200)]), lvl(110), 'UP', '15m', { ...ok, opposing: lvl(111.5), htfTrend: { tf: '1d', trend: 'DOWN' } });
    expect(s.contradicting.join(' ')).toMatch(/room before the next/);
    expect(s.contradicting.join(' ')).toMatch(/Daily trend is DOWN/);
  });
});

describe('breakdown mirrors breakout', () => {
  const dn = (c: number, v = 1000): Partial<TfCandle> => ({ o: c + 0.5, h: c + 0.8, l: c - 0.7, c, v });
  const base = (): TfCandle[] => bars(40, () => ({ o: 106, h: 107, l: 105, c: 106 }));
  const mk = (tail: Partial<TfCandle>[]) => [...base(), ...bars(tail.length, (i) => tail[i]!, base()[39]!.t + 900)];
  it('confirms a breakdown below support on volume with two closes', () => {
    const s = evaluateBreakout(mk([dn(99, 3000), dn(98.4, 1200)]), lvl(100, 'SUPPORT'), 'DOWN', '15m', ok);
    expect(s.state).toBe('CONFIRMED');
    expect(s.direction).toBe('DOWN');
    expect(s.confirmation).toMatch(/below/);
  });
  it('a live tick below support is only POTENTIAL', () => {
    const s = evaluateBreakout(mk([dn(105)]), lvl(100, 'SUPPORT'), 'DOWN', '15m', { ...ok, livePrice: 99.2 });
    expect(s.state).toBe('POTENTIAL_BREAKOUT');
  });
});

describe('structure scan', () => {
  it('returns setups best-first for levels near price', () => {
    const c = series([up(111, 3000), up(111.6, 1200)]);
    const r = evaluateStructure(c, [lvl(110), lvl(112.9), lvl(108, 'SUPPORT')], '15m', ok);
    expect(r.primary?.state).toBe('CONFIRMED');
    expect(r.setups.length).toBe(3);
  });
});

describe('levels', () => {
  it('clusters repeated swing highs into one resistance zone with the touch count', () => {
    const c = bars(60, (i) => {
      const peak = [10, 25, 40].includes(i);
      return peak ? { h: 110, c: 108, o: 106 } : { h: 105 + (i % 3) * 0.2 };
    });
    const lv = computeLevels(c, '15m').filter((l) => l.method === 'SWING_CLUSTER' && l.kind === 'RESISTANCE');
    const top = lv.find((l) => Math.abs(l.price - 110) < 0.5)!;
    expect(top).toBeDefined();
    expect(top.touches).toBe(3);
    expect(top.explain).toMatch(/3 confirmed swing highs/);
  });

  it('never makes a pivot of the newest bars (no look-ahead)', () => {
    const c = bars(20, (i) => (i === 19 ? { h: 130 } : {}));
    expect(pivots(c, 3, 100).highs.find((p) => p.i === 19)).toBeUndefined();
  });

  it('previous-session and opening-range levels for intraday series', () => {
    const d1 = sessionBars('2026-10-13', 15, 25, (i) => ({ h: 102 + (i === 5 ? 6 : 0), l: 98 - (i === 9 ? 4 : 0), v: 100 }));
    const d2 = sessionBars('2026-10-14', 15, 25, () => ({ h: 101, l: 99, v: 100 }));
    const done = aggregate([...d1, ...d2], 15, 15, fromIst('2026-10-14', 16, 0));
    const lv = computeLevels(done, '15m');
    expect(lv.find((l) => l.method === 'PREV_SESSION_HIGH')?.price).toBe(108);
    expect(lv.find((l) => l.method === 'PREV_SESSION_LOW')?.price).toBe(94);
    expect(lv.find((l) => l.method === 'OPENING_RANGE_HIGH')?.price).toBe(101);
    expect(computeLevels(done, '1d').some((l) => l.method === 'PREV_SESSION_HIGH')).toBe(false);
  });

  it('nearest() returns the closest level on each side', () => {
    const n = nearest([lvl(90, 'SUPPORT'), lvl(95, 'SUPPORT'), lvl(105), lvl(120)], 100);
    expect(n.below?.price).toBe(95);
    expect(n.above?.price).toBe(105);
  });
});

describe('VWAP and relative volume', () => {
  const day1 = (f: (i: number) => object = () => ({})) => aggregate(sessionBars('2026-10-13', 5, 20, (i) => ({ v: 100, ...f(i) })), 5, 5, fromIst('2026-10-14', 16));
  it('VWAP is volume weighted and resets each session', () => {
    const s = aggregate([...sessionBars('2026-10-13', 5, 3, (i) => ({ h: 100, l: 100, c: 100, v: i === 0 ? 100 : 100 })), ...sessionBars('2026-10-14', 5, 2, () => ({ h: 200, l: 200, c: 200, v: 50 }))], 5, 5, fromIst('2026-10-14', 16));
    const v = sessionVwap(s);
    expect(v[2]).toBeCloseTo(100);
    expect(v[3]).toBeCloseTo(200);
  });
  it('VWAP is unavailable (not zero) when volume is unknown or absent', () => {
    expect(sessionVwap(day1(() => ({ v: null })))).toSatisfy((a: (number | null)[]) => a.every((x) => x === null));
    expect(sessionVwap(day1(() => ({ v: 0 })))).toSatisfy((a: (number | null)[]) => a.every((x) => x === null));
    expect(vwapRead(day1(() => ({ v: null })), 100).position).toBe('UNKNOWN');
  });
  it('detects a VWAP reclaim', () => {
    const closes = [100, 99, 98, 97, 97, 97, 97, 101];
    const s = aggregate(sessionBars('2026-10-13', 5, closes.length, (i) => ({ h: closes[i]! + 1, l: closes[i]! - 1, c: closes[i]!, v: 100 })), 5, 5, fromIst('2026-10-14', 16));
    expect(vwapRead(s, 101).event).toBe("RECLAIM");
  });

  it('cumulative relative volume needs enough prior sessions', () => {
    const mk = (n: number) => {
      let all: ReturnType<typeof sessionBars> = [];
      for (let d = 0; d < n; d++) all = all.concat(sessionBars(`2026-10-${String(1 + d).padStart(2, '0')}`, 15, 8, () => ({ v: 100 })));
      return aggregate(all, 15, 15, fromIst('2026-10-30', 16));
    };
    expect(cumulativeRvol(mk(3)).value).toBeNull();
    expect(cumulativeRvol(mk(3)).note).toMatch(/Only 2 prior sessions/);
    const five = cumulativeRvol(mk(6));
    expect(five.value).toBeCloseTo(1);
    expect(five.samples).toBe(5);
  });

  it('cumulative relative volume compares like with like (same time of day)', () => {
    let all: ReturnType<typeof sessionBars> = [];
    for (let d = 1; d <= 5; d++) all = all.concat(sessionBars(`2026-10-0${d}`, 15, 25, () => ({ v: 100 })));
    all = all.concat(sessionBars('2026-10-06', 15, 4, () => ({ v: 200 }))); // half hour in, twice the pace
    const rv = cumulativeRvol(aggregate(all, 15, 15, fromIst('2026-10-30', 16)));
    expect(rv.value).toBeCloseTo(2);
  });

  it('bar relative volume refuses a thin baseline and unknown volume', () => {
    expect(barRvol(bars(5), 4).value).toBeNull();
    expect(barRvol(bars(30, (i) => (i === 29 ? { v: null } : {})), 29).value).toBeNull();
    expect(barRvol(bars(30, (i) => (i === 29 ? { v: 3000 } : {})), 29).value).toBeCloseTo(3);
    expect(barRvol(bars(30, (i) => ({ v: i === 29 ? 5 : 0 })), 29).note).toMatch(/zero/);
  });
});

describe('trend, agreement and structure reads', () => {
  const trending = (dir: 1 | -1) => bars(80, (i) => {
    const c = 100 + dir * i * 0.5 + (i % 2) * 0.1;
    return { o: c - 0.2, h: c + 0.6, l: c - 0.6, c };
  });
  it('detects trend up/down with swing structure, UNKNOWN with too little data', () => {
    expect(trendRead(trending(1)).trend).toBe('UP');
    expect(trendRead(trending(-1)).trend).toBe('DOWN');
    expect(trendRead(bars(20)).trend).toBe('UNKNOWN');
    expect(trendRead(bars(80)).trend).toBe('RANGE');
  });
  it('timeframe agreement: needs two known; reports disagreement honestly', () => {
    expect(mtfAgreement({ '15m': 'UP', '1d': 'UNKNOWN' }).alignment).toBe('UNKNOWN');
    expect(mtfAgreement({ '15m': 'UP', '1h': 'UP', '1d': 'UP' }).alignment).toBe('UP');
    const m = mtfAgreement({ '15m': 'UP', '1h': 'DOWN', '1d': 'UP' });
    expect(m.alignment).toBe('MIXED');
    expect(m.disagreeing).toContain('1h');
    expect(mtfAgreement({ '15m': 'UP', '1h': 'RANGE' }).alignment).toBe('MIXED');
  });
  it('range compression and gaps', () => {
    const squeeze = bars(60, (i) => (i >= 50 ? { h: 104.3, l: 103.7 } : { h: 107, l: 101 }));
    expect(compression(squeeze).compressed).toBe(true);
    expect(compression(bars(20)).compressed).toBeNull();
    const g = aggregate([...sessionBars('2026-10-13', 15, 25, () => ({ c: 100, h: 101, l: 99 })), ...sessionBars('2026-10-14', 15, 5, () => ({ o: 103, c: 103, h: 104, l: 102 }))], 15, 15, fromIst('2026-10-14', 16));
    expect(gapRead(g).kind).toBe('GAP_UP');
    expect(gapRead(g).gapPct).toBeCloseTo(3);
  });
  it('epoch helper sanity', () => {
    expect(epoch('2026-10-14', 9, 15)).toBe(Date.parse('2026-10-14T03:45:00Z') / 1000);
  });
});
