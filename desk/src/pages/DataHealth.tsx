import { useState } from 'react';
import type { HealthView } from '../../server/services/wb/misc';
import { fmtIst } from '../../shared/format';
import type { WbAlert } from '../../shared/wbalerts';
import { useGet, useSend } from '../api';
import { Async, Badge, Card, Empty, errMsg, PageHead, Skeleton, useToast } from '../ui';
import { STATE_HELP, StateBadge } from '../wb/common';
import type { DataState } from '../../shared/quality';

function Alerts() {
  const toast = useToast();
  const q = useGet<{ alerts: WbAlert[] }>('/api/workbench/alerts');
  const run = useSend<Record<string, never>, { created: WbAlert[]; evaluated: { underlyings: number; contracts: number }; skipped: string[] }>('POST', '/api/workbench/alerts/evaluate', ['/api/workbench/alerts']);
  const [last, setLast] = useState<string | null>(null);
  return (
    <Card
      title="Workbench alerts"
      right={<button className="btn sm" disabled={run.isPending} onClick={() => run.mutate({}, { onSuccess: (r) => { setLast(`${r.data.created.length} new from ${r.data.evaluated.underlyings} underlying(s) and ${r.data.evaluated.contracts} contract(s)${r.data.skipped.length ? `; skipped: ${r.data.skipped.join('; ')}` : ''}`); toast('Alerts evaluated'); }, onError: (e) => toast(errMsg(e), true) })}>{run.isPending ? 'Evaluating…' : 'Evaluate now'}</button>}
    >
      <p className="small muted">In-app only. These alerts are never sent to Telegram or anywhere else, and none is raised from stale or conflicting data. Each repeats at most once per six hours.</p>
      {last && <div className="small" role="status">{last}</div>}
      <Async q={q} skeleton={<Skeleton h={60} />}>
        {(d) => d.alerts.length === 0 ? <Empty>No alerts yet. Evaluate now to check your tracked contracts and equity watchlist.</Empty> : (
          <ul className="stack small" style={{ listStyle: 'none', margin: 0, padding: 0 }} data-testid="wb-alerts">
            {d.alerts.slice(0, 20).map((a) => (
              <li key={a.key}>
                <Badge kind={a.kindClass === 'DATA_HEALTH' ? 'warn' : 'info'}>{a.kind.replace(/_/g, ' ').toLowerCase()}</Badge> <strong>{a.message}</strong>
                <div className="muted">Data time {a.sourceAsOf ? `${fmtIst(a.sourceAsOf)} IST` : 'unknown'}{a.tf ? ` · ${a.tf}` : ''}{a.contractId ? ` · ${a.contractId}` : ''} · raised {fmtIst(a.createdAt)} IST</div>
                {a.evidence.length > 0 && <div className="muted">Evidence: {a.evidence.slice(0, 3).join(' | ')}</div>}
                {a.invalidation && <div className="muted">Invalidation: {a.invalidation}</div>}
              </li>
            ))}
          </ul>
        )}
      </Async>
    </Card>
  );
}

export function DataHealth() {
  const q = useGet<HealthView>('/api/workbench/health', { poll: 30_000 });
  return (
    <div className="page">
      <PageHead title="Data Health" sub="Where each number comes from, how fresh it must be, and what the app knows about its providers." />
      <Async q={q} skeleton={<Card><Skeleton h={240} /></Card>}>
        {(h) => (
          <>
            <Card title="Providers" right={<span className="small muted">{h.session.label}</span>}>
              <div className="tbl-wrap">
                <table data-testid="provider-table">
                  <thead><tr><th>Provider</th><th>Status</th><th>Last success</th><th>Last failure</th><th className="num">OK / failed</th><th className="num">Last latency</th></tr></thead>
                  <tbody>
                    {h.providers.map((p) => (
                      <tr key={p.id}>
                        <td>{p.label}</td>
                        <td>{!p.configured ? <Badge>not configured</Badge> : p.lastOkAt === null && p.lastErrorAt === null ? <Badge>not used yet</Badge> : p.lastErrorAt && (!p.lastOkAt || p.lastErrorAt > p.lastOkAt) ? <Badge kind="neg">failing</Badge> : <Badge kind="pos">ok</Badge>}</td>
                        <td className="small">{p.lastOkAt ? `${fmtIst(p.lastOkAt)} IST` : '-'}</td>
                        <td className="small">{p.lastErrorAt ? `${fmtIst(p.lastErrorAt)} IST: ${p.lastError}` : '-'}</td>
                        <td className="num">{p.ok} / {p.failed}</td>
                        <td className="num">{p.lastLatencyMs !== null ? `${p.lastLatencyMs} ms` : '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="small muted" style={{ marginTop: 6 }}>Counts are kept in memory and reset when the server restarts.</div>
            </Card>

            <div className="grid g2" style={{ alignItems: 'start' }}>
              <Card title="Data quality states">
                <dl className="stack small" style={{ margin: 0 }} data-testid="state-legend">
                  {(Object.keys(STATE_HELP) as DataState[]).map((s) => <div key={s}><StateBadge state={s} /> <span className="muted">{STATE_HELP[s]}</span></div>)}
                </dl>
              </Card>
              <Card title="Freshness limits (market open)">
                <dl className="kv">
                  <dt>Official or snapshot quote</dt><dd>{h.freshness.quoteSec} s</dd>
                  <dt>Option chain snapshot</dt><dd>{h.freshness.optionChainSec} s</dd>
                  <dt>Intraday candle feed</dt><dd>{h.freshness.intradayCandleSec} s</dd>
                  <dt>Delayed free feed (Yahoo)</dt><dd>{Math.round(h.freshness.delayedFeedSec / 60)} min, always labelled DELAYED</dd>
                </dl>
                <div className="small muted" style={{ marginTop: 8 }}>After these limits a value is STALE and cannot confirm a breakout or raise an alert. When the market is closed values are MARKET_CLOSED instead.</div>
                <ul className="small muted" style={{ margin: '8px 0 0', paddingLeft: 18 }}>{h.cache.map((c) => <li key={c}>{c}</li>)}</ul>
              </Card>
            </div>

            <Card title="Entitlements and licensing">
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{h.entitlements.map((e) => <li key={e}>{e}</li>)}</ul>
              <div className="small muted" style={{ marginTop: 8 }}>F&O universe: {h.universe.fnoStocks} stocks + {h.universe.indices} indices ({h.universe.source}).</div>
            </Card>
            <Card title="Known limitations">
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{h.limitations.map((e) => <li key={e}>{e}</li>)}</ul>
            </Card>
            <Alerts />
          </>
        )}
      </Async>
    </div>
  );
}
