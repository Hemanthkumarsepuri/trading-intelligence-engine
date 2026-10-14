import { describe, expect, it } from 'vitest';
import type { TfCandle } from '../shared/candles';
import { parseSearch, validateContract, type ContractValidation } from '../shared/contract';
import { pickExpiry, pickUnderlying, reorderBy, sameName } from '../shared/groups';
import { evaluateRule, validateRuleParams, type ContractRule, type RuleInput } from '../shared/wbrules';
import { dedupeAlerts } from '../shared/wbalerts';
import { bars } from './wbhelpers';

describe('quick-entry parsing', () => {
  it('understands the intended workflows', () => {
    expect(parseSearch('ANGELONE 300 CE OCT')).toEqual({ text: 'ANGELONE', strike: 300, type: 'CE', expiry: { month: '10' } });
    expect(parseSearch('angelone 305 ce oct')).toMatchObject({ strike: 305, type: 'CE' });
    expect(parseSearch('ANGELONE 300 PE')).toEqual({ text: 'ANGELONE', strike: 300, type: 'PE', expiry: null });
    expect(parseSearch('NIFTY 25000 CE')).toEqual({ text: 'NIFTY', strike: 25000, type: 'CE', expiry: null });
    expect(parseSearch('angelone 300 call 27oct2026')).toMatchObject({ type: 'CE', expiry: { day: '27', month: '10', year: '2026' } });
    expect(parseSearch('ANGELONE 2026-10-27 300 CE').expiry).toEqual({ iso: '2026-10-27' });
  });
  it('a missing strike, type or expiry stays missing (never guessed)', () => {
    expect(parseSearch('ANGELONE')).toEqual({ text: 'ANGELONE', strike: null, type: null, expiry: null });
    expect(parseSearch('ANGELONE OCT')).toMatchObject({ strike: null, type: null, expiry: { month: '10' } });
  });
});

describe('expiry resolution against LISTED expiries', () => {
  const listed = ['2026-10-27', '2026-11-24', '2026-12-29'];
  const today = '2026-10-14';
  it('a month that matches exactly one listed expiry resolves and says what was inferred', () => {
    const p = pickExpiry(listed, { month: '10' }, today);
    expect(p).toMatchObject({ status: 'ONE', expiry: '2026-10-27' });
    expect(p.status === 'ONE' && p.inferred).toMatch(/year was not typed/);
    expect(pickExpiry(listed, { day: '27', month: '10', year: '2026' }, today)).toMatchObject({ status: 'ONE', inferred: null });
    expect(pickExpiry(listed, { iso: '2026-11-24' }, today)).toMatchObject({ status: 'ONE', expiry: '2026-11-24' });
  });
  it('several matches (weekly series, two years) must be chosen by the user', () => {
    const weekly = ['2026-10-20', '2026-10-27', '2026-11-24'];
    expect(pickExpiry(weekly, { month: '10' }, today)).toEqual({ status: 'MANY', options: ['2026-10-20', '2026-10-27'] });
    expect(pickExpiry(['2026-12-29', '2027-12-28'], { month: '12' }, today)).toMatchObject({ status: 'MANY' });
  });
  it('no hint is never guessed', () => {
    expect(pickExpiry(listed, null, today)).toEqual({ status: 'MANY', options: listed });
  });
  it('expired, unlisted and empty are rejected with a reason', () => {
    expect(pickExpiry(listed, { iso: '2026-10-01' }, today)).toMatchObject({ status: 'NONE', reason: expect.stringMatching(/already expired/) });
    expect(pickExpiry(listed, { month: '09' }, today)).toMatchObject({ status: 'NONE', reason: expect.stringMatching(/No listed expiry matches.*2026-10-27/) });
    expect(pickExpiry([], { month: '10' }, today)).toMatchObject({ status: 'NONE' });
    expect(pickExpiry(['2026-10-01'], null, today)).toMatchObject({ status: 'NONE' });
  });
  it('underlying: exact symbol wins, several prefix hits must be chosen, none rejected', () => {
    expect(pickUnderlying('ANGELONE', [{ id: 'ANGELONE', name: 'Angel One' }, { id: 'ANGELONEX', name: 'x' }])).toEqual({ status: 'ONE', id: 'ANGELONE' });
    expect(pickUnderlying('TATA', [{ id: 'TATAMOTORS', name: 'Tata Motors' }, { id: 'TATASTEEL', name: 'Tata Steel' }])).toMatchObject({ status: 'MANY' });
    expect(pickUnderlying('ZZZ', [])).toEqual({ status: 'NONE' });
    expect(pickUnderlying('', [])).toEqual({ status: 'NONE' });
  });
});

describe('group helpers', () => {
  it('reorders by id list, ignoring unknown ids and keeping unlisted ones last', () => {
    const items = ['a', 'b', 'c', 'd'].map((id) => ({ id }));
    expect(reorderBy(items, ['c', 'a', 'zzz']).map((x) => x.id)).toEqual(['c', 'a', 'b', 'd']);
  });
  it('group names compare case- and space-insensitively', () => {
    expect(sameName('Angel One — October Calls', '  angel  one — october calls ')).toBe(true);
    expect(sameName('A', 'B')).toBe(false);
  });
});

// ---- rules --------------------------------------------------------------------------------------------------------------

const now = new Date('2026-10-14T05:00:00Z');
const ref = { underlying: 'ANGELONE', expiry: '2026-10-27', type: 'CE' as const, strike: 300 };
const contract = (over: Partial<Parameters<typeof validateContract>[0]> = {}): ContractValidation =>
  validateContract({ ref, leg: { ltp: 12, oi: 5000, prevOi: 4800, volume: 800, iv: 40, bid: 11.9, ask: 12.1, bidQty: 5000, askQty: 5000 }, lotSize: 250, spot: 295, asOf: '2026-10-14T04:59:40Z', fetchedAt: '2026-10-14T04:59:50Z', source: 'nse', latency: 'SNAPSHOT', marketOpen: true, now, ...over });
const rule = (kind: ContractRule['kind'], params: ContractRule['params'], over: Partial<ContractRule> = {}): ContractRule => ({ id: 'r1', contractId: 'ANGELONE|2026-10-27|CE|300', kind, params, enabled: true, note: '', createdAt: '', lastTriggeredAt: null, ...over });
const input = (r: ContractRule, over: Partial<RuleInput> = {}): RuleInput => ({ rule: r, now, contract: contract(), spot: 295, spotState: 'VALID', candles: null, underlyingFreshness: 'VALID', previous: null, ...over });

describe('rule parameters', () => {
  it('rejects malformed rules', () => {
    expect(validateRuleParams('PREMIUM_CROSS', { price: -1, direction: 'ABOVE' })).toMatch(/positive/);
    expect(validateRuleParams('PREMIUM_CROSS', { price: 10 })).toMatch(/above or below/);
    expect(validateRuleParams('CLOSE_BEYOND_LEVEL', { level: 300, direction: 'ABOVE', tf: '1m' })).toMatch(/5 minutes or longer/);
    expect(validateRuleParams('SPREAD_ABOVE', { spreadPct: 500 })).toMatch(/0 and 100/);
    expect(validateRuleParams('EXPIRY_WITHIN', { days: 0 })).toBeTruthy();
    expect(validateRuleParams('DATA_STALE', {})).toBeNull();
    expect(validateRuleParams('OPTION_VOLUME_ABOVE', { volume: 100 })).toBeNull();
  });
});

describe('rule evaluation', () => {
  it('premium cross fires once beyond the price and not when it was already beyond', () => {
    const r = rule('PREMIUM_CROSS', { price: 11, direction: 'ABOVE' });
    expect(evaluateRule(input(r)).alert?.message).toMatch(/premium 12 is at or above 11/);
    expect(evaluateRule(input(r, { previous: { premium: 12.5, spot: 295 } })).alert).toBeNull();
    expect(evaluateRule(input(r, { previous: { premium: 10, spot: 295 } })).alert).not.toBeNull();
    expect(evaluateRule(input(rule('PREMIUM_CROSS', { price: 20, direction: 'ABOVE' }))).alert).toBeNull();
    expect(evaluateRule(input(rule('PREMIUM_CROSS', { price: 13, direction: 'BELOW' }))).alert).not.toBeNull();
  });
  it('stale or closed-market option data never triggers premium, volume or spread rules', () => {
    for (const [k, p] of [['PREMIUM_CROSS', { price: 5, direction: 'ABOVE' }], ['OPTION_VOLUME_ABOVE', { volume: 1 }], ['SPREAD_ABOVE', { spreadPct: 0.1 }]] as const) {
      const stale = evaluateRule(input(rule(k, p), { contract: contract({ asOf: '2026-10-14T04:30:00Z' }) }));
      expect(stale.alert, k).toBeNull();
      expect(stale.withheld, k).toMatch(/STALE/);
      expect(evaluateRule(input(rule(k, p), { contract: contract({ marketOpen: false, asOf: '2026-10-13T10:00:00Z' }) })).alert, k).toBeNull();
    }
  });
  it('volume uses traded volume, and unknown volume is withheld rather than zero', () => {
    expect(evaluateRule(input(rule('OPTION_VOLUME_ABOVE', { volume: 500 }))).alert?.evidence.join(' ')).toMatch(/not open interest/);
    expect(evaluateRule(input(rule('OPTION_VOLUME_ABOVE', { volume: 900 }))).alert).toBeNull();
    const unknown = contract({ leg: { ltp: 12, oi: 1, prevOi: 1, volume: null, iv: null, bid: 11.9, ask: 12.1 } });
    expect(evaluateRule(input(rule('OPTION_VOLUME_ABOVE', { volume: 1 }), { contract: unknown })).withheld).toMatch(/unknown, not zero/);
  });
  it('spread rule fires above the threshold only', () => {
    expect(evaluateRule(input(rule('SPREAD_ABOVE', { spreadPct: 1 }))).alert).not.toBeNull(); // 1.67%
    expect(evaluateRule(input(rule('SPREAD_ABOVE', { spreadPct: 5 }))).alert).toBeNull();
  });
  it('data-stale rule fires only when stale and is a data-health alert', () => {
    expect(evaluateRule(input(rule('DATA_STALE', {}))).alert).toBeNull();
    const a = evaluateRule(input(rule('DATA_STALE', {}), { contract: contract({ asOf: '2026-10-14T04:30:00Z' }) })).alert;
    expect(a?.kindClass).toBe('DATA_HEALTH');
  });
  it('expiry rule is calendar based and works even on stale data', () => {
    expect(evaluateRule(input(rule('EXPIRY_WITHIN', { days: 14 }))).alert?.message).toMatch(/expires in 13\.\d days/);
    expect(evaluateRule(input(rule('EXPIRY_WITHIN', { days: 5 }))).alert).toBeNull();
    expect(evaluateRule(input(rule('EXPIRY_WITHIN', { days: 14 }), { contract: contract({ asOf: '2026-10-14T04:00:00Z' }) })).alert).not.toBeNull();
  });
  it('an expired contract is not evaluated', () => {
    const exp = contract({ now: new Date('2026-10-28T05:00:00Z'), asOf: '2026-10-28T04:59:00Z' });
    expect(evaluateRule(input(rule('EXPIRY_WITHIN', { days: 30 }), { contract: exp })).withheld).toMatch(/expired/);
  });
  it('underlying level fires on a crossing with fresh data only', () => {
    const r = rule('UNDERLYING_LEVEL', { level: 290, direction: 'ABOVE' });
    expect(evaluateRule(input(r)).alert).not.toBeNull();
    expect(evaluateRule(input(r, { previous: { premium: 12, spot: 291 } })).alert).toBeNull();
    expect(evaluateRule(input(r, { spotState: 'STALE' })).withheld).toMatch(/STALE/);
  });
  it('a disabled rule does nothing', () => {
    const r = rule('PREMIUM_CROSS', { price: 11, direction: 'ABOVE' }, { enabled: false });
    expect(evaluateRule(input(r))).toEqual({ alert: null, withheld: null });
  });

  const series = (tail: Partial<TfCandle>[]): TfCandle[] => {
    const base = bars(40, () => ({ o: 294, h: 295, l: 293, c: 294 }));
    return [...base, ...bars(tail.length, (i) => tail[i]!, base[39]!.t + 900)];
  };
  const up = (c: number, v = 1000, extra: Partial<TfCandle> = {}): Partial<TfCandle> => ({ o: c - 0.5, h: c + 0.7, l: c - 0.8, c, v, ...extra });
  const close = rule('CLOSE_BEYOND_LEVEL', { level: 300, direction: 'ABOVE', tf: '15m' });

  it('a close beyond the level needs a COMPLETED candle; the forming candle never triggers', () => {
    const forming = series([up(299)]);
    forming.push({ t: forming[forming.length - 1]!.t + 900, o: 299, h: 303, l: 298.9, c: 302, v: 9000, complete: false, have: 1, want: 3 });
    expect(evaluateRule(input(close, { candles: forming })).alert).toBeNull();
    const done = series([up(299), up(302)]);
    const a = evaluateRule(input(close, { candles: done })).alert;
    expect(a?.message).toMatch(/completed 15m candle closed above 300/);
    expect(a?.key).toContain(`|15m|${done[done.length - 1]!.t}`);
  });
  it('candle rules are withheld on stale, unknown or closed-market underlying data', () => {
    const done = series([up(299), up(302)]);
    for (const f of ['STALE', 'UNKNOWN', 'MARKET_CLOSED', 'CONFLICT'] as const) {
      const r = evaluateRule(input(close, { candles: done, underlyingFreshness: f }));
      expect(r.alert, f).toBeNull();
      expect(r.withheld, f).toMatch(new RegExp(f));
    }
    expect(evaluateRule(input(close, { candles: bars(10) })).withheld).toMatch(/at least 30/);
    expect(evaluateRule(input(close, { candles: null })).withheld).toMatch(/No candles/);
  });
  it('retest confirms or fails; a plain breakout without a retest does neither', () => {
    const r = rule('RETEST', { level: 300, direction: 'ABOVE', tf: '15m' });
    const held = series([up(301.2, 3000), up(301.4, 1200), up(302.4, 1100, { l: 300.1 }), up(302.2)]);
    expect(evaluateRule(input(r, { candles: held })).alert?.message).toMatch(/retest of 300 held/);
    const failed = series([up(301.2, 3000), up(298.6, 1500)]);
    expect(evaluateRule(input(r, { candles: failed })).alert?.message).toMatch(/failed/);
    const plain = series([up(301.2, 3000)]);
    expect(evaluateRule(input(r, { candles: plain })).alert).toBeNull();
  });
  it('alerts from rules deduplicate and keep the user note', () => {
    const r = rule('PREMIUM_CROSS', { price: 11, direction: 'ABOVE' }, { note: 'my plan' });
    const a = evaluateRule(input(r)).alert!;
    expect(a.evidence.join(' ')).toContain('Your note: my plan');
    expect(dedupeAlerts([a, a], {}, now)).toHaveLength(1);
    expect(dedupeAlerts([a], { [a.key]: new Date(now.getTime() - 60_000).toISOString() }, now)).toHaveLength(0);
  });
});
