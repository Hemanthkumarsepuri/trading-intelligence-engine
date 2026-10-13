// Indian tax estimates for market income. INFORMATIONAL ONLY - not tax advice.
//
// Rules modelled (FY 2026-27, listed equity):
//  - Delivery, held <= 12 months: STCG at 20% (s.111A)
//  - Delivery, held  > 12 months: LTCG at 12.5% on gains above INR 1.25 lakh (s.112A)
//  - STCL sets off against STCG and LTCG; LTCL sets off against LTCG only
//  - STT is NOT deductible against capital gains; brokerage/exchange/stamp are
//  - Intraday equity = speculative business income (slab rate)
//  - F&O = non-speculative business income (slab rate); STT is deductible there
//  - Health & education cess 4%. Surcharge and slab computation are NOT modelled.

import { tradeEconomics } from './charges';
import { financialYear, isLongTermEquity } from './india';
import type { Settings, Trade } from './types';

export const STCG_RATE = 0.2;
export const LTCG_RATE = 0.125;
export const LTCG_EXEMPTION = 125000;
export const CESS = 0.04;

export interface TaxSummary {
  fy: string;
  stcgGross: number;
  ltcgGross: number;
  stcgNet: number;
  ltcgNet: number;
  stclCarryForward: number;
  ltclCarryForward: number;
  ltcgExemptionUsed: number;
  stcgTaxable: number;
  ltcgTaxable: number;
  capitalGainsTax: number;
  speculative: { net: number; trades: number };
  nonSpeculative: {
    net: number;
    trades: number;
    /** ICAI-style turnover: sum of absolute trade results (+ premium on option sells) */
    turnover: number;
    futuresTurnover: number;
    optionsTurnover: number;
  };
  businessIncomeTaxEstimate: number;
  notes: string[];
}

const r0 = (x: number): number => Math.round(x);

export function summarizeTax(trades: Trade[], s: Settings, fy: string): TaxSummary {
  let stcg = 0;
  let ltcg = 0;
  let specNet = 0;
  let specCount = 0;
  let nsNet = 0;
  let nsCount = 0;
  let futTurn = 0;
  let optTurn = 0;

  for (const t of trades) {
    if (t.exitPrice === null || t.exitAt === null) continue;
    const exitDate = t.exitAt.slice(0, 10);
    if (financialYear(exitDate) !== fy) continue;
    const eco = tradeEconomics(t, s);

    if (t.segment === 'EQ_DELIVERY') {
      // Capital gain: STT is not a deductible expense.
      const gain = eco.gross - (eco.charges.total - eco.charges.stt);
      if (isLongTermEquity(t.entryAt.slice(0, 10), exitDate)) ltcg += gain;
      else stcg += gain;
    } else if (t.segment === 'EQ_INTRADAY') {
      specNet += eco.net;
      specCount++;
    } else {
      // F&O: business income, all charges (incl. STT) deductible.
      nsNet += eco.net;
      nsCount++;
      const abs = Math.abs(eco.gross);
      if (t.segment === 'FUT') futTurn += abs;
      else {
        // options: absolute result + premium received on the SELL leg
        const premiumReceived = (t.side === 'SHORT' ? t.entryPrice : t.exitPrice) * t.qty;
        optTurn += abs + premiumReceived;
      }
    }
  }

  // Set-off
  let stcgNet = stcg;
  let ltcgNet = ltcg;
  let stclCarry = 0;
  let ltclCarry = 0;
  if (stcgNet < 0) {
    const setoff = Math.min(-stcgNet, Math.max(ltcgNet, 0));
    ltcgNet -= setoff;
    stclCarry = -stcgNet - setoff;
    stcgNet = 0;
  }
  if (ltcgNet < 0) {
    ltclCarry = -ltcgNet;
    ltcgNet = 0;
  }

  const stcgTaxable = Math.max(0, stcgNet);
  const exemptionUsed = Math.min(LTCG_EXEMPTION, ltcgNet);
  const ltcgTaxable = Math.max(0, ltcgNet - LTCG_EXEMPTION);
  const cgTax = (stcgTaxable * STCG_RATE + ltcgTaxable * LTCG_RATE) * (1 + CESS);

  const businessNet = specNet + nsNet;
  const bizTax = Math.max(0, businessNet) * s.slabRate * (1 + CESS);

  const notes = [
    'Estimate only. Surcharge, slab computation and other income are not modelled. Consult a CA.',
    'STT is excluded from capital-gain costs but included as a business expense for intraday and F&O.',
  ];
  if (specNet < 0) notes.push('Speculative (intraday) losses can only be set off against speculative profits (carry forward 4 years).');
  if (nsNet < 0) notes.push('Non-speculative (F&O) losses carry forward 8 years if the return is filed on time.');
  if (nsCount > 0) notes.push('F&O needs ITR-3. Check whether a tax audit applies based on turnover and presumptive-scheme eligibility.');

  return {
    fy,
    stcgGross: r0(stcg),
    ltcgGross: r0(ltcg),
    stcgNet: r0(stcgNet),
    ltcgNet: r0(ltcgNet),
    stclCarryForward: r0(stclCarry),
    ltclCarryForward: r0(ltclCarry),
    ltcgExemptionUsed: r0(exemptionUsed),
    stcgTaxable: r0(stcgTaxable),
    ltcgTaxable: r0(ltcgTaxable),
    capitalGainsTax: r0(cgTax),
    speculative: { net: r0(specNet), trades: specCount },
    nonSpeculative: {
      net: r0(nsNet),
      trades: nsCount,
      turnover: r0(futTurn + optTurn),
      futuresTurnover: r0(futTurn),
      optionsTurnover: r0(optTurn),
    },
    businessIncomeTaxEstimate: r0(bizTax),
    notes,
  };
}

/** Fiscal years that have at least one closed trade, newest first. */
export function fiscalYearsWithTrades(trades: Trade[], now: Date): string[] {
  const set = new Set<string>();
  set.add(financialYear(now.toISOString().slice(0, 10)));
  for (const t of trades) if (t.exitAt) set.add(financialYear(t.exitAt.slice(0, 10)));
  return [...set].sort().reverse();
}
