// Add Contract: quick entry ("ANGELONE 300 CE OCT") or step by step. The server resolves the request against the
// provider's listed expiries and chain; this dialog only shows what was understood and asks for any missing choice.

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Resolution } from '../../server/services/wb/resolve';
import type { GroupView } from '../../server/services/wb/groupview';
import { fmtIst } from '../../shared/format';
import { request, useGet } from '../api';
import { Badge, errMsg, Modal, Spinner, useToast } from '../ui';
import { useDebounced } from './common';

export interface Sel {
  u?: string;
  expiry?: string;
  type?: 'CE' | 'PE';
  strike?: number;
}

const params = (s: Sel): string => {
  const p = new URLSearchParams();
  if (s.u) p.set('u', s.u);
  if (s.expiry) p.set('expiry', s.expiry);
  if (s.type) p.set('type', s.type);
  if (s.strike !== undefined) p.set('strike', String(s.strike));
  return p.toString();
};

const FIELD_LABEL = { underlying: 'underlying', expiry: 'expiry', type: 'call or put', strike: 'strike' } as const;

function Chip({ label, value, needed }: { label: string; value: string | null; needed: boolean }) {
  return (
    <span className="badge" style={needed ? { borderColor: 'var(--warn)' } : undefined} data-testid={`parsed-${label.toLowerCase()}`}>
      <span className="muted">{label}</span> {value ?? (needed ? 'choose below' : '—')}
    </span>
  );
}

export function AddContractDialog({ onClose, initial, groupId, onAdded }: { onClose: () => void; initial?: Sel; groupId?: string | null; onAdded?: (id: string, underlying: string) => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [text, setText] = useState('');
  const [sel, setSel] = useState<Sel | null>(initial ?? null);
  const [target, setTarget] = useState<string>(groupId ?? 'none');
  const [newName, setNewName] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const dtext = useDebounced(text.trim(), 350);
  const groups = useGet<{ groups: GroupView[] }>('/api/workbench/groups');

  const path = sel ? `/api/workbench/resolve?${params(sel)}` : dtext ? `/api/workbench/resolve?q=${encodeURIComponent(dtext)}` : null;
  const q = useGet<Resolution>(path, { cancel: true });
  const r = q.data?.data;
  const typing = text.trim() !== dtext;

  const choose = (field: NonNullable<Resolution['field']>, value: string) => {
    const p = r?.parsed;
    const base: Sel = { u: p?.underlying ?? undefined, expiry: p?.expiry ?? undefined, type: p?.type ?? undefined, strike: p?.strike ?? undefined };
    if (field === 'underlying') setSel({ ...base, u: value });
    else if (field === 'expiry') setSel({ ...base, expiry: value });
    else if (field === 'type') setSel({ ...base, type: value as 'CE' | 'PE' });
    else setSel({ ...base, strike: Number(value) });
  };
  const restart = () => { setSel(null); setText(''); };

  const add = async () => {
    if (!r?.contract) return;
    setBusy(true);
    try {
      let gid: string | null = target === 'none' ? null : target;
      if (target === '__new') {
        const g = await request<{ id: string }>('POST', '/api/workbench/groups', { name: newName, note: '' });
        gid = g.data.id;
      }
      const res = gid
        ? await request<{ duplicate: boolean }>('POST', `/api/workbench/groups/${gid}/items`, { id: r.contract.id, note })
        : await request<{ duplicate: boolean }>('POST', '/api/workbench/contracts', { id: r.contract.id, note });
      toast(res.data.duplicate ? 'Already in that list. Nothing changed.' : `${r.contract.label} added`);
      await qc.invalidateQueries({ predicate: (x) => typeof x.queryKey[1] === 'string' && (x.queryKey[1] as string).startsWith('/api/workbench/') });
      if (!res.data.duplicate) {
        onAdded?.(r.contract.id, r.contract.ref.underlying);
        onClose();
      }
    } catch (e) {
      toast(errMsg(e), true);
    } finally {
      setBusy(false);
    }
  };

  const groupsList = groups.data?.data.groups ?? [];
  const needName = target === '__new' && newName.trim().length === 0;

  return (
    <Modal title="Add exact contract" onClose={onClose}>
      <div className="stack" data-testid="add-contract-dialog">
        <div className="stack" style={{ gap: 4 }}>
          <label htmlFor="ac-q" className="small muted">Quick entry, for example ANGELONE 300 CE OCT, ANGELONE 300 PE, NIFTY 25000 CE</label>
          <div className="row" style={{ flexWrap: 'nowrap' }}>
            <input id="ac-q" autoFocus value={text} placeholder="SYMBOL strike CE|PE expiry" onChange={(e) => { setText(e.target.value); setSel(null); }} autoComplete="off" />
            {(q.isFetching || typing) && <Spinner />}
          </div>
          <div className="small muted">Or choose step by step: type the underlying, then pick each field as it is asked. Nothing is guessed.</div>
        </div>

        {r && (
          <div className="row" aria-label="Parsed fields" data-testid="parsed-fields">
            <Chip label="Underlying" value={r.parsed.underlying} needed={r.field === 'underlying'} />
            <Chip label="Expiry" value={r.parsed.expiry} needed={r.field === 'expiry'} />
            <Chip label="Type" value={r.parsed.type} needed={r.field === 'type'} />
            <Chip label="Strike" value={r.parsed.strike !== null ? String(r.parsed.strike) : null} needed={r.field === 'strike'} />
            {sel && <button type="button" className="btn sm ghost" onClick={restart}>Start over</button>}
          </div>
        )}

        {q.isError && <div className="banner neg small" role="alert">{errMsg(q.error)}</div>}

        {r?.status === 'NEEDS_SELECTION' && r.options && r.field && (
          <div className="stack" data-testid="needs-selection" style={{ gap: 6 }}>
            <div className="small"><strong>{r.reason}</strong></div>
            <div className="row">
              {r.field === 'type' || r.options.length <= 6 ? r.options.map((o) => <button key={o.value} type="button" className="btn sm" onClick={() => choose(r.field!, o.value)}>{o.label}</button>) : (
                <>
                  <label className="small muted" htmlFor="ac-pick">Select {FIELD_LABEL[r.field]}</label>
                  <select id="ac-pick" defaultValue="" onChange={(e) => e.target.value && choose(r.field!, e.target.value)} style={{ width: 'auto', minWidth: 200 }}>
                    <option value="">Choose…</option>
                    {r.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </>
              )}
            </div>
          </div>
        )}

        {r?.status === 'REJECTED' && <div className="banner neg small" role="alert" data-testid="rejected">{r.reason}</div>}

        {r?.status === 'RESOLVED' && r.contract && (
          <div className="stack" data-testid="resolved" style={{ gap: 8 }}>
            <div className="banner info">
              <div style={{ flex: 1 }}>
                <strong>{r.contract.label}</strong> <Badge kind="pos">listed</Badge>
                <dl className="kv" style={{ marginTop: 6 }}>
                  <dt>Exchange symbol</dt><dd>{r.contract.identity.canonicalSymbol ?? <span className="muted">not supplied by the provider</span>}</dd>
                  <dt>Instrument token</dt><dd>{r.contract.identity.instrumentKey ?? <span className="muted">none (NSE chain)</span>}</dd>
                  <dt>Exact expiry</dt><dd>{r.contract.ref.expiry} (15:30 IST)</dd>
                  <dt>Lot size</dt><dd>{r.contract.identity.lotSize ?? <span className="muted">unknown</span>}</dd>
                  <dt>Verified from</dt><dd>{r.contract.identity.source} at {fmtIst(r.contract.identity.verifiedAt)}</dd>
                </dl>
              </div>
            </div>
            {r.notes.length > 0 && <div className="small muted">{r.notes.join(' ')}</div>}
            <div className="row">
              <label className="small muted" htmlFor="ac-g">Add to</label>
              <select id="ac-g" value={target} onChange={(e) => setTarget(e.target.value)} style={{ width: 'auto' }}>
                <option value="none">Ungrouped watchlist</option>
                {groupsList.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                <option value="__new">New group…</option>
              </select>
              {target === '__new' && <input aria-label="New group name" placeholder="Angel One — October Calls" value={newName} onChange={(e) => setNewName(e.target.value)} style={{ width: 240 }} maxLength={60} />}
            </div>
            <div className="stack" style={{ gap: 2 }}>
              <label className="small muted" htmlFor="ac-n">Note (optional)</label>
              <input id="ac-n" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
            </div>
            <div className="row" style={{ justifyContent: 'flex-end' }}>
              <button type="button" className="btn" onClick={onClose}>Cancel</button>
              <button type="button" className="btn primary" disabled={busy || needName} onClick={() => void add()}>{busy ? 'Adding…' : 'Add to watchlist'}</button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
