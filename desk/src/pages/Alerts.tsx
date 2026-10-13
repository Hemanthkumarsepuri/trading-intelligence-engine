import { useState } from 'react';
import type { TickReport } from '../../server/services/alertsRunner';
import { ALERT_LABEL } from '../../shared/alerts';
import { fmtIst, relTime } from '../../shared/format';
import type { Alert, AlertEvent, AlertType } from '../../shared/types';
import { useGet, useSend, request } from '../api';
import { Async, Badge, Card, Empty, errMsg, Field, Icon, NumInput, PageHead, Skeleton, useConfirm, useToast } from '../ui';
import { useQueryClient } from '@tanstack/react-query';

interface AlertsData {
  alerts: Alert[];
  events: AlertEvent[];
  lastTick: TickReport | null;
  telegram: boolean;
}

const TYPES = Object.keys(ALERT_LABEL) as AlertType[];
const NEEDS_SYMBOL = (t: AlertType) => t !== 'VIX_ABOVE';
const NEEDS_THRESHOLD = (t: AlertType) => t !== 'FNO_BAN';

function AlertForm() {
  const toast = useToast();
  const [type, setType] = useState<AlertType>('PRICE_ABOVE');
  const [symbol, setSymbol] = useState('');
  const [threshold, setThreshold] = useState<number | null>(null);
  const [repeat, setRepeat] = useState<'once' | 'daily'>('once');
  const [note, setNote] = useState('');
  const create = useSend<Omit<Alert, 'id' | 'lastTriggeredAt' | 'createdAt'>>('POST', '/api/alerts', ['/api/alerts']);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    create.mutate(
      { type, symbol: NEEDS_SYMBOL(type) ? symbol.trim().toUpperCase() : 'INDIAVIX', threshold: NEEDS_THRESHOLD(type) ? (threshold ?? NaN) : 0, repeat, note, enabled: true },
      {
        onSuccess: () => { toast('Alert created'); setSymbol(''); setThreshold(null); setNote(''); },
        onError: (er) => toast(errMsg(er), true),
      },
    );
  };

  return (
    <Card title="New alert">
      <form className="stack" onSubmit={submit} style={{ gap: 14 }}>
        <div className="form-grid">
          <Field label="Condition">{(id) => <select id={id} value={type} onChange={(e) => setType(e.target.value as AlertType)}>{TYPES.map((t) => <option key={t} value={t}>{ALERT_LABEL[t]}</option>)}</select>}</Field>
          {NEEDS_SYMBOL(type) && <Field label="Symbol" hint="NSE symbol or NIFTY / BANKNIFTY">{(id) => <input id={id} value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="RELIANCE" required />}</Field>}
          {NEEDS_THRESHOLD(type) && <Field label="Level">{(id) => <NumInput id={id} value={threshold} onChange={setThreshold} />}</Field>}
          <Field label="Repeat">{(id) => <select id={id} value={repeat} onChange={(e) => setRepeat(e.target.value as 'once' | 'daily')}><option value="once">Once, then stop</option><option value="daily">At most once a day</option></select>}</Field>
        </div>
        <Field label="Note (shown in the notification)">{(id) => <input id={id} value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder="Why does this level matter?" />}</Field>
        <div><button className="btn primary" disabled={create.isPending}><Icon name="plus" />Create alert</button></div>
      </form>
    </Card>
  );
}

export function Alerts() {
  const q = useGet<AlertsData>('/api/alerts', { poll: 60_000 });
  const toast = useToast();
  const qc = useQueryClient();
  const confirm = useConfirm();
  const del = useSend<string>('DELETE', (id) => `/api/alerts/${id}`, ['/api/alerts']);
  const [running, setRunning] = useState(false);

  const toggle = async (a: Alert) => {
    try {
      await request('PUT', `/api/alerts/${a.id}`, { type: a.type, symbol: a.symbol, threshold: a.threshold, repeat: a.repeat, note: a.note, enabled: !a.enabled });
      await qc.invalidateQueries({ queryKey: ['api', '/api/alerts'] });
    } catch (e) {
      toast(errMsg(e), true);
    }
  };
  const rearm = async (a: Alert) => {
    try {
      // Changing nothing but re-saving does not re-arm; nudge via enabled cycle is wrong. The API re-arms on condition change,
      // so delete + recreate keeps the rule explicit and the audit trail honest.
      await request('DELETE', `/api/alerts/${a.id}`);
      await request('POST', '/api/alerts', { type: a.type, symbol: a.symbol, threshold: a.threshold, repeat: a.repeat, note: a.note, enabled: true });
      await qc.invalidateQueries({ queryKey: ['api', '/api/alerts'] });
      toast('Alert re-armed');
    } catch (e) {
      toast(errMsg(e), true);
    }
  };
  const runNow = async () => {
    setRunning(true);
    try {
      const r = await request<TickReport>('POST', '/api/alerts-run', {});
      toast(r.data.skippedReason ?? `Checked ${r.data.evaluated} alert(s), ${r.data.fired.length} fired`);
      await qc.invalidateQueries({ queryKey: ['api', '/api/alerts'] });
    } catch (e) {
      toast(errMsg(e), true);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="page">
      <PageHead title="Alerts" sub="Checked every 5 minutes while the market is open (Railway scheduler). Delivered to Telegram and logged here. Times are IST." right={<button className="btn" onClick={() => void runNow()} disabled={running}>{running ? 'Checking…' : 'Check now'}</button>} />
      <Async q={q} skeleton={<Card><Skeleton h={160} /></Card>}>
        {(d) => (
          <>
            {!d.telegram && <div className="banner warn small">Telegram is not configured, so alerts only appear in the log below. Add the bot token and chat id to the Railway API service (see TELEGRAM_SETUP.md) to get phone notifications.</div>}
            <AlertForm />
            <Card title="Your alerts" flush>
              {d.alerts.length === 0 ? (
                <Empty>No alerts yet.</Empty>
              ) : (
                <div className="tbl-wrap">
                  <table>
                    <thead><tr><th>Alert</th><th>Repeat</th><th>Status</th><th>Last fired</th><th /></tr></thead>
                    <tbody>
                      {d.alerts.map((a) => {
                        const spent = a.repeat === 'once' && a.lastTriggeredAt;
                        return (
                          <tr key={a.id}>
                            <td className="wrap">
                              <strong>{a.type === 'VIX_ABOVE' ? 'India VIX' : a.symbol}</strong> <span className="muted">{ALERT_LABEL[a.type]}{a.type !== 'FNO_BAN' ? ` ${a.threshold}` : ''}</span>
                              {a.note && <div className="small muted">{a.note}</div>}
                            </td>
                            <td>{a.repeat === 'once' ? 'Once' : 'Daily'}</td>
                            <td>{spent ? <Badge kind="info">Fired</Badge> : a.enabled ? <Badge kind="pos">Armed</Badge> : <Badge>Paused</Badge>}</td>
                            <td className="small muted">{a.lastTriggeredAt ? fmtIst(a.lastTriggeredAt) : '-'}</td>
                            <td className="right">
                              {spent ? <button className="btn sm" onClick={() => void rearm(a)}>Re-arm</button> : <button className="btn sm" onClick={() => void toggle(a)}>{a.enabled ? 'Pause' : 'Resume'}</button>}
                              <button className="icon-btn" aria-label="Delete alert" onClick={() => { if (confirm('Delete this alert?')) del.mutate(a.id, { onError: (e) => toast(errMsg(e), true) }); }}><Icon name="trash" /></button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            <div className="grid g2">
              <Card title="Recent notifications" flush>
                {d.events.length === 0 ? <Empty>Nothing has fired yet.</Empty> : (
                  <div className="tbl-wrap" style={{ maxHeight: 360, overflowY: 'auto' }}>
                    <table>
                      <tbody>
                        {d.events.slice(0, 40).map((e) => (
                          <tr key={e.id}>
                            <td className="wrap">{e.message}<div className="small muted">{relTime(e.at)} · {fmtIst(e.at)}{(e.attempts ?? 1) > 1 ? ` · ${e.attempts} attempts` : ''}</div>{!e.delivered && e.lastError && <div className="small warn-t" style={{ whiteSpace: 'normal' }}>{e.lastError}</div>}</td>
                            <td>{e.delivered ? <Badge kind="pos" title={e.deliveredAt ? `Delivered ${fmtIst(e.deliveredAt)}` : undefined}>Sent</Badge> : <Badge kind="warn" title="Logged, but Telegram delivery failed or is not configured">Not sent</Badge>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
              <Card title="Last scheduled check">
                {d.lastTick ? (
                  <dl className="kv">
                    <dt>Ran</dt><dd>{relTime(d.lastTick.ranAt)}</dd>
                    <dt>Evaluated</dt><dd>{d.lastTick.evaluated}</dd>
                    <dt>Fired</dt><dd>{d.lastTick.fired.length}</dd>
                    <dt>Skipped</dt><dd>{d.lastTick.skipped.length}</dd>
                    {d.lastTick.skippedReason && (<><dt>Note</dt><dd>{d.lastTick.skippedReason}</dd></>)}
                    {d.lastTick.risk && (<><dt>Risk guard</dt><dd>{d.lastTick.risk.state}</dd></>)}
                  </dl>
                ) : <Empty>No alert check has run yet. Checks run every 5 minutes from 08:30 to 16:30 IST on the Railway API service.</Empty>}
                {d.lastTick && d.lastTick.skipped.length > 0 && (
                  <details className="notes" style={{ marginTop: 10 }}>
                    <summary>Why alerts were skipped</summary>
                    <ul>{d.lastTick.skipped.slice(0, 10).map((s, i) => <li key={i}>{s.symbol}: {s.why}</li>)}</ul>
                  </details>
                )}
                {d.lastTick && d.lastTick.notifyFailures.length > 0 && <div className="banner warn small" style={{ marginTop: 10 }}>{d.lastTick.notifyFailures.join('; ')}</div>}
              </Card>
            </div>
          </>
        )}
      </Async>
    </div>
  );
}
