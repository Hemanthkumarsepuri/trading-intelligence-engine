# Operating Model

Status: **Stub — created per 2026-08-27 architecture review.** Describes how the running system behaves, not how to deploy/on-call for it (that level of runbook detail is written once there's something deployed to run — this covers the behavioral contract only).

Cross-references: [Architecture](../architecture/ARCHITECTURE.md), especially the Addendum (data freshness classes, data quality gate, failure-mode matrix §25).

---

## Market Hours

The scheduler (`app/scheduler/`) is the single source of truth for "is the market open" — no other module independently decides this. It tracks, from a versioned NSE calendar data file:

- Pre-market
- Regular session (open → close)
- Post-close
- Weekends
- NSE holidays
- Expiry days (Thursday for most NSE F&O as of the last known convention — the calendar file, not this document, is authoritative since exchange conventions can change)

`ANALYSIS_INTERVAL` cycles (§ Architecture Addendum A2) only fire during the regular session by default. Pre-market and post-close behavior (if any analysis runs at all) is a config choice, off by default in Phase 1.

## Scheduler Behavior

Per Addendum A2: `REAL_TIME`/`SHORT_INTERVAL` collectors run on their own loops, independent of the `ANALYSIS_INTERVAL` timer, so a 5-minute decision cycle consumes already-fresh normalized data rather than fetching cold. `SLOW_REFRESH` data (instrument master, calendar) refreshes on a long cycle or at startup. `EVENT_DRIVEN` ingestion (news, provider-health transitions) triggers off events, not a timer.

## Data Freshness

Every data point carries `data_timestamp` / `received_timestamp` / `analysis_timestamp` and a derived `data_age` (Addendum A2). Staleness thresholds are per freshness class and are configuration, not hardcoded constants. Stale data is never presented to an analysis cycle as current — the `DataQualityGate` (Addendum A3) is the enforcement point.

## Provider Failure

Provider health is tracked per provider (`provider_health` table, §7). A provider with too many consecutive failures moves to `OPEN` (circuit breaker skipped) and is retried on a `HALF_OPEN` schedule. The registry (§8) fails over to the next configured provider for that data kind. If no provider is available for data the current cycle needs, the cycle resolves to `NO_TRADE_DATA_INSUFFICIENT` — it does not wait indefinitely, and it does not silently proceed on partial data.

## System States

Every `analysis_runs` row carries one status (§24):

| Status | Meaning |
|---|---|
| `COMPLETED` | Full pipeline ran; final decision (including a NO_TRADE decision) recorded |
| `NO_TRADE_DATA_INSUFFICIENT` | `DataQualityGate` failed |
| `NO_TRADE_GATE` | Deterministic trade gate rejected (risk/strategy/behavioral) |
| `NO_TRADE_LLM_CONFLICT` | LLM red-team flagged a major conflict with a deterministic candidate |
| `FAILED` | Unhandled error in some stage; caught at the orchestration boundary, cycle aborted, audited |
| `SKIPPED` | Scheduler determined no cycle should run (market closed, holiday, etc.) |

## NO TRADE Behavior

NO TRADE is the default and the majority-expected outcome, not an edge case to special-case around. Every NO TRADE resolution — regardless of which stage produced it — is audited with the same completeness as a trade candidate: what data was available, what each stage computed, and which specific condition(s) failed. "Why didn't it trade today" must always be answerable from the audit log alone, without needing to re-run anything.

## Emergency Shutdown

Two independent stop mechanisms, both effective immediately without waiting for a graceful cycle boundary:

1. **Config kill switch** — a single settings flag (e.g. `SYSTEM_HALTED=true`) checked by the scheduler before starting any cycle; when set, all `ANALYSIS_INTERVAL` cycles resolve to `SKIPPED` and no data collection beyond `SLOW_REFRESH`/health checks runs.
2. **Process stop** — since there is no order execution capability in any phase (`SAFETY_LOCK.txt`), stopping the process is always a safe shutdown; there is no open-order or in-flight-execution state to reconcile, because none exists.

There is deliberately no "pause and resume mid-cycle" state — a cycle either completes (with whatever outcome) or is aborted and audited as `FAILED`/`SKIPPED`; nothing persists as ambiguous partial state.

## Audit Requirements

Every cycle, regardless of outcome, produces a complete `audit_events` trail (§21): one event per stage, success or reject, tagged with the cycle's `analysis_run_id`. This is not optional logging — it is the mechanism that makes replay, "what did the system know at time T," and post-hoc review of any decision possible. No stage may skip writing its audit event, including stages that short-circuit the pipeline (Addendum A6's hierarchy) — a run that stops at `RISK FAILURE` still audits that specific stage's full input/output before halting.
