import { useState } from 'react';
import type { PortfolioData } from '../../server/services/portfolio';
import type { HoldingView } from '../../shared/portfolio';
import { fmtNum } from '../../shared/format';
import { useGet, useSend, usePollMs, request } from '../api';
import { go } from '../nav';
import { Async, Badge, Card, Empty, errMsg, Field, Icon, Modal, Money, NumInput, PageHead, Pct, Skeleton, Stat, useConfirm, useToast } from '../ui';
import { useQueryClient } from '@tanstack/react-query';

function HoldingForm({ h, onClose }: { h?: HoldingView; onClose: () => void }) {
  const toast = useToast();
  const [symbol, setSymbol] = useState(h?.symbol ?? '');
  const [qty, setQty] = useState<number | null>(h?.qty ?? null);
  const [avg, setAvg] = useState<number | null>(h?.avgPrice ?? null);
  const [date, setDate] = useState(h?.buyDate ?? '');
  const [err, setErr] = useState<string | null>(null);
  const save = useSend<unknown>(h ? 'PUT' : 'POST', h ? `/api/holdings/${h.id}` : '/api/holdings', ['/api/portfolio']);
  return (
    <Modal title={h ? `Edit ${h.symbol}` : 'Add holding'} onClose={onClose}>
      <form className="stack" style={{ gap: 14 }} onSubmit={(e) => {
        e.preventDefault();
        save.mutate({ symbol: symbol.trim().toUpperCase(), qty, avgPrice: avg, buyDate: date, notes: '' }, { onSuccess: () => { toast('Saved'); onClose(); }, onError: (er) => setErr(errMsg(er)) });
      }}>
        <div className="form-grid">
          <Field label="NSE symbol">{(id) => <input id={id} value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} required disabled={Boolean(h)} />}</Field>
          <Field label="Quantity">{(id) => <NumInput id={id} value={qty} onChange={setQty} />}</Field>
          <Field label="Average buy price">{(id) => <NumInput id={id} value={avg} onChange={setAvg} />}</Field>
          <Field label="Buy date" hint="Needed for long-term vs short-term tax status">{(id) => <input id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
        </div>
        {err && <div className="banner neg small" role="alert">{err}</div>}
        <div className="row" style={{ justifyContent: 'flex-end' }}><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={save.isPending}>Save</button></div>
      </form>
    </Modal>
  );
}

interface ImportPreview { rows: { symbol: string; qty: number; avgPrice: number; buyDate: string }[]; errors: string[]; committed: boolean }

function ImportModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [csv, setCsv] = useState('');
  const [prev, setPrev] = useState<ImportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async (mode: 'preview' | 'append' | 'replace') => {
    setBusy(true);
    setErr(null);
    try {
      const r = await request<ImportPreview>('POST', '/api/holdings-import', { csv, mode, confirm: mode === 'replace' ? true : undefined });
      setPrev(r.data);
      if (mode !== 'preview') {
        await qc.invalidateQueries({ queryKey: ['api', '/api/portfolio'] });
        toast(`${r.data.rows.length} holdings ${mode === 'replace' ? 'replaced' : 'added'}`);
        onClose();
      }
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  };
  const onFile = (f: File | undefined) => {
    if (!f) return;
    if (f.size > 1_500_000) return setErr('File is too large for an import (1.5 MB max).');
    const r = new FileReader();
    r.onload = () => { setCsv(String(r.result ?? '')); setPrev(null); };
    r.readAsText(f);
  };
  return (
    <Modal title="Import holdings from CSV" onClose={onClose}>
      <div className="stack" style={{ gap: 14 }}>
        <p className="small muted">Works with Zerodha, Upstox and Groww holdings exports, or any CSV with symbol, quantity and average price columns (buy date optional).</p>
        <input type="file" accept=".csv,text/csv" aria-label="CSV file" onChange={(e) => onFile(e.target.files?.[0])} />
        <textarea value={csv} onChange={(e) => { setCsv(e.target.value); setPrev(null); }} placeholder="…or paste CSV here" style={{ minHeight: 110, fontFamily: 'var(--mono)', fontSize: 12 }} />
        {err && <div className="banner neg small" role="alert">{err}</div>}
        {prev && (
          <div className="stack">
            <div className="small"><strong>{prev.rows.length}</strong> valid row(s){prev.errors.length > 0 && <>, <span className="warn-t">{prev.errors.length} skipped</span></>}</div>
            {prev.errors.length > 0 && <ul className="small muted" style={{ margin: 0, paddingLeft: 18, maxHeight: 100, overflowY: 'auto' }}>{prev.errors.slice(0, 12).map((e) => <li key={e}>{e}</li>)}</ul>}
            <div className="tbl-wrap" style={{ maxHeight: 200, overflowY: 'auto' }}>
              <table><thead><tr><th>Symbol</th><th className="num">Qty</th><th className="num">Avg</th><th>Buy date</th></tr></thead>
                <tbody>{prev.rows.slice(0, 50).map((r, i) => <tr key={i}><td>{r.symbol}</td><td className="num">{r.qty}</td><td className="num">{fmtNum(r.avgPrice)}</td><td>{r.buyDate || '-'}</td></tr>)}</tbody></table>
            </div>
          </div>
        )}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" disabled={busy || !csv.trim()} onClick={() => void run('preview')}>Preview</button>
          <button className="btn" disabled={busy || !prev || prev.rows.length === 0} onClick={() => void run('append')}>Add to holdings</button>
          <button className="btn danger" disabled={busy || !prev || prev.rows.length === 0} onClick={() => { if (window.confirm('Replace ALL current holdings with this file?')) void run('replace'); }}>Replace all</button>
        </div>
      </div>
    </Modal>
  );
}

export function Portfolio() {
  const poll = usePollMs();
  const q = useGet<PortfolioData>('/api/portfolio', { poll });
  const toast = useToast();
  const confirm = useConfirm();
  const [form, setForm] = useState<HoldingView | 'new' | null>(null);
  const [imp, setImp] = useState(false);
  const del = useSend<string>('DELETE', (id) => `/api/holdings/${id}`, ['/api/portfolio']);

  return (
    <div className="page">
      <PageHead title="Portfolio" sub="Holdings valued at live prices, with tax-lot awareness." right={<><button className="btn" onClick={() => setImp(true)}>Import CSV</button><button className="btn primary" onClick={() => setForm('new')}><Icon name="plus" />Add</button></>} />
      <Async q={q} skeleton={<Card><Skeleton h={240} /></Card>}>
        {({ holdings, summary: s }) =>
          holdings.length === 0 ? (
            <Card><Empty>No holdings yet. Import your broker's holdings CSV or add them one by one. Your data stays in your own Netlify storage.</Empty></Card>
          ) : (
            <>
              <div className="grid g4">
                <Card><Stat label="Current value" value={<Money v={s.value} />} sub={`Invested ${fmtNum(s.invested, 0)}`} /></Card>
                <Card><Stat label="Total P&L" value={<Money v={s.pnl} signed />} sub={<Pct v={s.pnlPct} />} /></Card>
                <Card><Stat label="Today" value={<Money v={s.dayPnl} signed />} sub={<Pct v={s.dayPnlPct} />} /></Card>
                <Card><Stat label="Unrealised tax view" value={<><Money v={s.unrealizedStcg} signed /> ST</>} sub={<>LT <Money v={s.unrealizedLtcg} signed /></>} /></Card>
              </div>
              {s.warnings.length > 0 && <div className="stack">{s.warnings.map((w) => <div key={w} className="banner warn small">{w}</div>)}</div>}
              <div className="grid g2">
                <Card title="Sector exposure">
                  <div className="stack" style={{ gap: 8 }}>
                    {s.bySector.map((x) => (
                      <div key={x.sector}>
                        <div className="row between small"><span>{x.sector}</span><span>{x.pct.toFixed(1)}%</span></div>
                        <div className="bar"><i style={{ width: `${Math.min(100, x.pct)}%` }} /></div>
                      </div>
                    ))}
                  </div>
                </Card>
                <Card title="Tax lots">
                  <dl className="kv">
                    <dt>LTCG headroom this FY (₹1.25 L exempt)</dt><dd><Money v={s.ltcgHeadroom} /></dd>
                  </dl>
                  {s.turningLongTerm.length > 0 ? (
                    <div style={{ marginTop: 10 }}>
                      <div className="small muted">Turning long-term soon (held under 12 months)</div>
                      <div className="row" style={{ marginTop: 6 }}>{s.turningLongTerm.map((t) => <Badge key={t.symbol} kind="info">{t.symbol} · {t.days}d</Badge>)}</div>
                    </div>
                  ) : <p className="small muted" style={{ marginTop: 10 }}>No holdings are close to the 12-month mark.</p>}
                  <p className="small faint" style={{ marginTop: 10 }}>Selling before the 12-month mark makes gains short-term (20% vs 12.5% above the exemption). Informational only.</p>
                </Card>
              </div>
              <Card title="Holdings" flush>
                <div className="tbl-wrap">
                  <table>
                    <thead><tr><th>Symbol</th><th className="num">Qty</th><th className="num">Avg</th><th className="num">LTP</th><th className="num">Value</th><th className="num">P&L</th><th className="num">Day</th><th>Held</th><th /></tr></thead>
                    <tbody>
                      {holdings.map((h) => (
                        <tr key={h.id} className="hover" onClick={() => go(`/symbol/${h.symbol}`)}>
                          <td><strong>{h.symbol}</strong><div className="small muted">{h.sector}</div></td>
                          <td className="num">{fmtNum(h.qty, 0)}</td>
                          <td className="num">{fmtNum(h.avgPrice)}</td>
                          <td className="num">{h.price !== null ? fmtNum(h.price) : <span className="warn-t">no price</span>}</td>
                          <td className="num"><Money v={h.value ?? h.invested} /></td>
                          <td className="num"><Money v={h.pnl} signed /><div className="small"><Pct v={h.pnlPct} /></div></td>
                          <td className="num"><Money v={h.dayPnl} signed /></td>
                          <td>{h.longTerm === null ? <span className="muted small">no date</span> : h.longTerm ? <Badge kind="pos">Long-term</Badge> : <Badge>{h.daysToLongTerm}d to LT</Badge>}</td>
                          <td className="right" onClick={(e) => e.stopPropagation()}>
                            <button className="icon-btn" aria-label={`Edit ${h.symbol}`} onClick={() => setForm(h)}><Icon name="edit" /></button>
                            <button className="icon-btn" aria-label={`Delete ${h.symbol}`} onClick={() => { if (confirm(`Remove ${h.symbol} from holdings?`)) del.mutate(h.id, { onError: (e) => toast(errMsg(e), true) }); }}><Icon name="trash" /></button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </>
          )
        }
      </Async>
      {form && <HoldingForm h={form === 'new' ? undefined : form} onClose={() => setForm(null)} />}
      {imp && <ImportModal onClose={() => setImp(false)} />}
    </div>
  );
}
