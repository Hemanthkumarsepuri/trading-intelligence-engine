# GATE 1 — Full F&O live scan

Date/time: **2026-09-20 17:14 IST**.  
Branch: `phase-3-historical-validation`.  
HEAD at run: `4b40527`.  
Prerequisite: GATE 0 **PASS** (`docs/release/GATE_0_BASELINE_CERTIFICATION.md`).

## Status

**PENDING — MARKET CLOSED**

This is not FAIL. The gate cannot execute without an OPEN NSE session. Closed-session last prints were not used as a live scan.

## What was tested

```text
python -m scripts.certify_live_fno_scan --api https://api-production-983e.up.railway.app
```

Exit code **2**. Stderr:

```text
FULL F&O LIVE VALIDATION = PENDING — MARKET CLOSED
This script will not start a universe scan or relabel last-observed prints as live.
```

Stdout:

| Field | Value |
| --- | --- |
| `as_of_utc` | 2026-09-20T11:44:24.282654+00:00 |
| `health_status` | 200 |
| `session_window` | CLOSED |
| `is_trading_day` | false |
| `calendar_date_ist` | 2026-09-20 |
| `broker_execution` | impossible |

`POST /api/research/jobs/discover` was **not** called.

## Defects found

None. Session is honestly CLOSED.

## Fixes made

None. Harness already refuses non-OPEN sessions.

## Architecture checks (code, not a live run)

These do **not** convert this gate to PASS.

- Whole-market `run_daily_research()` uses `ScreeningConfig(survivor_cap=DEFAULT_WHOLE_MARKET_SURVIVOR_CAP)` with **`DEFAULT_WHOLE_MARKET_SURVIVOR_CAP = 1000`**.
- Default `ScreeningConfig.survivor_cap = 80` is only for callers that construct a config without that override.
- Historical Stage-2 cap of 30 is not the production default.
- Deferrals remain labelled `STAGE_2_DEFERRED_CAPACITY` / `STAGE_2_DEFERRED_RATE_BUDGET`.

## Live metrics (not claimed)

| Item | Result |
| --- | --- |
| Universe size | PENDING |
| Stage 1 | PENDING |
| Stage 2 | PENDING |
| Screener rows / research-state distribution | PENDING |
| Integrity sample (10 cases) | PENDING |
| Performance | PENDING |

## Tests

Gate 0 suite: 2167 pytest / ruff / mypy. No additional live-scan tests were invented.

## Production validation

Session CLOSED. No universe scan started. Latest job remains `status: NONE`.

## Evidence

Re-run the same command when `session_window == OPEN`. That is the only path to GATE 1 PASS.

## Next allowed gate

**NONE**

GATE 2 (live option contract matrix) must not start until GATE 1 is PASS.
