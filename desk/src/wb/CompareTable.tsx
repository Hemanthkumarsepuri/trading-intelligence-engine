// Side-by-side contract comparison, shared by the Compare screen and the watchlist quick comparison.
// Every cell keeps its own unknowns: a dash means the provider gave no value, never zero.

import type { ReactNode } from 'react';
import type { TrackedRow } from '../../server/services/wb/contracts';
import { fmtIst, fmtNum } from '../../shared/format';
import { Badge } from '../ui';
import { StateBadge, Sv, openWorkbench } from './common';

type Cell = (r: TrackedRow) => ReactNode;

const when = (iso: string | null | undefined) => (iso ? `${fmtIst(iso)}` : 'no provider time');

const ROWS: [string, Cell][] = [
  ['Contract', (r) => <a href={`#/workbench?u=${r.item.ref.underlying}&c=${encodeURIComponent(r.item.id)}`}>{r.item.ref.strike} {r.item.ref.type}</a>],
  ['Exact expiry', (r) => r.item.ref.expiry],
  ['Underlying price', (r) => (r.spot !== null ? <span className="mono">{fmtNum(r.spot)}</span> : <span className="muted">-</span>)],
  ['Underlying price time', (r) => <span className="small">{when(r.chain?.asOf)}</span>],
  ['Option premium (LTP)', (r) => (r.validation ? <Sv f={r.validation.ltp} /> : '-')],
  ['Premium time', (r) => <span className="small">{when(r.validation?.ltp.prov?.asOf ?? r.chain?.asOf)}</span>],
  ['Bid / Ask', (r) => (r.validation ? <><Sv f={r.validation.bid} hideBadge /> / <Sv f={r.validation.ask} hideBadge /></> : '-')],
  ['Spread', (r) => (r.validation ? <><Sv f={r.validation.spreadAbs} hideBadge /> (<Sv f={r.validation.spreadPct} suffix="%" hideBadge />)</> : '-')],
  ['Bid / Ask qty (units)', (r) => (r.validation ? <><Sv f={r.validation.bidQty} digits={0} hideBadge /> / <Sv f={r.validation.askQty} digits={0} hideBadge /></> : '-')],
  ['Traded volume (contracts)', (r) => (r.validation ? <Sv f={r.validation.volume} digits={0} hideBadge /> : '-')],
  ['Open interest (contracts)', (r) => (r.validation ? <Sv f={r.validation.oi} digits={0} hideBadge /> : '-')],
  ['OI change vs previous day', (r) => (r.validation ? <Sv f={r.validation.oiChange} digits={0} hideBadge /> : '-')],
  ['Implied volatility', (r) => (r.validation ? <Sv f={r.validation.iv} suffix="%" hideBadge /> : '-')],
  ['Delta', (r) => (r.validation ? <><Sv f={r.validation.delta} digits={3} hideBadge />{r.validation.delta.prov?.note === 'calculated' && <span className="small muted"> calc.</span>}</> : '-')],
  ['Gamma', (r) => (r.validation ? <Sv f={r.validation.gamma} digits={5} hideBadge /> : '-')],
  ['Theta / day', (r) => (r.validation ? <Sv f={r.validation.theta} hideBadge /> : '-')],
  ['Vega / vol pt', (r) => (r.validation ? <Sv f={r.validation.vega} hideBadge /> : '-')],
  ['Intrinsic / time value', (r) => (r.validation ? <><Sv f={r.validation.intrinsic} hideBadge /> / <Sv f={r.validation.timeValue} hideBadge /></> : '-')],
  ['Lot size', (r) => (r.validation?.lotSize ?? <span className="muted">unknown</span>)],
  ['One-lot premium outlay', (r) => (r.validation ? <Sv f={r.validation.oneLotPremium} digits={0} prefix="₹" hideBadge /> : '-')],
  ['Days to expiry', (r) => (r.validation ? (r.validation.daysToExpiry !== null ? fmtNum(r.validation.daysToExpiry, 1) : 'expired') : '-')],
  ['Liquidity class', (r) => (r.validation ? r.validation.liquidity.cls.replace('_', ' ').toLowerCase() : '-')],
  ['Source / latency', (r) => <span className="small">{r.chain ? `${r.chain.source} · ${r.validation?.ltp.prov?.latency.toLowerCase() ?? 'n/a'}${r.chain.stale ? ' · last good copy' : ''}` : 'none'}</span>],
  ['Data quality', (r) => (r.validation ? <StateBadge state={r.validation.state} /> : <Badge title="No live data for this contract">{r.problem ? 'NO DATA' : 'UNKNOWN'}</Badge>)],
];

export function CompareTable({ rows, labelHeader = true }: { rows: TrackedRow[]; labelHeader?: boolean }) {
  return (
    <div className="tbl-wrap">
      <table data-testid="compare-table">
        <thead>
          <tr>
            <th>Measure</th>
            {rows.map((r) => (
              <th key={r.item.id} className="num">
                {labelHeader ? r.label : `${r.item.ref.strike} ${r.item.ref.type}`}
                <div className="small muted" style={{ fontWeight: 400 }}>
                  <button type="button" className="btn sm ghost" onClick={() => openWorkbench(r.item.ref.underlying, r.item.id)}>Open charts</button>
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.some((r) => r.problem) && (
            <tr>
              <td>Problem</td>
              {rows.map((r) => <td key={r.item.id} className="num small warn-t">{r.problem ?? '-'}</td>)}
            </tr>
          )}
          {ROWS.map(([label, f]) => (
            <tr key={label}>
              <td>{label}</td>
              {rows.map((r) => <td key={r.item.id} className="num">{f(r)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="small muted" style={{ marginTop: 6 }}>
        Premium history: option candles need an entitled provider (Upstox token); without one the Workbench chart shows only snapshots this app observed. Delayed or snapshot quotes are labelled per contract.
        Comparison is for research and says nothing about which contract is better; liquidity, volatility sensitivity and premium behaviour differ per contract.
      </div>
    </div>
  );
}
