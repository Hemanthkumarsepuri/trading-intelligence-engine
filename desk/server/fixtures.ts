// Deterministic demo data for DESK_OFFLINE=1 (UI development, screenshots,
// demos without network). Everything carries source 'fixture' and the API adds
// a DEMO DATA note, so it can never be mistaken for a real market print.

import type { ChainRow, NormalizedChain } from '../shared/optionchain';
import type { Candle, Quote } from '../shared/types';
import type { NewsItem } from './providers/news';
import type { FiiDiiDay, ParticipantOI } from './providers/nse';
import type { IpoRow } from './providers/upstox';

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function rng(seed: number): () => number {
  let a = seed || 1;
  return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

const BASE: Record<string, number> = {
  '^NSEI': 25120, '^NSEBANK': 55340, '^BSESN': 82210, '^CNXFIN': 26480, 'NIFTY_MID_SELECT.NS': 13050, '^INDIAVIX': 14.6,
  '^GSPC': 6890, '^IXIC': 23400, '^DJI': 46800, '^N225': 47200, '^HSI': 26100, '000001.SS': 3900, '^FTSE': 9500, '^GDAXI': 24100,
  'BZ=F': 66.4, 'GC=F': 3980, 'SI=F': 47.8, 'INR=X': 88.4, 'DX-Y.NYB': 99.1, '^TNX': 4.12,
};

export function basePrice(yahooSymbol: string): number {
  if (BASE[yahooSymbol]) return BASE[yahooSymbol]!;
  if (yahooSymbol.startsWith('^CNX') || yahooSymbol.endsWith('.NS') && yahooSymbol.startsWith('NIFTY')) return 8000 + (hash(yahooSymbol) % 40000);
  return 150 + (hash(yahooSymbol) % 4800);
}

/** 1y of daily candles ending today (IST), ~1.2% daily vol with mild drift */
export function fixtureDaily(yahooSymbol: string, now: Date, days = 260): Candle[] {
  const r = rng(hash(yahooSymbol));
  const end = basePrice(yahooSymbol);
  const vol = yahooSymbol.startsWith('^') ? 0.008 : 0.014;
  const closes: number[] = [end];
  for (let i = 1; i < days; i++) closes.unshift(closes[0]! / (1 + (r() - 0.48) * 2 * vol));
  const out: Candle[] = [];
  const todayMidnight = Math.floor(now.getTime() / 86400000) * 86400;
  for (let i = 0; i < days; i++) {
    const c = closes[i]!;
    const o = i === 0 ? c : closes[i - 1]! * (1 + (r() - 0.5) * 0.004);
    const h = Math.max(o, c) * (1 + r() * 0.008);
    const l = Math.min(o, c) * (1 - r() * 0.008);
    out.push({ t: todayMidnight - (days - 1 - i) * 86400 + 3.75 * 3600, o, h, l, c, v: Math.round(800_000 + r() * 4_000_000) });
  }
  return out;
}

export function fixtureQuote(id: string, yahooSymbol: string, now: Date): Quote {
  const d = fixtureDaily(yahooSymbol, now, 8);
  const last = d[d.length - 1]!;
  const prev = d[d.length - 2]!;
  // small intraday wobble so refreshes visibly move
  const wob = 1 + Math.sin(now.getTime() / 60000 + hash(yahooSymbol)) * 0.0008;
  const price = last.c * wob;
  return {
    symbol: id, price, prevClose: prev.c, change: price - prev.c, changePct: ((price - prev.c) / prev.c) * 100,
    open: last.o, high: Math.max(last.h, price), low: Math.min(last.l, price), volume: last.v ?? undefined,
    marketTime: now.toISOString(), fetchedAt: now.toISOString(), source: 'fixture',
  };
}

export function fixtureChain(underlying: string, spot: number, expiry: string, step: number, now: Date): NormalizedChain {
  const r = rng(hash(underlying + expiry));
  const atm = Math.round(spot / step) * step;
  const rows: ChainRow[] = [];
  for (let k = -18; k <= 18; k++) {
    const strike = atm + k * step;
    const dist = Math.abs(k);
    const bell = Math.exp(-(dist * dist) / 90);
    const ceOi = Math.round((150000 + r() * 120000) * bell * (k >= 0 ? 1.5 : 0.6) * 10);
    const peOi = Math.round((150000 + r() * 120000) * bell * (k <= 0 ? 1.5 : 0.6) * 10);
    const intrinsicCe = Math.max(0, spot - strike);
    const intrinsicPe = Math.max(0, strike - spot);
    const tv = Math.max(2, 140 * Math.exp(-(dist * dist) / 40));
    rows.push({
      strike,
      ce: { ltp: +(intrinsicCe + tv).toFixed(2), oi: ceOi, prevOi: Math.round(ceOi * (0.85 + r() * 0.25)), volume: Math.round(ceOi * (0.5 + r())), iv: +(12 + dist * 0.35 + r()).toFixed(2), bid: null, ask: null },
      pe: { ltp: +(intrinsicPe + tv).toFixed(2), oi: peOi, prevOi: Math.round(peOi * (0.85 + r() * 0.25)), volume: Math.round(peOi * (0.5 + r())), iv: +(12.5 + dist * 0.4 + r()).toFixed(2), bid: null, ask: null },
    });
  }
  return { underlying, expiry, spot, rows, source: 'fixture', fetchedAt: now.toISOString() };
}

export function fixtureNews(now: Date): NewsItem[] {
  const mk = (i: number, title: string, source: string, tone: NewsItem['tone'], tags: string[]): NewsItem => ({
    id: `fx${i}`, title, link: '', source, publishedAt: new Date(now.getTime() - i * 47 * 60000).toISOString(), tone, tags,
  });
  return [
    mk(1, 'Sensex, Nifty open firm as banks lead; FIIs turn net buyers', 'Demo feed', 'positive', ['FII / DII']),
    mk(2, 'RBI keeps repo rate unchanged, maintains neutral stance', 'Demo feed', 'neutral', ['RBI / Rates']),
    mk(3, 'Brent crude slips below $67 on demand worries; OMCs in focus', 'Demo feed', 'negative', ['Crude / Commodities']),
    mk(4, 'IT majors report mixed Q2 numbers; deal pipeline commentary awaited', 'Demo feed', 'neutral', ['Results']),
    mk(5, 'SEBI tightens disclosure norms for SME IPOs', 'Demo feed', 'neutral', ['SEBI / Regulation', 'IPO']),
    mk(6, 'Rupee weakens past 88.4 as dollar index firms', 'Demo feed', 'negative', ['Rupee', 'Global']),
  ];
}

export function fixtureFiiDii(now: Date): FiiDiiDay {
  const r = rng(hash(now.toISOString().slice(0, 10)));
  const fiiNet = Math.round((r() - 0.5) * 6000);
  const diiNet = Math.round((r() - 0.3) * 5000);
  return {
    date: now.toISOString().slice(0, 10),
    fii: { buy: 12000 + Math.abs(fiiNet), sell: 12000 + (fiiNet < 0 ? Math.abs(fiiNet) * 2 : 0), net: fiiNet },
    dii: { buy: 11000 + Math.abs(diiNet), sell: 9000, net: diiNet },
  };
}

export function fixtureParticipantOi(now: Date): ParticipantOI {
  const p = (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number) => ({ indexFutLong: a, indexFutShort: b, stockFutLong: c, stockFutShort: d, indexCallLong: e, indexCallShort: f, indexPutLong: g, indexPutShort: h });
  return {
    date: now.toISOString().slice(0, 10),
    participants: {
      CLIENT: p(160000, 90000, 2100000, 900000, 600000, 700000, 650000, 640000),
      DII: p(60000, 180000, 2000000, 2500000, 20000, 30000, 25000, 24000),
      FII: p(95000, 140000, 2300000, 1900000, 400000, 620000, 580000, 430000),
      PRO: p(70000, 80000, 600000, 700000, 500000, 520000, 480000, 530000),
    },
  };
}

export function fixtureIpos(): IpoRow[] {
  return [
    { id: 'fx1', name: 'Demo Industries Ltd', symbol: null, status: 'open', type: 'regular', issueSizeCr: 1250, priceBand: '310-326', opens: '2026-10-12', closes: '2026-10-14', subscription: '2.4x' },
    { id: 'fx2', name: 'Sample Foods SME', symbol: null, status: 'upcoming', type: 'sme', issueSizeCr: 42, priceBand: '98', opens: '2026-10-16', closes: '2026-10-20', subscription: null },
  ];
}
