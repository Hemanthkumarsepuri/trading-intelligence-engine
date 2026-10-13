import { describe, expect, it } from 'vitest';
import { evaluateAlert, type AlertContext } from '../shared/alerts';
import { fmtQty } from '../shared/format';
import { analyze, knownVolume } from '../shared/indicators';
import type { Alert } from '../shared/types';
import { parseUpstoxCandles } from '../server/providers/upstox';
import { parseYahooChart } from '../server/providers/yahoo';
import { candles } from './helpers';

// 25 days of history; the last candle is "today". prior20 = the 20 days before it.
const series = (prior: (i: number) => number | null, today: number | null) =>
  candles(25, (i) => ({ v: i === 24 ? today : prior(i) }));
const ratio = (c: ReturnType<typeof candles>) => analyze(c)!.volRatio20;

describe('knownVolume', () => {
  it('keeps real numbers (including an explicit zero) and nulls everything else', () => {
    expect(knownVolume(1234)).toBe(1234);
    expect(knownVolume(0)).toBe(0);
    for (const bad of [null, undefined, NaN, Infinity, -5, '1000', '', {}, []]) expect(knownVolume(bad)).toBeNull();
  });
});

describe('provider candle volume', () => {
  const yahoo = (volume: unknown[]) =>
    parseYahooChart({ chart: { result: [{ meta: { symbol: 'X' }, timestamp: [1, 2, 3, 4], indicators: { quote: [{ open: [1, 1, 1, 1], high: [2, 2, 2, 2], low: [1, 1, 1, 1], close: [1, 1, 1, 1], volume: volume as number[] }] } }] } }, 'X').candles.map((c) => c.v);
  it('Yahoo: missing, malformed and negative volume stay unknown; explicit zero is kept', () => {
    expect(yahoo([500, null, 0, 'abc'])).toEqual([500, null, 0, null]);
    expect(yahoo([-1, undefined as unknown as number, 7, 8])).toEqual([null, null, 7, 8]);
  });
  it('Yahoo: a missing volume series yields unknown, not zero', () => {
    const c = parseYahooChart({ chart: { result: [{ meta: {}, timestamp: [1], indicators: { quote: [{ open: [1], high: [2], low: [1], close: [1] }] } }] } }, 'X').candles;
    expect(c[0]!.v).toBeNull();
  });
  it('Upstox: 5 columns (no volume), null, string and zero are distinguished', () => {
    const rows = [
      ['2026-10-01T00:00:00+05:30', 1, 2, 1, 1, 900],
      ['2026-10-02T00:00:00+05:30', 1, 2, 1, 1, null],
      ['2026-10-03T00:00:00+05:30', 1, 2, 1, 1, 'x'],
      ['2026-10-04T00:00:00+05:30', 1, 2, 1, 1, 0],
      ['2026-10-05T00:00:00+05:30', 1, 2, 1, 1], // too short: row dropped entirely
    ];
    expect(parseUpstoxCandles(rows).map((c) => c.v)).toEqual([900, null, null, 0]);
  });
});

describe('volume ratio (20-day average)', () => {
  it('computes a genuine spike: 20 x 1000 prior, 3000 today => 3.0', () => {
    expect(ratio(series(() => 1000, 3000))).toBeCloseTo(3, 10);
  });
  it('does not treat unknown days as zero: 10 known x1000 + 10 unknown, 3000 today => 3.0 (the old zero-fill gave 6.0)', () => {
    // prior20 = candles 4..23; make 10 of them unknown
    expect(ratio(series((i) => (i % 2 === 0 ? 1000 : null), 3000))).toBeCloseTo(3, 10);
  });
  it('keeps an observed zero in the average: 19 x1000 + one 0 => avg 950, 1900 today => 2.0', () => {
    expect(ratio(series((i) => (i === 10 ? 0 : 1000), 1900))).toBeCloseTo(2, 10);
  });
  it('is unknown when fewer than 10 prior days have a known volume (9 known)', () => {
    expect(ratio(series((i) => (i >= 4 && i < 13 ? 1000 : null), 5000))).toBeNull();
  });
  it('is computed at exactly 10 known days (boundary), from the known days only', () => {
    expect(ratio(series((i) => (i >= 4 && i < 14 ? 1000 : null), 5000))).toBeCloseTo(5, 10);
  });
  it('is unknown when today\'s volume is unknown', () => {
    expect(ratio(series(() => 1000, null))).toBeNull();
  });
  it('is unknown when the whole history is unknown or zero', () => {
    expect(ratio(series(() => null, 5000))).toBeNull();
    expect(ratio(series(() => 0, 5000))).toBeNull();
  });
  it('does not emit volume observations when volume evidence is insufficient', () => {
    const t = analyze(series(() => null, 9_999_999))!;
    expect(t.observations).not.toContain('VOLUME_SURGE');
    expect(t.observations).not.toContain('BREAKOUT_20D_WITH_VOLUME');
  });
});

describe('VOLUME_SPIKE alert', () => {
  const NOW = new Date('2026-10-12T05:00:00.000Z');
  const alert: Alert = { id: 'a', type: 'VOLUME_SPIKE', symbol: 'TCS', threshold: 2, enabled: true, repeat: 'once', note: '', lastTriggeredAt: null, createdAt: '2026-10-01T00:00:00.000Z' };
  const ctx = (c: ReturnType<typeof candles>): AlertContext => ({ quotes: {}, tech: { TCS: analyze(c) }, vix: null, banned: new Set(), now: NOW, maxQuoteAgeMin: 5, marketLive: true });
  it('fires on a real 3x spike', () => {
    expect(evaluateAlert(alert, ctx(series(() => 1000, 3000)))).toMatchObject({ fired: true });
  });
  it('does not fire when half the history is unknown and today is a normal 1000', () => {
    // zero-fill would give avg 500 => ratio 2.0 => a false spike
    expect(evaluateAlert(alert, ctx(series((i) => (i % 2 === 0 ? 1000 : null), 1000)))).toMatchObject({ fired: false });
  });
  it('does not fire with insufficient known history or unknown current volume', () => {
    expect(evaluateAlert(alert, ctx(series((i) => (i >= 4 && i < 13 ? 1000 : null), 9000)))).toMatchObject({ fired: false });
    expect(evaluateAlert(alert, ctx(series(() => 1000, null)))).toMatchObject({ fired: false });
  });
});

describe('display of unknown versus zero volume', () => {
  it('shows a dash for unknown and 0 for an observed zero', () => {
    expect(fmtQty(null)).toBe('-');
    expect(fmtQty(undefined)).toBe('-');
    expect(fmtQty(0)).toBe('0');
  });
});

describe('option-chain OI and volume: missing stays null, a real zero stays zero', () => {
  it('NSE', async () => {
    const { parseNseChain } = await import('../server/providers/nse');
    const c = parseNseChain({ records: { underlyingValue: 25000, data: [{ strikePrice: 25000, expiryDate: '27-Oct-2026', CE: { openInterest: 0, totalTradedVolume: 0 }, PE: { lastPrice: 5 } }] } }, 'NIFTY', '2026-10-27', 't');
    expect(c.rows[0]!.ce.oi).toBe(0);
    expect(c.rows[0]!.ce.volume).toBe(0);
    expect(c.rows[0]!.pe.oi).toBeNull();
    expect(c.rows[0]!.pe.volume).toBeNull();
  });
  it('Upstox', async () => {
    const { parseUpstoxChain } = await import('../server/providers/upstox');
    const c = parseUpstoxChain({ status: 'success', data: [{ strike_price: 25000, call_options: { market_data: { ltp: 1, oi: 0, volume: 0 } }, put_options: { market_data: { ltp: 2, oi: 'x' as unknown as number } } }] }, 'NIFTY', '2026-10-27', 't');
    expect(c.rows[0]!.ce.oi).toBe(0);
    expect(c.rows[0]!.ce.volume).toBe(0);
    expect(c.rows[0]!.pe.oi).toBeNull();
    expect(c.rows[0]!.pe.volume).toBeNull();
  });
});
