// Portfolio valuation, tax-lot awareness and holdings-CSV import. Pure functions.

import { LTCG_EXEMPTION } from './tax';
import { addDays, isLongTermEquity } from './india';
import type { Holding, Quote } from './types';

export interface HoldingView {
  id: string;
  symbol: string;
  qty: number;
  avgPrice: number;
  buyDate: string;
  invested: number;
  price: number | null;
  prevClose: number | null;
  value: number | null;
  pnl: number | null;
  pnlPct: number | null;
  dayPnl: number | null;
  daysHeld: number | null;
  /** null when the buy date is unknown */
  longTerm: boolean | null;
  daysToLongTerm: number | null;
  sector: string;
}

function daysBetween(a: string, b: string): number {
  const [y1, m1, d1] = a.split('-').map(Number) as [number, number, number];
  const [y2, m2, d2] = b.split('-').map(Number) as [number, number, number];
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86400000);
}

export function viewHolding(h: Holding, q: Quote | undefined, sector: string | undefined, today: string): HoldingView {
  const invested = h.qty * h.avgPrice;
  const price = q?.price ?? null;
  const value = price !== null ? price * h.qty : null;
  const pnl = value !== null ? value - invested : null;
  const known = /^\d{4}-\d{2}-\d{2}$/.test(h.buyDate);
  const longTerm = known ? isLongTermEquity(h.buyDate, today) : null;
  let daysToLongTerm: number | null = null;
  if (known && longTerm === false) {
    // first day it becomes long term = buy + 12 months + 1 day
    for (let d = 0; d < 400; d++) {
      if (isLongTermEquity(h.buyDate, addDays(today, d))) { daysToLongTerm = d; break; }
    }
  }
  return {
    id: h.id,
    symbol: h.symbol,
    qty: h.qty,
    avgPrice: h.avgPrice,
    buyDate: h.buyDate,
    invested,
    price,
    prevClose: q?.prevClose ?? null,
    value,
    pnl,
    pnlPct: pnl !== null && invested > 0 ? (pnl / invested) * 100 : null,
    dayPnl: q ? (q.price - q.prevClose) * h.qty : null,
    daysHeld: known ? daysBetween(h.buyDate, today) : null,
    longTerm,
    daysToLongTerm,
    sector: sector ?? 'Unclassified',
  };
}

export interface PortfolioSummary {
  invested: number;
  value: number;
  pnl: number;
  pnlPct: number | null;
  dayPnl: number;
  dayPnlPct: number | null;
  /** how many holdings had no live price (excluded from value, shown at cost) */
  unpriced: number;
  bySector: { sector: string; value: number; pct: number }[];
  concentration: { symbol: string; pct: number }[];
  unrealizedStcg: number;
  unrealizedLtcg: number;
  /** remaining tax-free LTCG this FY if all long-term holdings were sold today (excludes realized gains) */
  ltcgHeadroom: number;
  turningLongTerm: { symbol: string; days: number }[];
  warnings: string[];
}

export function summarizePortfolio(views: HoldingView[]): PortfolioSummary {
  let invested = 0;
  let value = 0;
  let dayPnl = 0;
  let prevValue = 0;
  let unpriced = 0;
  let stcg = 0;
  let ltcg = 0;
  const sector = new Map<string, number>();
  const bySym = new Map<string, number>();
  const turning: { symbol: string; days: number }[] = [];

  for (const v of views) {
    invested += v.invested;
    // value at cost when unpriced so allocation percentages stay meaningful
    const val = v.value ?? v.invested;
    if (v.value === null) unpriced++;
    value += val;
    if (v.dayPnl !== null && v.price !== null) {
      dayPnl += v.dayPnl;
      prevValue += v.price * v.qty - v.dayPnl;
    }
    sector.set(v.sector, (sector.get(v.sector) ?? 0) + val);
    bySym.set(v.symbol, (bySym.get(v.symbol) ?? 0) + val);
    if (v.pnl !== null) {
      if (v.longTerm === true) ltcg += v.pnl;
      else if (v.longTerm === false) stcg += v.pnl;
    }
    if (v.daysToLongTerm !== null && v.daysToLongTerm > 0 && v.daysToLongTerm <= 45 && (v.pnl ?? 0) > 0) {
      turning.push({ symbol: v.symbol, days: v.daysToLongTerm });
    }
  }

  const total = value || 1;
  const bySector = [...sector.entries()].map(([s, val]) => ({ sector: s, value: val, pct: (val / total) * 100 })).sort((a, b) => b.value - a.value);
  const concentration = [...bySym.entries()].map(([symbol, val]) => ({ symbol, pct: (val / total) * 100 })).sort((a, b) => b.pct - a.pct).slice(0, 5);

  const warnings: string[] = [];
  if (concentration[0] && concentration[0].pct > 25) warnings.push(`${concentration[0].symbol} is ${concentration[0].pct.toFixed(0)}% of the portfolio (above 25%).`);
  if (bySector[0] && bySector[0].sector !== 'Unclassified' && bySector[0].pct > 40) warnings.push(`${bySector[0].sector} is ${bySector[0].pct.toFixed(0)}% of the portfolio (above 40%).`);
  if (unpriced > 0) warnings.push(`${unpriced} holding(s) have no live price and are shown at cost.`);
  if (views.some((v) => v.longTerm === null)) warnings.push('Some holdings have no buy date, so STCG/LTCG cannot be classified. Edit them to add one.');

  return {
    invested,
    value,
    pnl: value - invested,
    pnlPct: invested > 0 ? ((value - invested) / invested) * 100 : null,
    dayPnl,
    dayPnlPct: prevValue > 0 ? (dayPnl / prevValue) * 100 : null,
    unpriced,
    bySector,
    concentration,
    unrealizedStcg: stcg,
    unrealizedLtcg: ltcg,
    ltcgHeadroom: Math.max(0, LTCG_EXEMPTION - Math.max(0, ltcg)),
    turningLongTerm: turning.sort((a, b) => a.days - b.days),
    warnings,
  };
}

// ---- CSV import --------------------------------------------------------------

function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let inQ = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]!;
    if (inQ) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') inQ = false;
      else cur += ch;
    } else if (ch === '"') inQ = true;
    else if (ch === ',') { out.push(cur); cur = ''; }
    else cur += ch;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');

function parseNum(s: string | undefined): number | null {
  if (s === undefined) return null;
  const n = Number(s.replace(/[,₹\s]/g, ''));
  return Number.isFinite(n) ? n : null;
}

function parseDate(s: string | undefined): string {
  if (!s) return '';
  const t = s.trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(t)) return t.slice(0, 10);
  const m = t.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$/); // dd-mm-yyyy (Indian order)
  if (m) return `${m[3]}-${m[2]!.padStart(2, '0')}-${m[1]!.padStart(2, '0')}`;
  return '';
}

export interface ImportResult {
  rows: { symbol: string; qty: number; avgPrice: number; buyDate: string }[];
  errors: string[];
}

/** Accepts Zerodha / Upstox / Groww style holdings exports and plain symbol,qty,price[,date] files. */
export function parseHoldingsCsv(text: string): ImportResult {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter((l) => l.trim().length > 0);
  const errors: string[] = [];
  if (lines.length < 2) return { rows: [], errors: ['File has no data rows.'] };

  const header = splitCsvLine(lines[0]!).map(norm);
  const find = (...names: string[]) => header.findIndex((h) => names.includes(h));
  const iSym = find('instrument', 'symbol', 'stock', 'scrip', 'tradingsymbol', 'name', 'stockname');
  const iQty = find('qty', 'quantity', 'qtyavailable', 'shares', 'holdingqty');
  const iAvg = find('avgcost', 'averagecost', 'avgprice', 'averageprice', 'buyprice', 'buyavg', 'avgbuyprice', 'averagebuyprice', 'price');
  const iDate = find('buydate', 'date', 'purchasedate', 'boughton', 'tradedate');

  if (iSym < 0 || iQty < 0 || iAvg < 0) {
    return { rows: [], errors: ['Could not find columns. Need at least: symbol (or instrument), quantity, average price.'] };
  }

  const rows: ImportResult['rows'] = [];
  for (let li = 1; li < lines.length; li++) {
    const cells = splitCsvLine(lines[li]!);
    const raw = (cells[iSym] ?? '').toUpperCase().replace(/\.NS$|\.BO$/, '').replace(/-(EQ|BE|SM)$/, '').trim();
    const qty = parseNum(cells[iQty]);
    const avg = parseNum(cells[iAvg]);
    if (!raw) continue;
    if (qty === null || qty <= 0 || avg === null || avg <= 0) {
      errors.push(`Row ${li + 1} (${raw}): invalid quantity or price, skipped.`);
      continue;
    }
    if (!/^[A-Z0-9&-]{1,20}$/.test(raw)) {
      errors.push(`Row ${li + 1}: "${raw}" does not look like an NSE symbol, skipped.`);
      continue;
    }
    rows.push({ symbol: raw, qty, avgPrice: avg, buyDate: iDate >= 0 ? parseDate(cells[iDate]) : '' });
  }
  return { rows, errors };
}
