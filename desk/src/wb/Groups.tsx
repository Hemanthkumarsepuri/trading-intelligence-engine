// Named contract groups: create, rename, reorder, notes, saved chart preferences, expired-contract handling.

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { GroupView } from '../../server/services/wb/groupview';
import type { TrackedRow } from '../../server/services/wb/contracts';
import type { ChartPrefs } from '../../shared/groups';
import { TIMEFRAMES } from '../../shared/candles';
import { request, useGet } from '../api';
import { go } from '../nav';
import { Badge, Card, Empty, errMsg, Icon, Skeleton, useConfirm, useToast } from '../ui';
import { AddContractDialog } from './AddContract';
import { StateBadge, Sv } from './common';

export function useWb() {
  const qc = useQueryClient();
  const toast = useToast();
  return async (method: 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown, ok?: string): Promise<boolean> => {
    try {
      await request(method, path, body);
      await qc.invalidateQueries({ predicate: (x) => typeof x.queryKey[1] === 'string' && (x.queryKey[1] as string).startsWith('/api/workbench/') });
      if (ok) toast(ok);
      return true;
    } catch (e) {
      toast(errMsg(e), true);
      return false;
    }
  };
}

export const DEFAULT_SAVED: Required<Omit<ChartPrefs, 'tf'>> = {
  overlays: { vwap: true, ema20: true, ema50: true, bb: false },
  panes: { rsi: false, macd: false, atr: false },
  levelGroups: { swing: true, session: true, opening: true, rolling: false },
  showSetup: true,
  showEvents: true,
  view: 'both',
};

export function PrefsEditor({ value, onSave, onClear, title }: { value: ChartPrefs | undefined; onSave: (p: ChartPrefs) => void; onClear: () => void; title: string }) {
  const [p, setP] = useState<ChartPrefs>(value ?? {});
  const o = { ...DEFAULT_SAVED.overlays, ...p.overlays };
  const n = { ...DEFAULT_SAVED.panes, ...p.panes };
  const box = (label: string, checked: boolean, on: (v: boolean) => void) => (
    <label className="row small" style={{ gap: 6 }}><input type="checkbox" style={{ width: 'auto' }} checked={checked} onChange={(e) => on(e.target.checked)} />{label}</label>
  );
  return (
    <fieldset style={{ border: 0, padding: 0, margin: 0 }} aria-label={title}>
      <legend className="small muted">{title}</legend>
      <div className="row" style={{ gap: 14 }}>
        <label className="small muted" htmlFor={`pf-${title}`}>Default timeframe</label>
        <select id={`pf-${title}`} style={{ width: 'auto' }} value={p.tf ?? ''} onChange={(e) => setP({ ...p, tf: (e.target.value || undefined) as ChartPrefs['tf'] })}>
          <option value="">Keep current</option>
          {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        {box('VWAP', o.vwap, (v) => setP({ ...p, overlays: { ...o, vwap: v } }))}
        {box('EMA 20', o.ema20, (v) => setP({ ...p, overlays: { ...o, ema20: v } }))}
        {box('EMA 50', o.ema50, (v) => setP({ ...p, overlays: { ...o, ema50: v } }))}
        {box('Bollinger', o.bb, (v) => setP({ ...p, overlays: { ...o, bb: v } }))}
        {box('RSI', n.rsi, (v) => setP({ ...p, panes: { ...n, rsi: v } }))}
        {box('MACD', n.macd, (v) => setP({ ...p, panes: { ...n, macd: v } }))}
        {box('ATR', n.atr, (v) => setP({ ...p, panes: { ...n, atr: v } }))}
      </div>
      <div className="row" style={{ marginTop: 6 }}>
        <button type="button" className="btn sm" onClick={() => onSave({ ...DEFAULT_SAVED, ...p, overlays: o, panes: n })}>Save chart settings</button>
        {value && <button type="button" className="btn sm ghost" onClick={onClear}>Clear saved settings</button>}
      </div>
    </fieldset>
  );
}

function ItemRow({ g, idx, last, entry, picked, onPick }: { g: GroupView; idx: number; last: boolean; entry: GroupView['items'][number]; picked: boolean; onPick: () => void }) {
  const call = useWb();
  const confirm = useConfirm();
  const [note, setNote] = useState(entry.item.note);
  const r: TrackedRow = entry.row;
  const v = r.validation;
  const expired = r.problem === 'This contract has expired.';
  const move = (d: -1 | 1) => {
    const order = g.items.map((x) => x.item.id);
    const j = idx + d;
    [order[idx], order[j]] = [order[j]!, order[idx]!];
    void call('PUT', `/api/workbench/groups/${g.id}/items`, { order });
  };
  const open = `#/workbench?u=${r.item.ref.underlying}&c=${encodeURIComponent(r.item.id)}&g=${g.id}`;
  return (
    <tr>
      <td><input type="checkbox" style={{ width: 'auto' }} aria-label={`Compare ${r.label}`} checked={picked} disabled={expired} onChange={onPick} /></td>
      <td>
        <a href={open}><strong>{r.label}</strong></a> {expired && <Badge kind="warn">expired</Badge>}
        {r.problem && !expired && <div className="small warn-t">{r.problem}</div>}
        {entry.item.identity?.canonicalSymbol && <div className="small muted">{entry.item.identity.canonicalSymbol}</div>}
        <input aria-label={`Note for ${r.label}`} placeholder="Note" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} onBlur={() => note !== entry.item.note && void call('PUT', `/api/workbench/groups/${g.id}/item?contract=${encodeURIComponent(r.item.id)}`, { note })} style={{ marginTop: 4, maxWidth: 260 }} />
      </td>
      <td className="num">{v ? <Sv f={v.ltp} hideBadge /> : '-'}</td>
      <td className="num">{v ? <Sv f={v.spreadPct} suffix="%" hideBadge /> : '-'}</td>
      <td className="num">{v ? <Sv f={v.volume} digits={0} hideBadge /> : '-'}</td>
      <td className="num">{v ? <Sv f={v.oi} digits={0} hideBadge /> : '-'}</td>
      <td>{v ? <StateBadge state={v.state} /> : <Badge>{expired ? 'EXPIRED' : 'no data'}</Badge>}</td>
      <td className="right" style={{ whiteSpace: 'nowrap' }}>
        <button className="icon-btn" aria-label={`Move ${r.label} up`} disabled={idx === 0} onClick={() => move(-1)}>↑</button>
        <button className="icon-btn" aria-label={`Move ${r.label} down`} disabled={last} onClick={() => move(1)}>↓</button>
        <button className="icon-btn" aria-label={`Remove ${r.label} from ${g.name}`} onClick={() => { if (confirm(`Remove ${r.label} from "${g.name}"?`)) void call('DELETE', `/api/workbench/groups/${g.id}/item?contract=${encodeURIComponent(r.item.id)}`, undefined, 'Removed'); }}><Icon name="trash" /></button>
      </td>
    </tr>
  );
}

function GroupCard({ g, index, order, onAdd }: { g: GroupView; index: number; order: string[]; onAdd: (id: string) => void }) {
  const total = order.length;
  const call = useWb();
  const confirm = useConfirm();
  const [rename, setRename] = useState<string | null>(null);
  const [note, setNote] = useState(g.note);
  const [picked, setPicked] = useState<string[]>([]);
  const moveGroup = (d: -1 | 1) => {
    const next = [...order];
    [next[index], next[index + d]] = [next[index + d]!, next[index]!];
    void call('PUT', '/api/workbench/groups', { order: next });
  };
  const expired = g.items.filter((x) => x.row.problem === 'This contract has expired.');
  const comparable = picked.slice(0, 4);

  return (
    <section className="card" data-group-id={g.id} data-testid="contract-group" aria-label={`Group ${g.name}`}>
      <header className="card-h">
        {rename === null ? <h2>{g.name}</h2> : (
          <form className="row" onSubmit={(e) => { e.preventDefault(); void call('PUT', `/api/workbench/groups/${g.id}`, { name: rename }).then((ok) => ok && setRename(null)); }}>
            <input aria-label="Group name" value={rename} onChange={(e) => setRename(e.target.value)} maxLength={60} autoFocus style={{ width: 260 }} />
            <button className="btn sm primary">Save</button>
            <button type="button" className="btn sm ghost" onClick={() => setRename(null)}>Cancel</button>
          </form>
        )}
        <div className="row">
          <button className="icon-btn" aria-label={`Move group ${g.name} up`} disabled={index === 0} onClick={() => moveGroup(-1)}>↑</button>
          <button className="icon-btn" aria-label={`Move group ${g.name} down`} disabled={index === total - 1} onClick={() => moveGroup(1)}>↓</button>
          <button className="btn sm" onClick={() => setRename(g.name)}>Rename</button>
          <button className="btn sm" onClick={() => onAdd(g.id)}><Icon name="plus" />Add contract</button>
          <button className="btn sm danger" onClick={() => { if (confirm(`Delete the group "${g.name}" and its ${g.items.length} contract(s)? Contracts stay available to add again.`)) void call('DELETE', `/api/workbench/groups/${g.id}`, undefined, 'Group deleted'); }}>Delete</button>
        </div>
      </header>
      <div className="card-b stack">
        {g.items.length === 0 ? <Empty>This group is empty. Use Add contract: search the underlying, pick expiry, CE/PE and strike.</Empty> : (
          <div className="tbl-wrap">
            <table data-testid="group-items">
              <thead><tr><th><span className="sr-only">Compare</span></th><th>Contract</th><th className="num">LTP</th><th className="num">Spread</th><th className="num">Volume</th><th className="num">OI</th><th>Quality</th><th /></tr></thead>
              <tbody>
                {g.items.map((e, i) => <ItemRow key={e.item.id} g={g} idx={i} last={i === g.items.length - 1} entry={e} picked={picked.includes(e.item.id)} onPick={() => setPicked((p) => (p.includes(e.item.id) ? p.filter((x) => x !== e.item.id) : p.length >= 4 ? p : [...p, e.item.id]))} />)}
              </tbody>
            </table>
          </div>
        )}
        <div className="row">
          <button className="btn sm" disabled={comparable.length < 2} onClick={() => go(`/compare?ids=${encodeURIComponent(comparable.join(','))}`)}>Compare selected ({comparable.length})</button>
          {expired.length > 0 && <button className="btn sm danger" onClick={() => { if (confirm(`Remove ${expired.length} expired contract(s) from "${g.name}"?`)) void Promise.all(expired.map((x) => call('DELETE', `/api/workbench/groups/${g.id}/item?contract=${encodeURIComponent(x.item.id)}`))); }}>Remove {expired.length} expired</button>}
          <span className="small muted">Tick up to four to compare. {expired.length > 0 ? 'Expired contracts are kept visible until you remove them.' : ''}</span>
        </div>
        <details className="notes">
          <summary>Notes and saved chart settings</summary>
          <div className="stack" style={{ marginTop: 8 }}>
            <label className="small muted" htmlFor={`gn-${g.id}`}>Group note</label>
            <textarea id={`gn-${g.id}`} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} onBlur={() => note !== g.note && void call('PUT', `/api/workbench/groups/${g.id}`, { note })} />
            <PrefsEditor key={JSON.stringify(g.prefs ?? null)} title={`Chart settings for ${g.name}`} value={g.prefs} onSave={(p) => void call('PUT', `/api/workbench/groups/${g.id}`, { prefs: p }, 'Chart settings saved')} onClear={() => void call('PUT', `/api/workbench/groups/${g.id}`, { prefs: null }, 'Cleared')} />
            <div className="small muted">Saved settings apply when you open a contract from this group. A contract can override them from the Workbench.</div>
          </div>
        </details>
      </div>
    </section>
  );
}

export function ContractGroups() {
  const q = useGet<{ groups: GroupView[]; ungrouped: TrackedRow[] }>('/api/workbench/groups', { poll: 60_000 });
  const call = useWb();
  const [name, setName] = useState('');
  const [adding, setAdding] = useState<{ groupId: string | null } | null>(null);

  return (
    <>
      <Card
        title="Contract groups"
        right={<button className="btn primary sm" onClick={() => setAdding({ groupId: null })}><Icon name="plus" />Add contract</button>}
      >
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (name.trim()) void call('POST', '/api/workbench/groups', { name, note: '' }, 'Group created').then((ok) => ok && setName('')); }}>
          <label className="small muted" htmlFor="ng">New group</label>
          <input id="ng" placeholder="Angel One — October Calls" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} style={{ width: 280 }} />
          <button className="btn sm" disabled={!name.trim()}>Create</button>
        </form>
        {q.isPending && <Skeleton h={60} />}
        {q.isError && <div className="banner neg small" role="alert">{errMsg(q.error)}</div>}
        {q.data && q.data.data.groups.length === 0 && <div className="small muted" style={{ marginTop: 8 }}>No groups yet. Create one, then add exact contracts such as ANGELONE 300 CE and 305 CE for the same expiry.</div>}
      </Card>
      {q.data?.data.groups.map((g, i, a) => <GroupCard key={g.id} g={g} index={i} order={a.map((x) => x.id)} onAdd={(id) => setAdding({ groupId: id })} />)}
      {adding && <AddContractDialog groupId={adding.groupId} onClose={() => setAdding(null)} />}
    </>
  );
}
