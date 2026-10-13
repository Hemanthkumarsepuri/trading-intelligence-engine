// Instrument universe: indices, sectors, global cues. Config, not logic - if a
// vendor renames a symbol, fix it here. Anything a provider cannot resolve is
// reported in the response notes instead of being silently dropped.

export interface IndexDef {
  id: string;
  name: string;
  yahoo: string;
  /** Upstox instrument_key (index) */
  upstox?: string;
}

export const MAIN_INDICES: IndexDef[] = [
  { id: 'NIFTY', name: 'Nifty 50', yahoo: '^NSEI', upstox: 'NSE_INDEX|Nifty 50' },
  { id: 'BANKNIFTY', name: 'Bank Nifty', yahoo: '^NSEBANK', upstox: 'NSE_INDEX|Nifty Bank' },
  { id: 'SENSEX', name: 'Sensex', yahoo: '^BSESN', upstox: 'BSE_INDEX|SENSEX' },
  { id: 'FINNIFTY', name: 'Fin Nifty', yahoo: '^CNXFIN', upstox: 'NSE_INDEX|Nifty Fin Service' },
  { id: 'MIDCPNIFTY', name: 'Midcap Select', yahoo: 'NIFTY_MID_SELECT.NS', upstox: 'NSE_INDEX|NIFTY MID SELECT' },
];

export const VIX_DEF: IndexDef = { id: 'INDIAVIX', name: 'India VIX', yahoo: '^INDIAVIX', upstox: 'NSE_INDEX|India VIX' };

export const SECTOR_INDICES: IndexDef[] = [
  { id: 'IT', name: 'IT', yahoo: '^CNXIT', upstox: 'NSE_INDEX|Nifty IT' },
  { id: 'AUTO', name: 'Auto', yahoo: '^CNXAUTO', upstox: 'NSE_INDEX|Nifty Auto' },
  { id: 'FMCG', name: 'FMCG', yahoo: '^CNXFMCG', upstox: 'NSE_INDEX|Nifty FMCG' },
  { id: 'PHARMA', name: 'Pharma', yahoo: '^CNXPHARMA', upstox: 'NSE_INDEX|Nifty Pharma' },
  { id: 'METAL', name: 'Metal', yahoo: '^CNXMETAL', upstox: 'NSE_INDEX|Nifty Metal' },
  { id: 'ENERGY', name: 'Energy', yahoo: '^CNXENERGY', upstox: 'NSE_INDEX|Nifty Energy' },
  { id: 'REALTY', name: 'Realty', yahoo: '^CNXREALTY', upstox: 'NSE_INDEX|Nifty Realty' },
  { id: 'PSUBANK', name: 'PSU Bank', yahoo: '^CNXPSUBANK', upstox: 'NSE_INDEX|Nifty PSU Bank' },
  { id: 'MEDIA', name: 'Media', yahoo: '^CNXMEDIA', upstox: 'NSE_INDEX|Nifty Media' },
  { id: 'INFRA', name: 'Infra', yahoo: '^CNXINFRA', upstox: 'NSE_INDEX|Nifty Infra' },
  { id: 'FINSERV', name: 'Fin Services', yahoo: '^CNXFIN', upstox: 'NSE_INDEX|Nifty Fin Service' },
  { id: 'PVTBANK', name: 'Private Bank', yahoo: 'NIFTY_PVT_BANK.NS', upstox: 'NSE_INDEX|Nifty Pvt Bank' },
];

export type GlobalGroup = 'US' | 'ASIA' | 'EUROPE' | 'COMMODITY' | 'FX' | 'RATES';

export interface GlobalDef {
  id: string;
  name: string;
  yahoo: string;
  group: GlobalGroup;
  /** why an Indian trader cares */
  why: string;
}

export const GLOBAL_CUES: GlobalDef[] = [
  { id: 'SPX', name: 'S&P 500', yahoo: '^GSPC', group: 'US', why: 'Overnight lead for Indian large caps' },
  { id: 'NDX', name: 'Nasdaq', yahoo: '^IXIC', group: 'US', why: 'Drives IT-services sentiment' },
  { id: 'DJI', name: 'Dow Jones', yahoo: '^DJI', group: 'US', why: 'US cyclicals' },
  { id: 'N225', name: 'Nikkei 225', yahoo: '^N225', group: 'ASIA', why: 'Asian session tone' },
  { id: 'HSI', name: 'Hang Seng', yahoo: '^HSI', group: 'ASIA', why: 'EM flows and China risk' },
  { id: 'SSE', name: 'Shanghai', yahoo: '000001.SS', group: 'ASIA', why: 'Metals and China demand' },
  { id: 'FTSE', name: 'FTSE 100', yahoo: '^FTSE', group: 'EUROPE', why: 'Europe session' },
  { id: 'DAX', name: 'DAX', yahoo: '^GDAXI', group: 'EUROPE', why: 'Europe session' },
  { id: 'BRENT', name: 'Brent crude', yahoo: 'BZ=F', group: 'COMMODITY', why: 'India imports ~85% of its crude: hurts OMCs, paints, aviation; helps ONGC' },
  { id: 'GOLD', name: 'Gold', yahoo: 'GC=F', group: 'COMMODITY', why: 'Safe-haven demand; jewellery and gold-loan names' },
  { id: 'SILVER', name: 'Silver', yahoo: 'SI=F', group: 'COMMODITY', why: 'Industrial and safe-haven demand' },
  { id: 'USDINR', name: 'USD/INR', yahoo: 'INR=X', group: 'FX', why: 'A weaker rupee helps IT/pharma exporters, hurts importers and FPI returns' },
  { id: 'DXY', name: 'Dollar index', yahoo: 'DX-Y.NYB', group: 'FX', why: 'A strong dollar usually means FPI outflows from EM' },
  { id: 'US10Y', name: 'US 10Y yield', yahoo: '^TNX', group: 'RATES', why: 'Rising US yields pull FPI money out of Indian equities' },
];

export interface OptionUnderlying {
  id: string;
  name: string;
  upstoxKey: string;
  /** symbol used by NSE's own option-chain API */
  nseSymbol: string;
  yahoo: string;
}

export const OPTION_UNDERLYINGS: OptionUnderlying[] = [
  { id: 'NIFTY', name: 'Nifty 50', upstoxKey: 'NSE_INDEX|Nifty 50', nseSymbol: 'NIFTY', yahoo: '^NSEI' },
  { id: 'BANKNIFTY', name: 'Bank Nifty', upstoxKey: 'NSE_INDEX|Nifty Bank', nseSymbol: 'BANKNIFTY', yahoo: '^NSEBANK' },
  { id: 'FINNIFTY', name: 'Fin Nifty', upstoxKey: 'NSE_INDEX|Nifty Fin Service', nseSymbol: 'FINNIFTY', yahoo: '^CNXFIN' },
  { id: 'MIDCPNIFTY', name: 'Midcap Select', upstoxKey: 'NSE_INDEX|NIFTY MID SELECT', nseSymbol: 'MIDCPNIFTY', yahoo: 'NIFTY_MID_SELECT.NS' },
];

/**
 * Used only if NSE's equity list cannot be fetched, so symbol search and the
 * default watchlist still work. Names are the common ones, not legal names.
 */
export const FALLBACK_SYMBOLS: { symbol: string; name: string }[] = [
  { symbol: 'RELIANCE', name: 'Reliance Industries' }, { symbol: 'TCS', name: 'Tata Consultancy Services' },
  { symbol: 'HDFCBANK', name: 'HDFC Bank' }, { symbol: 'ICICIBANK', name: 'ICICI Bank' },
  { symbol: 'INFY', name: 'Infosys' }, { symbol: 'SBIN', name: 'State Bank of India' },
  { symbol: 'BHARTIARTL', name: 'Bharti Airtel' }, { symbol: 'ITC', name: 'ITC' },
  { symbol: 'LT', name: 'Larsen & Toubro' }, { symbol: 'KOTAKBANK', name: 'Kotak Mahindra Bank' },
  { symbol: 'AXISBANK', name: 'Axis Bank' }, { symbol: 'HINDUNILVR', name: 'Hindustan Unilever' },
  { symbol: 'BAJFINANCE', name: 'Bajaj Finance' }, { symbol: 'MARUTI', name: 'Maruti Suzuki' },
  { symbol: 'SUNPHARMA', name: 'Sun Pharmaceutical' }, { symbol: 'TITAN', name: 'Titan Company' },
  { symbol: 'ULTRACEMCO', name: 'UltraTech Cement' }, { symbol: 'ASIANPAINT', name: 'Asian Paints' },
  { symbol: 'NTPC', name: 'NTPC' }, { symbol: 'POWERGRID', name: 'Power Grid Corporation' },
  { symbol: 'ONGC', name: 'Oil & Natural Gas Corporation' }, { symbol: 'COALINDIA', name: 'Coal India' },
  { symbol: 'TATASTEEL', name: 'Tata Steel' }, { symbol: 'JSWSTEEL', name: 'JSW Steel' },
  { symbol: 'HCLTECH', name: 'HCL Technologies' }, { symbol: 'WIPRO', name: 'Wipro' },
  { symbol: 'TECHM', name: 'Tech Mahindra' }, { symbol: 'M&M', name: 'Mahindra & Mahindra' },
  { symbol: 'BAJAJ-AUTO', name: 'Bajaj Auto' }, { symbol: 'EICHERMOT', name: 'Eicher Motors' },
  { symbol: 'HEROMOTOCO', name: 'Hero MotoCorp' }, { symbol: 'CIPLA', name: 'Cipla' },
  { symbol: 'DRREDDY', name: "Dr. Reddy's Laboratories" }, { symbol: 'APOLLOHOSP', name: 'Apollo Hospitals' },
  { symbol: 'NESTLEIND', name: 'Nestle India' }, { symbol: 'BRITANNIA', name: 'Britannia Industries' },
  { symbol: 'GRASIM', name: 'Grasim Industries' }, { symbol: 'HINDALCO', name: 'Hindalco Industries' },
  { symbol: 'ADANIENT', name: 'Adani Enterprises' }, { symbol: 'ADANIPORTS', name: 'Adani Ports & SEZ' },
  { symbol: 'BAJAJFINSV', name: 'Bajaj Finserv' }, { symbol: 'INDUSINDBK', name: 'IndusInd Bank' },
  { symbol: 'TATACONSUM', name: 'Tata Consumer Products' }, { symbol: 'SBILIFE', name: 'SBI Life Insurance' },
  { symbol: 'HDFCLIFE', name: 'HDFC Life Insurance' }, { symbol: 'BEL', name: 'Bharat Electronics' },
];

export const DEFAULT_WATCHLIST = ['RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'SBIN', 'ITC', 'LT'];

const SYMBOL_RE = /^[A-Z0-9][A-Z0-9&-]{0,19}$/;
export function isValidSymbol(s: string): boolean {
  return SYMBOL_RE.test(s);
}
export function cleanSymbol(s: string): string {
  return s.trim().toUpperCase().replace(/\.NS$|\.BO$/, '');
}
