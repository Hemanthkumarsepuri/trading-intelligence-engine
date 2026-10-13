// NSE data: static archive files (reliable, no cookies) and the interactive
// nseindia.com JSON APIs (best-effort: NSE's bot protection frequently blocks
// datacenter IPs, so every use has a fallback and a visible failure state).

import { previousTradingDay, istDateStr } from '../../shared/india';
import type { ChainRow, NormalizedChain, OptionLeg } from '../../shared/optionchain';
import { fetchText, fetchJson, ProviderError, BROWSER_UA, type FetchOpts } from '../http';

const ARCHIVE_HOSTS = ['https://nsearchives.nseindia.com', 'https://archives.nseindia.com'];

const ddmmyyyy = (d: string): string => `${d.slice(8, 10)}${d.slice(5, 7)}${d.slice(0, 4)}`;

async function archiveGet(paths: string[], opts: FetchOpts = {}): Promise<{ text: string; url: string }> {
  let last: unknown;
  for (const p of paths) {
    for (const host of ARCHIVE_HOSTS) {
      try {
        const { text } = await fetchText(host + p, { timeoutMs: 9000, retries: 0, ...opts });
        // Archive 404 pages are HTML; a CSV never starts with a tag.
        if (/^\s*<(!doctype|html)/i.test(text)) throw new ProviderError('MALFORMED', `HTML instead of CSV at ${host}${p}`);
        return { text, url: host + p };
      } catch (e) {
        last = e;
      }
    }
  }
  throw last instanceof Error ? last : new ProviderError('NETWORK', 'NSE archive unreachable');
}

/** Try today, then earlier trading days, until a dated file exists. */
async function archiveLatest(
  now: Date,
  build: (dateStr: string) => string[],
  extraHolidays: readonly string[],
  maxBack = 6,
  opts: FetchOpts = {},
): Promise<{ text: string; url: string; date: string }> {
  let date = istDateStr(now);
  let last: unknown;
  for (let i = 0; i <= maxBack; i++) {
    try {
      const r = await archiveGet(build(date), opts);
      return { ...r, date };
    } catch (e) {
      last = e;
      // a 403/429 means we are blocked, not that the file is missing: stop early
      if (e instanceof ProviderError && (e.kind === 'BLOCKED' || e.kind === 'RATE_LIMITED')) throw e;
    }
    date = previousTradingDay(date, extraHolidays);
  }
  throw last instanceof Error ? last : new ProviderError('HTTP', 'No recent archive file found');
}

// ---- CSV helpers --------------------------------------------------------------

export function splitCsv(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let q = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]!;
    if (q) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') q = false; else cur += ch;
    } else if (ch === '"') q = true;
    else if (ch === ',') { out.push(cur); cur = ''; } else cur += ch;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

export function csvRows(text: string, headerToken: string): { header: string[]; rows: string[][] } {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter((l) => l.trim());
  const idx = lines.findIndex((l) => splitCsv(l).some((c) => c.trim().toLowerCase() === headerToken.toLowerCase()));
  if (idx < 0) throw new ProviderError('MALFORMED', `CSV header containing "${headerToken}" not found`);
  const header = splitCsv(lines[idx]!);
  return { header, rows: lines.slice(idx + 1).map(splitCsv) };
}

const key = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');

// ---- Symbol master + sectors + lots ---------------------------------------------

export interface SymbolRow {
  symbol: string;
  name: string;
  isin: string;
}

export async function fetchEquityList(opts: FetchOpts = {}): Promise<SymbolRow[]> {
  const { text } = await archiveGet(['/content/equities/EQUITY_L.csv'], opts);
  const { header, rows } = csvRows(text, 'SYMBOL');
  const h = header.map(key);
  const iSym = h.indexOf('symbol');
  const iName = h.indexOf('nameofcompany');
  const iSeries = h.indexOf('series');
  const iIsin = h.findIndex((x) => x.startsWith('isin'));
  if (iSym < 0) throw new ProviderError('MALFORMED', 'EQUITY_L.csv: SYMBOL column missing');
  const out: SymbolRow[] = [];
  for (const r of rows) {
    const series = iSeries >= 0 ? r[iSeries] : 'EQ';
    if (series && !['EQ', 'BE', 'BZ', 'SM', 'ST'].includes(series)) continue;
    const symbol = r[iSym];
    if (!symbol) continue;
    out.push({ symbol, name: r[iName] ?? symbol, isin: r[iIsin] ?? '' });
  }
  if (out.length < 500) throw new ProviderError('MALFORMED', `EQUITY_L.csv parsed to only ${out.length} rows`);
  return out;
}

export async function fetchSectorMap(opts: FetchOpts = {}): Promise<Record<string, string>> {
  const { text } = await archiveGet(['/content/indices/ind_nifty500list.csv'], opts);
  const { header, rows } = csvRows(text, 'Symbol');
  const h = header.map(key);
  const iSym = h.indexOf('symbol');
  const iInd = h.indexOf('industry');
  if (iSym < 0 || iInd < 0) throw new ProviderError('MALFORMED', 'Nifty 500 list: Symbol/Industry columns missing');
  const map: Record<string, string> = {};
  for (const r of rows) if (r[iSym] && r[iInd]) map[r[iSym]!] = r[iInd]!;
  if (Object.keys(map).length < 200) throw new ProviderError('MALFORMED', 'Nifty 500 list parsed to too few rows');
  return map;
}

/** F&O stock lot sizes for the current series. */
export async function fetchLotSizes(opts: FetchOpts = {}): Promise<Record<string, number>> {
  const { text } = await archiveGet(['/content/fo/fo_mktlots.csv'], opts);
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter((l) => l.trim());
  // first data column is the underlying, second the symbol; the series columns follow (current month first)
  const out: Record<string, number> = {};
  for (const line of lines.slice(1)) {
    const c = splitCsv(line);
    const symbol = c[1];
    const lot = Number((c[2] ?? '').replace(/,/g, ''));
    if (symbol && Number.isFinite(lot) && lot > 0 && /^[A-Z0-9&-]+$/.test(symbol)) out[symbol] = lot;
  }
  if (Object.keys(out).length < 50) throw new ProviderError('MALFORMED', 'fo_mktlots.csv parsed to too few rows');
  return out;
}

// ---- F&O ban -----------------------------------------------------------------------

export function parseBanList(text: string): string[] {
  const out = new Set<string>();
  for (const line of text.replace(/^\uFEFF/, '').split(/\r?\n/)) {
    const cells = splitCsv(line).filter(Boolean);
    if (cells.length === 0) continue;
    const cand = cells[cells.length - 1]!.toUpperCase();
    // skip title/header lines
    if (/^(SECURITIES|SYMBOL|SR|S\.?NO|NO)/.test(cand) || /\s/.test(cand)) continue;
    if (/^[A-Z0-9&-]{1,20}$/.test(cand) && !/^\d+$/.test(cand)) out.add(cand);
  }
  return [...out].sort();
}

const MONTHS: Record<string, string> = { JAN: '01', FEB: '02', MAR: '03', APR: '04', MAY: '05', JUN: '06', JUL: '07', AUG: '08', SEP: '09', OCT: '10', NOV: '11', DEC: '12' };

/** Trade date printed in the ban file's title line ("Securities in Ban For Trade Date 13-OCT-2026"), or null. */
export function parseBanTradeDate(text: string): string | null {
  const m = /trade\s*date\s*[:-]?\s*(\d{1,2})[-/ ]([A-Za-z]{3}|\d{1,2})[-/ ](\d{4})/i.exec(text.slice(0, 400));
  if (!m) return null;
  const mon = /^\d+$/.test(m[2]!) ? m[2]!.padStart(2, '0') : MONTHS[m[2]!.toUpperCase()];
  if (!mon) return null;
  return `${m[3]}-${mon}-${m[1]!.padStart(2, '0')}`;
}

/**
 * F&O ban list. `asOf` is the trade date the list applies to, taken from the
 * file itself (or from the dated archive name). It is null when the date cannot
 * be established, and is never assumed to be "today".
 */
export async function fetchBanList(now: Date, extraHolidays: readonly string[] = [], opts: FetchOpts = {}): Promise<{ symbols: string[]; asOf: string | null }> {
  const r = await archiveLatest(now, (d) => ['/content/fo/fo_secban.csv', `/archives/fo/sec_ban/fo_secban_${ddmmyyyy(d)}.csv`], extraHolidays, 0, opts);
  const fromHeader = parseBanTradeDate(r.text);
  const fromName = r.url.includes('/sec_ban/fo_secban_') ? r.date : null;
  return { symbols: parseBanList(r.text), asOf: fromHeader ?? fromName };
}

// ---- Participant-wise open interest (FII / DII / Pro / Client) ------------------------

export interface ParticipantOI {
  date: string;
  participants: Record<'CLIENT' | 'DII' | 'FII' | 'PRO', {
    indexFutLong: number; indexFutShort: number;
    stockFutLong: number; stockFutShort: number;
    indexCallLong: number; indexCallShort: number; indexPutLong: number; indexPutShort: number;
  }>;
}

export function parseParticipantOi(text: string, date: string): ParticipantOI {
  const { header, rows } = csvRows(text, 'Client Type');
  const h = header.map(key);
  const col = (name: string) => h.indexOf(name);
  const idx = {
    type: col('clienttype'),
    ifl: col('futureindexlong'), ifs: col('futureindexshort'),
    sfl: col('futurestocklong'), sfs: col('futurestockshort'),
    icl: col('optionindexcalllong'), ics: col('optionindexcallshort'),
    ipl: col('optionindexputlong'), ips: col('optionindexputshort'),
  };
  if (Object.values(idx).some((i) => i < 0)) throw new ProviderError('MALFORMED', 'Participant OI: expected columns missing');
  // A blank or unparseable cell is a malformed file, never a zero position.
  const n = (r: string[], i: number): number => {
    const raw = (r[i] ?? '').replace(/,/g, '').trim();
    const v = raw === '' ? NaN : Number(raw);
    if (!Number.isFinite(v)) throw new ProviderError('MALFORMED', `Participant OI: non-numeric value in column ${i + 1}`);
    return v;
  };
  const participants = {} as ParticipantOI['participants'];
  for (const r of rows) {
    const t = (r[idx.type] ?? '').toUpperCase();
    if (t === 'CLIENT' || t === 'DII' || t === 'FII' || t === 'PRO') {
      participants[t] = {
        indexFutLong: n(r, idx.ifl), indexFutShort: n(r, idx.ifs),
        stockFutLong: n(r, idx.sfl), stockFutShort: n(r, idx.sfs),
        indexCallLong: n(r, idx.icl), indexCallShort: n(r, idx.ics),
        indexPutLong: n(r, idx.ipl), indexPutShort: n(r, idx.ips),
      };
    }
  }
  if (!participants.FII) throw new ProviderError('MALFORMED', 'Participant OI: FII row missing');
  return { date, participants };
}

export async function fetchParticipantOi(now: Date, extraHolidays: readonly string[] = [], opts: FetchOpts = {}): Promise<ParticipantOI> {
  const r = await archiveLatest(now, (d) => [`/content/nsccl/fao_participant_oi_${ddmmyyyy(d)}.csv`], extraHolidays, 6, opts);
  return parseParticipantOi(r.text, r.date);
}

/** FII index-futures positioning read, in plain numbers. */
export function fiiPositioning(oi: ParticipantOI) {
  const f = oi.participants.FII;
  const long = f.indexFutLong;
  const short = f.indexFutShort;
  const total = long + short;
  return {
    date: oi.date,
    indexFutNet: long - short,
    indexFutLongPct: total > 0 ? (long / total) * 100 : null,
    indexCallNet: f.indexCallLong - f.indexCallShort,
    indexPutNet: f.indexPutLong - f.indexPutShort,
    stockFutNet: f.stockFutLong - f.stockFutShort,
    dii: {
      indexFutNet: oi.participants.DII ? oi.participants.DII.indexFutLong - oi.participants.DII.indexFutShort : null,
    },
    client: {
      indexFutNet: oi.participants.CLIENT ? oi.participants.CLIENT.indexFutLong - oi.participants.CLIENT.indexFutShort : null,
    },
    pro: {
      indexFutNet: oi.participants.PRO ? oi.participants.PRO.indexFutLong - oi.participants.PRO.indexFutShort : null,
    },
  };
}

// ---- Delivery % (EOD bhavcopy) ----------------------------------------------------------

export interface DeliveryRow {
  symbol: string;
  date: string;
  deliveryPct: number | null;
  tradedQty: number | null;
  deliveredQty: number | null;
}

export async function fetchDeliveryMap(now: Date, extraHolidays: readonly string[] = [], opts: FetchOpts = {}): Promise<{ date: string; rows: Record<string, DeliveryRow> }> {
  const r = await archiveLatest(now, (d) => [`/products/content/sec_bhavdata_full_${ddmmyyyy(d)}.csv`], extraHolidays, 6, { timeoutMs: 15000, ...opts });
  const { header, rows } = csvRows(r.text, 'SYMBOL');
  const h = header.map(key);
  const iS = h.indexOf('symbol');
  const iSeries = h.indexOf('series');
  const iTq = h.indexOf('ttltrdqnty');
  const iDq = h.indexOf('delivqty');
  const iDp = h.indexOf('delivper');
  if (iS < 0 || iDp < 0) throw new ProviderError('MALFORMED', 'Bhavcopy: SYMBOL/DELIV_PER missing');
  const out: Record<string, DeliveryRow> = {};
  const nn = (s: string | undefined) => { const v = Number((s ?? '').replace(/,/g, '')); return Number.isFinite(v) && (s ?? '').trim() !== '-' && (s ?? '').trim() !== '' ? v : null; };
  for (const x of rows) {
    if (iSeries >= 0 && x[iSeries] !== 'EQ') continue;
    const sym = x[iS];
    if (!sym) continue;
    out[sym] = { symbol: sym, date: r.date, deliveryPct: nn(x[iDp]), tradedQty: nn(x[iTq]), deliveredQty: nn(x[iDq]) };
  }
  return { date: r.date, rows: out };
}

// ---- Interactive APIs (best-effort) ---------------------------------------------------------

let cookieJar: { cookie: string; at: number } | null = null;

async function nseCookie(opts: FetchOpts): Promise<string> {
  if (cookieJar && Date.now() - cookieJar.at < 4 * 60_000) return cookieJar.cookie;
  const { headers } = await fetchText('https://www.nseindia.com/', {
    headers: { 'User-Agent': BROWSER_UA, Accept: 'text/html,application/xhtml+xml', 'Accept-Language': 'en-US,en;q=0.9' },
    timeoutMs: 8000,
    retries: 0,
    ...opts,
  });
  const raw = headers.getSetCookie?.() ?? [];
  const cookie = raw.map((c) => c.split(';')[0]).join('; ');
  if (!cookie) throw new ProviderError('BLOCKED', 'NSE returned no session cookies (blocked or changed)');
  cookieJar = { cookie, at: Date.now() };
  return cookie;
}

export async function nseApi<T>(path: string, opts: FetchOpts = {}): Promise<T> {
  const cookie = await nseCookie(opts);
  return fetchJson<T>(`https://www.nseindia.com${path}`, {
    headers: { 'User-Agent': BROWSER_UA, Cookie: cookie, Referer: 'https://www.nseindia.com/', 'Accept-Language': 'en-US,en;q=0.9' },
    timeoutMs: 9000,
    retries: 0,
    ...opts,
  });
}

export interface FiiDiiDay {
  date: string;
  fii: { buy: number; sell: number; net: number } | null;
  dii: { buy: number; sell: number; net: number } | null;
}

export function parseFiiDii(rows: unknown): FiiDiiDay {
  if (!Array.isArray(rows)) throw new ProviderError('MALFORMED', 'FII/DII: not an array');
  const pick = (re: RegExp) => {
    const r = (rows as Record<string, unknown>[]).find((x) => re.test(String(x.category ?? '')));
    if (!r) return null;
    const b = Number(String(r.buyValue ?? '').replace(/,/g, ''));
    const s = Number(String(r.sellValue ?? '').replace(/,/g, ''));
    const n = Number(String(r.netValue ?? '').replace(/,/g, ''));
    if (![b, s, n].every(Number.isFinite)) return null;
    return { buy: b, sell: s, net: n };
  };
  const first = rows[0] as Record<string, unknown> | undefined;
  return { date: String(first?.date ?? ''), fii: pick(/fii|fpi/i), dii: pick(/dii/i) };
}

export async function fetchFiiDii(opts: FetchOpts = {}): Promise<FiiDiiDay> {
  return parseFiiDii(await nseApi<unknown>('/api/fiidiiTradeReact', opts));
}

export async function fetchNseHolidays(opts: FetchOpts = {}): Promise<string[]> {
  const body = await nseApi<{ CM?: { tradingDate?: string }[] }>('/api/holiday-master?type=trading', opts);
  const months: Record<string, string> = { Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12' };
  const out: string[] = [];
  for (const h of body.CM ?? []) {
    const m = String(h.tradingDate ?? '').match(/^(\d{2})-([A-Za-z]{3})-(\d{4})$/);
    if (m && months[m[2]!]) out.push(`${m[3]}-${months[m[2]!]}-${m[1]}`);
  }
  if (out.length === 0) throw new ProviderError('MALFORMED', 'NSE holiday master: no CM holidays parsed');
  return out;
}

export interface Breadth {
  advances: number;
  declines: number;
  unchanged: number;
}

type BreadthBody = { data?: { index?: string; advances?: string | number; declines?: string | number; unchanged?: string | number }[] };

export function parseBreadth(body: BreadthBody): Breadth {
  const row = body.data?.find((d) => d.index === 'NIFTY 500') ?? body.data?.find((d) => d.index === 'NIFTY 50');
  if (!row) throw new ProviderError('MALFORMED', 'allIndices: breadth row not found');
  const n = (x: unknown): number => {
    const v = typeof x === 'string' ? Number(x.replace(/,/g, '').trim() || NaN) : Number(x ?? NaN);
    if (!Number.isFinite(v)) throw new ProviderError('MALFORMED', 'allIndices: breadth counts missing or non-numeric');
    return v;
  };
  return { advances: n(row.advances), declines: n(row.declines), unchanged: n(row.unchanged) };
}

export async function fetchBreadth(opts: FetchOpts = {}): Promise<Breadth> {
  return parseBreadth(await nseApi<BreadthBody>('/api/allIndices', opts));
}


// ---- NSE option chain (best-effort fallback when no Upstox token) -------------------

interface NseLeg { openInterest?: number; changeinOpenInterest?: number; totalTradedVolume?: number; impliedVolatility?: number; lastPrice?: number; bidprice?: number; askPrice?: number; underlyingValue?: number }

export function parseNseChain(
  body: { records?: { expiryDates?: string[]; data?: { strikePrice?: number; expiryDate?: string; CE?: NseLeg; PE?: NseLeg }[]; underlyingValue?: number } },
  underlying: string,
  expiryIso: string,
  fetchedAt: string,
): NormalizedChain {
  const rec = body.records;
  if (!rec?.data) throw new ProviderError('MALFORMED', 'NSE option chain: records.data missing');
  const mon: Record<string, string> = { Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12' };
  const toIso = (d: string | undefined) => { const m = (d ?? '').match(/^(\d{2})-([A-Za-z]{3})-(\d{4})$/); return m && mon[m[2]!] ? `${m[3]}-${mon[m[2]!]}-${m[1]}` : ''; };
  const n = (x: unknown) => (typeof x === 'number' && Number.isFinite(x) ? x : null);
  const leg = (l: NseLeg | undefined): OptionLeg => ({
    ltp: n(l?.lastPrice),
    oi: n(l?.openInterest),
    prevOi: n(l?.openInterest) !== null && n(l?.changeinOpenInterest) !== null ? (l!.openInterest as number) - (l!.changeinOpenInterest as number) : null,
    volume: n(l?.totalTradedVolume),
    iv: n(l?.impliedVolatility) && (l!.impliedVolatility as number) > 0 ? (l!.impliedVolatility as number) : null,
    bid: n(l?.bidprice),
    ask: n(l?.askPrice),
  });
  const rows: ChainRow[] = [];
  for (const r of rec.data) {
    if (toIso(r.expiryDate) !== expiryIso || n(r.strikePrice) === null) continue;
    rows.push({ strike: r.strikePrice as number, ce: leg(r.CE), pe: leg(r.PE) });
  }
  if (rows.length === 0) throw new ProviderError('MALFORMED', `NSE option chain: no rows for expiry ${expiryIso}`);
  return { underlying, expiry: expiryIso, spot: n(rec.underlyingValue), rows: rows.sort((a, b) => a.strike - b.strike), source: 'nse', fetchedAt };
}

export function nseExpiries(body: { records?: { expiryDates?: string[] } }): string[] {
  const mon: Record<string, string> = { Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12' };
  return (body.records?.expiryDates ?? [])
    .map((d) => { const m = d.match(/^(\d{2})-([A-Za-z]{3})-(\d{4})$/); return m && mon[m[2]!] ? `${m[3]}-${mon[m[2]!]}-${m[1]}` : ''; })
    .filter(Boolean)
    .sort();
}

export async function fetchNseChainRaw(nseSymbol: string, opts: FetchOpts = {}) {
  return nseApi<Parameters<typeof parseNseChain>[0]>(`/api/option-chain-indices?symbol=${encodeURIComponent(nseSymbol)}`, opts);
}
