// Input validation for every write. Reject bad data at the door so analytics
// never have to guess (e.g. a stop loss on the wrong side of entry).

import { z } from 'zod';
import { cleanSymbol, isValidSymbol } from '../shared/universe';

const symbol = z
  .string()
  .transform(cleanSymbol)
  .refine(isValidSymbol, 'Symbol must be 1-20 characters: letters, digits, & or -');

const isoDate = z.string().refine((s) => !Number.isNaN(Date.parse(s)), 'Invalid date/time').transform((s) => new Date(s).toISOString());
const price = z.number().finite().positive().max(1e8);
const nullablePrice = price.nullable();

export const SEGMENTS = ['EQ_DELIVERY', 'EQ_INTRADAY', 'FUT', 'OPT'] as const;

export const tradeInput = z
  .object({
    symbol: z.string().trim().min(1).max(40).transform((s) => s.toUpperCase()),
    segment: z.enum(SEGMENTS),
    side: z.enum(['LONG', 'SHORT']),
    qty: z.number().finite().positive().max(1e9),
    entryPrice: price,
    exitPrice: nullablePrice.default(null),
    entryAt: isoDate,
    exitAt: isoDate.nullable().default(null),
    stopLoss: nullablePrice.default(null),
    target: nullablePrice.default(null),
    setup: z.string().trim().max(60).default(''),
    tags: z.array(z.string().trim().min(1).max(30)).max(10).default([]),
    emotion: z.number().int().min(1).max(5).nullable().default(null),
    planFollowed: z.boolean().nullable().default(null),
    notes: z.string().max(2000).default(''),
  })
  .superRefine((t, ctx) => {
    const bad = (path: string, message: string) => ctx.addIssue({ code: 'custom', path: [path], message });
    if (t.segment === 'EQ_DELIVERY' && t.side === 'SHORT') bad('side', 'Delivery trades cannot be short. Use intraday for short selling.');
    if ((t.segment === 'EQ_DELIVERY' || t.segment === 'EQ_INTRADAY') && !Number.isInteger(t.qty)) bad('qty', 'Equity quantity must be a whole number.');
    if ((t.exitPrice === null) !== (t.exitAt === null)) bad('exitAt', 'Exit price and exit time must both be set, or both empty.');
    if (t.exitAt && Date.parse(t.exitAt) < Date.parse(t.entryAt)) bad('exitAt', 'Exit cannot be before entry.');
    if (Date.parse(t.entryAt) > Date.now() + 5 * 60_000) bad('entryAt', 'Entry time is in the future.');
    if (t.stopLoss !== null) {
      if (t.side === 'LONG' && t.stopLoss >= t.entryPrice) bad('stopLoss', 'For a long trade the stop loss must be below the entry price.');
      if (t.side === 'SHORT' && t.stopLoss <= t.entryPrice) bad('stopLoss', 'For a short trade the stop loss must be above the entry price.');
    }
    if (t.target !== null) {
      if (t.side === 'LONG' && t.target <= t.entryPrice) bad('target', 'For a long trade the target must be above the entry price.');
      if (t.side === 'SHORT' && t.target >= t.entryPrice) bad('target', 'For a short trade the target must be below the entry price.');
    }
    if (t.segment === 'OPT' && t.side === 'SHORT' && t.stopLoss === null) {
      // allowed, but surfaced as a violation in the journal rather than blocked
    }
  });
export type TradeInput = z.infer<typeof tradeInput>;

export const holdingInput = z.object({
  symbol,
  qty: z.number().finite().positive().max(1e9),
  avgPrice: price,
  buyDate: z.union([z.literal(''), z.string().regex(/^\d{4}-\d{2}-\d{2}$/)]).default(''),
  notes: z.string().max(500).default(''),
});
export type HoldingInput = z.infer<typeof holdingInput>;

export const watchInput = z.object({
  symbol,
  note: z.string().max(300).default(''),
  support: nullablePrice.default(null),
  resistance: nullablePrice.default(null),
});

export const ALERT_TYPES = ['PRICE_ABOVE', 'PRICE_BELOW', 'DAY_CHANGE_ABOVE', 'DAY_CHANGE_BELOW', 'RSI_ABOVE', 'RSI_BELOW', 'VOLUME_SPIKE', 'VIX_ABOVE', 'FNO_BAN'] as const;

export const alertInput = z
  .object({
    type: z.enum(ALERT_TYPES),
    symbol: z.string().trim().min(1).max(20).transform((s) => s.toUpperCase().replace(/\.NS$/, '')),
    threshold: z.number().finite().min(-1e8).max(1e8),
    repeat: z.enum(['once', 'daily']).default('once'),
    note: z.string().max(200).default(''),
    enabled: z.boolean().default(true),
  })
  .superRefine((a, ctx) => {
    if (a.type === 'VIX_ABOVE') return;
    if (!isValidSymbol(a.symbol)) ctx.addIssue({ code: 'custom', path: ['symbol'], message: 'Invalid symbol' });
    if ((a.type === 'RSI_ABOVE' || a.type === 'RSI_BELOW') && (a.threshold <= 0 || a.threshold >= 100)) ctx.addIssue({ code: 'custom', path: ['threshold'], message: 'RSI level must be between 0 and 100' });
    if (a.type === 'VOLUME_SPIKE' && a.threshold <= 1) ctx.addIssue({ code: 'custom', path: ['threshold'], message: 'Volume multiple must be above 1' });
    if ((a.type === 'PRICE_ABOVE' || a.type === 'PRICE_BELOW') && a.threshold <= 0) ctx.addIssue({ code: 'custom', path: ['threshold'], message: 'Price must be positive' });
  });

export const settingsInput = z
  .object({
    capital: z.number().finite().min(1000).max(1e10),
    riskPerTradePct: z.number().finite().min(0.05).max(10),
    maxDailyLossPct: z.number().finite().min(0.1).max(25),
    maxTradesPerDay: z.number().int().min(1).max(100),
    maxOpenPositions: z.number().int().min(1).max(100),
    brokerageDeliveryFlat: z.number().finite().min(0).max(500),
    brokeragePct: z.number().finite().min(0).max(1),
    brokerageCap: z.number().finite().min(0).max(1000),
    brokerageOptionsFlat: z.number().finite().min(0).max(500),
    dpChargePerSell: z.number().finite().min(0).max(100),
    slabRate: z.number().finite().min(0).max(0.5),
    watchlistSort: z.enum(['symbol', 'changePct', 'volume']),
  })
  .partial();

export const backupInput = z.object({
  version: z.literal(1),
  confirm: z.literal(true),
  trades: z.array(z.record(z.unknown())).max(50000).default([]),
  holdings: z.array(z.record(z.unknown())).max(5000).default([]),
  watchlist: z.array(z.record(z.unknown())).max(500).default([]),
  alerts: z.array(z.record(z.unknown())).max(500).default([]),
  settings: z.record(z.unknown()).default({}),
});

export const loginInput = z.object({ password: z.string().min(1).max(256) });
