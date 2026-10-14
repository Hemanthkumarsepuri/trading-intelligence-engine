// Scanner: transparent qualification, filter explanations and ranking. No composite score.
// A candidate is something to RESEARCH. Every candidate carries an audit trail of the checks applied.

import { RANK, type BreakoutSetup, type BreakoutState, type StructureResult } from './breakout';
import type { Timeframe } from './candles';
import type { ContractRef, ContractValidation, LiquidityClass, OptType } from './contract';
import type { Upcoming } from './events';
import { mtfAgreement, type MtfAgreement, type RelVol, type Trend, type TrendRead } from './levels';
import type { DataState } from './quality';

export interface TfAnalysis {
  structure: StructureResult;
  trend: TrendRead;
  barsCompleted: number;
}

export interface ScanInput {
  symbol: string;
  name: string | null;
  sector: string | null;
  isIndex: boolean;
  lotSize: number | null;
  price: number | null;
  changePct: number | null;
  priceAsOf: string | null;
  freshness: DataState;
  tfs: Partial<Record<Timeframe, TfAnalysis>>;
  rvol: RelVol | null;
  sectorChangePct: number | null;
  benchChangePct: number | null;
  upcoming: Upcoming[];
  inFnoBan: boolean;
  /** null = the option chain could not be retrieved */
  contracts: ContractValidation[] | null;
  contractNote: string | null;
}

export interface ScanFilters {
  segment: 'ALL' | 'STOCK' | 'INDEX';
  direction: 'ANY' | 'UP' | 'DOWN';
  /** lifecycle states that may qualify */
  states: BreakoutState[];
  minLiquidity: 'ANY' | 'MODERATE' | 'LIQUID';
  optionType: 'ANY' | OptType;
  expiry: string | null;
  trend: 'ANY' | 'UP' | 'DOWN';
  minRvol: number | null;
  /** exclude names with results/board meetings inside `eventDays` */
  avoidEvents: boolean;
  eventDays: number;
  requireOptions: boolean;
  allowStale: false;
}

export const DEFAULT_FILTERS: ScanFilters = {
  segment: 'ALL', direction: 'ANY', states: ['APPROACHING_LEVEL', 'POTENTIAL_BREAKOUT', 'CONFIRMATION_PENDING', 'CONFIRMED'],
  minLiquidity: 'ANY', optionType: 'ANY', expiry: null, trend: 'ANY', minRvol: null, avoidEvents: false, eventDays: 3, requireOptions: true, allowStale: false,
};

export interface AuditStep {
  check: string;
  passed: boolean | null;
  detail: string;
}

export interface Candidate {
  symbol: string;
  name: string | null;
  sector: string | null;
  isIndex: boolean;
  price: number | null;
  changePct: number | null;
  priceAsOf: string | null;
  dataState: DataState;
  setup: BreakoutSetup;
  mtf: MtfAgreement;
  rvol: RelVol | null;
  sectorRelPct: number | null;
  benchRelPct: number | null;
  upcoming: Upcoming[];
  inFnoBan: boolean;
  /** contracts to INSPECT (not recommendations) */
  contracts: ContractValidation[];
  contractNote: string | null;
  why: string[];
  risks: string[];
  audit: AuditStep[];
}

export interface Exclusion {
  symbol: string;
  reason: string;
  audit: AuditStep[];
}

const TF_ORDER: Timeframe[] = ['1d', '1h', '30m', '15m', '5m', '3m', '1m'];
const LIQ_RANK: Record<LiquidityClass, number> = { LIQUID: 3, MODERATE: 2, ILLIQUID: 1, DATA_INSUFFICIENT: 0 };

export function bestSetup(tfs: ScanInput['tfs']): { tf: Timeframe; setup: BreakoutSetup } | null {
  let best: { tf: Timeframe; setup: BreakoutSetup } | null = null;
  for (const tf of TF_ORDER) {
    const p = tfs[tf]?.structure.primary;
    if (!p) continue;
    if (!best || RANK[p.state] > RANK[best.setup.state]) best = { tf, setup: p };
  }
  return best;
}

export function evaluateCandidate(inp: ScanInput, f: ScanFilters): { candidate: Candidate } | { excluded: Exclusion } {
  const audit: AuditStep[] = [];
  const out = (reason: string): { excluded: Exclusion } => ({ excluded: { symbol: inp.symbol, reason, audit } });
  const step = (check: string, passed: boolean | null, detail: string) => audit.push({ check, passed, detail });

  const seg = f.segment === 'ALL' || (f.segment === 'INDEX') === inp.isIndex;
  step('Segment filter', seg, `${inp.isIndex ? 'Index' : 'Stock'} vs filter ${f.segment}`);
  if (!seg) return out('segment filter');

  const fresh = inp.freshness !== 'STALE' && inp.freshness !== 'CONFLICT';
  step('Data freshness', fresh, `Underlying data state: ${inp.freshness}`);
  if (!fresh) return out(`data ${inp.freshness.toLowerCase()}`);

  const usable = Object.values(inp.tfs).filter((t) => t && t.barsCompleted >= 30).length;
  step('History sufficiency', usable > 0, `${usable} timeframe(s) have ≥ 30 completed bars`);
  if (usable === 0) return out('insufficient history');

  const best = bestSetup(inp.tfs);
  const okState = best !== null && f.states.includes(best.setup.state);
  step('Active setup', okState, best ? `Best structure: ${best.setup.state} on ${best.tf} at level ${best.setup.level.price}` : 'No structure could be evaluated');
  if (!best || !okState) return out(best ? `no qualifying setup (${best.setup.state})` : 'no structure');

  const dirOk = f.direction === 'ANY' || best.setup.direction === f.direction;
  step('Direction filter', dirOk, `Setup direction ${best.setup.direction}, filter ${f.direction}`);
  if (!dirOk) return out('direction filter');

  const trends: Partial<Record<Timeframe, Trend>> = {};
  for (const [tf, a] of Object.entries(inp.tfs) as [Timeframe, TfAnalysis][]) trends[tf] = a.trend.trend;
  const mtf = mtfAgreement(trends);
  const trendOk = f.trend === 'ANY' || mtf.alignment === f.trend;
  step('Trend filter', trendOk, `Multi-timeframe alignment ${mtf.alignment}: ${mtf.detail}`);
  if (!trendOk) return out('trend filter');

  const rvOk = f.minRvol === null || (inp.rvol?.value != null && inp.rvol.value >= f.minRvol);
  step('Relative volume', f.minRvol === null ? null : rvOk, inp.rvol?.value != null ? `${inp.rvol.value.toFixed(2)}× (${inp.rvol.method})` : `unavailable: ${inp.rvol?.note ?? 'no intraday volume'}`);
  if (!rvOk) return out(inp.rvol?.value == null ? 'relative volume unavailable' : 'relative volume below filter');

  const soon = inp.upcoming.filter((u) => u.daysAway <= f.eventDays);
  step('Scheduled events', f.avoidEvents ? soon.length === 0 : null, soon.length ? soon.map((u) => `${u.kind} in ${u.daysAway}d`).join('; ') : 'None inside the window');
  if (f.avoidEvents && soon.length) return out('scheduled event nearby');

  // options
  let contracts: ContractValidation[] = [];
  if (inp.contracts === null) {
    step('Option chain', f.requireOptions ? false : null, inp.contractNote ?? 'Option chain unavailable');
    if (f.requireOptions) return out('option chain unavailable');
  } else {
    contracts = inp.contracts.filter((c) => (f.optionType === 'ANY' || c.ref.type === f.optionType) && (f.expiry === null || c.ref.expiry === f.expiry));
    const quoted = contracts.filter((c) => c.quoteAvailable);
    step('Option contracts', quoted.length > 0, `${quoted.length} of ${contracts.length} selected contracts have a quote`);
    if (f.minLiquidity !== 'ANY') {
      const need = LIQ_RANK[f.minLiquidity];
      contracts = contracts.filter((c) => LIQ_RANK[c.liquidity.cls] >= need);
      step('Option liquidity', contracts.length > 0, `${contracts.length} contract(s) at ${f.minLiquidity} or better`);
    }
    if (f.requireOptions && contracts.filter((c) => c.quoteAvailable).length === 0) return out(f.minLiquidity !== 'ANY' ? 'no contract meets the liquidity filter' : 'no quoted option contracts');
  }

  const why: string[] = [`${best.setup.state.replace(/_/g, ' ').toLowerCase()} ${best.setup.direction === 'UP' ? 'above' : 'below'} ${best.setup.level.price} on the ${best.tf} chart (${best.setup.level.method.replace(/_/g, ' ').toLowerCase()}).`, ...best.setup.supporting];
  const risks: string[] = [...best.setup.contradicting, ...best.setup.missing];
  if (soon.length) risks.push(`Scheduled event: ${soon.map((u) => `${u.kind.toLowerCase().replace('_', ' ')} in ${u.daysAway} day(s)`).join(', ')}.`);
  if (inp.inFnoBan) risks.push('Stock is in the F&O ban list: no fresh positions can be opened.');
  if (mtf.alignment === 'MIXED') risks.push(`Timeframes disagree: ${mtf.detail}`);
  const sectorRel = inp.changePct !== null && inp.sectorChangePct !== null ? inp.changePct - inp.sectorChangePct : null;
  const benchRel = inp.changePct !== null && inp.benchChangePct !== null ? inp.changePct - inp.benchChangePct : null;
  if (mtf.alignment === best.setup.direction) why.push(`All known timeframes trend ${best.setup.direction === 'UP' ? 'up' : 'down'}.`);

  return {
    candidate: {
      symbol: inp.symbol, name: inp.name, sector: inp.sector, isIndex: inp.isIndex, price: inp.price, changePct: inp.changePct, priceAsOf: inp.priceAsOf, dataState: inp.freshness,
      setup: best.setup, mtf, rvol: inp.rvol, sectorRelPct: sectorRel, benchRelPct: benchRel, upcoming: inp.upcoming, inFnoBan: inp.inFnoBan,
      contracts, contractNote: inp.contractNote, why, risks, audit,
    },
  };
}

/**
 * Best-first ordering with a fixed, published tie-break chain (no blended score):
 * 1. lifecycle state, 2. agreeing timeframes with the setup direction, 3. relative volume (unknown last), 4. nearer to the level.
 */
export function rankCandidates(cs: Candidate[]): Candidate[] {
  const agree = (c: Candidate) => (c.mtf.alignment === c.setup.direction ? 100 + c.mtf.agreeing.length : c.mtf.alignment === 'MIXED' ? c.mtf.agreeing.length : 0);
  return [...cs].sort(
    (a, b) =>
      RANK[b.setup.state] - RANK[a.setup.state] ||
      agree(b) - agree(a) ||
      (b.rvol?.value ?? -1) - (a.rvol?.value ?? -1) ||
      Math.abs(a.setup.distanceAtr ?? 99) - Math.abs(b.setup.distanceAtr ?? 99) ||
      a.symbol.localeCompare(b.symbol),
  );
}

export const RANKING_RULE = 'Ordered by (1) lifecycle state, (2) timeframes agreeing with the setup direction, (3) relative volume (unknown ranks last), (4) closeness to the level, (5) symbol. There is no blended score.';

export interface ScanSummary {
  universe: number;
  evaluated: number;
  stockCandidates: number;
  contractCount: number;
  excludedByReason: Record<string, number>;
  message: string;
}

export function summarize(candidates: Candidate[], excluded: Exclusion[], universe: number, evaluated: number, target = 5): ScanSummary {
  const excludedByReason: Record<string, number> = {};
  for (const e of excluded) excludedByReason[e.reason] = (excludedByReason[e.reason] ?? 0) + 1;
  const contractCount = candidates.reduce((a, c) => a + c.contracts.length, 0);
  const stocks = candidates.length;
  const top = Object.entries(excludedByReason).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([r, n]) => `${n} × ${r}`).join('; ');
  const message = stocks >= target
    ? `${stocks} underlyings qualified (${contractCount} contracts to inspect) out of ${evaluated} evaluated.`
    : `${stocks} of the ${evaluated} evaluated underlyings qualified; fewer than ${target} met every filter.${top ? ` Most exclusions: ${top}.` : ''} No candidates were added to reach ${target}.`;
  return { universe, evaluated, stockCandidates: stocks, contractCount, excludedByReason, message };
}

/** Contracts to inspect for a setup: the ATM strike and the next OTM strike on the side that matches the setup direction. */
export function selectStrikes(strikes: number[], spot: number, direction: 'UP' | 'DOWN'): { atm: number; otm: number | null; criteria: string } | null {
  const s = [...new Set(strikes)].sort((a, b) => a - b);
  if (s.length === 0 || !(spot > 0)) return null;
  const atm = s.reduce((b, x) => (Math.abs(x - spot) < Math.abs(b - spot) ? x : b), s[0]!);
  const i = s.indexOf(atm);
  const otm = direction === 'UP' ? s[i + 1] ?? null : s[i - 1] ?? null;
  return { atm, otm, criteria: `At-the-money strike (${atm}) and the next ${direction === 'UP' ? 'higher' : 'lower'} strike for ${direction === 'UP' ? 'calls' : 'puts'}, on the nearest expiry with enough time left. The user can change all of these in the Workbench.` };
}

export const refKey = (r: ContractRef) => `${r.underlying}|${r.expiry}|${r.type}|${r.strike}`;
