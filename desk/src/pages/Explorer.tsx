import { useMemo, useState } from 'react';
import type { ExplorerRow, ExplorerView } from '../../server/services/wb/contracts';
import type { ContractValidation } from '../../shared/contract';
import { fmtIst } from '../../shared/format';
import { OPTION_UNDERLYINGS } from '../../shared/universe';
import { useGet, useSend } from '../api';
import { setQuery, useLoc } from '../nav';
import { Async, Badge, Card, Empty, errMsg, PageHead, Segmented, Skeleton, useToast } from '../ui';
import { NOT_ADVICE, Prov, StateBadge, Sv, openWorkbench, useDebounced } from '../wb/common';

type Side = 'CE' | 'PE' | 'BOTH';
type Sort = 'strike' | 'volume' | 'oi' | 'spread';

const LIQ_ORDER = { LIQUID: 3, MODERATE: 2, ILLIQUID: 1, DATA_INSUFFICIENT: 0 } as const;

function Leg({ c, onAdd, onOpen }: { c: ContractValidation; onAdd: () => void; onOpen: () => void }) {
  return (
    <>
      <td className="num"><Sv f={c.oi} digits={0} hideBadge /></td>
      <td className="num"><Sv f={c.volume} digits={0} hideBadge /></td>
      <td className="num"><Sv f={c.spreadPct} suffix="%" hideBadge /></td>
      <td className="num" title={c.state}><Sv f={c.ltp} hideBadge /> {c.state !== 'VALID' && c.quoteAvailable && <StateBadge state={c.state} />}</td>
      <td>
        <span className="small">{c.liquidity.cls.replace('_', ' ').toLowerCase()}</span>
      </td>
      <td>
        <button type="button" className="btn sm" onClick={onOpen} aria-label={`Open ${c.ref.strike} ${c.ref.type} in Workbench`}>Open</button>{' '}
        <button type="button" className="btn sm" onClick={onAdd} aria-label={`Add ${c.ref.strike} ${c.ref.type} to watchlist`}>Add</button>
      </td>
    </>
  );
}

export function Explorer() {
  const loc = useLoc();
  const toast = useToast();
  const u = loc.query.get('u') ?? 'NIFTY';
  const expiryQ = loc.query.get('expiry');
  const [side, setSide] = useState<Side>('BOTH');
  const [moneyness, setMoneyness] = useState<'ALL' | 'ITM' | 'ATM' | 'OTM'>('ALL');
  const [minLiq, setMinLiq] = useState<'ANY' | 'MODERATE' | 'LIQUID'>('ANY');
  const [minVol, setMinVol] = useState('');
  const [minOi, setMinOi] = useState('');
  const [sort, setSort] = useState<Sort>('strike');
  const [sym, setSym] = useState('');
  const dsym = useDebounced(sym.trim().toUpperCase(), 300);
  const each = 20;

  const q = useGet<ExplorerView>(`/api/workbench/chain/${encodeURIComponent(u)}?each=${each}${expiryQ ? `&expiry=${expiryQ}` : ''}`, { poll: 30_000 });
  const add = useSend<{ id: string }, { duplicate: boolean }>('POST', '/api/workbench/contracts', ['/api/workbench/contracts']);
  const addId = (id: string) => add.mutate({ id }, { onSuccess: (r) => toast(r.data.duplicate ? 'Already in your watchlist. Nothing changed.' : 'Contract added to your watchlist'), onError: (e) => toast(errMsg(e), true) });

  const rows = useMemo(() => {
    const d = q.data?.data.rows ?? [];
    const keep = (r: ExplorerRow, c: ContractValidation, m: ExplorerRow['moneyness']['ce']): boolean => {
      if (moneyness !== 'ALL' && m !== moneyness) return false;
      if (minLiq !== 'ANY' && LIQ_ORDER[c.liquidity.cls] < LIQ_ORDER[minLiq]) return false;
      if (minVol !== '' && !(c.volume.value !== null && c.volume.value >= Number(minVol))) return false;
      if (minOi !== '' && !(c.oi.value !== null && c.oi.value >= Number(minOi))) return false;
      return r.strike > 0;
    };
    const out = d.filter((r) => (side !== 'PE' ? keep(r, r.ce, r.moneyness.ce) : false) || (side !== 'CE' ? keep(r, r.pe, r.moneyness.pe) : false));
    const key = (r: ExplorerRow): number => {
      const c = side === 'PE' ? r.pe : r.ce;
      return sort === 'volume' ? -(c.volume.value ?? -1) : sort === 'oi' ? -(c.oi.value ?? -1) : sort === 'spread' ? (c.spreadPct.value ?? 1e9) : r.strike;
    };
    return [...out].sort((a, b) => key(a) - key(b));
  }, [q.data, side, moneyness, minLiq, minVol, minOi, sort]);

  return (
    <div className="page">
      <PageHead title="Contract Explorer" sub="Every listed strike for one underlying and expiry, with the quality of each quote. Volume and open interest are different measures and are never mixed." />
      <Card title="Underlying and expiry">
        <div className="row" style={{ alignItems: 'flex-end', gap: 14 }}>
          <div className="stack" style={{ gap: 2 }}>
            <label className="small muted" htmlFor="ex-u">Index</label>
            <select id="ex-u" value={OPTION_UNDERLYINGS.some((o) => o.id === u) ? u : ''} onChange={(e) => { if (e.target.value) setQuery('u', e.target.value); setQuery('expiry', null); }} style={{ width: 'auto' }}>
              <option value="">Custom…</option>
              {OPTION_UNDERLYINGS.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </div>
          <form className="stack" style={{ gap: 2 }} onSubmit={(e) => { e.preventDefault(); if (dsym) { setQuery('u', dsym); setQuery('expiry', null); setSym(''); } }}>
            <label className="small muted" htmlFor="ex-s">F&O stock</label>
            <input id="ex-s" placeholder="e.g. RELIANCE, then Enter" value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} style={{ width: 200 }} />
          </form>
          {q.data && (
            <div className="stack" style={{ gap: 2 }}>
              <label className="small muted" htmlFor="ex-e">Expiry</label>
              <select id="ex-e" value={q.data.data.expiry} onChange={(e) => setQuery('expiry', e.target.value)} style={{ width: 'auto' }}>
                {q.data.data.expiries.map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
            </div>
          )}
          <button type="button" className="btn sm" onClick={() => openWorkbench(u)}>Open {u} in Workbench</button>
        </div>
      </Card>

      <Async q={q} skeleton={<Card><Skeleton h={320} /></Card>}>
        {(d) => (
          <>
            <div className="row" style={{ gap: 18 }}>
              <strong>{d.underlying.name}</strong>
              <span>Spot <Sv f={d.spot} /></span>
              <span>Lot size <Sv f={d.lotSize} digits={0} /></span>
              <span>ATM {d.atmStrike ?? '-'}</span>
              <StateBadge state={d.state} />
              <span className="small muted">{d.rows.length} of {d.totalStrikes} strikes shown</span>
            </div>
            {d.expiryReason && <div className="small muted">Default expiry: {d.expiryReason}</div>}
            <Card title="Filter and sort">
              <div className="row" style={{ alignItems: 'flex-end', gap: 14 }}>
                <Segmented value={side} onChange={setSide} label="Side" options={[{ value: 'BOTH', label: 'Both' }, { value: 'CE', label: 'Calls' }, { value: 'PE', label: 'Puts' }]} />
                <Segmented value={moneyness} onChange={setMoneyness} label="Moneyness" options={[{ value: 'ALL', label: 'All' }, { value: 'ITM', label: 'ITM' }, { value: 'ATM', label: 'ATM' }, { value: 'OTM', label: 'OTM' }]} />
                <div className="stack" style={{ gap: 2 }}>
                  <label className="small muted" htmlFor="ex-liq">Liquidity</label>
                  <select id="ex-liq" value={minLiq} onChange={(e) => setMinLiq(e.target.value as typeof minLiq)} style={{ width: 'auto' }}>
                    <option value="ANY">Any</option><option value="MODERATE">Moderate or better</option><option value="LIQUID">Liquid only</option>
                  </select>
                </div>
                <div className="stack" style={{ gap: 2 }}><label className="small muted" htmlFor="ex-v">Min volume</label><input id="ex-v" inputMode="numeric" value={minVol} onChange={(e) => setMinVol(e.target.value.replace(/\D/g, ''))} style={{ width: 90 }} /></div>
                <div className="stack" style={{ gap: 2 }}><label className="small muted" htmlFor="ex-o">Min open interest</label><input id="ex-o" inputMode="numeric" value={minOi} onChange={(e) => setMinOi(e.target.value.replace(/\D/g, ''))} style={{ width: 110 }} /></div>
                <div className="stack" style={{ gap: 2 }}>
                  <label className="small muted" htmlFor="ex-sort">Sort by</label>
                  <select id="ex-sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)} style={{ width: 'auto' }}>
                    <option value="strike">Strike</option><option value="volume">Volume</option><option value="oi">Open interest</option><option value="spread">Tightest spread</option>
                  </select>
                </div>
              </div>
            </Card>
            {rows.length === 0 ? (
              <Empty>No strike matches these filters. Contracts with unknown volume, open interest or spread are excluded by those filters, not treated as zero.</Empty>
            ) : (
              <Card flush title="Chain">
                <div className="tbl-wrap" style={{ maxHeight: 560, overflowY: 'auto' }}>
                  <table data-testid="explorer-table">
                    <thead>
                      <tr>
                        {side !== 'PE' && <><th className="num">CE OI</th><th className="num">CE vol</th><th className="num">CE spread</th><th className="num">CE LTP</th><th>CE liquidity</th><th>CE</th></>}
                        <th className="center">Strike</th>
                        {side !== 'CE' && <><th className="num">PE OI</th><th className="num">PE vol</th><th className="num">PE spread</th><th className="num">PE LTP</th><th>PE liquidity</th><th>PE</th></>}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.strike} style={r.strike === d.atmStrike ? { background: 'var(--card-2)' } : undefined}>
                          {side !== 'PE' && <Leg c={r.ce} onAdd={() => addId(r.ce.id)} onOpen={() => openWorkbench(u, r.ce.id)} />}
                          <td className="center"><strong>{r.strike}</strong>{r.strike === d.atmStrike && <> <Badge kind="info">ATM</Badge></>}</td>
                          {side !== 'CE' && <Leg c={r.pe} onAdd={() => addId(r.pe.id)} onOpen={() => openWorkbench(u, r.pe.id)} />}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
            <div className="small muted"><Prov p={d.provenance} state={d.state} /></div>
            <div className="small muted">Provider time {d.provenance.asOf ? fmtIst(d.provenance.asOf) : 'not stated'} IST · OI and volume are in contracts; depth quantities are in units.</div>
            <div className="small muted">{NOT_ADVICE}</div>
          </>
        )}
      </Async>
    </div>
  );
}
