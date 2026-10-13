// Market-reading helpers shared by server and UI. Descriptive, not predictive.

export interface Reading {
  label: string;
  tone: 'calm' | 'normal' | 'warn' | 'alert';
  detail: string;
}

/** India VIX regime bands used by most Indian derivatives traders. */
export function vixRegime(v: number | null): Reading {
  if (v === null || !Number.isFinite(v)) return { label: 'Unavailable', tone: 'normal', detail: 'India VIX could not be fetched.' };
  if (v < 12) return { label: 'Very low', tone: 'calm', detail: 'Option premiums are cheap and the market is complacent. Sudden shocks hurt more here; selling premium pays little for the risk.' };
  if (v < 15) return { label: 'Low', tone: 'calm', detail: 'Calm conditions. Expect ranges to be orderly; buying options is relatively cheap.' };
  if (v < 20) return { label: 'Normal', tone: 'normal', detail: 'Typical volatility for Indian markets.' };
  if (v < 25) return { label: 'Elevated', tone: 'warn', detail: 'Premiums are rich and daily swings are larger. Size down and widen stops, or avoid naked option selling.' };
  return { label: 'High fear', tone: 'alert', detail: 'Stress regime. Gaps and fast reversals are common; risk limits matter more than setups.' };
}

export interface BreadthReading {
  ratio: number | null;
  label: string;
}

export function breadthReading(adv: number, dec: number): BreadthReading {
  if (adv + dec === 0) return { ratio: null, label: 'No data' };
  const ratio = adv / Math.max(dec, 1);
  if (ratio >= 2) return { ratio, label: 'Broad advance' };
  if (ratio >= 1.2) return { ratio, label: 'Positive' };
  if (ratio > 0.8) return { ratio, label: 'Mixed' };
  if (ratio > 0.5) return { ratio, label: 'Negative' };
  return { ratio, label: 'Broad decline' };
}

/** Net institutional flow in INR crore -> plain label. */
export function flowLabel(netCr: number | null): string {
  if (netCr === null) return 'Unavailable';
  const a = Math.abs(netCr);
  const mag = a >= 5000 ? 'Heavy' : a >= 1500 ? 'Strong' : a >= 300 ? 'Moderate' : 'Light';
  return `${mag} ${netCr >= 0 ? 'buying' : 'selling'}`;
}

export function fiiFuturesRead(longPct: number | null): Reading {
  if (longPct === null) return { label: 'Unavailable', tone: 'normal', detail: 'Participant OI file not available.' };
  if (longPct >= 65) return { label: 'Net long heavy', tone: 'calm', detail: 'FIIs hold a large share of index futures on the long side.' };
  if (longPct >= 45) return { label: 'Balanced', tone: 'normal', detail: 'FII index-futures positioning is roughly balanced.' };
  if (longPct >= 30) return { label: 'Net short', tone: 'warn', detail: 'FIIs hold more short than long index-futures contracts. Positioning data only; it is often a hedge against cash holdings.' };
  return { label: 'Heavily short', tone: 'alert', detail: 'FIIs hold far more short than long index-futures contracts. Positioning data only; often a hedge against cash holdings.' };
}
