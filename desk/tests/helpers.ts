import { DEFAULT_SETTINGS, type Candle, type Quote, type Settings, type Trade } from '../shared/types';

export const S: Settings = { ...DEFAULT_SETTINGS, capital: 500000 };

export function trade(p: Partial<Trade> & Pick<Trade, 'id'>): Trade {
  return {
    symbol: 'RELIANCE',
    segment: 'EQ_INTRADAY',
    side: 'LONG',
    qty: 100,
    entryPrice: 100,
    exitPrice: null,
    entryAt: '2026-10-12T04:00:00.000Z',
    exitAt: null,
    stopLoss: null,
    target: null,
    setup: '',
    tags: [],
    emotion: null,
    planFollowed: null,
    notes: '',
    createdAt: '2026-10-12T04:00:00.000Z',
    updatedAt: '2026-10-12T04:00:00.000Z',
    ...p,
  };
}

export function quote(symbol: string, price: number, prevClose: number, marketTime: string | null, extra: Partial<Quote> = {}): Quote {
  return {
    symbol, price, prevClose, change: price - prevClose,
    changePct: prevClose ? ((price - prevClose) / prevClose) * 100 : 0,
    marketTime, fetchedAt: marketTime ?? '2026-10-12T05:00:00.000Z', source: 'fixture', ...extra,
  };
}

/** flat candles, oldest first, one per day ending at end epoch */
export function candles(n: number, f: (i: number) => Partial<Candle> = () => ({})): Candle[] {
  const out: Candle[] = [];
  for (let i = 0; i < n; i++) {
    const base: Candle = { t: 1_700_000_000 + i * 86400, o: 100, h: 101, l: 99, c: 100, v: 1000 };
    out.push({ ...base, ...f(i) });
  }
  return out;
}
