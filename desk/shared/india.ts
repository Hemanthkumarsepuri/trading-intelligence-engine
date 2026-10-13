// Indian market calendar and session logic. Pure functions, no I/O.
//
// Source of the 2026 holiday list: NSE circular NSE/CMTR/71775 (12 Dec 2025),
// the same list the TIRE engine already carries in data/reference. NSE
// publishes the next year's list in December; until it is added here the
// calendar reports `calendarKnown = false` for that year instead of silently
// treating every weekday as a session (this was defect M-7 in the TIRE
// forensic report).

import type { SessionState } from './types';

const IST_OFFSET_MS = 330 * 60 * 1000;

/** CM-segment trading holidays (exchange closed). Keyed by year. */
export const HOLIDAYS: Readonly<Record<number, readonly string[]>> = {
  2026: [
    '2026-01-26', '2026-03-03', '2026-03-26', '2026-03-31', '2026-04-03',
    '2026-04-14', '2026-05-01', '2026-05-28', '2026-06-26', '2026-09-14',
    '2026-10-02', '2026-10-20', '2026-11-10', '2026-11-24', '2026-12-25',
  ],
};

/** Special sessions on days that would otherwise be closed (Muhurat). */
export const SPECIAL_SESSIONS: readonly string[] = ['2026-11-08'];

export interface IstParts {
  year: number;
  month: number; // 1-12
  day: number;
  hour: number;
  minute: number;
  second: number;
  /** 0 = Sunday */
  dow: number;
  dateStr: string; // YYYY-MM-DD in IST
}

export function istParts(d: Date): IstParts {
  const s = new Date(d.getTime() + IST_OFFSET_MS);
  const year = s.getUTCFullYear();
  const month = s.getUTCMonth() + 1;
  const day = s.getUTCDate();
  return {
    year,
    month,
    day,
    hour: s.getUTCHours(),
    minute: s.getUTCMinutes(),
    second: s.getUTCSeconds(),
    dow: s.getUTCDay(),
    dateStr: `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`,
  };
}

export function istDateStr(d: Date): string {
  return istParts(d).dateStr;
}

/** Build a Date from an IST wall-clock date (YYYY-MM-DD) and time. */
export function fromIst(dateStr: string, hour = 0, minute = 0): Date {
  const [y, m, d] = dateStr.split('-').map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d, hour, minute) - IST_OFFSET_MS);
}

export function addDays(dateStr: string, n: number): string {
  const [y, m, d] = dateStr.split('-').map(Number) as [number, number, number];
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, '0')}-${String(t.getUTCDate()).padStart(2, '0')}`;
}

export function calendarCovers(year: number): boolean {
  return Object.prototype.hasOwnProperty.call(HOLIDAYS, year);
}

export interface DayStatus {
  trading: boolean;
  special: boolean;
  /** false when we cannot rule out an unlisted holiday */
  known: boolean;
  reason: string;
}

export function dayStatus(dateStr: string, extraHolidays: readonly string[] = []): DayStatus {
  const [y, m, d] = dateStr.split('-').map(Number) as [number, number, number];
  const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  if (SPECIAL_SESSIONS.includes(dateStr)) {
    return { trading: true, special: true, known: true, reason: 'Special (Muhurat) session' };
  }
  if (dow === 0 || dow === 6) {
    return { trading: false, special: false, known: true, reason: 'Weekend' };
  }
  if (extraHolidays.includes(dateStr)) {
    return { trading: false, special: false, known: true, reason: 'Exchange holiday' };
  }
  const list = HOLIDAYS[y];
  if (list === undefined) {
    // Fail visible: weekday of a year we have no calendar for.
    return { trading: true, special: false, known: false, reason: `Holiday calendar for ${y} not loaded` };
  }
  if (list.includes(dateStr)) {
    return { trading: false, special: false, known: true, reason: 'Exchange holiday' };
  }
  return { trading: true, special: false, known: true, reason: 'Regular trading day' };
}

export function nextTradingDay(fromDateStr: string, extraHolidays: readonly string[] = []): string {
  let cur = addDays(fromDateStr, 1);
  for (let i = 0; i < 20; i++) {
    if (dayStatus(cur, extraHolidays).trading) return cur;
    cur = addDays(cur, 1);
  }
  return cur;
}

export function previousTradingDay(fromDateStr: string, extraHolidays: readonly string[] = []): string {
  let cur = addDays(fromDateStr, -1);
  for (let i = 0; i < 20; i++) {
    if (dayStatus(cur, extraHolidays).trading) return cur;
    cur = addDays(cur, -1);
  }
  return cur;
}

const PRE_OPEN_START = 9 * 60; // 09:00
const OPEN_START = 9 * 60 + 15; // 09:15
const CLOSE = 15 * 60 + 30; // 15:30

export function sessionState(now: Date, extraHolidays: readonly string[] = []): SessionState {
  const p = istParts(now);
  const status = dayStatus(p.dateStr, extraHolidays);
  const minutes = p.hour * 60 + p.minute;
  const base = {
    istNow: now.toISOString(),
    isTradingDay: status.trading,
    calendarKnown: status.known,
    reason: status.reason,
  };

  if (status.special) {
    return {
      ...base,
      phase: 'SPECIAL',
      label: 'Special session day',
      nextChangeLabel: null,
      nextChangeAt: null,
    };
  }

  if (!status.trading) {
    const nxt = nextTradingDay(p.dateStr, extraHolidays);
    return {
      ...base,
      phase: 'CLOSED',
      label: status.reason === 'Weekend' ? 'Closed (weekend)' : 'Closed (holiday)',
      nextChangeLabel: 'Pre-open',
      nextChangeAt: fromIst(nxt, 9, 0).toISOString(),
    };
  }

  if (minutes < PRE_OPEN_START) {
    return { ...base, phase: 'CLOSED', label: 'Closed (before pre-open)', nextChangeLabel: 'Pre-open', nextChangeAt: fromIst(p.dateStr, 9, 0).toISOString() };
  }
  if (minutes < OPEN_START) {
    return { ...base, phase: 'PRE_OPEN', label: 'Pre-open session', nextChangeLabel: 'Market opens', nextChangeAt: fromIst(p.dateStr, 9, 15).toISOString() };
  }
  if (minutes < CLOSE) {
    return { ...base, phase: 'OPEN', label: 'Market open', nextChangeLabel: 'Market closes', nextChangeAt: fromIst(p.dateStr, 15, 30).toISOString() };
  }
  const nxt = nextTradingDay(p.dateStr, extraHolidays);
  return { ...base, phase: 'CLOSED', label: 'Closed (after market)', nextChangeLabel: 'Pre-open', nextChangeAt: fromIst(nxt, 9, 0).toISOString() };
}

/** True while a live price should be moving (pre-open counts as not live). */
export function isLiveSession(now: Date, extraHolidays: readonly string[] = []): boolean {
  const s = sessionState(now, extraHolidays);
  return s.phase === 'OPEN' || s.phase === 'SPECIAL';
}

/** Age in minutes of an ISO timestamp, or Infinity when missing/invalid. */
export function ageMinutes(iso: string | null | undefined, now: Date): number {
  if (!iso) return Infinity;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return Infinity;
  return Math.max(0, (now.getTime() - t) / 60000);
}

// ---- Financial year --------------------------------------------------------

/** Indian financial year label for a date: 'YYYY-YY' (April to March). */
export function financialYear(dateStr: string): string {
  const [y, m] = dateStr.split('-').map(Number) as [number, number];
  const start = m >= 4 ? y : y - 1;
  return `${start}-${String((start + 1) % 100).padStart(2, '0')}`;
}

export function fyBounds(fy: string): { start: string; end: string } {
  const startYear = Number(fy.slice(0, 4));
  return { start: `${startYear}-04-01`, end: `${startYear + 1}-03-31` };
}

/** Listed-equity long-term test: held for MORE than 12 months. */
export function isLongTermEquity(buyDate: string, sellDate: string): boolean {
  const [y, m, d] = buyDate.split('-').map(Number) as [number, number, number];
  // buy + 12 months (clamping Feb 29)
  const target = new Date(Date.UTC(y + 1, m - 1, d));
  if (target.getUTCMonth() !== (m - 1 + 12) % 12) target.setUTCDate(0);
  const cutoff = `${target.getUTCFullYear()}-${String(target.getUTCMonth() + 1).padStart(2, '0')}-${String(target.getUTCDate()).padStart(2, '0')}`;
  return sellDate > cutoff;
}
