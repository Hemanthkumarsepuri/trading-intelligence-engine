// User-defined, contract-specific alert rules. Pure evaluation; deduplication and storage live elsewhere.
//
// Gating (the point of this module):
//  * price/volume/spread rules need the option data to be VALID (fresh);
//  * rules about candle closes use COMPLETED candles only and need fresh underlying data;
//  * a data-stale rule fires only on STALE;
//  * an expiry rule is calendar based and needs no market data.
// A rule that cannot be evaluated returns a `withheld` reason, never a trigger.

import { evaluateBreakout } from './breakout';
import { atrLast } from './levels';
import type { TfCandle, Timeframe } from './candles';
import type { ContractValidation } from './contract';
import { istDateStr } from './india';
import type { DataState } from './quality';
import type { WbAlert } from './wbalerts';

export type RuleKind = 'PREMIUM_CROSS' | 'UNDERLYING_LEVEL' | 'CLOSE_BEYOND_LEVEL' | 'RETEST' | 'OPTION_VOLUME_ABOVE' | 'SPREAD_ABOVE' | 'DATA_STALE' | 'EXPIRY_WITHIN';

export const RULE_LABEL: Record<RuleKind, string> = {
  PREMIUM_CROSS: 'Option premium crosses a price',
  UNDERLYING_LEVEL: 'Underlying reaches a level',
  CLOSE_BEYOND_LEVEL: 'Underlying closes beyond a level (completed candle)',
  RETEST: 'Breakout retest confirms or fails',
  OPTION_VOLUME_ABOVE: 'Option volume reaches a threshold',
  SPREAD_ABOVE: 'Bid/ask spread exceeds a threshold',
  DATA_STALE: 'Contract data becomes stale',
  EXPIRY_WITHIN: 'Contract approaches expiry',
};

export interface RuleParams {
  price?: number;
  level?: number;
  direction?: 'ABOVE' | 'BELOW';
  tf?: Timeframe;
  volume?: number;
  spreadPct?: number;
  days?: number;
}

export interface ContractRule {
  id: string;
  contractId: string;
  kind: RuleKind;
  params: RuleParams;
  enabled: boolean;
  note: string;
  createdAt: string;
  lastTriggeredAt: string | null;
}

/** Returns the problem with a rule's parameters, or null when it is well formed. */
export function validateRuleParams(kind: RuleKind, p: RuleParams): string | null {
  const pos = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x) && x > 0;
  const dir = p.direction === 'ABOVE' || p.direction === 'BELOW';
  switch (kind) {
    case 'PREMIUM_CROSS': return !pos(p.price) ? 'Premium price must be positive.' : !dir ? 'Choose above or below.' : null;
    case 'UNDERLYING_LEVEL': return !pos(p.level) ? 'Level must be positive.' : !dir ? 'Choose above or below.' : null;
    case 'CLOSE_BEYOND_LEVEL':
    case 'RETEST': return !pos(p.level) ? 'Level must be positive.' : !dir ? 'Choose above or below.' : !p.tf || p.tf === '1m' || p.tf === '3m' ? 'Choose a timeframe of 5 minutes or longer.' : null;
    case 'OPTION_VOLUME_ABOVE': return !pos(p.volume) ? 'Volume must be positive.' : null;
    case 'SPREAD_ABOVE': return !pos(p.spreadPct) || (p.spreadPct as number) > 100 ? 'Spread must be between 0 and 100 percent.' : null;
    case 'EXPIRY_WITHIN': return !pos(p.days) || (p.days as number) > 60 ? 'Days must be between 1 and 60.' : null;
    case 'DATA_STALE': return null;
  }
}

export interface RuleInput {
  rule: ContractRule;
  now: Date;
  contract: ContractValidation | null;
  /** underlying price from the same snapshot as the option quote, with its state */
  spot: number | null;
  spotState: DataState;
  /** underlying candles for rule.params.tf (completed and forming); null when unavailable */
  candles: TfCandle[] | null;
  underlyingFreshness: DataState;
  previous: { premium: number | null; spot: number | null } | null;
}

export interface RuleResult {
  alert: WbAlert | null;
  /** why a rule that might otherwise fire was not evaluated */
  withheld: string | null;
}

const side = (v: number, level: number, d: 'ABOVE' | 'BELOW') => (d === 'ABOVE' ? v >= level : v <= level);

export function evaluateRule(i: RuleInput): RuleResult {
  const { rule, now, contract: c } = i;
  const created = now.toISOString();
  const day = istDateStr(now);
  const label = c ? `${c.ref.underlying} ${c.ref.expiry} ${c.ref.strike} ${c.ref.type}` : rule.contractId;
  const mk = (keyPart: string, message: string, evidence: string[], asOf: string | null, extra: Partial<WbAlert> = {}): RuleResult => ({
    withheld: null,
    alert: { key: `USER_RULE|${rule.id}|${keyPart}`, kind: 'USER_RULE', ruleId: rule.id, symbol: c?.ref.underlying ?? rule.contractId.split('|')[0] ?? '', contractId: rule.contractId, tf: rule.params.tf ?? null, message, evidence: [...evidence, ...(rule.note ? [`Your note: ${rule.note}`] : [])], invalidation: null, sourceAsOf: asOf, createdAt: created, kindClass: 'CONDITION', ...extra },
  });
  const none = (why: string | null = null): RuleResult => ({ alert: null, withheld: why });
  if (!rule.enabled) return none();
  if (!c) return none('The contract could not be loaded, so the rule was not evaluated.');
  if (c.state === 'UNSUPPORTED') return none('The contract has expired or is unsupported.');
  const p = rule.params;
  const optionAsOf = c.ltp.prov?.asOf ?? c.bid.prov?.asOf ?? null;

  switch (rule.kind) {
    case 'EXPIRY_WITHIN': {
      if (c.daysToExpiry === null || p.days === undefined) return none();
      return c.daysToExpiry <= p.days ? mk(day, `${label} expires in ${c.daysToExpiry.toFixed(1)} days (your limit: ${p.days}).`, [`Expiry ${c.ref.expiry} 15:30 IST.`], null) : none();
    }
    case 'DATA_STALE':
      return c.state === 'STALE' ? mk(`${day}|${Math.floor(now.getTime() / 3_600_000)}`, `${label}: option data is stale; values shown are not current.`, c.notes, optionAsOf, { kindClass: 'DATA_HEALTH' }) : none();
    case 'PREMIUM_CROSS': {
      if (c.state !== 'VALID') return none(`Premium rule withheld: data is ${c.state}.`);
      const v = c.ltp.value;
      if (v === null || p.price === undefined || !p.direction) return none('No last price.');
      if (!side(v, p.price, p.direction)) return none();
      const prev = i.previous?.premium ?? null;
      if (prev !== null && side(prev, p.price, p.direction)) return none(); // was already beyond: not a cross
      return mk(day, `${label}: premium ${v} is ${p.direction === 'ABOVE' ? 'at or above' : 'at or below'} ${p.price}.`, [`Last price ${v}`], optionAsOf);
    }
    case 'OPTION_VOLUME_ABOVE': {
      if (c.state !== 'VALID') return none(`Volume rule withheld: data is ${c.state}.`);
      const v = c.volume.value;
      if (v === null || p.volume === undefined) return none('Volume is unknown, not zero.');
      return v >= p.volume ? mk(day, `${label}: traded volume ${v} contracts reached ${p.volume}.`, ['Volume is contracts traded today, not open interest.'], optionAsOf) : none();
    }
    case 'SPREAD_ABOVE': {
      if (c.state !== 'VALID') return none(`Spread rule withheld: data is ${c.state}.`);
      const s = c.spreadPct.value;
      if (s === null || p.spreadPct === undefined) return none('No two-sided quote.');
      return s >= p.spreadPct ? mk(`${day}|${Math.floor(now.getTime() / 3_600_000)}`, `${label}: spread ${s.toFixed(2)}% is above ${p.spreadPct}%.`, [`Bid ${c.bid.value}, ask ${c.ask.value}`], optionAsOf) : none();
    }
    case 'UNDERLYING_LEVEL': {
      if (i.spotState !== 'VALID' || i.spot === null) return none(`Underlying level rule withheld: underlying data is ${i.spotState}.`);
      if (p.level === undefined || !p.direction) return none();
      if (!side(i.spot, p.level, p.direction)) return none();
      const prev = i.previous?.spot ?? null;
      if (prev !== null && side(prev, p.level, p.direction)) return none();
      return mk(day, `${c.ref.underlying} ${i.spot} reached ${p.direction === 'ABOVE' ? 'above' : 'below'} ${p.level}.`, [`Underlying price ${i.spot}`], c.ltp.prov?.asOf ?? null);
    }
    case 'CLOSE_BEYOND_LEVEL':
    case 'RETEST': {
      if (i.underlyingFreshness !== 'VALID') return none(`Candle-close rule withheld: underlying data is ${i.underlyingFreshness}; confirmation needs fresh data.`);
      if (!i.candles || p.level === undefined || !p.direction || !p.tf) return none('No candles for this timeframe.');
      const done = i.candles.filter((x) => x.complete);
      if (done.length < 30) return none(`Only ${done.length} completed ${p.tf} bars; at least 30 are needed.`);
      const lastDone = done[done.length - 1]!;
      const asOf = new Date((lastDone.t + 1) * 1000).toISOString();
      if (rule.kind === 'CLOSE_BEYOND_LEVEL') {
        const a = atrLast(done);
        const buf = (a ?? 0) * 0.1;
        const beyond = p.direction === 'ABOVE' ? lastDone.c > p.level + buf : lastDone.c < p.level - buf;
        return beyond ? mk(`${p.tf}|${lastDone.t}`, `${c.ref.underlying}: completed ${p.tf} candle closed ${p.direction === 'ABOVE' ? 'above' : 'below'} ${p.level} (close ${lastDone.c}).`, ['Only completed candles count; the forming candle is ignored.', `Close margin ${buf.toFixed(2)} (0.1×ATR).`], asOf) : none();
      }
      const level = { id: `rule:${rule.id}`, price: p.level, low: p.level, high: p.level, kind: p.direction === 'ABOVE' ? ('RESISTANCE' as const) : ('SUPPORT' as const), method: 'SWING_CLUSTER' as const, tf: p.tf, touches: 2, from: 0, to: 0, explain: 'user level' };
      const s = evaluateBreakout(i.candles, level, p.direction === 'ABOVE' ? 'UP' : 'DOWN', p.tf, { freshness: 'VALID' });
      if (s.retestHeld === true && (s.state === 'CONFIRMED' || s.state === 'CONFIRMATION_PENDING')) return mk(`${p.tf}|confirm|${s.breakoutBarT}`, `${c.ref.underlying}: retest of ${p.level} held after a completed ${p.tf} close ${p.direction === 'ABOVE' ? 'above' : 'below'} it.`, s.supporting, asOf);
      if (s.state === 'FAILED' || s.state === 'INVALIDATED') return mk(`${p.tf}|fail|${s.breakoutBarT}`, `${c.ref.underlying}: the break of ${p.level} failed (a completed ${p.tf} close returned through it).`, s.contradicting, asOf);
      return none();
    }
  }
}
