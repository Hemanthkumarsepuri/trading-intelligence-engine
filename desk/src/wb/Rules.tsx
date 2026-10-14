// User-defined alert rules for exact contracts: create, enable/disable, edit, delete. In-app only.

import { useState } from 'react';
import type { ContractRule, RuleKind, RuleParams } from '../../shared/wbrules';
import { RULE_LABEL, validateRuleParams } from '../../shared/wbrules';
import { fmtIst } from '../../shared/format';
import { useGet } from '../api';
import { Badge, Empty, useConfirm } from '../ui';
import { useWb } from './Groups';

const KINDS = Object.keys(RULE_LABEL) as RuleKind[];

function describe(r: ContractRule): string {
  const p = r.params;
  switch (r.kind) {
    case 'PREMIUM_CROSS': return `premium ${p.direction === 'ABOVE' ? 'at or above' : 'at or below'} ${p.price}`;
    case 'UNDERLYING_LEVEL': return `underlying ${p.direction === 'ABOVE' ? 'above' : 'below'} ${p.level}`;
    case 'CLOSE_BEYOND_LEVEL': return `completed ${p.tf} close ${p.direction === 'ABOVE' ? 'above' : 'below'} ${p.level}`;
    case 'RETEST': return `retest of ${p.level} (${p.direction === 'ABOVE' ? 'break above' : 'break below'}, ${p.tf})`;
    case 'OPTION_VOLUME_ABOVE': return `volume ≥ ${p.volume} contracts`;
    case 'SPREAD_ABOVE': return `spread ≥ ${p.spreadPct}%`;
    case 'DATA_STALE': return 'data becomes stale';
    case 'EXPIRY_WITHIN': return `${p.days} day(s) or fewer to expiry`;
  }
}

const num = (s: string): number | undefined => (s.trim() === '' || Number.isNaN(Number(s)) ? undefined : Number(s));

function RuleForm({ contractId, existing, onDone }: { contractId: string; existing?: ContractRule; onDone: () => void }) {
  const call = useWb();
  const [kind, setKind] = useState<RuleKind>(existing?.kind ?? 'PREMIUM_CROSS');
  const [p, setP] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(existing?.params ?? {}).map(([k, v]) => [k, String(v)])));
  const [dir, setDir] = useState<'ABOVE' | 'BELOW'>(existing?.params.direction ?? 'ABOVE');
  const [tf, setTf] = useState<string>(existing?.params.tf ?? '15m');
  const [note, setNote] = useState(existing?.note ?? '');
  const field = (id: string, label: string, key: string) => (
    <div className="stack" style={{ gap: 2 }}>
      <label className="small muted" htmlFor={`rf-${id}`}>{label}</label>
      <input id={`rf-${id}`} inputMode="decimal" value={p[key] ?? ''} onChange={(e) => setP({ ...p, [key]: e.target.value.replace(/[^0-9.]/g, '') })} style={{ width: 110 }} />
    </div>
  );
  const needsDir = kind === 'PREMIUM_CROSS' || kind === 'UNDERLYING_LEVEL' || kind === 'CLOSE_BEYOND_LEVEL' || kind === 'RETEST';
  const needsTf = kind === 'CLOSE_BEYOND_LEVEL' || kind === 'RETEST';
  const params: RuleParams = {
    ...(kind === 'PREMIUM_CROSS' ? { price: num(p.price ?? '') } : {}),
    ...(kind === 'UNDERLYING_LEVEL' || needsTf ? { level: num(p.level ?? '') } : {}),
    ...(needsDir ? { direction: dir } : {}),
    ...(needsTf ? { tf: tf as RuleParams['tf'] } : {}),
    ...(kind === 'OPTION_VOLUME_ABOVE' ? { volume: num(p.volume ?? '') } : {}),
    ...(kind === 'SPREAD_ABOVE' ? { spreadPct: num(p.spreadPct ?? '') } : {}),
    ...(kind === 'EXPIRY_WITHIN' ? { days: num(p.days ?? '') } : {}),
  };
  const problem = validateRuleParams(kind, params);
  const save = async () => {
    const body = { contractId, kind, params, enabled: existing?.enabled ?? true, note };
    const ok = existing ? await call('PUT', `/api/workbench/rules/${existing.id}`, body, 'Rule updated') : await call('POST', '/api/workbench/rules', body, 'Rule created');
    if (ok) onDone();
  };
  return (
    <form className="stack" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); if (!problem) void save(); }} aria-label={existing ? 'Edit rule' : 'New rule'}>
      <div className="row" style={{ alignItems: 'flex-end', gap: 12 }}>
        <div className="stack" style={{ gap: 2 }}>
          <label className="small muted" htmlFor="rf-kind">Condition</label>
          <select id="rf-kind" value={kind} onChange={(e) => setKind(e.target.value as RuleKind)} style={{ width: 'auto' }}>
            {KINDS.map((k) => <option key={k} value={k}>{RULE_LABEL[k]}</option>)}
          </select>
        </div>
        {needsDir && (
          <div className="stack" style={{ gap: 2 }}>
            <label className="small muted" htmlFor="rf-dir">Direction</label>
            <select id="rf-dir" value={dir} onChange={(e) => setDir(e.target.value as 'ABOVE' | 'BELOW')} style={{ width: 'auto' }}><option value="ABOVE">Above</option><option value="BELOW">Below</option></select>
          </div>
        )}
        {kind === 'PREMIUM_CROSS' && field('price', 'Premium', 'price')}
        {(kind === 'UNDERLYING_LEVEL' || needsTf) && field('level', 'Underlying level', 'level')}
        {needsTf && (
          <div className="stack" style={{ gap: 2 }}>
            <label className="small muted" htmlFor="rf-tf">Timeframe</label>
            <select id="rf-tf" value={tf} onChange={(e) => setTf(e.target.value)} style={{ width: 'auto' }}>{['5m', '15m', '30m', '1h', '1d'].map((t) => <option key={t} value={t}>{t}</option>)}</select>
          </div>
        )}
        {kind === 'OPTION_VOLUME_ABOVE' && field('volume', 'Volume (contracts)', 'volume')}
        {kind === 'SPREAD_ABOVE' && field('spread', 'Spread %', 'spreadPct')}
        {kind === 'EXPIRY_WITHIN' && field('days', 'Days', 'days')}
        <div className="stack" style={{ gap: 2 }}>
          <label className="small muted" htmlFor="rf-note">Note</label>
          <input id="rf-note" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} style={{ width: 200 }} />
        </div>
      </div>
      {(kind === 'CLOSE_BEYOND_LEVEL' || kind === 'RETEST') && <div className="small muted">Uses completed candles only, and only while underlying data is fresh. A forming candle never triggers it.</div>}
      {problem && <div className="small warn-t" role="status">{problem}</div>}
      <div className="row"><button className="btn sm primary" disabled={Boolean(problem)}>{existing ? 'Save rule' : 'Add rule'}</button><button type="button" className="btn sm ghost" onClick={onDone}>Cancel</button></div>
    </form>
  );
}

/** Rules for one contract (`contractId` set) or every rule (Data Health). */
export function RulesManager({ contractId, label }: { contractId?: string; label?: string }) {
  const q = useGet<{ rules: ContractRule[] }>('/api/workbench/rules');
  const call = useWb();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<string | 'new' | null>(null);
  const rules = (q.data?.data.rules ?? []).filter((r) => !contractId || r.contractId === contractId);
  return (
    <div className="stack" data-testid="rules-manager" style={{ gap: 8 }}>
      <div className="row between">
        <strong className="small">{label ?? 'Alert rules'}</strong>
        {contractId && editing !== 'new' && <button className="btn sm" onClick={() => setEditing('new')}>New rule</button>}
      </div>
      <div className="small muted">In-app alerts only; nothing is sent elsewhere. Each rule fires at most once per bar, day or hour as appropriate, and never from stale or incomplete data.</div>
      {contractId && editing === 'new' && <RuleForm contractId={contractId} onDone={() => setEditing(null)} />}
      {rules.length === 0 && editing !== 'new' ? <Empty>No rules{contractId ? ' for this contract' : ''}.</Empty> : (
        <ul className="stack small" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {rules.map((r) => (
            <li key={r.id} data-testid="rule-row">
              {editing === r.id ? <RuleForm contractId={r.contractId} existing={r} onDone={() => setEditing(null)} /> : (
                <div className="row" style={{ gap: 8 }}>
                  <Badge kind={r.enabled ? 'pos' : undefined}>{r.enabled ? 'on' : 'off'}</Badge>
                  <span><strong>{RULE_LABEL[r.kind]}</strong>: {describe(r)}{!contractId && <span className="muted"> · {r.contractId.replace(/\|/g, ' ')}</span>}{r.note && <span className="muted"> · {r.note}</span>}{r.lastTriggeredAt && <span className="muted"> · last fired {fmtIst(r.lastTriggeredAt)}</span>}</span>
                  <button className="btn sm" onClick={() => void call('POST', `/api/workbench/rules/${r.id}/enabled`, { enabled: !r.enabled })}>{r.enabled ? 'Disable' : 'Enable'}</button>
                  {contractId && <button className="btn sm" onClick={() => setEditing(r.id)}>Edit</button>}
                  <button className="btn sm danger" onClick={() => { if (confirm('Delete this rule?')) void call('DELETE', `/api/workbench/rules/${r.id}`, undefined, 'Rule deleted'); }}>Delete</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
