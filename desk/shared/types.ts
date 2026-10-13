// Shared domain types - imported by both the Railway API server and the React app.
// Keep this file dependency-free.

export type Segment = 'EQ_DELIVERY' | 'EQ_INTRADAY' | 'FUT' | 'OPT';
export type Side = 'LONG' | 'SHORT';

export interface Candle {
  /** epoch seconds, UTC */
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export type QuoteSource = 'upstox' | 'yahoo' | 'nse' | 'fixture';

export interface Quote {
  symbol: string;
  name?: string;
  price: number;
  prevClose: number;
  change: number;
  changePct: number;
  open?: number;
  high?: number;
  low?: number;
  volume?: number;
  /** when the exchange last traded this instrument (ISO, UTC) */
  marketTime: string | null;
  /** when we fetched it (ISO, UTC) */
  fetchedAt: string;
  source: QuoteSource;
}

export interface Meta {
  asOf: string;
  source: string;
  /** true when the upstream failed and a cached copy is being served */
  stale: boolean;
  /** human-readable data-quality notes (never silently dropped) */
  notes: string[];
}

export interface ApiOk<T> {
  ok: true;
  data: T;
  meta: Meta;
}
export interface ApiErr {
  ok: false;
  error: { code: string; message: string };
}
export type ApiResult<T> = ApiOk<T> | ApiErr;

// ---- Journal -------------------------------------------------------------

export interface Trade {
  id: string;
  symbol: string;
  segment: Segment;
  side: Side;
  /** units: shares for equity, (lots * lotSize) for F&O */
  qty: number;
  entryPrice: number;
  /** null = still open */
  exitPrice: number | null;
  entryAt: string;
  exitAt: string | null;
  stopLoss: number | null;
  target: number | null;
  setup: string;
  tags: string[];
  /** 1 (calm) .. 5 (heated) self-rated at entry */
  emotion: number | null;
  planFollowed: boolean | null;
  notes: string;
  createdAt: string;
  updatedAt: string;
}

export interface Holding {
  id: string;
  symbol: string;
  qty: number;
  avgPrice: number;
  /** YYYY-MM-DD */
  buyDate: string;
  notes: string;
}

export interface WatchItem {
  symbol: string;
  note: string;
  /** user-drawn levels */
  support: number | null;
  resistance: number | null;
  addedAt: string;
}

export type AlertType =
  | 'PRICE_ABOVE'
  | 'PRICE_BELOW'
  | 'DAY_CHANGE_ABOVE'
  | 'DAY_CHANGE_BELOW'
  | 'RSI_ABOVE'
  | 'RSI_BELOW'
  | 'VOLUME_SPIKE'
  | 'VIX_ABOVE'
  | 'FNO_BAN';

export interface Alert {
  id: string;
  type: AlertType;
  /** NSE symbol, or INDIAVIX / NIFTY for index-level alerts */
  symbol: string;
  threshold: number;
  enabled: boolean;
  /** once = disable after firing; daily = at most once per trading day */
  repeat: 'once' | 'daily';
  note: string;
  lastTriggeredAt: string | null;
  createdAt: string;
}

export interface AlertEvent {
  id: string;
  alertId: string;
  at: string;
  message: string;
  delivered: boolean;
  /** delivery attempts so far (absent on events written before retries existed = 1) */
  attempts?: number;
  /** earliest time a retry may be attempted; also acts as a lease against duplicate retries */
  nextAttemptAt?: string | null;
  lastError?: string | null;
  deliveredAt?: string | null;
}

export interface TelegramFailure {
  at: string;
  kind: string;
  category: string;
  message: string;
}

export interface TelegramLog {
  lastSuccessAt: string | null;
  lastSuccessKind: string | null;
  lastTestAt: string | null;
  failures: TelegramFailure[];
}

export interface Settings {
  /** trading capital in INR - the denominator for all risk percentages */
  capital: number;
  riskPerTradePct: number;
  maxDailyLossPct: number;
  maxTradesPerDay: number;
  maxOpenPositions: number;
  /** delivery brokerage as flat INR per executed order (0 for most discount brokers) */
  brokerageDeliveryFlat: number;
  /** intraday / futures: percent of turnover, capped at brokerageCap per order */
  brokeragePct: number;
  brokerageCap: number;
  /** options: flat INR per executed order */
  brokerageOptionsFlat: number;
  /** DP charge per scrip per delivery sell day, INR (incl. GST) */
  dpChargePerSell: number;
  /** the user's marginal slab rate for business-income estimates (fraction, e.g. 0.30) */
  slabRate: number;
  watchlistSort: 'symbol' | 'changePct' | 'volume';
}

export const DEFAULT_SETTINGS: Settings = {
  capital: 500000,
  riskPerTradePct: 1,
  maxDailyLossPct: 2,
  maxTradesPerDay: 6,
  maxOpenPositions: 5,
  brokerageDeliveryFlat: 0,
  brokeragePct: 0.03,
  brokerageCap: 20,
  brokerageOptionsFlat: 20,
  dpChargePerSell: 15.34,
  slabRate: 0.3,
  watchlistSort: 'symbol',
};

// ---- Market ----------------------------------------------------------------

export interface SessionState {
  phase: 'OPEN' | 'PRE_OPEN' | 'CLOSED' | 'SPECIAL';
  label: string;
  isTradingDay: boolean;
  /** false when the holiday calendar does not cover this year */
  calendarKnown: boolean;
  reason: string;
  istNow: string;
  nextChangeLabel: string | null;
  nextChangeAt: string | null;
}

export interface ProviderHealth {
  id: string;
  label: string;
  configured: boolean;
  ok: boolean | null;
  detail: string;
  latencyMs: number | null;
  checkedAt: string | null;
}
