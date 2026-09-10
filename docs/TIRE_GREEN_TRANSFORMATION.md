# TIRE — GREEN transformation (implementation vs runtime)

This file records what this pass changed in code. Live scan timings belong
only in a row measured after restarting the process that actually serves
`/api/research/discover`.

## Code changes

- Historical structure from bounded M15 → IST session bars; no lookahead.
- Pipeline `PRE_BREAKOUT_COMPRESSION` only when multi-day compression + proximity exist.
- MAIN BLOCKER class `INSUFFICIENT_HISTORY`; KAYNES contract unusable still outranks it.
- PCR missing prior snapshot labeled `INSUFFICIENT_HISTORY`.
- Move context can use ATR% when historical ATR exists.
- `ScanSnapshotCache` on whole-market Stage 2 (OHLCV by calendar window; quotes by minute).
- Qwen schema tightened; `POST /api/research/explain`.
- UI: Discover-first, human labels, MAIN BLOCKER language, optional AI panel, auto whole-market scan on load.

## What still requires a live process

- Universe count and Stage 1/2 durations
- RELIANCE / KAYNES / NIFTY / Qwen browser UAT against the restarted app

## Live measurement (restarted process, 2026-09-10 IST, after hours)

`GET http://127.0.0.1:8010/api/research/discover` (blank symbols) on the
restarted GREEN-pass process:

| Fact | Value |
|---|---|
| Universe source | `upstox_instrument_master.NSE_FO_equity_underlyings` |
| Universe count | 210 |
| Stage 1 | 210 / 210 in **104.0s** |
| Stage 2 | 27 / 30 in **513.6s** (3 failures) |
| Cap | applied; 179 matching names not promoted |
| F&O ban | `FNO_BAN_STATUS_UNKNOWN` |
| Developing | 1 |
| Event-driven | 0 |
| Already moved | 0 |
| Needs data | 1 |
| Wall clock | **618.3s** |

This is **not** faster than the earlier ~4–5.5 minute in-session scans.
Stage 1 was slower than the previously measured ~10.5s (after-hours /
provider throttle / single worker). Snapshot reuse did not make this run
materially shorter. Do not claim a performance GREEN from this pass.

Uvicorn access-logs the discover request only when it **finishes**.
Single worker: a discover run blocks `/api/health` and Qwen probes.
