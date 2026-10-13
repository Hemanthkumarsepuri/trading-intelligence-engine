import { describe, expect, it } from 'vitest';
import { evaluateAlert, type AlertContext } from '../shared/alerts';
import { analyze } from '../shared/indicators';
import type { Alert } from '../shared/types';
import { candles, quote } from './helpers';

const NOW = new Date('2026-10-12T05:00:00.000Z');
const alert = (p: Partial<Alert>): Alert => ({ id: 'a', type: 'PRICE_ABOVE', symbol: 'TCS', threshold: 3000, enabled: true, repeat: 'once', note: '', lastTriggeredAt: null, createdAt: '2026-10-01T00:00:00.000Z', ...p });
const ctx = (over: Partial<AlertContext> = {}): AlertContext => ({
  quotes: { TCS: quote('TCS', 3050, 3000, '2026-10-12T04:58:00.000Z') }, tech: {}, vix: quote('INDIAVIX', 14, 13.5, '2026-10-12T04:59:00.000Z'), banned: new Set(), now: NOW, maxQuoteAgeMin: 20, marketLive: true, ...over,
});

describe('evaluateAlert', () => {
  it('fires a price alert on a fresh quote', () => {
    const r = evaluateAlert(alert({}), ctx());
    expect(r).toMatchObject({ fired: true });
  });
  it('does not fire from a stale print while the market is live', () => {
    const r = evaluateAlert(alert({}), ctx({ quotes: { TCS: quote('TCS', 3050, 3000, '2026-10-12T04:00:00.000Z') } }));
    expect(r).toMatchObject({ fired: false });
    expect((r as { skipped?: string }).skipped).toMatch(/stale/);
  });
  it('honours once/daily repeat rules', () => {
    expect(evaluateAlert(alert({ lastTriggeredAt: '2026-10-11T05:00:00.000Z' }), ctx())).toMatchObject({ fired: false });
    expect(evaluateAlert(alert({ repeat: 'daily', lastTriggeredAt: '2026-10-11T05:00:00.000Z' }), ctx())).toMatchObject({ fired: true });
    expect(evaluateAlert(alert({ repeat: 'daily', lastTriggeredAt: '2026-10-12T04:00:00.000Z' }), ctx())).toMatchObject({ fired: false });
  });
  it('ignores disabled alerts', () => expect(evaluateAlert(alert({ enabled: false }), ctx())).toMatchObject({ fired: false }));
  it('day-change and below alerts', () => {
    expect(evaluateAlert(alert({ type: 'DAY_CHANGE_ABOVE', threshold: 1.5 }), ctx())).toMatchObject({ fired: true }); // +1.67%
    expect(evaluateAlert(alert({ type: 'PRICE_BELOW', threshold: 3000 }), ctx())).toMatchObject({ fired: false });
  });
  it('VIX and F&O ban alerts', () => {
    expect(evaluateAlert(alert({ type: 'VIX_ABOVE', symbol: 'INDIAVIX', threshold: 13 }), ctx())).toMatchObject({ fired: true });
    expect(evaluateAlert(alert({ type: 'VIX_ABOVE', symbol: 'INDIAVIX', threshold: 13 }), ctx({ vix: null }))).toMatchObject({ fired: false });
    expect(evaluateAlert(alert({ type: 'FNO_BAN' }), ctx({ banned: new Set(['TCS']) }))).toMatchObject({ fired: true });
  });
  it('never fires a VIX alert from a stale VIX print while live', () => {
    const r = evaluateAlert(alert({ type: 'VIX_ABOVE', symbol: 'INDIAVIX', threshold: 13 }), ctx({ vix: quote('INDIAVIX', 14, 13.5, '2026-10-12T03:30:00.000Z') }));
    expect(r).toMatchObject({ fired: false });
    expect((r as { skipped?: string }).skipped).toMatch(/stale/);
  });
  it('treats a quote without an exchange timestamp as stale while live', () => {
    const r = evaluateAlert(alert({}), ctx({ quotes: { TCS: quote('TCS', 3050, 3000, null) } }));
    expect(r).toMatchObject({ fired: false });
  });
  it('skips ban alerts when the list is not verifiably for today', () => {
    const r = evaluateAlert(alert({ type: 'FNO_BAN' }), ctx({ banned: null }));
    expect(r).toMatchObject({ fired: false });
    expect((r as { skipped?: string }).skipped).toMatch(/ban list/);
  });
  it('skips history alerts when the history is a stale cached copy', () => {
    const up = candles(40, (i) => ({ c: 100 + i, h: 101 + i, l: 99 + i }));
    const r = evaluateAlert(alert({ type: 'RSI_ABOVE', threshold: 70 }), ctx({ tech: { TCS: analyze(up)! }, techStale: new Set(['TCS']) }));
    expect(r).toMatchObject({ fired: false });
  });
  it('includes the IST trade time in the message', () => {
    const r = evaluateAlert(alert({}), ctx());
    expect((r as { message: string }).message).toMatch(/traded 10:28:00 IST/);
  });
  it('RSI alerts need history and use the daily RSI', () => {
    const up = candles(40, (i) => ({ c: 100 + i, h: 101 + i, l: 99 + i }));
    const tech = analyze(up)!;
    expect(evaluateAlert(alert({ type: 'RSI_ABOVE', threshold: 70 }), ctx({ tech: { TCS: tech } }))).toMatchObject({ fired: true });
    expect(evaluateAlert(alert({ type: 'RSI_ABOVE', threshold: 70 }), ctx())).toMatchObject({ fired: false });
  });
  it('still evaluates after hours (no freshness gate when the market is closed)', () => {
    const r = evaluateAlert(alert({}), ctx({ marketLive: false, quotes: { TCS: quote('TCS', 3050, 3000, '2026-10-09T10:00:00.000Z') } }));
    expect(r).toMatchObject({ fired: true });
  });
});
