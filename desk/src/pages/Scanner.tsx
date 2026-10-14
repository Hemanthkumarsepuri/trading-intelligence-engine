import { useState } from 'react';
import type { ScanResponse } from '../../server/services/wb/scanner';
import type { Candidate } from '../../shared/scanner';
import { fmtIst, fmtNum, fmtSigned } from '../../shared/format';
import { useGet } from '../api';
import { Badge, Card, Empty, ErrorBox, PageHead, Skeleton, Spinner } from '../ui';
import { Disclosure, Evidence, Lifecycle, NOT_ADVICE, StateBadge, Sv, openWorkbench } from '../wb/common';

interface Filters {
  segment: 'ALL' | 'STOCK' | 'INDEX';
  direction: 'ANY' | 'UP' | 'DOWN';
  minLiquidity: 'ANY' | 'MODERATE' | 'LIQUID';
  optionType: 'ANY' | 'CE' | 'PE';
  trend: 'ANY' | 'UP' | 'DOWN';
  minRvol: string;
  avoidEvents: boolean;
  confirmedOnly: boolean;
  cap: number;
}
const INITIAL: Filters = { segment: 'ALL', direction: 'ANY', minLiquidity: 'ANY', optionType: 'ANY', trend: 'ANY', minRvol: '', avoidEvents: false, confirmedOnly: false, cap: 40 };

function queryOf(f: Filters): string {
  const p = new URLSearchParams();
  p.set('segment', f.segment);
  p.set('direction', f.direction);
  p.set('minLiquidity', f.minLiquidity);
  p.set('optionType', f.optionType);
  p.set('trend', f.trend);
  if (f.minRvol !== '' && Number(f.minRvol) > 0) p.set('minRvol', f.minRvol);
  if (f.avoidEvents) p.set('avoidEvents', '1');
  if (f.confirmedOnly) p.set('states', 'CONFIRMED,CONFIRMATION_PENDING');
  p.set('cap', String(f.cap));
  return p.toString();
}

function Sel<T extends string>({ id, label, value, options, onChange }: { id: string; label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="stack" style={{ gap: 2 }}>
      <label className="small muted" htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value as T)} style={{ width: 'auto' }}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

function CandidateCard({ c }: { c: Candidate }) {
  const s = c.setup;
  return (
    <section className="card" data-testid="candidate-card" aria-label={`${c.symbol} candidate`}>
      <header className="card-h">
        <h2>{c.symbol} <span className="muted small">{c.name}{c.sector ? ` · ${c.sector}` : ''}</span></h2>
        <div className="row"><Lifecycle state={s.state} /><StateBadge state={c.dataState} /></div>
      </header>
      <div className="card-b stack">
        <div className="row" style={{ gap: 14 }}>
          <span className="mono" style={{ fontSize: 18 }}>{c.price !== null ? fmtNum(c.price) : '-'}</span>
          <span className={c.changePct === null ? 'muted' : c.changePct >= 0 ? 'pos' : 'neg'}>{c.changePct !== null ? `${fmtSigned(c.changePct, 2)}%` : ''}</span>
          <span className="small muted">{c.priceAsOf ? `price time ${fmtIst(c.priceAsOf)} IST` : 'no price timestamp'}</span>
        </div>
        <div className="small">
          <strong>{s.direction === 'UP' ? 'Resistance' : 'Support'} {fmtNum(s.level.price)}</strong> on the {s.tf} chart · {s.level.method.replace(/_/g, ' ').toLowerCase()} · {fmtNum(Math.abs(s.distanceAtr ?? 0))}×ATR from price
        </div>
        <dl className="kv">
          <dt>Relative volume</dt><dd>{c.rvol?.value != null ? `${c.rvol.value.toFixed(2)}×` : <span className="muted" title={c.rvol?.note}>unavailable</span>}</dd>
          <dt>Timeframes</dt><dd>{c.mtf.alignment}</dd>
          <dt>vs sector</dt><dd>{c.sectorRelPct !== null ? `${fmtSigned(c.sectorRelPct, 2)} pts` : '-'}</dd>
          <dt>vs NIFTY 50</dt><dd>{c.benchRelPct !== null ? `${fmtSigned(c.benchRelPct, 2)} pts` : '-'}</dd>
        </dl>
        <Evidence title="Why it qualified" items={c.why} tone="pos" />
        <Evidence title="What could invalidate it" items={[s.invalidation, ...c.risks]} tone="neg" />
        <div className="small"><strong>Confirmation:</strong> {s.confirmation}</div>
        {c.upcoming.length > 0 && <div className="small"><Badge kind="warn">Event</Badge> {c.upcoming.map((u) => `${u.event.headline.slice(0, 80)} in ${u.daysAway} day(s)`).join(' · ')}</div>}
        <div>
          <div className="small" style={{ fontWeight: 650 }}>Contracts to inspect <span className="muted">(not recommendations)</span></div>
          {c.contracts.length === 0 ? <div className="small muted">{c.contractNote ?? 'No contracts.'}</div> : (
            <div className="tbl-wrap">
              <table>
                <thead><tr><th>Contract</th><th className="num">Premium</th><th className="num">Volume</th><th className="num">OI</th><th className="num">Spread</th><th>Liquidity</th></tr></thead>
                <tbody>
                  {c.contracts.map((k) => (
                    <tr key={k.id}>
                      <td><a href={`#/workbench?u=${c.symbol}&c=${encodeURIComponent(k.id)}`}>{k.ref.expiry} {k.ref.strike} {k.ref.type}</a></td>
                      <td className="num"><Sv f={k.ltp} hideBadge /></td>
                      <td className="num"><Sv f={k.volume} digits={0} hideBadge /></td>
                      <td className="num"><Sv f={k.oi} digits={0} hideBadge /></td>
                      <td className="num"><Sv f={k.spreadPct} suffix="%" hideBadge /></td>
                      <td>{k.liquidity.cls.replace('_', ' ').toLowerCase()} {k.state !== 'VALID' && <StateBadge state={k.state} />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {c.contractNote && c.contracts.length > 0 && <div className="small muted">{c.contractNote}</div>}
        </div>
        <Disclosure summary="Audit trail: every check applied">
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            {c.audit.map((a, i) => <li key={i}>{a.passed === true ? '✓' : a.passed === false ? '✗' : '–'} <strong>{a.check}:</strong> {a.detail}</li>)}
          </ul>
        </Disclosure>
        <div className="row">
          <button type="button" className="btn primary" onClick={() => openWorkbench(c.symbol, c.contracts[0]?.id, s.tf)}>Open in Workbench</button>
        </div>
      </div>
    </section>
  );
}

export function Scanner() {
  const [f, setF] = useState<Filters>(INITIAL);
  const [applied, setApplied] = useState<Filters | null>(null);
  const q = useGet<ScanResponse>(applied ? `/api/workbench/scan?${queryOf(applied)}` : null);
  const d = q.data?.data;

  return (
    <div className="page">
      <PageHead title="Market scanner" sub="F&O underlyings with an active breakout structure, ordered by evidence. Candidates are things to research, not trades to take." />
      <Card title="Filters">
        <form
          className="row"
          style={{ alignItems: 'flex-end', gap: 14 }}
          onSubmit={(e) => { e.preventDefault(); setApplied({ ...f }); }}
          aria-label="Scanner filters"
        >
          <Sel id="f-seg" label="Underlying" value={f.segment} onChange={(v) => setF({ ...f, segment: v })} options={[{ value: 'ALL', label: 'Stocks and indices' }, { value: 'STOCK', label: 'Stock options' }, { value: 'INDEX', label: 'Index options' }]} />
          <Sel id="f-dir" label="Setup direction" value={f.direction} onChange={(v) => setF({ ...f, direction: v })} options={[{ value: 'ANY', label: 'Any' }, { value: 'UP', label: 'Upside (resistance)' }, { value: 'DOWN', label: 'Downside (support)' }]} />
          <Sel id="f-opt" label="Option type" value={f.optionType} onChange={(v) => setF({ ...f, optionType: v })} options={[{ value: 'ANY', label: 'Matches setup' }, { value: 'CE', label: 'Calls only' }, { value: 'PE', label: 'Puts only' }]} />
          <Sel id="f-liq" label="Contract liquidity" value={f.minLiquidity} onChange={(v) => setF({ ...f, minLiquidity: v })} options={[{ value: 'ANY', label: 'Any' }, { value: 'MODERATE', label: 'Moderate or better' }, { value: 'LIQUID', label: 'Liquid only' }]} />
          <Sel id="f-trend" label="Timeframes agree" value={f.trend} onChange={(v) => setF({ ...f, trend: v })} options={[{ value: 'ANY', label: 'Any' }, { value: 'UP', label: 'All up' }, { value: 'DOWN', label: 'All down' }]} />
          <div className="stack" style={{ gap: 2 }}>
            <label className="small muted" htmlFor="f-rv">Min relative volume</label>
            <input id="f-rv" inputMode="decimal" placeholder="e.g. 1.5" value={f.minRvol} onChange={(e) => setF({ ...f, minRvol: e.target.value.replace(/[^0-9.]/g, '') })} style={{ width: 100 }} />
          </div>
          <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={f.confirmedOnly} onChange={(e) => setF({ ...f, confirmedOnly: e.target.checked })} style={{ width: 'auto' }} />Confirmed or pending only</label>
          <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={f.avoidEvents} onChange={(e) => setF({ ...f, avoidEvents: e.target.checked })} style={{ width: 'auto' }} />Hide names with results soon</label>
          <button className="btn primary" disabled={q.isFetching}>{q.isFetching ? 'Scanning…' : 'Run scan'}</button>
        </form>
        <div className="small muted" style={{ marginTop: 8 }}>Scans up to {f.cap} liquid F&O names to stay inside provider limits. Results are cached for a couple of minutes.</div>
      </Card>

      {!applied && <Empty>Choose filters and run the scan. Nothing is scanned until you do, so no provider is called in the background.</Empty>}
      {applied && q.isFetching && !d && <Card><div className="row"><Spinner /> <span>Scanning… this can take up to a minute on a cold cache.</span></div><Skeleton h={120} /></Card>}
      {q.isError && <ErrorBox error={q.error} retry={() => void q.refetch()} />}

      {d && (
        <>
          <div className="banner info" role="status" data-testid="scan-summary">
            <div style={{ flex: 1 }}>
              <strong>{d.summary.stockCandidates} underlying{d.summary.stockCandidates === 1 ? '' : 's'} · {d.summary.contractCount} contract{d.summary.contractCount === 1 ? '' : 's'} to inspect</strong>
              <div className="small">{d.summary.message}</div>
              <div className="small muted">Scanned {d.limits.evaluated} of {d.limits.universe} symbols · {d.session.label} · as of {fmtIst(d.asOf)} IST</div>
            </div>
          </div>
          {d.notes.length > 0 && <div className="small muted">{d.notes.join(' ')}</div>}
          {d.candidates.length === 0 && <Empty>No underlying met every filter. {Object.entries(d.summary.excludedByReason).slice(0, 4).map(([r, n]) => `${n} × ${r}`).join('; ')}. Loosen a filter to see more.</Empty>}
          <div className="grid g2" style={{ alignItems: 'start' }}>
            {d.candidates.map((c) => <CandidateCard key={c.symbol} c={c} />)}
          </div>
          <div className="small muted">{d.ranking}</div>
          {d.excluded.length > 0 && (
            <Card title={`Excluded (${d.excluded.length})`}>
              <Disclosure summary="Why the others were left out">
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                  {Object.entries(d.summary.excludedByReason).sort((a, b) => b[1] - a[1]).map(([r, n]) => <li key={r}>{n} × {r}: {d.excluded.filter((e) => e.reason === r).slice(0, 8).map((e) => e.symbol).join(', ')}{n > 8 ? '…' : ''}</li>)}
                </ul>
              </Disclosure>
            </Card>
          )}
          <div className="small muted">{NOT_ADVICE}</div>
        </>
      )}
    </div>
  );
}
