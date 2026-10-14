// The one scheduler for TIRE Desk. It runs inside the Railway service (and
// nowhere else: the Netlify scheduled functions were removed), so there is a
// single, clearly defined owner of every background job.
//
// Restart safety: all job state lives in storage, not memory. After a restart
// or a crash the next tick sees when each job last started/succeeded and
// catches up (a missed pre-market brief inside its window, a missed nightly
// backup). Every job is itself idempotent (alerts are CAS-claimed, digests are
// claimed once per day, backups overwrite the day's snapshot), and each start
// is claimed with a CAS write, so overlapping ticks cannot double-run a job.

import { istDateStr, istParts } from '../shared/india';
import type { Ctx } from './config';
import { read, update } from './kv';
import { runAlertsTick } from './services/alertsRunner';
import { runBackup } from './services/backup';
import { runDigest } from './services/digests';
import { runOffsiteBackup } from './services/offsite';
import { KEYS } from './services/userdata';

export type JobId = 'alerts' | 'digest-pre' | 'digest-eod' | 'backup' | 'offsite';

export interface JobState {
  lastStartAt: string | null;
  lastEndAt: string | null;
  lastOk: boolean | null;
  lastError: string | null;
  /** IST date of the last successful run (used by once-a-day jobs) */
  lastOkDate: string | null;
  runs: number;
}

export interface SchedulerState {
  heartbeatAt: string | null;
  startedAt: string | null;
  instance: string | null;
  jobs: Partial<Record<JobId, JobState>>;
}

const EMPTY_JOB: JobState = { lastStartAt: null, lastEndAt: null, lastOk: null, lastError: null, lastOkDate: null, runs: 0 };
const EMPTY: SchedulerState = { heartbeatAt: null, startedAt: null, instance: null, jobs: {} };

const MIN = 60_000;

interface JobDef {
  id: JobId;
  /** whether the job should start now, given its stored state */
  due: (now: Date, s: JobState) => boolean;
  run: (ctx: Ctx) => Promise<unknown>;
  /** a run is abandoned (and may be retried) after this long */
  timeoutMs: number;
  /** a "successful" run that should still be retried later in the window */
  incomplete?: (result: unknown) => boolean;
}

const minutesOfDayIst = (d: Date): number => {
  const p = istParts(d);
  return p.hour * 60 + p.minute;
};
const weekday = (d: Date): boolean => {
  const dow = istParts(d).dow;
  return dow >= 1 && dow <= 5;
};
const since = (iso: string | null, now: Date): number => (iso ? now.getTime() - Date.parse(iso) : Infinity);
const inWindow = (d: Date, from: number, to: number) => {
  const m = minutesOfDayIst(d);
  return m >= from && m < to;
};

/** Once-a-day job inside an IST window: retried every `every` until it succeeds for today. */
const dailyInWindow = (from: number, to: number, every: number) => (now: Date, s: JobState): boolean =>
  weekday(now) && inWindow(now, from, to) && s.lastOkDate !== istDateStr(now) && since(s.lastStartAt, now) >= every;

export const JOBS: JobDef[] = [
  {
    // Every 5 minutes, 08:30-16:30 IST on weekdays. The runner checks the exchange
    // calendar and only evaluates live-price alerts while the market is open.
    id: 'alerts',
    due: (now, s) => weekday(now) && inWindow(now, 8 * 60 + 30, 16 * 60 + 30) && since(s.lastStartAt, now) >= 5 * MIN - 5_000,
    run: (ctx) => runAlertsTick(ctx),
    timeoutMs: 4 * MIN,
  },
  {
    // Pre-market brief, 08:45-09:14 IST (before the 09:15 open). Caught up after a restart inside the window.
    id: 'digest-pre',
    due: dailyInWindow(8 * 60 + 45, 9 * 60 + 15, 5 * MIN),
    run: (ctx) => runDigest(ctx, 'pre'),
    timeoutMs: 2 * MIN,
    incomplete: (r) => !(r as { sent?: boolean; reason?: string }).sent && !/already sent|not a trading day|not configured/i.test((r as { reason?: string }).reason ?? ''),
  },
  {
    // End-of-day summary, 17:00-20:00 IST (after NSE usually posts provisional FII/DII data).
    id: 'digest-eod',
    due: dailyInWindow(17 * 60, 20 * 60, 10 * MIN),
    run: (ctx) => runDigest(ctx, 'eod'),
    timeoutMs: 2 * MIN,
    incomplete: (r) => !(r as { sent?: boolean; reason?: string }).sent && !/already sent|not a trading day|not configured/i.test((r as { reason?: string }).reason ?? ''),
  },
  {
    // Nightly snapshot at or after 02:00 IST, every day; retried every 30 min until it succeeds.
    id: 'backup',
    due: (now, s) => minutesOfDayIst(now) >= 2 * 60 && s.lastOkDate !== istDateStr(now) && since(s.lastStartAt, now) >= 30 * MIN,
    run: (ctx) => runBackup(ctx),
    timeoutMs: 2 * MIN,
  },
  {
    // Encrypted off-volume copy, at or after 02:10 IST, retried every 30 min until it succeeds.
    // Independent of the local snapshot: a destination outage never fails or repeats the local backup.
    // A no-op (counted as done) when no destination is configured.
    id: 'offsite',
    due: (now, s) => minutesOfDayIst(now) >= 2 * 60 + 10 && s.lastOkDate !== istDateStr(now) && since(s.lastStartAt, now) >= 30 * MIN,
    run: (ctx) => runOffsiteBackup(ctx),
    timeoutMs: 5 * MIN,
  },
];

function withTimeout<T>(p: Promise<T>, ms: number, id: string): Promise<T> {
  let t: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([p, new Promise<T>((_, rej) => { t = setTimeout(() => rej(new Error(`${id} timed out after ${Math.round(ms / 1000)}s`)), ms); })]).finally(() => clearTimeout(t));
}

export class Scheduler {
  private timer: ReturnType<typeof setInterval> | null = null;
  private running = new Map<JobId, Promise<void>>();
  private ticking: Promise<void> | null = null;

  constructor(private ctx: Ctx, private instance: string, private jobs: JobDef[] = JOBS) {}

  async start(intervalMs = 30_000): Promise<void> {
    const now = this.ctx.now().toISOString();
    await update<SchedulerState>(this.ctx.kv, KEYS.scheduler, EMPTY, (cur) => ({ ...EMPTY, ...cur, startedAt: now, instance: this.instance, heartbeatAt: now }));
    void this.tick();
    this.timer = setInterval(() => void this.tick(), intervalMs);
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    await this.ticking;
    await Promise.allSettled([...this.running.values()]);
  }

  /** One pass: heartbeat, then start every due job that is not already running here. */
  tick(): Promise<void> {
    if (this.ticking) return this.ticking;
    this.ticking = (async () => {
      try {
        const now = this.ctx.now();
        const state = await update<SchedulerState>(this.ctx.kv, KEYS.scheduler, EMPTY, (cur) => ({ ...EMPTY, ...cur, heartbeatAt: now.toISOString(), instance: this.instance }));
        for (const job of this.jobs) {
          if (this.running.has(job.id)) continue;
          if (!job.due(now, { ...EMPTY_JOB, ...state.jobs[job.id] })) continue;
          if (!(await this.claim(job, now))) continue;
          const p = this.execute(job).finally(() => this.running.delete(job.id));
          this.running.set(job.id, p);
        }
      } catch (e) {
        console.error('[scheduler] tick failed', e instanceof Error ? e.message : e);
      } finally {
        this.ticking = null;
      }
    })();
    return this.ticking;
  }

  /** Wait for jobs started by previous ticks (tests). */
  async drain(): Promise<void> {
    await this.ticking;
    await Promise.allSettled([...this.running.values()]);
  }

  private async claim(job: JobDef, now: Date): Promise<boolean> {
    let claimed = false;
    await update<SchedulerState>(this.ctx.kv, KEYS.scheduler, EMPTY, (cur) => {
      const js = { ...EMPTY_JOB, ...cur.jobs[job.id] };
      claimed = job.due(now, js);
      if (!claimed) return cur;
      return { ...EMPTY, ...cur, jobs: { ...cur.jobs, [job.id]: { ...js, lastStartAt: now.toISOString(), runs: js.runs + 1 } } };
    });
    return claimed;
  }

  private async execute(job: JobDef): Promise<void> {
    let ok = false;
    let error: string | null = null;
    try {
      const result = await withTimeout(job.run(this.ctx), job.timeoutMs, job.id);
      ok = !(job.incomplete?.(result) ?? false);
      if (!ok) error = `not completed: ${JSON.stringify(result).slice(0, 200)}`;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      console.error(`[scheduler] ${job.id} failed: ${error}`);
    }
    const end = this.ctx.now();
    await update<SchedulerState>(this.ctx.kv, KEYS.scheduler, EMPTY, (cur) => {
      const js = { ...EMPTY_JOB, ...cur.jobs[job.id] };
      return {
        ...EMPTY,
        ...cur,
        jobs: { ...cur.jobs, [job.id]: { ...js, lastEndAt: end.toISOString(), lastOk: ok, lastError: error, lastOkDate: ok ? istDateStr(end) : js.lastOkDate } },
      };
    }).catch((e) => console.error('[scheduler] could not record job end', e));
  }
}

export async function schedulerStatus(ctx: Ctx): Promise<SchedulerState & { enabled: boolean; healthy: boolean | null; note: string }> {
  const s = { ...EMPTY, ...(await read<Partial<SchedulerState>>(ctx.kv, KEYS.scheduler, {})) };
  const age = since(s.heartbeatAt, ctx.now());
  const enabled = ctx.cfg.schedulerEnabled;
  const healthy = enabled ? age < 3 * MIN : null;
  const note = !enabled
    ? 'Scheduler is off in this process (DESK_SCHEDULER=off or not production). Alerts, digests and backups do not run automatically.'
    : healthy
      ? 'Running on the Railway service.'
      : `No scheduler heartbeat for ${Number.isFinite(age) ? Math.round(age / MIN) + ' min' : 'ever'}. The service may be restarting or stopped.`;
  return { ...s, enabled, healthy, note };
}
