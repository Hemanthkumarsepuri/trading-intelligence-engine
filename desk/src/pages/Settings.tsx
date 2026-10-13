import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { TickReport } from '../../server/services/alertsRunner';
import { fmtIst } from '../../shared/format';
import type { ProviderHealth, Settings as S } from '../../shared/types';
import { request, useGet, useSend } from '../api';
import { Async, Badge, Card, errMsg, Field, NumInput, PageHead, Skeleton, useToast } from '../ui';

function RiskForm({ s }: { s: S }) {
  const toast = useToast();
  const [v, setV] = useState<S>(s);
  useEffect(() => setV(s), [s]);
  const save = useSend<Partial<S>>('PUT', '/api/settings', ['/api/settings', '/api/journal', '/api/risk', '/api/portfolio']);
  const num = (k: keyof S) => (n: number | null) => setV((cur) => ({ ...cur, [k]: n ?? 0 }));
  return (
    <form className="stack" style={{ gap: 14 }} onSubmit={(e) => {
      e.preventDefault();
      const { watchlistSort: _w, ...rest } = v;
      save.mutate(rest, { onSuccess: () => toast('Settings saved'), onError: (er) => toast(errMsg(er), true) });
    }}>
      <h3>Capital and risk limits</h3>
      <div className="form-grid">
        <Field label="Trading capital (₹)" hint="Denominator for all risk percentages">{(id) => <NumInput id={id} value={v.capital} onChange={num('capital')} />}</Field>
        <Field label="Risk per trade (%)">{(id) => <NumInput id={id} value={v.riskPerTradePct} onChange={num('riskPerTradePct')} />}</Field>
        <Field label="Max daily loss (%)">{(id) => <NumInput id={id} value={v.maxDailyLossPct} onChange={num('maxDailyLossPct')} />}</Field>
        <Field label="Max trades per day">{(id) => <NumInput id={id} value={v.maxTradesPerDay} onChange={num('maxTradesPerDay')} step="1" />}</Field>
        <Field label="Max open positions">{(id) => <NumInput id={id} value={v.maxOpenPositions} onChange={num('maxOpenPositions')} step="1" />}</Field>
      </div>
      <h3>Brokerage and tax</h3>
      <div className="form-grid">
        <Field label="Delivery brokerage per order (₹)">{(id) => <NumInput id={id} value={v.brokerageDeliveryFlat} onChange={num('brokerageDeliveryFlat')} />}</Field>
        <Field label="Intraday / futures brokerage (% of turnover)">{(id) => <NumInput id={id} value={v.brokeragePct} onChange={num('brokeragePct')} />}</Field>
        <Field label="Brokerage cap per order (₹)">{(id) => <NumInput id={id} value={v.brokerageCap} onChange={num('brokerageCap')} />}</Field>
        <Field label="Options brokerage per order (₹)">{(id) => <NumInput id={id} value={v.brokerageOptionsFlat} onChange={num('brokerageOptionsFlat')} />}</Field>
        <Field label="DP charge per sell (₹ incl. GST)">{(id) => <NumInput id={id} value={v.dpChargePerSell} onChange={num('dpChargePerSell')} />}</Field>
        <Field label="Tax slab rate (fraction)" hint="0.30 = 30%">{(id) => <NumInput id={id} value={v.slabRate} onChange={num('slabRate')} />}</Field>
      </div>
      <div><button className="btn primary" disabled={save.isPending}>Save settings</button></div>
    </form>
  );
}

interface JobState {
  lastStartAt: string | null;
  lastEndAt: string | null;
  lastOk: boolean | null;
  lastError: string | null;
}
interface SchedulerInfo {
  enabled: boolean;
  healthy: boolean | null;
  note: string;
  heartbeatAt: string | null;
  jobs: Partial<Record<'alerts' | 'digest-pre' | 'digest-eod' | 'backup', JobState>>;
}
interface StatusData {
  problems: string[];
  storage: string;
  offline: boolean;
  providers: ProviderHealth[];
  lastTick: TickReport | null;
  scheduler: SchedulerInfo;
  backups: string[];
  now: string;
}

const JOB_LABEL: Record<string, string> = { alerts: 'Alert checks (every 5 min, 08:30–16:30 IST)', 'digest-pre': 'Pre-market brief (08:45 IST)', 'digest-eod': 'End-of-day summary (17:00 IST)', backup: 'Nightly backup (02:00 IST)' };

function Status() {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useGet<StatusData>('/api/status');
  const [busy, setBusy] = useState<string | null>(null);
  const act = async (label: string, fn: () => Promise<string>) => {
    setBusy(label);
    try {
      toast(await fn());
      await qc.invalidateQueries({ queryKey: ['api', '/api/status'] });
    } catch (e) {
      toast(errMsg(e), true);
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card title="Data sources and system health" right={<button className="btn sm" onClick={() => void q.refetch()} disabled={q.isFetching}>{q.isFetching ? 'Testing…' : 'Run self-test'}</button>}>
      <Async q={q} skeleton={<Skeleton h={160} />}>
        {(d) => (
          <div className="stack">
            {d.problems.map((p) => <div key={p} className="banner warn small">{p}</div>)}
            <table>
              <tbody>
                {d.providers.map((p) => (
                  <tr key={p.id}>
                    <td className="wrap"><strong>{p.label}</strong><div className="small muted" style={{ whiteSpace: 'normal' }}>{p.detail}</div></td>
                    <td className="num nowrap">{p.latencyMs !== null ? `${p.latencyMs} ms` : ''}</td>
                    <td>{p.ok === null ? <Badge>Not set up</Badge> : p.ok ? <Badge kind="pos">Working</Badge> : <Badge kind="neg">Failing</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3>Scheduler</h3>
            <div className={`banner small ${d.scheduler.healthy === false ? 'warn' : 'info'}`}>{d.scheduler.note}{d.scheduler.heartbeatAt ? ` Last heartbeat ${fmtIst(d.scheduler.heartbeatAt)}.` : ''}</div>
            <table>
              <tbody>
                {Object.entries(JOB_LABEL).map(([id, label]) => {
                  const j = d.scheduler.jobs[id as keyof SchedulerInfo['jobs']];
                  return (
                    <tr key={id}>
                      <td className="wrap">{label}{j?.lastError && <div className="small warn-t" style={{ whiteSpace: 'normal' }}>{j.lastError}</div>}</td>
                      <td className="small muted nowrap">{j?.lastEndAt ? fmtIst(j.lastEndAt) : 'never'}</td>
                      <td>{j?.lastOk === true ? <Badge kind="pos">OK</Badge> : j?.lastOk === false ? <Badge kind="neg">Failed</Badge> : <Badge>—</Badge>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="row">
              <button className="btn sm" disabled={busy !== null} onClick={() => void act('pre', async () => { const r = await request<{ sent: boolean; reason?: string }>('POST', '/api/digest/pre', {}); return r.data.sent ? 'Pre-market brief sent' : (r.data.reason ?? 'Not sent'); })}>Send pre-market brief now</button>
              <button className="btn sm" disabled={busy !== null} onClick={() => void act('eod', async () => { const r = await request<{ sent: boolean; reason?: string }>('POST', '/api/digest/eod', {}); return r.data.sent ? 'End-of-day summary sent' : (r.data.reason ?? 'Not sent'); })}>Send EOD summary now</button>
            </div>
            <p className="small faint">Storage: {d.storage}. Last alert check: {d.lastTick ? fmtIst(d.lastTick.ranAt) : 'never'}.</p>
          </div>
        )}
      </Async>
    </Card>
  );
}

interface TelegramStatus {
  configured: boolean;
  chatHint: string | null;
  lastSuccessAt: string | null;
  lastSuccessKind: string | null;
  lastTestAt: string | null;
  failures: { at: string; kind: string; category: string; message: string }[];
}

function Telegram() {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useGet<TelegramStatus>('/api/telegram/status', { poll: 60_000 });
  const [busy, setBusy] = useState(false);
  const test = async () => {
    setBusy(true);
    try {
      const r = await request<{ connection: boolean; sent: boolean; bot: string | null; error: string | null }>('POST', '/api/telegram/test', {});
      if (r.data.sent) toast(`Test message sent by ${r.data.bot ?? 'your bot'}. Check Telegram.`);
      else toast(`Telegram: ${r.data.error ?? 'not sent'}`, true);
    } catch (e) {
      toast(errMsg(e), true);
    } finally {
      setBusy(false);
      await qc.invalidateQueries({ queryKey: ['api', '/api/telegram/status'] });
    }
  };
  return (
    <Card title="Telegram alerts" right={<button className="btn sm" disabled={busy || !q.data?.data.configured} onClick={() => void test()}>{busy ? 'Sending…' : 'Send test message'}</button>}>
      <Async q={q} skeleton={<Skeleton h={80} />}>
        {(t) => (
          <div className="stack">
            {!t.configured && <div className="banner warn small">Not configured. Add the bot token and chat id to the Railway API service (TELEGRAM_SETUP.md). They never go into this website.</div>}
            <dl className="kv">
              <dt>Chat</dt><dd>{t.chatHint ?? '-'}</dd>
              <dt>Last successful send</dt><dd>{t.lastSuccessAt ? `${fmtIst(t.lastSuccessAt)} (${t.lastSuccessKind})` : 'never'}</dd>
              <dt>Last test</dt><dd>{t.lastTestAt ? fmtIst(t.lastTestAt) : 'never'}</dd>
            </dl>
            {t.failures.length > 0 ? (
              <details className="notes">
                <summary>{t.failures.length} recent failure{t.failures.length > 1 ? 's' : ''} (latest {fmtIst(t.failures[0]!.at)})</summary>
                <ul>{t.failures.slice(0, 10).map((f, i) => <li key={i}>{fmtIst(f.at)} · {f.kind} · {f.category}: {f.message}</li>)}</ul>
              </details>
            ) : <p className="small muted">No recent delivery failures.</p>}
            <p className="small faint">Undelivered alerts are retried up to 3 times within 30 minutes. All times IST.</p>
          </div>
        )}
      </Async>
    </Card>
  );
}

function Snapshots() {
  const toast = useToast();
  const qc = useQueryClient();
  const q = useGet<StatusData>('/api/status');
  const [busy, setBusy] = useState<string | null>(null);
  const verify = async (key: string) => {
    setBusy(key);
    try {
      const r = await request<{ ok: boolean; counts: Record<string, number>; problems: string[] }>('GET', `/api/backup/verify?key=${encodeURIComponent(key)}`);
      toast(r.data.ok ? `Verified: ${Object.entries(r.data.counts).map(([k, v]) => `${v} ${k}`).join(', ')}` : `Invalid: ${r.data.problems.join('; ')}`, !r.data.ok);
    } catch (e) {
      toast(errMsg(e), true);
    } finally {
      setBusy(null);
    }
  };
  const restore = async (key: string) => {
    if (!window.confirm(`Restore ${key}? Your current data is snapshotted first, so this can be undone.`)) return;
    setBusy(key);
    try {
      await request('POST', '/api/backup/restore', { key, confirm: true });
      await qc.invalidateQueries();
      toast('Snapshot restored');
    } catch (e) {
      toast(errMsg(e), true);
    } finally {
      setBusy(null);
    }
  };
  const keys = q.data?.data.backups ?? [];
  return (
    <Card title="Server snapshots" flush>
      {keys.length === 0 ? <div className="empty">No snapshots yet. The first runs at 02:00 IST, or use “Snapshot now”.</div> : (
        <table>
          <tbody>
            {keys.map((k) => (
              <tr key={k}>
                <td className="mono small">{k.replace('backup/', '')}</td>
                <td className="right">
                  <button className="btn sm" disabled={busy !== null} onClick={() => void verify(k)}>Verify</button>{' '}
                  <button className="btn sm danger" disabled={busy !== null} onClick={() => void restore(k)}>Restore</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Backup() {
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);

  const exportNow = async () => {
    setBusy(true);
    try {
      const r = await request<unknown>('GET', '/api/export');
      const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `tire-desk-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) {
      toast(errMsg(e), true);
    } finally {
      setBusy(false);
    }
  };
  const importFile = async (f: File | undefined) => {
    if (!f) return;
    if (f.size > 4_500_000) return toast('Backup file is too large to import (4.5 MB max).', true);
    try {
      const body = JSON.parse(await f.text()) as Record<string, unknown>;
      const counts = ['trades', 'holdings', 'watchlist', 'alerts'].map((k) => `${Array.isArray(body[k]) ? (body[k] as unknown[]).length : 0} ${k}`).join(', ');
      if (!window.confirm(`Replace ALL current data with this backup (${counts})?\n\nA snapshot of your current data is saved first.`)) return;
      setBusy(true);
      await request('POST', '/api/import', { ...body, version: 1, confirm: true });
      await qc.invalidateQueries();
      toast('Backup restored');
    } catch (e) {
      toast(e instanceof SyntaxError ? 'That file is not valid JSON.' : errMsg(e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Backup and restore">
      <div className="stack">
        <p className="small muted">Your data lives on the Railway API service's volume. A snapshot is taken automatically every night at 02:00 IST (last 14 kept). Download a copy for safekeeping any time.</p>
        <div className="row">
          <button className="btn" disabled={busy} onClick={() => void exportNow()}>Download backup (.json)</button>
          <label className="btn" style={{ cursor: 'pointer' }}>
            Restore from file…
            <input type="file" accept="application/json,.json" hidden onChange={(e) => { void importFile(e.target.files?.[0]); e.target.value = ''; }} />
          </label>
          <button className="btn" disabled={busy} onClick={async () => { setBusy(true); try { await request('POST', '/api/backup/run', {}); await qc.invalidateQueries({ queryKey: ['api', '/api/status'] }); toast('Snapshot saved'); } catch (e) { toast(errMsg(e), true); } finally { setBusy(false); } }}>Snapshot now</button>
        </div>
      </div>
    </Card>
  );
}

export function Settings() {
  const q = useGet<S>('/api/settings');
  return (
    <div className="page">
      <PageHead title="Settings" sub="Risk limits, charges, data sources and backups." />
      <Card><Async q={q} skeleton={<Skeleton h={200} />}>{(s) => <RiskForm s={s} />}</Async></Card>
      <Status />
      <Telegram />
      <Backup />
      <Snapshots />
      <Card title="About">
        <p className="small muted">TIRE Desk is a private, read-only research and journaling tool. It never places orders and nothing in it is investment advice. Market data comes from free public sources and may be delayed or briefly unavailable; every screen says when it is showing stale or demo data. Charges and tax figures are estimates.</p>
      </Card>
    </div>
  );
}
