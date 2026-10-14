// Shared pieces for the Options Workbench screens: data-quality badges, sourced values, hooks.

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { fmtIst, fmtNum } from '../../shared/format';
import type { DataState, Provenance, Sourced } from '../../shared/quality';
import { go } from '../nav';
import { Badge } from '../ui';

export function useDebounced<T>(v: T, ms = 250): T {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

const STATE_KIND: Record<DataState, 'pos' | 'neg' | 'warn' | 'info' | undefined> = {
  VALID: 'pos',
  STALE: 'warn',
  DATA_INSUFFICIENT: 'warn',
  CONFLICT: 'neg',
  UNKNOWN: undefined,
  UNSUPPORTED: undefined,
  MARKET_CLOSED: 'info',
};

export const STATE_HELP: Record<DataState, string> = {
  VALID: 'Recent enough for the session: within the freshness limit for its source.',
  STALE: 'Older than the freshness limit while the market is open. Shown for reference; it cannot confirm a breakout or raise an alert.',
  DATA_INSUFFICIENT: 'Not enough history or fields to compute this reliably.',
  CONFLICT: 'Inputs disagree (for example two providers, or premium below intrinsic). Nothing is silently chosen.',
  UNKNOWN: 'The provider did not supply this value, or its time cannot be verified. It is not zero.',
  UNSUPPORTED: 'This provider or contract type does not offer this measurement.',
  MARKET_CLOSED: 'The market is closed. This is the last session\'s value, not an outage.',
};

export function StateBadge({ state, label }: { state: DataState; label?: string }) {
  return (
    <Badge kind={STATE_KIND[state]} title={STATE_HELP[state]}>
      {label ?? state.replace(/_/g, ' ')}
    </Badge>
  );
}

export function provText(p: Provenance | null | undefined): string {
  if (!p) return 'No source';
  const when = p.asOf ? `provider time ${fmtIst(p.asOf)} IST` : 'provider gave no timestamp';
  return `${p.source} · ${when} · fetched ${fmtIst(p.fetchedAt)} IST · ${p.latency.toLowerCase()}${p.note ? ` · ${p.note}` : ''}`;
}

/** A provider value with its state; unknown values show a dash and the reason on hover, never 0. */
export function Sv({ f, digits = 2, prefix = '', suffix = '', hideBadge }: { f: Sourced<number> | undefined; digits?: number; prefix?: string; suffix?: string; hideBadge?: boolean }) {
  if (!f) return <span className="muted">-</span>;
  const text = f.value === null ? '-' : `${prefix}${fmtNum(f.value, digits)}${suffix}`;
  const showBadge = !hideBadge && f.state !== 'VALID';
  return (
    <span title={f.value === null ? f.note ?? STATE_HELP[f.state] : provText(f.prov)} className={f.value === null ? 'muted' : undefined}>
      <span className="mono">{text}</span>
      {showBadge && (
        <>
          {' '}
          <StateBadge state={f.state} />
        </>
      )}
    </span>
  );
}

export function Prov({ p, state }: { p: Provenance | null | undefined; state?: DataState }) {
  return (
    <span className="small muted">
      {state && <StateBadge state={state} />} {provText(p)}
    </span>
  );
}

export const openWorkbench = (symbol: string, contractId?: string, tf?: string): void => {
  const q = new URLSearchParams({ u: symbol });
  if (contractId) q.set('c', contractId);
  if (tf) q.set('tf', tf);
  go(`/workbench?${q.toString()}`);
};

export const LIFECYCLE_KIND: Record<string, 'pos' | 'neg' | 'warn' | 'info' | undefined> = {
  CONFIRMED: 'pos',
  CONFIRMATION_PENDING: 'info',
  POTENTIAL_BREAKOUT: 'info',
  APPROACHING_LEVEL: undefined,
  NOT_READY: undefined,
  FAILED: 'neg',
  INVALIDATED: 'neg',
  DATA_INSUFFICIENT: 'warn',
  STALE: 'warn',
};

export const LIFECYCLE_HELP: Record<string, string> = {
  NOT_READY: 'No nearby level in play.',
  APPROACHING_LEVEL: 'Price is within 0.5×ATR of a level. Nothing has broken yet.',
  POTENTIAL_BREAKOUT: 'Price traded through the level, but no completed candle has closed beyond it.',
  CONFIRMATION_PENDING: 'A completed candle closed beyond the level; volume or hold conditions are not all met.',
  CONFIRMED: 'Close beyond the level, volume expansion and a hold, on fresh data. A research state, not an instruction.',
  FAILED: 'A completed candle closed back through the level after the break.',
  INVALIDATED: 'Price closed well on the wrong side; the setup premise is gone.',
  DATA_INSUFFICIENT: 'Not enough completed bars or inputs conflict.',
  STALE: 'Data is older than the freshness limit; no confirmation possible.',
};

const SHORT_STATE: Record<string, string> = { CONFIRMATION_PENDING: 'PENDING', POTENTIAL_BREAKOUT: 'POTENTIAL', APPROACHING_LEVEL: 'APPROACH', DATA_INSUFFICIENT: 'NO DATA' };

export function Lifecycle({ state, short }: { state: string; short?: boolean }) {
  return (
    <Badge kind={LIFECYCLE_KIND[state]} title={`${state.replace(/_/g, ' ')}: ${LIFECYCLE_HELP[state]}`}>
      {short && SHORT_STATE[state] ? SHORT_STATE[state] : state.replace(/_/g, ' ')}
    </Badge>
  );
}

export function Evidence({ title, items, tone }: { title: string; items: string[]; tone?: 'pos' | 'neg' | 'warn' }) {
  if (items.length === 0) return null;
  return (
    <div>
      <div className="small" style={{ fontWeight: 650, color: tone ? `var(--${tone})` : undefined }}>{title}</div>
      <ul className="small" style={{ margin: '4px 0 0', paddingLeft: 18 }}>
        {items.map((s, i) => <li key={i}>{s}</li>)}
      </ul>
    </div>
  );
}

/** Disclosure that stays closed until asked; used for audit trails and rules. */
export function Disclosure({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return (
    <details className="notes">
      <summary>{summary}</summary>
      <div style={{ marginTop: 6 }}>{children}</div>
    </details>
  );
}

export function useInterval(fn: () => void, ms: number | null): void {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const t = setInterval(() => saved.current(), ms);
    return () => clearInterval(t);
  }, [ms]);
}

export const NOT_ADVICE = 'Research view only. Neither a recommendation nor an instruction to trade. A correct directional view does not guarantee that an option will gain value.';
