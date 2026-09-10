# TIRE — GREEN GATE (this pass)

## KNOWN (verified in code)

- Whole-market GET `/api/research/daily` and `/discover` are still synchronous (compat/tests).
- Interactive Discover uses `POST /api/research/jobs/discover` + poll latest.
- Stage 1 screens quotes in batches; Stage 2 is bounded concurrency (default 6) with `ScanSnapshotCache`.
- Historical M15 lookback is ~10 calendar days; `<5` completed sessions → `INSUFFICIENT_HISTORY`.
- Qwen explain can hang LM Studio; health now hits `/models` with a 2s timeout.
- 5paisa is not wired.
- Home previously auto-started a whole-market scan; that is removed.

## UNKNOWN until live restart

- Exact Stage 1 / Stage 2 wall times on this machine after the job architecture.
- Whether LM Studio completes a tiny generation within 8s.

## BROKEN (addressed here)

- Auto-scan on load.
- Qwen health = full generation.
- Discover occupying the HTTP request for ~10 minutes.

## SLOW

- Stage 2 option chain / news / futures per promoted name (dominant cost).
- Stage 1 after hours still depends on quote-batch latency.

## OPTIONAL

- Local Qwen. GREEN does not require a working GPU model.
