import { useState } from 'react';
import type { Segment, Side, Trade } from '../../shared/types';
import { fromIst, istParts } from '../../shared/india';
import { useSend } from '../api';
import { errMsg, Field, Modal, NumInput, Segmented, useToast } from '../ui';

const SEGMENTS: { value: Segment; label: string }[] = [
  { value: 'EQ_DELIVERY', label: 'Equity delivery' },
  { value: 'EQ_INTRADAY', label: 'Equity intraday' },
  { value: 'FUT', label: 'Futures' },
  { value: 'OPT', label: 'Options' },
];

/** Inputs are IST wall-clock, whatever the browser's timezone is. */
export function toIstInput(iso: string | null): string {
  if (!iso) return '';
  const p = istParts(new Date(iso));
  const z = (n: number) => String(n).padStart(2, '0');
  return `${p.dateStr}T${z(p.hour)}:${z(p.minute)}`;
}
export function fromIstInput(v: string): string | null {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})$/.exec(v);
  if (!m) return null;
  return fromIst(m[1]!, Number(m[2]), Number(m[3])).toISOString();
}

export function TradeForm({ trade, setups, onClose }: { trade?: Trade; setups: string[]; onClose: () => void }) {
  const toast = useToast();
  const [symbol, setSymbol] = useState(trade?.symbol ?? '');
  const [segment, setSegment] = useState<Segment>(trade?.segment ?? 'EQ_INTRADAY');
  const [side, setSide] = useState<Side>(trade?.side ?? 'LONG');
  const [qty, setQty] = useState<number | null>(trade?.qty ?? null);
  const [entryPrice, setEntryPrice] = useState<number | null>(trade?.entryPrice ?? null);
  const [exitPrice, setExitPrice] = useState<number | null>(trade?.exitPrice ?? null);
  const [entryAt, setEntryAt] = useState(toIstInput(trade?.entryAt ?? new Date().toISOString()));
  const [exitAt, setExitAt] = useState(toIstInput(trade?.exitAt ?? null));
  const [stopLoss, setStop] = useState<number | null>(trade?.stopLoss ?? null);
  const [target, setTarget] = useState<number | null>(trade?.target ?? null);
  const [setup, setSetup] = useState(trade?.setup ?? '');
  const [tags, setTags] = useState((trade?.tags ?? []).join(', '));
  const [emotion, setEmotion] = useState<number | null>(trade?.emotion ?? null);
  const [plan, setPlan] = useState<'' | 'yes' | 'no'>(trade?.planFollowed === true ? 'yes' : trade?.planFollowed === false ? 'no' : '');
  const [notes, setNotes] = useState(trade?.notes ?? '');
  const [err, setErr] = useState<string | null>(null);

  const save = useSend<unknown>(trade ? 'PUT' : 'POST', trade ? `/api/trades/${trade.id}` : '/api/trades', ['/api/journal', '/api/risk']);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const entryIso = fromIstInput(entryAt);
    if (!entryIso) return setErr('Enter a valid entry time.');
    const exitIso = exitAt ? fromIstInput(exitAt) : null;
    if (qty === null || entryPrice === null) return setErr('Quantity and entry price are required.');
    save.mutate(
      {
        symbol: symbol.trim().toUpperCase(),
        segment,
        side,
        qty,
        entryPrice,
        exitPrice,
        entryAt: entryIso,
        exitAt: exitIso,
        stopLoss,
        target,
        setup: setup.trim(),
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        emotion,
        planFollowed: plan === '' ? null : plan === 'yes',
        notes,
      },
      { onSuccess: () => { toast(trade ? 'Trade updated' : 'Trade logged'); onClose(); }, onError: (e2) => setErr(errMsg(e2)) },
    );
  };

  const lotHint = segment === 'FUT' || segment === 'OPT' ? 'Total units = lots × lot size (e.g. 2 lots of Nifty = 130)' : undefined;

  return (
    <Modal title={trade ? `Edit ${trade.symbol}` : 'Log a trade'} onClose={onClose}>
      <form className="stack" style={{ gap: 14 }} onSubmit={submit}>
        <div className="row" style={{ gap: 12 }}>
          <Segmented<Side> label="Side" value={side} onChange={setSide} options={[{ value: 'LONG', label: 'Long / Buy' }, { value: 'SHORT', label: 'Short / Sell' }]} />
        </div>
        <div className="form-grid">
          <Field label="Symbol / contract" hint="e.g. RELIANCE or NIFTY 25000 CE">{(id) => <input id={id} value={symbol} onChange={(e) => setSymbol(e.target.value)} required maxLength={40} />}</Field>
          <Field label="Segment">{(id) => <select id={id} value={segment} onChange={(e) => setSegment(e.target.value as Segment)}>{SEGMENTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}</select>}</Field>
          <Field label="Quantity (units)" hint={lotHint}>{(id) => <NumInput id={id} value={qty} onChange={setQty} min={0} />}</Field>
          <Field label="Entry price">{(id) => <NumInput id={id} value={entryPrice} onChange={setEntryPrice} min={0} />}</Field>
          <Field label="Entry time (IST)">{(id) => <input id={id} type="datetime-local" value={entryAt} onChange={(e) => setEntryAt(e.target.value)} required />}</Field>
          <Field label="Stop loss" hint="Needed to measure risk and R-multiple">{(id) => <NumInput id={id} value={stopLoss} onChange={setStop} min={0} />}</Field>
          <Field label="Target">{(id) => <NumInput id={id} value={target} onChange={setTarget} min={0} />}</Field>
          <Field label="Exit price" hint="Leave empty while the trade is open">{(id) => <NumInput id={id} value={exitPrice} onChange={setExitPrice} min={0} />}</Field>
          <Field label="Exit time (IST)">{(id) => <input id={id} type="datetime-local" value={exitAt} onChange={(e) => setExitAt(e.target.value)} />}</Field>
        </div>
        <div className="form-grid">
          <Field label="Setup">{(id) => (<><input id={id} list="setups" value={setup} onChange={(e) => setSetup(e.target.value)} maxLength={60} placeholder="e.g. ORB, pullback" /><datalist id="setups">{setups.map((s) => <option key={s} value={s} />)}</datalist></>)}</Field>
          <Field label="Tags" hint="Comma separated">{(id) => <input id={id} value={tags} onChange={(e) => setTags(e.target.value)} placeholder="expiry-day, news" />}</Field>
          <Field label="Emotion at entry (1 calm – 5 heated)">{(id) => <select id={id} value={emotion ?? ''} onChange={(e) => setEmotion(e.target.value ? Number(e.target.value) : null)}><option value="">Not rated</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}</select>}</Field>
          <Field label="Followed my plan?">{(id) => <select id={id} value={plan} onChange={(e) => setPlan(e.target.value as '' | 'yes' | 'no')}><option value="">Not answered</option><option value="yes">Yes</option><option value="no">No</option></select>}</Field>
        </div>
        <Field label="Notes">{(id) => <textarea id={id} value={notes} maxLength={2000} onChange={(e) => setNotes(e.target.value)} placeholder="What was the thesis? What would invalidate it? What did you learn?" />}</Field>
        {err && <div className="banner neg small" role="alert">{err}</div>}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={save.isPending}>{trade ? 'Save changes' : 'Log trade'}</button>
        </div>
      </form>
    </Modal>
  );
}
