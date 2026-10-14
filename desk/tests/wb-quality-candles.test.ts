import { describe, expect, it } from 'vitest';
import { aggregate, availability, markDaily, MIN_ANALYSIS_BARS } from '../shared/candles';
import { fromIst } from '../shared/india';
import { classifyFreshness, isActionable, parseNseTimestamp, reconcile } from '../shared/quality';
import { epoch, sessionBars } from './wbhelpers';

const D1 = '2026-10-13'; // Tuesday
const D2 = '2026-10-14'; // Wednesday
const after = fromIst(D2, 16, 0); // well after both sessions closed

describe('freshness states', () => {
  const now = new Date('2026-10-14T05:00:00Z');
  const base = { hasValue: true, now, marketOpen: true, maxAgeSec: 120 };
  it('VALID when recent, STALE when old, UNKNOWN without timestamp', () => {
    expect(classifyFreshness({ ...base, asOf: '2026-10-14T04:59:30Z' })).toBe('VALID');
    expect(classifyFreshness({ ...base, asOf: '2026-10-14T04:50:00Z' })).toBe('STALE');
    expect(classifyFreshness({ ...base, asOf: null })).toBe('UNKNOWN');
    expect(classifyFreshness({ ...base, asOf: 'garbage' })).toBe('UNKNOWN');
  });
  it('a missing value is UNKNOWN, never valid', () => {
    expect(classifyFreshness({ ...base, hasValue: false, asOf: '2026-10-14T04:59:59Z' })).toBe('UNKNOWN');
  });
  it('closed market is MARKET_CLOSED, not an outage and not stale', () => {
    expect(classifyFreshness({ ...base, marketOpen: false, asOf: '2026-10-13T10:00:00Z' })).toBe('MARKET_CLOSED');
  });
  it('a future timestamp is a CONFLICT, not fresh data', () => {
    expect(classifyFreshness({ ...base, asOf: '2026-10-14T06:00:00Z' })).toBe('CONFLICT');
  });
  it('only VALID is actionable', () => {
    for (const s of ['STALE', 'DATA_INSUFFICIENT', 'CONFLICT', 'UNKNOWN', 'UNSUPPORTED', 'MARKET_CLOSED'] as const) expect(isActionable(s)).toBe(false);
    expect(isActionable('VALID')).toBe(true);
  });
});

describe('timestamps and provider conflicts', () => {
  it('parses NSE IST timestamps to UTC', () => {
    expect(parseNseTimestamp('14-Oct-2026 15:24:23')).toBe('2026-10-14T09:54:23.000Z');
    expect(parseNseTimestamp('14-Oct-2026')).toBe('2026-10-13T18:30:00.000Z');
    expect(parseNseTimestamp('nonsense')).toBeNull();
    expect(parseNseTimestamp(undefined)).toBeNull();
  });
  it('conflicting providers pick neither value', () => {
    const r = reconcile(1000, 1020, 0.5, ['NSE', 'Yahoo']);
    expect(r.state).toBe('CONFLICT');
    expect(r.value).toBeNull();
    expect(reconcile(1000, 1002, 0.5, ['NSE', 'Yahoo'])).toMatchObject({ state: 'VALID', value: 1000 });
    expect(reconcile(null, null, 0.5, ['a', 'b']).state).toBe('UNKNOWN');
    expect(reconcile(null, 5, 0.5, ['a', 'b'])).toMatchObject({ state: 'VALID', value: 5 });
  });
});

describe('session-aligned aggregation', () => {
  it('3m from 1m: buckets start 09:15, 09:18 ...', () => {
    const src = sessionBars(D1, 1, 30, (i) => ({ o: 100 + i, c: 100 + i + 0.5, h: 101 + i, l: 99 + i, v: 10 }));
    const out = aggregate(src, 1, 3, after);
    expect(out).toHaveLength(10);
    expect(out[0]!.t).toBe(epoch(D1, 9, 15));
    expect(out[1]!.t).toBe(epoch(D1, 9, 18));
    expect(out[0]).toMatchObject({ o: 100, c: 102.5, h: 103, l: 99, v: 30, complete: true, have: 3, want: 3 });
  });

  it('5m, 15m, 30m, 1h all align to 09:15, and 1h ends with a 15-minute bar at 15:15', () => {
    const src = sessionBars(D1, 15, 25, () => ({ v: 10 })); // full session of 15m bars: 09:15..15:15
    expect(aggregate(src, 15, 15, after)).toHaveLength(25);
    const m30 = aggregate(src, 15, 30, after);
    expect(m30[0]!.t).toBe(epoch(D1, 9, 15));
    expect(m30[1]!.t).toBe(epoch(D1, 9, 45));
    const h = aggregate(src, 15, 60, after);
    expect(h.map((x) => x.t)).toEqual([9, 10, 11, 12, 13, 14, 15].map((hr) => epoch(D1, hr, 15)));
    expect(h[6]).toMatchObject({ want: 1, have: 1, complete: true }); // 15:15-15:30 is a legitimate 15-minute bar
    expect(h[0]).toMatchObject({ want: 4, have: 4, complete: true, v: 40 });
  });

  it('a missing source bar makes the bucket incomplete and is never fabricated', () => {
    const src = sessionBars(D1, 5, 12).filter((_, i) => i !== 1);
    const out = aggregate(src, 5, 15, after);
    expect(out[0]).toMatchObject({ have: 2, want: 3, complete: false });
    expect(out[1]!.complete).toBe(true);
  });

  it('the bucket still forming is incomplete', () => {
    const src = sessionBars(D1, 5, 9);
    const now = new Date((epoch(D1, 9, 15) + 8 * 300 + 60) * 1000); // inside the last 5m bar... bucket 09:45 not finished
    const out = aggregate(src, 5, 15, now);
    expect(out.at(-1)!.complete).toBe(false);
    expect(out[0]!.complete).toBe(true);
  });

  it('pre-open and post-close rows are dropped', () => {
    const pre = sessionBars(D1, 5, 3, () => ({}), 9 * 60);
    const post = sessionBars(D1, 5, 2, () => ({}), 15 * 60 + 30);
    const out = aggregate([...pre, ...sessionBars(D1, 5, 3), ...post], 5, 5, after);
    expect(out.map((x) => x.t)).toEqual([epoch(D1, 9, 15), epoch(D1, 9, 20), epoch(D1, 9, 25)].slice(0, 3));
  });

  it('an off-grid snapshot bar (provider "now" bar) is kept but never complete', () => {
    const src = [...sessionBars(D1, 5, 3), { t: epoch(D1, 9, 30, 7), o: 1, h: 2, l: 0.5, c: 1.5, v: 5 }];
    const out = aggregate(src, 5, 5, after);
    const snap = out.find((x) => x.t === epoch(D1, 9, 30))!;
    expect(snap.complete).toBe(false);
  });

  it('an exact-grid bar wins over an off-grid snapshot of the same slot', () => {
    const src = [...sessionBars(D1, 5, 3), { t: epoch(D1, 9, 20, 40), o: 9, h: 9, l: 9, c: 9, v: 9 }];
    const out = aggregate(src, 5, 5, after);
    expect(out.find((x) => x.t === epoch(D1, 9, 20))).toMatchObject({ c: 100, complete: true });
  });

  it('unknown volume in any bar makes the bucket volume unknown, not zero', () => {
    const src = sessionBars(D1, 5, 3, (i) => ({ v: i === 1 ? null : 10 }));
    expect(aggregate(src, 5, 15, after)[0]!.v).toBeNull();
    const zero = sessionBars(D1, 5, 3, () => ({ v: 0 }));
    expect(aggregate(zero, 5, 15, after)[0]!.v).toBe(0); // an observed zero stays zero
  });

  it('keeps sessions separate across the overnight gap and a holiday-free weekend', () => {
    const a = sessionBars(D1, 15, 25);
    const b = sessionBars(D2, 15, 25);
    const h = aggregate([...a, ...b], 15, 60, after);
    expect(h).toHaveLength(14);
    expect(h[7]!.t).toBe(epoch(D2, 9, 15));
    expect(h[7]!.o).toBe(100); // the new session's open, not merged with yesterday's close
  });

  it('rejects impossible conversions instead of guessing', () => {
    expect(() => aggregate([], 5, 3, after)).toThrow();
    expect(() => aggregate([], 15, 20, after)).toThrow();
  });
});

describe('daily bars and availability', () => {
  it('today\'s daily bar is complete only after the close', () => {
    const day = (d: string) => ({ t: epoch(d, 9, 15), o: 1, h: 2, l: 0.5, c: 1.5, v: 1 });
    const during = markDaily([day(D1), day(D2)], fromIst(D2, 12, 0));
    expect(during.map((c) => c.complete)).toEqual([true, false]);
    const closed = markDaily([day(D1), day(D2)], fromIst(D2, 15, 45));
    expect(closed.map((c) => c.complete)).toEqual([true, true]);
  });
  it('explains an unavailable timeframe instead of showing a thin chart', () => {
    const few = aggregate(sessionBars(D1, 15, 10), 15, 15, after);
    const a = availability('15m', few);
    expect(a.available).toBe(false);
    expect(a.reason).toContain(String(MIN_ANALYSIS_BARS));
    expect(availability('1m', null, 'provider down')).toMatchObject({ available: false, reason: 'provider down' });
    const many = aggregate([...sessionBars(D1, 15, 25), ...sessionBars(D2, 15, 25)], 15, 15, after);
    expect(availability('15m', many)).toMatchObject({ available: true, bars: 50 });
  });
});
