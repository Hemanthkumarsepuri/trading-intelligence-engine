import { describe, expect, it } from 'vitest';
import { dayStatus, financialYear, fromIst, isLongTermEquity, nextTradingDay, previousTradingDay, sessionState, istParts } from '../shared/india';

const utc = (s: string) => new Date(s);

describe('trading calendar', () => {
  it('knows listed holidays, weekends and ordinary days', () => {
    expect(dayStatus('2026-10-02')).toMatchObject({ trading: false, reason: 'Exchange holiday' });
    expect(dayStatus('2026-10-10')).toMatchObject({ trading: false, reason: 'Weekend' });
    expect(dayStatus('2026-10-12')).toMatchObject({ trading: true, known: true });
  });
  it('treats the Sunday Muhurat date as a special session', () => {
    expect(dayStatus('2026-11-08')).toMatchObject({ trading: true, special: true });
  });
  it('fails visible for a year with no calendar (defect M-7 in TIRE)', () => {
    const s = dayStatus('2027-02-03');
    expect(s.trading).toBe(true);
    expect(s.known).toBe(false);
    expect(s.reason).toMatch(/2027/);
  });
  it('accepts live holidays fetched from NSE', () => {
    expect(dayStatus('2027-01-26', ['2027-01-26']).trading).toBe(false);
  });
  it('skips weekends and holidays when stepping', () => {
    expect(nextTradingDay('2026-10-01')).toBe('2026-10-05'); // 2 Oct holiday, 3-4 weekend
    expect(previousTradingDay('2026-10-05')).toBe('2026-10-01');
  });
});

describe('session state (IST)', () => {
  it('is OPEN at 09:20 IST', () => expect(sessionState(utc('2026-10-12T03:50:00Z')).phase).toBe('OPEN'));
  it('is PRE_OPEN at 09:05 IST', () => expect(sessionState(utc('2026-10-12T03:35:00Z')).phase).toBe('PRE_OPEN'));
  it('is CLOSED at 15:35 IST', () => expect(sessionState(utc('2026-10-12T10:05:00Z')).phase).toBe('CLOSED'));
  it('opens exactly at 09:15 and closes exactly at 15:30', () => {
    expect(sessionState(utc('2026-10-12T03:45:00Z')).phase).toBe('OPEN');
    expect(sessionState(utc('2026-10-12T03:44:59Z')).phase).toBe('PRE_OPEN');
    expect(sessionState(utc('2026-10-12T10:00:00Z')).phase).toBe('CLOSED');
    expect(sessionState(utc('2026-10-12T09:59:59Z')).phase).toBe('OPEN');
  });
  it('is closed on a holiday even at mid-session time', () => {
    const s = sessionState(utc('2026-10-02T05:00:00Z'));
    expect(s.phase).toBe('CLOSED');
    expect(s.isTradingDay).toBe(false);
  });
  it('reports the next open on a Friday evening as Monday pre-open', () => {
    const s = sessionState(utc('2026-10-09T11:00:00Z')); // Fri 16:30 IST
    expect(s.nextChangeAt).toBe(fromIst('2026-10-12', 9, 0).toISOString());
  });
  it('converts dates to IST correctly across midnight UTC', () => {
    expect(istParts(utc('2026-10-12T19:00:00Z')).dateStr).toBe('2026-10-13');
  });
});

describe('financial year and holding period', () => {
  it('maps dates to Indian FY', () => {
    expect(financialYear('2026-03-31')).toBe('2025-26');
    expect(financialYear('2026-04-01')).toBe('2026-27');
  });
  it('requires MORE than 12 months for long term', () => {
    expect(isLongTermEquity('2025-10-12', '2026-10-12')).toBe(false);
    expect(isLongTermEquity('2025-10-12', '2026-10-13')).toBe(true);
  });
  it('handles a 29 Feb purchase', () => {
    expect(isLongTermEquity('2024-02-29', '2025-02-28')).toBe(false);
    expect(isLongTermEquity('2024-02-29', '2025-03-01')).toBe(true);
  });
});
