import { useMemo, useState } from 'react';
import type { EventsView } from '../../server/services/wb/events';
import type { ContractWatch } from '../../shared/contract';
import { fmtIst } from '../../shared/format';
import { DEFAULT_WATCHLIST } from '../../shared/universe';
import { useGet } from '../api';
import { setQuery, useLoc } from '../nav';
import { Async, Badge, Card, Empty, PageHead, Skeleton } from '../ui';
import { Disclosure } from '../wb/common';

const KIND_BADGE: Record<string, 'pos' | 'warn' | 'info' | undefined> = { CONFIRMED_EXCHANGE: 'pos', REPORTED: 'info', UNVERIFIED: 'warn' };
const SRC_BADGE: Record<string, 'pos' | 'neg' | 'warn' | 'info' | undefined> = { OK: 'pos', PARTIAL: 'warn', UNAVAILABLE: 'neg', UNSUPPORTED: undefined };

export function Events() {
  const loc = useLoc();
  const fromUrl = (loc.query.get('symbols') ?? '').split(',').map((s) => s.trim().toUpperCase()).filter(Boolean);
  const wl = useGet<{ symbol: string }[] | { rows: { item: { symbol: string } }[] }>(fromUrl.length ? null : '/api/watchlist');
  const tracked = useGet<{ rows: { item: ContractWatch }[] }>(fromUrl.length ? null : '/api/workbench/contracts');
  const [text, setText] = useState('');

  const symbols = useMemo(() => {
    if (fromUrl.length) return fromUrl.slice(0, 8);
    const w = (wl.data?.data as { rows?: { item: { symbol: string } }[] } | undefined)?.rows?.map((r) => r.item.symbol) ?? [];
    const t = tracked.data?.data.rows.map((r) => r.item.ref.underlying) ?? [];
    const merged = [...new Set([...t, ...w])];
    return (merged.length ? merged : DEFAULT_WATCHLIST).slice(0, 6);
  }, [fromUrl.join(','), wl.data, tracked.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const ready = fromUrl.length > 0 || (!wl.isPending && !tracked.isPending);
  const q = useGet<EventsView>(ready ? `/api/workbench/events?symbols=${encodeURIComponent(symbols.join(','))}` : null);

  return (
    <div className="page">
      <PageHead title="News and Events" sub="Exchange filings, scheduled results and public headlines, each with its publisher and original time. A headline is possible context for a move, never proof of its cause." />
      <Card title="Symbols">
        <form className="row" onSubmit={(e) => { e.preventDefault(); const list = text.split(/[ ,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean).slice(0, 8); if (list.length) { setQuery('symbols', list.join(',')); setText(''); } }}>
          <label className="small muted" htmlFor="ev-s">Symbols (up to 8)</label>
          <input id="ev-s" placeholder="RELIANCE, TCS" value={text} onChange={(e) => setText(e.target.value)} style={{ width: 240 }} />
          <button className="btn">Load</button>
          <span className="small muted">Showing: {symbols.join(', ')}</span>
          {fromUrl.length > 0 && <button type="button" className="btn sm ghost" onClick={() => setQuery('symbols', null)}>Use my watchlist</button>}
        </form>
      </Card>
      {!ready || q.isPending ? <Card><Skeleton h={200} /></Card> : null}
      <Async q={q} skeleton={<span />}>
        {(d) => (
          <>
            <Card title="Sources">
              <ul className="stack small" style={{ listStyle: 'none', margin: 0, padding: 0 }} data-testid="event-sources">
                {d.sources.map((s) => (
                  <li key={s.id}><Badge kind={SRC_BADGE[s.state]}>{s.state}</Badge> <strong>{s.label}</strong> <span className="muted">{s.detail}{s.retrievedAt ? ` Retrieved ${fmtIst(s.retrievedAt)} IST.` : ''}</span></li>
                ))}
              </ul>
            </Card>
            {d.upcoming.length > 0 && (
              <Card title="Scheduled soon" right={<Badge kind="warn">{d.upcoming.length}</Badge>}>
                <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                  {d.upcoming.map((u) => <li key={u.event.id}>{u.event.headline} <span className="muted">· in {u.daysAway} day(s), {u.event.scheduledFor}. {u.event.limitation}</span></li>)}
                </ul>
              </Card>
            )}
            <Card title={`Timeline (${d.events.length})`} flush>
              {d.events.length === 0 ? <div className="card-b"><Empty>Nothing was found for these symbols. That can mean quiet news, or that the sources above are unavailable. Nothing is filled in.</Empty></div> : (
                <div className="tbl-wrap">
                  <table data-testid="events-table">
                    <thead><tr><th>Published (IST)</th><th>Item</th><th>Source</th><th>Status</th><th>Symbols</th></tr></thead>
                    <tbody>
                      {d.events.map((e) => (
                        <tr key={e.id}>
                          <td className="small">{e.publishedAt ? fmtIst(e.publishedAt) : e.scheduledFor ? `scheduled ${e.scheduledFor}` : <span className="muted">no time given</span>}</td>
                          <td>
                            {e.link ? <a href={e.link} target="_blank" rel="noreferrer noopener">{e.headline}</a> : e.headline}
                            <Disclosure summary={<span className="small muted">details</span>}>
                              <div className="small">
                                <div>Type {e.type.replace('_', ' ').toLowerCase()} · retrieved {fmtIst(e.retrievedAt)} IST · source zone {e.sourceZone}{e.timestampReliable ? '' : ' · time not reliable enough to place on a chart'}</div>
                                {e.limitation && <div className="muted">{e.limitation}</div>}
                              </div>
                            </Disclosure>
                          </td>
                          <td className="small">{e.publisher}</td>
                          <td><Badge kind={KIND_BADGE[e.verification]}>{e.verification.replace('_', ' ').toLowerCase()}</Badge></td>
                          <td className="small">{e.symbols.join(', ') || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            {d.notes.length > 0 && <div className="small muted">{d.notes.join(' ')}</div>}
          </>
        )}
      </Async>
    </div>
  );
}
