// Resolve a typed or structured contract request to an exact, provider-listed contract.
// The identity (symbol, instrument key, lot size) is read from the provider's chain; it is never
// built from the user's text. Anything unlisted, expired, unsupported or ambiguous is returned with
// an explanation and, where a choice is needed, the options to choose from.

import { contractId, labelFor, parseNseIdentifier, parseSearch, type ContractRef, type OptType, type SearchQuery } from '../../../shared/contract';
import { pickExpiry, pickUnderlying, type ContractIdentity, type ResolvedContract } from '../../../shared/groups';
import { istDateStr } from '../../../shared/india';
import { OPTION_UNDERLYINGS } from '../../../shared/universe';
import type { Ctx } from '../../config';
import { getSymbolMaster } from '../symbols';
import { fnoStocks, loadChain, loadExpiries, resolveInstrument } from './data';

export interface ResolveInput {
  q?: string;
  u?: string;
  expiry?: string;
  type?: OptType;
  strike?: number;
}

export type ResolveField = 'underlying' | 'expiry' | 'type' | 'strike';

export interface Resolution {
  status: 'RESOLVED' | 'NEEDS_SELECTION' | 'REJECTED';
  /** what was understood from the text, shown before anything is added */
  parsed: { underlying: string | null; strike: number | null; type: OptType | null; expiry: string | null };
  /** which field the user must choose next */
  field?: ResolveField;
  options?: { value: string; label: string }[];
  reason?: string;
  contract?: ResolvedContract;
  notes: string[];
}

const MAX_OPTIONS = 40;

export function toQuery(i: ResolveInput): SearchQuery {
  if (i.q !== undefined) return parseSearch(i.q.slice(0, 80));
  return { text: (i.u ?? '').trim().toUpperCase(), strike: i.strike ?? null, type: i.type ?? null, expiry: i.expiry ? { iso: i.expiry } : null };
}

async function underlyingHits(ctx: Ctx, text: string): Promise<{ id: string; name: string; fno: boolean }[]> {
  const t = text.toUpperCase();
  const [master, fno] = await Promise.all([getSymbolMaster(ctx), fnoStocks(ctx)]);
  const out = new Map<string, { id: string; name: string; fno: boolean }>();
  for (const u of OPTION_UNDERLYINGS) if (u.id === t || u.id.startsWith(t) || u.name.toUpperCase().includes(t)) out.set(u.id, { id: u.id, name: u.name, fno: true });
  for (const r of master.rows) {
    if (r.symbol === t || r.symbol.startsWith(t) || r.name.toUpperCase().includes(t)) out.set(r.symbol, { id: r.symbol, name: r.name, fno: r.symbol in fno });
    if (out.size > 60) break;
  }
  // an F&O symbol the master list does not carry (fallback list) is still honoured when NSE lists its lot size
  if (!out.has(t) && t in fno) out.set(t, { id: t, name: t, fno: true });
  return [...out.values()];
}

export async function resolveContract(ctx: Ctx, input: ResolveInput): Promise<Resolution> {
  const q = toQuery(input);
  const notes: string[] = [];
  const parsed: Resolution['parsed'] = { underlying: q.text || null, strike: q.strike, type: q.type, expiry: null };
  const reject = (reason: string): Resolution => ({ status: 'REJECTED', parsed, reason, notes });
  const need = (field: ResolveField, options: { value: string; label: string }[], reason: string): Resolution => ({ status: 'NEEDS_SELECTION', parsed, field, options, reason, notes });

  if (!q.text) return reject('Type an underlying symbol first, for example ANGELONE 300 CE OCT.');

  // 1. underlying
  const hits = await underlyingHits(ctx, q.text);
  const pick = pickUnderlying(q.text, hits);
  if (pick.status === 'NONE') return reject(`"${q.text}" is not a known NSE symbol or index. Check the spelling or search by company name.`);
  if (pick.status === 'MANY') return need('underlying', pick.options.map((o) => ({ value: o.id, label: `${o.id} · ${o.name}` })), `"${q.text}" matches several instruments. Choose one.`);
  parsed.underlying = pick.id;
  const inst = await resolveInstrument(ctx, pick.id);
  if (!inst) return reject(`${pick.id} could not be resolved against the NSE symbol list.`);
  if (!inst.fno) return reject(`${inst.id} is not in the F&O segment, so it has no listed option contracts.`);

  // 2. expiry, from the LISTED expiries only
  const ex = await loadExpiries(ctx, inst);
  const today = istDateStr(ctx.now());
  const ep = pickExpiry(ex.expiries, q.expiry, today);
  if (ep.status === 'NONE') return reject(ep.reason);
  if (ep.status === 'MANY') return need('expiry', ep.options.map((e) => ({ value: e, label: e })), q.expiry ? 'That date matches more than one listed expiry. Choose the exact expiry.' : 'Choose an expiry. None is picked for you.');
  parsed.expiry = ep.expiry;
  if (ep.inferred) notes.push(ep.inferred);

  // 3. option type
  if (!q.type) return need('type', [{ value: 'CE', label: 'Call (CE)' }, { value: 'PE', label: 'Put (PE)' }], 'Choose call or put.');

  // 4. strike, from the live chain
  const set = await loadChain(ctx, inst, ep.expiry);
  const strikes = [...new Set(set.chain.rows.map((r) => r.strike))].sort((a, b) => a - b);
  if (q.strike === null) {
    const spot = set.chain.spot ?? strikes[Math.floor(strikes.length / 2)] ?? 0;
    const near = [...strikes].sort((a, b) => Math.abs(a - spot) - Math.abs(b - spot)).slice(0, MAX_OPTIONS).sort((a, b) => a - b);
    return need('strike', near.map((s) => ({ value: String(s), label: String(s) })), 'Choose a listed strike.');
  }
  const row = set.chain.rows.find((r) => Math.abs(r.strike - q.strike!) < 0.001);
  if (!row) {
    const near = [...strikes].sort((a, b) => Math.abs(a - q.strike!) - Math.abs(b - q.strike!)).slice(0, 6).sort((a, b) => a - b);
    return reject(`Strike ${q.strike} is not listed for ${inst.id} ${ep.expiry}. Nearest listed strikes: ${near.join(', ')}.`);
  }

  // 5. identity from the provider, cross-checked
  const ref: ContractRef = { underlying: inst.id, expiry: ep.expiry, type: q.type, strike: row.strike };
  const leg = q.type === 'CE' ? row.ce : row.pe;
  const key = leg.instrumentKey ?? null;
  if (key && /^OPT(STK|IDX)/.test(key)) {
    const p = parseNseIdentifier(key);
    if (!p || p.underlying !== ref.underlying || p.expiry !== ref.expiry || p.type !== ref.type || Math.abs(p.strike - ref.strike) > 0.001) return reject(`The provider's identifier ${key} does not match ${labelFor(ref)}; the contract was not added.`);
  }
  if (!key) notes.push('The provider did not supply an instrument identifier for this contract.');
  const lot = ex.lotSize ?? inst.lotSize;
  if (lot === null) notes.push('The official lot size is not available from the provider right now.');
  const identity: ContractIdentity = { underlying: ref.underlying, expiry: ref.expiry, type: ref.type, strike: ref.strike, canonicalSymbol: key, instrumentKey: key && key.includes('|') ? key : null, lotSize: lot, source: set.chain.source, verifiedAt: ctx.now().toISOString() };
  if (set.chain.source === 'fixture') notes.push('DEMO DATA: this identity comes from the synthetic demo chain.');
  const contract: ResolvedContract = { ref, id: contractId(ref), label: labelFor(ref), identity };
  return { status: 'RESOLVED', parsed, contract, notes };
}
