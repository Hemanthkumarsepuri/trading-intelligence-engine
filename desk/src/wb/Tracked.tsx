// Tracked option contracts: live validation, quick removal and side-by-side comparison.

import { useState } from 'react';
import type { TrackedRow } from '../../server/services/wb/contracts';
import { useGet, useSend, usePollMs } from '../api';
import { Async, Badge, Card, Empty, errMsg, Icon, Skeleton, useConfirm, useToast } from '../ui';
import { CompareTable } from './CompareTable';
import { StateBadge, Sv, openWorkbench } from './common';

const MAX_COMPARE = 4;

export function TrackedContracts() {
  const poll = usePollMs();
  const q = useGet<{ rows: TrackedRow[] }>('/api/workbench/contracts', { poll });
  const toast = useToast();
  const confirm = useConfirm();
  const [picked, setPicked] = useState<string[]>([]);
  const del = useSend<string>('DELETE', (id) => `/api/workbench/contracts?id=${encodeURIComponent(id)}`, ['/api/workbench/contracts']);

  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= MAX_COMPARE ? p : [...p, id]));

  return (
    <Async q={q} skeleton={<Card><Skeleton h={90} /></Card>}>
      {(d) => {
        const cmp = d.rows.filter((r) => picked.includes(r.item.id) && r.validation);
        return (
          <>
            <Card title="Tracked option contracts" right={<a className="btn sm" href="#/workbench">Add from the Workbench</a>}>
              {d.rows.length === 0 ? (
                <Empty>No option contracts tracked yet. Search a stock in the Options Workbench, pick an expiry, call or put and a strike, then Add to watchlist.</Empty>
              ) : (
                <div className="tbl-wrap">
                  <table data-testid="tracked-contracts">
                    <thead><tr><th><span className="sr-only">Compare</span></th><th>Contract</th><th className="num">LTP</th><th className="num">Spread</th><th className="num">Volume</th><th className="num">OI</th><th className="num">1-lot premium</th><th>Quality</th><th /></tr></thead>
                    <tbody>
                      {d.rows.map((r) => {
                        const v = r.validation;
                        return (
                          <tr key={r.item.id}>
                            <td><input type="checkbox" style={{ width: 'auto' }} aria-label={`Compare ${r.label}`} checked={picked.includes(r.item.id)} disabled={!v || (!picked.includes(r.item.id) && picked.length >= MAX_COMPARE)} onChange={() => toggle(r.item.id)} /></td>
                            <td>
                              <a href={`#/workbench?u=${r.item.ref.underlying}&c=${encodeURIComponent(r.item.id)}`}><strong>{r.label}</strong></a>
                              {r.problem && <div className="small warn-t">{r.problem}</div>}
                              {r.item.note && <div className="small muted">{r.item.note}</div>}
                            </td>
                            <td className="num">{v ? <Sv f={v.ltp} hideBadge /> : '-'}</td>
                            <td className="num">{v ? <Sv f={v.spreadPct} suffix="%" hideBadge /> : '-'}</td>
                            <td className="num">{v ? <Sv f={v.volume} digits={0} hideBadge /> : '-'}</td>
                            <td className="num">{v ? <Sv f={v.oi} digits={0} hideBadge /> : '-'}</td>
                            <td className="num">{v ? <Sv f={v.oneLotPremium} digits={0} prefix="₹" hideBadge /> : '-'}</td>
                            <td>{v ? <><StateBadge state={v.state} /> <span className="small muted">{v.liquidity.cls.replace('_', ' ').toLowerCase()}</span></> : <Badge>no data</Badge>}</td>
                            <td className="right">
                              <button className="icon-btn" aria-label={`Open ${r.label}`} onClick={() => openWorkbench(r.item.ref.underlying, r.item.id)}><Icon name="options" /></button>
                              <button className="icon-btn" aria-label={`Remove ${r.label}`} onClick={() => { if (confirm(`Stop tracking ${r.label}?`)) del.mutate(r.item.id, { onSuccess: () => { setPicked((p) => p.filter((x) => x !== r.item.id)); toast('Removed'); }, onError: (e) => toast(errMsg(e), true) }); }}><Icon name="trash" /></button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {d.rows.length > 1 && <div className="small muted" style={{ marginTop: 6 }}>Tick up to {MAX_COMPARE} contracts to compare them side by side.</div>}
            </Card>
            {cmp.length >= 2 && (
              <Card title="Comparison" right={<a className="btn sm" href={`#/compare?ids=${encodeURIComponent(cmp.map((r) => r.item.id).join(','))}`}>Open full comparison</a>}>
                <CompareTable rows={cmp} />
              </Card>
            )}
          </>
        );
      }}
    </Async>
  );
}
