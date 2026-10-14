// Workbench alerts: derived from deterministic state, deduplicated, and gated on data quality.
// In-app only. Nothing here sends a notification; external delivery stays under the existing
// (disabled-by-default) Telegram settings.

import type { BreakoutSetup } from './breakout';
import type { Timeframe } from './candles';
import type { ContractValidation } from './contract';
import type { Upcoming } from './events';
import type { RelVol } from './levels';
import { isActionable, type DataState } from './quality';

export type WbAlertKind =
  | 'APPROACHING_LEVEL'
  | 'POTENTIAL_BREAKOUT'
  | 'CONFIRMED_BREAKOUT'
  | 'FAILED_BREAKOUT'
  | 'ABNORMAL_RELATIVE_VOLUME'
  | 'LIQUIDITY_DETERIORATION'
  | 'SPREAD_WIDENING'
  | 'CONTRACT_DATA_STALE'
  | 'EVENT_PROXIMITY';

export interface WbAlert {
  /** dedupe key: the same condition on the same bar/level yields the same key */
  key: string;
  kind: WbAlertKind;
  symbol: string;
  contractId: string | null;
  tf: Timeframe | null;
  message: string;
  evidence: string[];
  invalidation: string | null;
  /** timestamp of the data the alert rests on */
  sourceAsOf: string | null;
  createdAt: string;
  /** data-health alerts describe a problem; they are not trading conditions */
  kindClass: 'CONDITION' | 'DATA_HEALTH';
}

export interface AlertInput {
  symbol: string;
  freshness: DataState;
  asOf: string | null;
  now: Date;
  setup: BreakoutSetup | null;
  rvol: RelVol | null;
  contract: ContractValidation | null;
  /** previous observation of the same contract, if any */
  previous: { spreadPct: number | null; liquidity: string | null } | null;
  upcoming: Upcoming[];
}

export const ABNORMAL_RVOL = 2;
export const SPREAD_WIDEN_FACTOR = 2;
export const SPREAD_WIDEN_MIN_PCT = 3;

export function deriveAlerts(i: AlertInput): WbAlert[] {
  const out: WbAlert[] = [];
  const created = i.now.toISOString();
  const mk = (kind: WbAlertKind, key: string, message: string, evidence: string[], extra: Partial<WbAlert> = {}): void => {
    out.push({ key: `${kind}|${i.symbol}|${key}`, kind, symbol: i.symbol, contractId: i.contract?.id ?? null, tf: i.setup?.tf ?? null, message, evidence, invalidation: null, sourceAsOf: i.asOf, createdAt: created, kindClass: 'CONDITION', ...extra });
  };

  const live = isActionable(i.freshness) || i.freshness === 'MARKET_CLOSED';
  if (i.setup && live) {
    const s = i.setup;
    const ev = [...s.supporting, ...s.contradicting.map((c) => `Against: ${c}`)];
    const where = `${s.direction === 'UP' ? 'above' : 'below'} ${s.level.price} (${s.tf})`;
    const inv = s.invalidation;
    if (s.state === 'APPROACHING_LEVEL') mk('APPROACHING_LEVEL', `${s.tf}|${s.level.id}`, `${i.symbol} is approaching ${s.level.price} on ${s.tf}.`, ev, { invalidation: inv });
    if (s.state === 'POTENTIAL_BREAKOUT') mk('POTENTIAL_BREAKOUT', `${s.tf}|${s.level.id}|${s.observedThrough}`, `${i.symbol}: potential breakout ${where}; no completed close beyond yet.`, ev, { invalidation: inv });
    // confirmed only on actionable (VALID) data, never on a closed-market replay of old bars
    if (s.state === 'CONFIRMED' && i.freshness === 'VALID') mk('CONFIRMED_BREAKOUT', `${s.tf}|${s.level.id}|${s.breakoutBarT}`, `${i.symbol}: breakout ${where} met every confirmation rule.`, ev, { invalidation: inv });
    if (s.state === 'FAILED') mk('FAILED_BREAKOUT', `${s.tf}|${s.level.id}|${s.breakoutBarT}`, `${i.symbol}: breakout ${where} failed (closed back through the level).`, ev, { invalidation: inv });
  }
  if (live && i.freshness === 'VALID' && i.rvol?.value != null && i.rvol.value >= ABNORMAL_RVOL) {
    const bucket = Math.floor(i.now.getTime() / (30 * 60_000));
    mk('ABNORMAL_RELATIVE_VOLUME', `${bucket}`, `${i.symbol}: relative volume ${i.rvol.value.toFixed(2)}× (≥ ${ABNORMAL_RVOL}×).`, [i.rvol.note]);
  }

  const c = i.contract;
  if (c) {
    if (c.state === 'STALE' || i.freshness === 'STALE') {
      const hour = Math.floor(i.now.getTime() / 3_600_000);
      mk('CONTRACT_DATA_STALE', `${c.id}|${hour}`, `${c.id}: option data is stale; no alerts are raised from it.`, c.notes, { kindClass: 'DATA_HEALTH' });
    } else if (c.state === 'VALID') {
      const sp = c.spreadPct.value;
      const prev = i.previous?.spreadPct ?? null;
      if (sp !== null && prev !== null && sp >= SPREAD_WIDEN_MIN_PCT && sp >= prev * SPREAD_WIDEN_FACTOR) {
        mk('SPREAD_WIDENING', `${c.id}|${Math.floor(i.now.getTime() / 3_600_000)}`, `${c.id}: bid/ask spread widened from ${prev.toFixed(2)}% to ${sp.toFixed(2)}%.`, [`Spread ${sp.toFixed(2)}% of mid.`]);
      }
      const rank = (s: string | null) => (s === 'LIQUID' ? 3 : s === 'MODERATE' ? 2 : s === 'ILLIQUID' ? 1 : 0);
      if (i.previous?.liquidity && rank(c.liquidity.cls) > 0 && rank(c.liquidity.cls) < rank(i.previous.liquidity)) {
        mk('LIQUIDITY_DETERIORATION', `${c.id}|${i.previous.liquidity}>${c.liquidity.cls}`, `${c.id}: liquidity class fell from ${i.previous.liquidity} to ${c.liquidity.cls}.`, c.liquidity.reasons);
      }
    }
  }

  for (const u of i.upcoming) {
    if (u.daysAway <= 3) mk('EVENT_PROXIMITY', `${u.event.id}`, `${i.symbol}: ${u.kind.toLowerCase().replace('_', ' ')} in ${u.daysAway} day(s).`, [u.event.headline], { kindClass: 'CONDITION' });
  }
  return out;
}

/** Keep only alerts whose key has not fired within `cooldownSec`. `seen` maps key -> ISO of last firing. */
export function dedupeAlerts(alerts: WbAlert[], seen: Record<string, string>, now: Date, cooldownSec = 6 * 3600): WbAlert[] {
  const fired = new Set<string>();
  const out: WbAlert[] = [];
  for (const a of alerts) {
    if (fired.has(a.key)) continue;
    const last = seen[a.key] ? Date.parse(seen[a.key]!) : NaN;
    if (!Number.isNaN(last) && now.getTime() - last < cooldownSec * 1000) continue;
    fired.add(a.key);
    out.push(a);
  }
  return out;
}
