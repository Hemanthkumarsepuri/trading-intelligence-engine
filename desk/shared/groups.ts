// Custom contract watchlists, saved preferences and the pure rules for resolving a typed query to
// an exact listed contract. Nothing here constructs an instrument identity: identities come only
// from the provider's chain (see server/services/wb/resolve.ts).

import type { Timeframe } from './candles';
import { expiryMatches, type ContractRef, type OptType, type SearchQuery } from './contract';

export interface ChartPrefs {
  tf?: Timeframe;
  overlays?: { vwap: boolean; ema20: boolean; ema50: boolean; bb: boolean };
  panes?: { rsi: boolean; macd: boolean; atr: boolean };
  levelGroups?: { swing: boolean; session: boolean; opening: boolean; rolling: boolean };
  showSetup?: boolean;
  showEvents?: boolean;
  view?: 'both' | 'underlying' | 'option';
}

/** What the provider told us about one listed contract when it was added. */
export interface ContractIdentity {
  underlying: string;
  expiry: string;
  type: OptType;
  strike: number;
  /** provider's own symbol (NSE identifier) or instrument key; null when the provider supplies none */
  canonicalSymbol: string | null;
  instrumentKey: string | null;
  lotSize: number | null;
  source: string;
  verifiedAt: string;
}

export interface GroupItem {
  id: string;
  note: string;
  addedAt: string;
  prefs?: ChartPrefs;
  identity?: ContractIdentity;
}

export interface ContractGroup {
  id: string;
  name: string;
  note: string;
  prefs?: ChartPrefs;
  items: GroupItem[];
  createdAt: string;
}

export const MAX_GROUPS = 20;
export const MAX_ITEMS_PER_GROUP = 40;

export function normalizeGroupName(s: string): string {
  return s.replace(/\s+/g, ' ').trim();
}

export function sameName(a: string, b: string): boolean {
  return normalizeGroupName(a).toLowerCase() === normalizeGroupName(b).toLowerCase();
}

/** Reorder `ids` to follow `order`; ids missing from `order` keep their relative position at the end. Unknown ids in `order` are ignored. */
export function reorderBy<T extends { id: string }>(items: T[], order: string[]): T[] {
  const rank = new Map(order.map((id, i) => [id, i]));
  return [...items].sort((a, b) => (rank.get(a.id) ?? 1e9) - (rank.get(b.id) ?? 1e9));
}

// ---- resolving a query against listed expiries -------------------------------------------------------------

export type ExpiryPick = { status: 'ONE'; expiry: string; inferred: string | null } | { status: 'MANY'; options: string[] } | { status: 'NONE'; reason: string };

/**
 * Choose the expiry for a typed expiry hint from the LISTED expiries. A hint matching exactly one listed
 * expiry resolves; several (weekly series, or two years) must be chosen by the user; none is rejected.
 * No hint at all is never guessed: the user chooses.
 */
export function pickExpiry(listed: string[], hint: SearchQuery['expiry'], today: string): ExpiryPick {
  const future = [...new Set(listed)].filter((e) => e >= today).sort();
  if (future.length === 0) return { status: 'NONE', reason: 'No upcoming expiry is listed for this underlying.' };
  if (!hint) return { status: 'MANY', options: future };
  if (hint.iso && hint.iso < today) return { status: 'NONE', reason: `${hint.iso} has already expired.` };
  const hits = future.filter((e) => expiryMatches(e, hint));
  if (hits.length === 1) {
    const inferred = !hint.iso && !hint.year ? 'The year was not typed; it was taken from the only listed expiry that matches.' : !hint.iso && !hint.day ? 'The day was not typed; it was taken from the only listed expiry that matches.' : null;
    return { status: 'ONE', expiry: hits[0]!, inferred };
  }
  if (hits.length > 1) return { status: 'MANY', options: hits };
  return { status: 'NONE', reason: `No listed expiry matches that date. Listed: ${future.slice(0, 6).join(', ')}.` };
}

/** Exact symbol wins; otherwise several prefix/name hits must be chosen; none is rejected. */
export function pickUnderlying(text: string, hits: { id: string; name: string }[]): { status: 'ONE'; id: string } | { status: 'MANY'; options: { id: string; name: string }[] } | { status: 'NONE' } {
  const t = text.trim().toUpperCase();
  if (!t) return { status: 'NONE' };
  const exact = hits.filter((h) => h.id === t);
  if (exact.length === 1) return { status: 'ONE', id: exact[0]!.id };
  if (hits.length === 1) return { status: 'ONE', id: hits[0]!.id };
  return hits.length === 0 ? { status: 'NONE' } : { status: 'MANY', options: hits.slice(0, 12) };
}

export interface ResolvedContract {
  ref: ContractRef;
  id: string;
  label: string;
  identity: ContractIdentity;
}
