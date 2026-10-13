// Indian-locale formatting helpers. Safe on null/NaN so the UI never prints "NaN".

const inr0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function isNum(x: unknown): x is number {
  return typeof x === 'number' && Number.isFinite(x);
}

export function fmtNum(x: number | null | undefined, digits = 2): string {
  if (!isNum(x)) return '-';
  return digits === 0 ? inr0.format(x) : digits === 2 ? inr2.format(x) : x.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtINR(x: number | null | undefined, digits = 0): string {
  if (!isNum(x)) return '-';
  const sign = x < 0 ? '-' : '';
  return `${sign}₹${fmtNum(Math.abs(x), digits)}`;
}

/** Compact Indian units: 1.2 L, 3.4 Cr */
export function fmtCompactINR(x: number | null | undefined): string {
  if (!isNum(x)) return '-';
  const a = Math.abs(x);
  const sign = x < 0 ? '-' : '';
  if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
  return `${sign}₹${fmtNum(a, 0)}`;
}

export function fmtPct(x: number | null | undefined, digits = 2, signed = true): string {
  if (!isNum(x)) return '-';
  const s = signed && x > 0 ? '+' : '';
  return `${s}${x.toFixed(digits)}%`;
}

export function fmtSigned(x: number | null | undefined, digits = 2): string {
  if (!isNum(x)) return '-';
  return `${x > 0 ? '+' : ''}${fmtNum(x, digits)}`;
}

/** Open interest and volume: 1.23 L / 4.5 Cr style, no rupee sign. */
export function fmtQty(x: number | null | undefined): string {
  if (!isNum(x)) return '-';
  const a = Math.abs(x);
  const sign = x < 0 ? '-' : '';
  if (a >= 1e7) return `${sign}${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${sign}${(a / 1e5).toFixed(2)} L`;
  if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(1)} K`;
  return `${sign}${Math.round(a)}`;
}

const istFmt = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});
const istTimeFmt = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

export function fmtIst(iso: string | null | undefined): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return `${istFmt.format(d)} IST`;
}

export function fmtIstTime(iso: string | Date | null | undefined): string {
  if (!iso) return '-';
  const d = iso instanceof Date ? iso : new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return istTimeFmt.format(d);
}

export function relTime(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '-';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '-';
  const s = Math.round((now.getTime() - t) / 1000);
  if (s < 0) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** 'pos' | 'neg' | 'flat' for colouring (green up / red down, Indian convention). */
export function tone(x: number | null | undefined): 'pos' | 'neg' | 'flat' {
  if (!isNum(x) || x === 0) return 'flat';
  return x > 0 ? 'pos' : 'neg';
}
