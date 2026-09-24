# Sprint 3.5 — Research Loop Certification & Scheduled Outcome Sweep

## Objective

Close the operational gap found in Sprint 3.4: the forward outcome sweep had **no automatic caller**, so T0 observations were captured but their +1/+3/+5 outcomes were never evaluated. Make the loop `live F&O → immutable T0 → scheduled sweep → +1 → +3 → +5 → ResearchProgression` automatic, deterministic, idempotent, restart-safe, fail-safe and auditable — by adding a **trigger only**. No outcome logic, observation model, availability contract or Watch behavior was changed.

## Certified Baseline

Branch `phase-3-historical-validation`, HEAD `2a9d70092c886477b4e16ed46e778efaf558ae9f` (Sprint 3.4), tree clean. Measured before changes: pytest **2320 passed**; Ruff clean; `mypy app --strict` clean (171 files); `tests/safety` 67; no-lookahead tests 53; `mypy app tests` 55 pre-existing errors.

## Architecture Inspection

Traced, not assumed:

- **Process model.** `Procfile`/`railway.toml`/`nixpacks.toml` run one `uvicorn app.api.main:app` process (no `--workers`); `restartPolicyType = "ON_FAILURE"`, health check `/api/health`. Deploys and crashes restart the process; nothing in memory survives.
- **Lifespan.** `real_lifespan` in `app/api/main.py` wires provider, instrument master and JSONL repositories into `app.state` once; tests substitute a no-op lifespan.
- **Scheduler infrastructure.** None is used. `apscheduler` and `redis` appear in `requirements.txt`/`pyproject.toml` but are imported nowhere. There is no periodic task anywhere; the only background pattern is `research_jobs` (a user-triggered discovery job run on a worker thread with its own event loop and HTTP client so `/api/health` stays responsive).
- **Existing callers of the sweep.** None (`sweep_due_research_outcomes`/`query_observations_due_for_sweep` were only called by tests).
- **Persistence.** Append-only JSONL under `TIRE_DATA_ROOT`; checkpoint writes are idempotent (`save_checkpoint_once`, Sprint 3.4).
- **Sweep behavior found.** `sweep_all_due_research_outcomes` was all-or-nothing on an unexpected exception and returned only the new checkpoints (no counts).

## Scheduler Architecture Decision

A small **lifespan-owned asyncio trigger loop** (`app/orchestration/outcome_scheduler.py`), no third-party scheduler:

```
real_lifespan -> start_outcome_sweep(app) -> OutcomeSweepScheduler loop
   every 900 s (first tick 30 s after startup):  run_once()
      -> runner(as_of = real clock)   [worker thread, own event loop + HTTP client]
      -> sweep_all_due_research_outcomes_detailed()
            -> query_observations_due_for_sweep -> canonical due rule -> canonical outcome engine
            -> save_checkpoint_once  (idempotent)
```

`run_once()` is the one function the production loop, tests and any manual invocation share. It never raises (except cancellation), passes only the real clock as `as_of`, and contains no research logic (an AST test asserts it imports/references none of the due, outcome or persistence functions).

## Why This Trigger Mechanism

- The deployment is a single process, and the correctness state lives in files, so the trigger only needs to be *eventually* invoked — "what is due" is recomputed from persisted observations and checkpoints on every tick. A missed tick, crash, redeploy or downtime is recovered by the next tick; nothing in memory is needed for correctness, only for observability.
- APScheduler/Celery/Redis would add a dependency and a second source of truth for a job this small; the unused `apscheduler`/`redis` entries were deliberately not adopted.
- The existing daily-research cycle is user-triggered (`/api/research/discover`), not a schedule, so there was no cadence to piggy-back on.
- The sweep runs on its own single worker thread (same isolation pattern as the discovery job) so slow provider calls cannot stall the API.

## Railway / Restart Model

Treat the loop as disposable. On startup the first tick fires after 30 s and finds everything that came due while the process was down (tested: down through +1 and +3 → both created on the first tick after restart). A process restart mid-sweep leaves either a persisted checkpoint (never redone) or nothing (retried). Caveat, not solved here: checkpoint durability requires `TIRE_DATA_ROOT` to be on a persistent volume — the same requirement every journal in the app already has.

## Due Semantics

Unchanged and not duplicated. A horizon is due at the target session's **close** (Sprint 3.4: `horizon_close_utc`); the scheduler only asks the outcome engine. Tested with an injected clock: ticks at 09:30 IST, 15:29 IST, weekends and holiday-shifted sessions create nothing; a tick at 15:30 IST creates +1; a mutation that fires due-ness six hours early fails 2 tests. The scheduler never passes a caller-chosen or future `as_of` (mutation: +10 days fails 13 tests).

## Overlap Protection

An `asyncio.Lock` in the scheduler: a tick arriving while a sweep runs is skipped and counted (`overlaps_skipped`). It is process-local **by design and is not a distributed lock**. Two processes on the same data directory would still be safe against *duplicates* because persistence refuses a second checkpoint per (observation, horizon) (tested with two repository instances on the same files); cross-process atomicity of the JSONL files remains the known, deferred limitation.

## Idempotency

Layered: the sweep skips horizons that already have a checkpoint; `save_checkpoint_once` refuses a second one; a persisted checkpoint is byte-for-byte unchanged by later ticks, even when later data differs (tested with violent post-+1 data). Repeated ticks and repeated restarts leave exactly one line per horizon.

## Retry Semantics

- **Persisted checkpoint** (including one recorded as insufficient): final, never recomputed or upgraded — tested (data appearing later does not change an insufficient +1).
- **Evaluation failed before persistence** (re-analysis error): nothing is written, the horizon is reported `horizons_due_unresolved`, health is `DEGRADED`, and the next tick retries it (tested: 503 → nothing persisted → next tick succeeds). No infinite in-tick retry loop and no invented data.

## Failure Isolation

`sweep_all_due_research_outcomes_detailed` isolates each observation: an unexpected exception for one is recorded (exception *type* only) and the rest are still processed (tested). A whole-sweep exception is caught in `run_once`, status becomes `FAILED`, the loop continues and the next tick can recover (tested). Nothing a failure does can touch T0: the sweep only reads observations.

## Observability

Structured log line per sweep plus counters in `status_payload()`: last attempt / last success time, attempts, overlaps skipped, consecutive failures, last error **type** (no message, no stack — tested with a secret in the exception text), and the last result: observations total / examined / still awaiting / failed, horizons created / created-insufficient / already complete / not yet due / due-unresolved, and total data-insufficient horizons.

## Health/Status

`GET /api/health` gains `outcome_sweep`. `health` describes the **trigger**: `NOT_STARTED`, `NOT_CONFIGURED` (no provider), `DISABLED`, `NEVER_RAN`, `OPERATIONAL`, `DEGRADED` (an observation failed or a due horizon could not be evaluated), `FAILED`. `outcomes_complete` is a separate field and is `true` only when no persisted observation is still awaiting a horizon **and** no persisted horizon lacked price data; it is `null` when nothing has run or nothing is persisted. A sweep having run is never reported as outcomes complete (tested: everything captured but one insufficient horizon → `outcomes_complete: false`). Configuration: `OUTCOME_SWEEP_ENABLED` (default true), `OUTCOME_SWEEP_INTERVAL_SECONDS` (default 900, min 60).

## Manual Trigger

**Not added.** A state-changing sweep endpoint would be an unauthenticated mutation route, opening the known API-authentication gap that this sprint must not solve; a test asserts no sweep route exists. Read-only status is on `/api/health`; recovery is a restart (first tick after 30 s) and certification uses `run_once()`.

## T0 Immutability

Byte-level: the observations file is identical after ticks at +1, +3, +5 and three days later with violent data; outcomes live in a separate file; reloaded T0 equals the original (expiry, strike, option type, direction, availability, provenance). A mutation that makes the sweep rewrite the observation fails 6 tests.

## Outcome Persistence

`research_outcome_checkpoints.jsonl` survives restarts (mutation: losing it fails 12 tests); after a full +1/+3/+5 run the persisted bytes are deterministic across two independent runs; `query_all_checkpoints` (new, read-only) supports the counts.

## Historical Replay Regression

Replay output remains byte-identical to the Sprint 3.1 baseline (golden test) and the replay suites pass.

## Forward Capture Regression

The 31 Sprint 3.3 tests pass unchanged.

## Watch Regression

Untouched; all watch tests pass (99).

## No-Lookahead Verification

The scheduler supplies only the real clock; a later tick with future data leaves an earlier checkpoint identical (also compared against an on-time sweep field by field); the +5-day-later scenarios cannot alter +1. Mutations: unbounded horizon window fails 4 tests; scheduler passing a future `as_of` fails 13. The 53 pre-existing lookahead tests pass.

## Adversarial Tests

Each mutation was applied, the suites run, and the file restored: scheduler never invokes the sweep (19 fail); date-based due check (2); overlap guard removed (1 — after adding a timeout so it fails instead of hanging); checkpoint dedupe removed (1), and dedupe + sweep-skip both removed (9); checkpoints lost across restart (12); sweep rewrites T0 (6); horizon window unbounded (4); holidays bypassed (2); due-ness moved earlier (2); future `as_of` (13). Note: removing only the sweep's "skip existing" check is *not* caught — by design, `save_checkpoint_once` is an independent second layer.

## Test Results

Full pytest **2346 passed**, 0 failed (2320 + 26). Ruff clean. `mypy app --strict` clean (172 files). `mypy app tests` 55 = baseline. Safety 67; lookahead 53; LLM + broker lock 33; watch 99.

## Safety Verification

No order/broker/LLM code added or reachable from the scheduler (AST test; broker-lock and LLM suites pass). No prediction, profit, win-rate or ranking. `assert_broker_execution_disabled` still runs first in the lifespan.

## Deployment Considerations

Start: `start_outcome_sweep` in the lifespan after provider setup (never raises; records `NOT_CONFIGURED`/`DISABLED` instead). Stop: `stop_outcome_sweep` cancels the loop task, awaits it (no orphan task) and shuts its worker thread down without waiting (`cancel_futures`); a sweep already executing on that thread finishes its bounded network calls in the background and cannot block process exit beyond them. Exceptions never escape the loop. One process is assumed; see Overlap Protection for what is and isn't guaranteed beyond it.

## Live Certification Status

**LIVE CERTIFICATION NOT PERFORMED.** The market is closed (evening, POST_MARKET) and no Upstox credentials are available in this environment; no live observation was manufactured. Procedure for the first real open-session certification: (1) deploy with `UPSTOX_ACCESS_TOKEN`, a persistent `TIRE_DATA_ROOT`, and `OUTCOME_SWEEP_ENABLED=true`; (2) during an OPEN session run a discovery scan for one explicit symbol so a forward observation is captured; confirm it via `GET /api/research/{id}/capture` (`verified: true`); (3) confirm `GET /api/health` → `outcome_sweep.health` = `OPERATIONAL` after the first tick; (4) after the next session's close (15:30 IST) confirm the +1 checkpoint appears via `GET /api/research/{id}/outcome` (`horizons[0].state = AVAILABLE`) within ~15 minutes, that the observation JSONL is byte-identical, and that repeating/restarting creates no duplicate; (5) repeat for +3 and +5.

## Known Limitations

- Persistence is process-local for locking; multi-process cross-atomicity of JSONL files is unaddressed (deferred).
- Durable checkpoints need a persistent data volume on Railway.
- State-derived checkpoint fields are only valid when a tick lands between the horizon close and the next session open; the 15-minute cadence makes that the normal case, downtime makes it not (documented on the checkpoint via `captured_late`).
- A transient candle-fetch failure inside an otherwise successful analysis is still recorded as an insufficient horizon (append-only); a wholly failed analysis is retried.
- Health reflects this process's memory: after a restart it reads `NEVER_RAN` until the first tick.
- Counts about "insufficient" cover persisted checkpoints only.

## Deferred Work

API authentication (and therefore a safe manual trigger), SYSTEM_HALTED, JSONL cross-process/torn-write hardening, dependency cleanup (including the unused `apscheduler`/`redis`), aggregation segmented by evidence availability, and the live certification run itself.

## Definition of Done

- [x] Sweep invoked automatically by a lifespan-owned trigger; scheduler is a trigger only
- [x] Due-ness and outcomes remain the outcome engine's; no future `as_of`
- [x] Idempotent, restart-safe, missed-tick and downtime recovery, overlap-safe
- [x] Failures isolated and reported honestly; no false green
- [x] T0 byte-identical; outcomes in a separate persisted file
- [x] Mutation tests confirm the guards actually bite
- [x] Full pytest, Ruff, mypy strict, safety, lookahead all green
- [x] One focused commit, pushed normally

## Final Commit

The single commit containing this document, `feat: automate research outcome sweep`, on top of `2a9d700`. A commit cannot embed its own hash; see `git log` on `phase-3-historical-validation`.

## Final Status

**COMPLETE — LIVE CERTIFICATION NOT PERFORMED.** Explicitly:

- No new market-data provider was added.
- No synthetic data was introduced.
- No broker execution was introduced.
- No prediction was introduced.
- No profitability metrics were introduced.
- No win-rate metrics were introduced.
- No ML was added.
- No pattern ranking was added.
- No historical options provider was added.
- No historical futures provider was added.
- No historical news provider was added.
- Sprint 3.6 was **NOT** started.
