// Indian transaction-cost model (NSE, retail, FY 2026-27).
//
// Rates verified against public broker disclosures and the Budget 2026 STT
// change (futures 0.05%, options 0.15%, both on the sell side, from 1 Apr
// 2026). They are data, not logic: if a rate changes, edit RATES below and
// the dated note. Brokerage is user-configurable in Settings because it is
// broker-specific. Always cross-check one trade against your contract note.

import type { Segment, Settings, Side, Trade } from './types';

export const RATES_AS_OF = '2026-04-01';

interface SegmentRates {
  /** STT as a fraction of turnover, applied on the listed side(s) */
  sttBuy: number;
  sttSell: number;
  /** NSE exchange transaction charge, fraction of turnover */
  exchange: number;
  /** stamp duty, buy side only */
  stampBuy: number;
}

export const RATES: Record<Segment, SegmentRates> = {
  EQ_DELIVERY: { sttBuy: 0.001, sttSell: 0.001, exchange: 0.0000307, stampBuy: 0.00015 },
  EQ_INTRADAY: { sttBuy: 0, sttSell: 0.00025, exchange: 0.0000307, stampBuy: 0.00003 },
  FUT: { sttBuy: 0, sttSell: 0.0005, exchange: 0.0000183, stampBuy: 0.00002 },
  // options: STT and exchange charge are on PREMIUM turnover
  OPT: { sttBuy: 0, sttSell: 0.0015, exchange: 0.0003553, stampBuy: 0.00003 },
};

/** SEBI turnover fee: INR 10 per crore */
export const SEBI_FEE = 10 / 1e7;
export const GST_RATE = 0.18;

export interface ChargeBreakdown {
  brokerage: number;
  stt: number;
  exchange: number;
  sebi: number;
  stamp: number;
  gst: number;
  dp: number;
  total: number;
}

export type Leg = 'BUY' | 'SELL';

type BrokerageSettings = Pick<
  Settings,
  'brokerageDeliveryFlat' | 'brokeragePct' | 'brokerageCap' | 'brokerageOptionsFlat' | 'dpChargePerSell'
>;

const r2 = (x: number): number => Math.round(x * 100) / 100;

/** Charges for ONE executed leg (one order). */
export function legCharges(segment: Segment, leg: Leg, price: number, qty: number, s: BrokerageSettings): ChargeBreakdown {
  const turnover = price * qty;
  const rate = RATES[segment];

  let brokerage: number;
  if (segment === 'EQ_DELIVERY') brokerage = s.brokerageDeliveryFlat;
  else if (segment === 'OPT') brokerage = s.brokerageOptionsFlat;
  else brokerage = Math.min(turnover * (s.brokeragePct / 100), s.brokerageCap);

  const stt = turnover * (leg === 'BUY' ? rate.sttBuy : rate.sttSell);
  const exchange = turnover * rate.exchange;
  const sebi = turnover * SEBI_FEE;
  const stamp = leg === 'BUY' ? turnover * rate.stampBuy : 0;
  const gst = (brokerage + exchange + sebi) * GST_RATE;
  // DP charge applies once per scrip on a delivery SELL; already includes GST.
  const dp = segment === 'EQ_DELIVERY' && leg === 'SELL' ? s.dpChargePerSell : 0;

  const total = brokerage + stt + exchange + sebi + stamp + gst + dp;
  return {
    brokerage: r2(brokerage),
    stt: r2(stt),
    exchange: r2(exchange),
    sebi: r2(sebi),
    stamp: r2(stamp),
    gst: r2(gst),
    dp: r2(dp),
    total: r2(total),
  };
}

function add(a: ChargeBreakdown, b: ChargeBreakdown): ChargeBreakdown {
  return {
    brokerage: r2(a.brokerage + b.brokerage),
    stt: r2(a.stt + b.stt),
    exchange: r2(a.exchange + b.exchange),
    sebi: r2(a.sebi + b.sebi),
    stamp: r2(a.stamp + b.stamp),
    gst: r2(a.gst + b.gst),
    dp: r2(a.dp + b.dp),
    total: r2(a.total + b.total),
  };
}

export const ZERO_CHARGES: ChargeBreakdown = { brokerage: 0, stt: 0, exchange: 0, sebi: 0, stamp: 0, gst: 0, dp: 0, total: 0 };

function legsFor(side: Side): { entry: Leg; exit: Leg } {
  return side === 'LONG' ? { entry: 'BUY', exit: 'SELL' } : { entry: 'SELL', exit: 'BUY' };
}

export interface TradeEconomics {
  gross: number;
  charges: ChargeBreakdown;
  net: number;
  closed: boolean;
}

/** Gross P&L, charges and net P&L. Open trades include the entry leg only. */
export function tradeEconomics(t: Trade, s: BrokerageSettings): TradeEconomics {
  const { entry, exit } = legsFor(t.side);
  const entryCharges = legCharges(t.segment, entry, t.entryPrice, t.qty, s);
  if (t.exitPrice === null) {
    return { gross: 0, charges: entryCharges, net: 0, closed: false };
  }
  const exitCharges = legCharges(t.segment, exit, t.exitPrice, t.qty, s);
  const charges = add(entryCharges, exitCharges);
  const gross = (t.side === 'LONG' ? t.exitPrice - t.entryPrice : t.entryPrice - t.exitPrice) * t.qty;
  return { gross: r2(gross), charges, net: r2(gross - charges.total), closed: true };
}

/** Break-even move needed to cover round-trip charges, in price points. */
export function breakEvenPoints(segment: Segment, side: Side, price: number, qty: number, s: BrokerageSettings): number {
  if (qty <= 0) return 0;
  const exitGuess = price;
  const { entry, exit } = legsFor(side);
  const total = legCharges(segment, entry, price, qty, s).total + legCharges(segment, exit, exitGuess, qty, s).total;
  return r2(total / qty);
}
