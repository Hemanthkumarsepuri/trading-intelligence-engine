import type { TfCandle } from '../shared/candles';
import { fromIst } from '../shared/india';
import type { Candle } from '../shared/types';

export const epoch = (day: string, h: number, m: number, s = 0): number => fromIst(day, h, m).getTime() / 1000 + s;

/** Source bars of `stepMin` minutes for one session from `fromMin` (minutes after midnight IST) for `n` bars. */
export function sessionBars(day: string, stepMin: number, n: number, f: (i: number) => Partial<Candle> = () => ({}), fromMin = 9 * 60 + 15): Candle[] {
  const out: Candle[] = [];
  for (let i = 0; i < n; i++) {
    const m = fromMin + i * stepMin;
    out.push({ t: epoch(day, Math.floor(m / 60), m % 60), o: 100, h: 101, l: 99, c: 100, v: 100, ...f(i) });
  }
  return out;
}

/** Plain completed bars on a 15-minute grid (session alignment is irrelevant to breakout/level maths). */
export function bars(n: number, f: (i: number) => Partial<TfCandle> = () => ({}), start = 1_700_000_000): TfCandle[] {
  const out: TfCandle[] = [];
  for (let i = 0; i < n; i++) out.push({ t: start + i * 900, o: 104, h: 105, l: 103, c: 104, v: 1000, complete: true, have: 1, want: 1, ...f(i) });
  return out;
}
