import { useEffect, useState } from 'react';
import type { WatchRow } from '../../server/services/watchlist';
import { OBSERVATION_LABEL } from '../../shared/indicators';
import { fmtNum } from '../../shared/format';
import { useGet, useSend, usePollMs } from '../api';
import { go } from '../nav';
import { TrackedContracts } from '../wb/Tracked';
import { Async, Badge, Card, Empty, errMsg, Field, Icon, Modal, NumInput, PageHead, Pct, Segmented, Skeleton, useConfirm, useToast } from '../ui';

type Sort = 'symbol' | 'changePct' | 'volume';

function useDebounced<T>(v: T, ms = 250): T {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

function AddForm() {
  const toast = useToast();
  const [sym, setSym] = useState('');
  const dq = useDebounced(sym.trim());
  const sugg = useGet<{ results: { symbol: string; name: string }[] }>(dq.length >= 1 ? `/api/symbols/search?q=${encodeURIComponent(dq)}` : null);
  const add = useSend<{ symbol: string }>('POST', '/api/watchlist', ['/api/watchlist', '/api/symbols']);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!sym.trim()) return;
    add.mutate({ symbol: sym.trim() }, {
      onSuccess: (r) => {
        // The server may accept a symbol it could not verify (NSE list unreachable): say so, never add it silently.
        const notes = r.meta.notes.filter(Boolean);
        if (notes.length > 0) toast(`${sym.toUpperCase()} added. ${notes.join(' ')}`, true);
        else toast(`${sym.toUpperCase()} added`);
        setSym('');
      },
      onError: (er) => toast(errMsg(er), true),
    });
  };
  return (
    <form className="row" onSubmit={submit} style={{ flexWrap: 'nowrap' }}>
      <input list="sym-list" placeholder="Add NSE symbol, e.g. TATAMOTORS" value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} style={{ maxWidth: 280 }} aria-label="NSE symbol" />
      <datalist id="sym-list">{sugg.data?.data.results.map((r) => <option key={r.symbol} value={r.symbol}>{r.name}</option>)}</datalist>
      <button className="btn primary" disabled={add.isPending || !sym.trim()}><Icon name="plus" />Add</button>
    </form>
  );
}

function EditModal({ row, onClose }: { row: WatchRow; onClose: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState(row.item.note);
  const [support, setSupport] = useState<number | null>(row.item.support);
  const [resistance, setResistance] = useState<number | null>(row.item.resistance);
  const save = useSend<{ note: string; support: number | null; resistance: number | null }>('PUT', `/api/watchlist/${row.item.symbol}`, ['/api/watchlist']);
  return (
    <Modal title={`${row.item.symbol}: notes & levels`} onClose={onClose}>
      <div className="stack" style={{ gap: 14 }}>
        <div className="form-grid">
          <Field label="Your support level">{(id) => <NumInput id={id} value={support} onChange={setSupport} />}</Field>
          <Field label="Your resistance level">{(id) => <NumInput id={id} value={resistance} onChange={setResistance} />}</Field>
        </div>
        <Field label="Note">{(id) => <textarea id={id} value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />}</Field>
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate({ note, support, resistance }, { onSuccess: () => { toast('Saved'); onClose(); }, onError: (e) => toast(errMsg(e), true) })}>Save</button>
        </div>
      </div>
    </Modal>
  );
}

export function Watchlist() {
  const poll = usePollMs();
  const q = useGet<{ rows: WatchRow[] }>('/api/watchlist', { poll });
  const settings = useGet<{ watchlistSort: Sort }>('/api/settings');
  const [sort, setSort] = useState<Sort | null>(null);
  const [edit, setEdit] = useState<WatchRow | null>(null);
  const toast = useToast();
  const confirm = useConfirm();
  const del = useSend<string>('DELETE', (s) => `/api/watchlist/${s}`, ['/api/watchlist']);
  const effSort = sort ?? settings.data?.data.watchlistSort ?? 'symbol';

  return (
    <div className="page">
      <PageHead
        title="Watchlist"
        sub="Quotes with daily technicals. Click a row for the chart."
        right={<Segmented<Sort> label="Sort" value={effSort} onChange={setSort} options={[{ value: 'symbol', label: 'A–Z' }, { value: 'changePct', label: 'Day %' }, { value: 'volume', label: 'Volume' }]} />}
      />
      <AddForm />
      <Async q={q} skeleton={<Card><Skeleton h={240} /></Card>}>
        {(d) => {
          const rows = [...d.rows].sort((a, b) =>
            effSort === 'symbol' ? a.item.symbol.localeCompare(b.item.symbol) : effSort === 'changePct' ? (b.quote?.changePct ?? -99) - (a.quote?.changePct ?? -99) : (b.tech?.volRatio20 ?? -1) - (a.tech?.volRatio20 ?? -1),
          );
          return (
            <Card flush>
              {rows.length === 0 ? (
                <Empty>Your watchlist is empty. Add a symbol above.</Empty>
              ) : (
                <div className="tbl-wrap">
                  <table>
                    <thead>
                      <tr><th>Symbol</th><th className="num">LTP</th><th className="num">Day %</th><th className="num">Vol vs 20d</th><th className="num">RSI</th><th>Trend</th><th className="hide-m">52w position</th><th className="hide-m">Observations</th><th /></tr>
                    </thead>
                    <tbody>
                      {rows.map((r) => (
                        <tr key={r.item.symbol} className="hover" onClick={() => go(`/symbol/${r.item.symbol}`)}>
                          <td>
                            <strong>{r.item.symbol}</strong> {r.inFnoBan && <Badge kind="neg">F&O ban</Badge>}
                            {r.item.note && <div className="small muted" style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.item.note}</div>}
                            {r.gaps.length > 0 && <div className="small warn-t">{r.gaps.join(', ')}</div>}
                          </td>
                          <td className="num">{r.quote ? fmtNum(r.quote.price) : '-'}</td>
                          <td className="num">{r.quote ? <Pct v={r.quote.changePct} /> : '-'}</td>
                          <td className="num">{r.tech?.volRatio20 != null ? `${r.tech.volRatio20.toFixed(1)}x` : '-'}</td>
                          <td className="num">{r.tech?.rsi14 != null ? r.tech.rsi14.toFixed(0) : '-'}</td>
                          <td>{r.tech ? <Badge kind={r.tech.trend === 'UP' ? 'pos' : r.tech.trend === 'DOWN' ? 'neg' : undefined}>{r.tech.trend === 'UNKNOWN' ? 'n/a' : r.tech.trend.toLowerCase()}</Badge> : '-'}</td>
                          <td className="hide-m" style={{ minWidth: 110 }}>
                            {r.tech?.pos52w != null ? <div title={`${r.tech.pos52w.toFixed(0)}% of 52-week range`}><div className="bar"><i style={{ width: `${r.tech.pos52w}%` }} /></div><div className="small faint">{fmtNum(r.tech.low52w, 0)} – {fmtNum(r.tech.high52w, 0)}</div></div> : '-'}
                          </td>
                          <td className="hide-m wrap" style={{ maxWidth: 260 }}>{r.tech?.observations.slice(0, 2).map((o) => <span key={o} className="chip">{OBSERVATION_LABEL[o]}</span>)}</td>
                          <td className="right" onClick={(e) => e.stopPropagation()}>
                            <button className="icon-btn" aria-label={`Edit ${r.item.symbol}`} onClick={() => setEdit(r)}><Icon name="edit" /></button>
                            <button className="icon-btn" aria-label={`Remove ${r.item.symbol}`} onClick={() => { if (confirm(`Remove ${r.item.symbol} from your watchlist?`)) del.mutate(r.item.symbol, { onError: (e) => toast(errMsg(e), true) }); }}><Icon name="trash" /></button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          );
        }}
      </Async>
      <TrackedContracts />
      {edit && <EditModal row={edit} onClose={() => setEdit(null)} />}
    </div>
  );
}
