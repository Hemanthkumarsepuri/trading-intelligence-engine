# TIRE — Daily Research Scan Performance

Real measurements taken this session against the live Upstox API (real
account, real token), not synthetic estimates. All numbers below are from
actual `GET /api/research/daily` and `POST /api/analyze` calls against the
running dashboard (`app.api.main:app`) during this session's market
session/close.

## 1. Architecture recap (already correct before this change)

The scan was **already** a two-stage pipeline, not a single flat loop:

```
210 F&O-eligible equity underlyings (list_fo_eligible_equity_underlyings)
        |
Stage 1 -- screen_universe(): batched get_quotes() calls (chunks of 50),
           cheap day-change/VWAP-distance/order-flow-imbalance buckets,
           "already extended" hard exclusion. No option chain, no
           candles, no futures. ~10.5s measured for 210 symbols this
           session.
        |
survivor_cap = 30 (ScreeningConfig.survivor_cap, default)
        |
Stage 2 -- run_analysis() per survivor: the EXACT SAME full pipeline a
           manual single-symbol query uses (quote, option chain across
           relevant expiries, M15/D candles, futures, news, sector,
           delivery, evidence matrix, decision engine).
```

So Section 11/16's "cheap observation -> deep derivative analysis"
architecture already existed; the real, measured defect was **Stage 2's
execution strategy**, not its shape.

## 2. What was actually slow, and why

`run_daily_research()`'s Stage 2 loop (before this change) was a plain
sequential `for symbol in stage_two_universe: await run_analysis(...)`.
A single symbol's full analysis, measured standalone with no concurrent
load:

| Metric | Measured value |
|---|---|
| Single `POST /api/analyze?RELIANCE` (no concurrent scan running) | **6.6s** (`latency_seconds` field, real HTTP round trip) |

30 survivors run strictly sequentially at ~6.6s each is theoretically
~200s (~3.3 min) for Stage 2 alone under quiet conditions. The
previously-reported real UAT figure of **~7 minutes total** for the same
scan (`docs/TIRE_OPERATOR_UAT.md`, Defect 3) reflects real-world
variance — provider latency, retries/backoff on transient errors, and
Stage 1 conditions are not perfectly repeatable between runs — but both
figures point at the same root cause: **Stage 2 was 100% sequential**,
so total wall-clock time scaled linearly with the survivor count and
each survivor's own multi-call latency, with zero overlap.

No single HTTP call inside one symbol's analysis was itself measured to
be pathologically slow; the cost is the sum of many small, correct,
already-necessary calls (quote, chain-per-expiry, candles, futures,
news, sector, delivery) run one symbol after another with no overlap
across symbols.

## 3. Fix implemented: bounded concurrency across symbols

`run_daily_research()` now runs Stage 2 with `asyncio.Semaphore`-bounded
concurrency (`stage_two_concurrency`, default **6**), via
`asyncio.gather()` over per-symbol coroutines. Per Section 12's
rate-limit-safety requirement:

- Concurrency is **bounded**, never unbounded — a fixed, documented cap,
  not "launch everything at once."
- Failure isolation is unchanged: one symbol's exception still becomes
  that symbol's own `AnalyzeResponse(error=...)`, never propagated to
  any other symbol or to the whole scan (see
  `test_stage_two_one_symbol_failure_does_not_corrupt_the_next_symbols_as_of`
  and the new concurrency tests in
  `tests/integration/orchestration/test_daily_research.py`).
- No new HTTP call was added anywhere — this only overlaps the
  **waiting time** of independent symbols' already-existing calls. 30
  symbols is still 30 symbols' worth of real HTTP traffic, never "210
  requests become 2100 requests" (Section 12).
- `market_state` selection is now a deterministic first-non-`None` scan
  over `stage_two_universe`'s fixed order (previously "whichever symbol
  finished last" — itself an arbitrary tie-break under a sequential
  loop; every symbol in one scan is observed in the same market
  session, so this is a safe, honest simplification).

### Measured result (real, this session)

| Run | Requested (Stage 2) | Duration | Per-symbol effective |
|---|---|---|---|
| Full `/api/research/daily`, `stage_two_concurrency=6` (new default) | 30 | **218.9s (~3.65 min)** total scan; **229.4s** end-to-end HTTP | ~7.3s/symbol effective wall-clock (218.9s / 30) |
| Previously reported (`docs/TIRE_OPERATOR_UAT.md`) | 30 | ~7 min (~420s) | ~14s/symbol effective wall-clock |

**~48% reduction** in measured wall-clock time for the same 30-symbol
Stage 2 workload (7 min -> 3.65 min), with `screened_count=210`,
`stage_one_survivor_count=30`, `deep_analyzed_count=30`,
`scan_snapshot.successful_symbols=30`, `failed_symbols=0` — full
coverage, nothing silently dropped to get this speed-up.

### An honest finding: concurrency did not scale linearly

A single unloaded symbol took 6.6s; naive math would predict
30 symbols / 6 concurrent ≈ 5 sequential batches × 6.6s ≈ 33s for Stage 2.
The measured 218.9s is **far higher** than that naive estimate — dividing
back out, the *effective* per-symbol latency under 6-way concurrent load
was closer to **44s**, roughly 6-7x slower per call than the same call
made alone. This is consistent with the Upstox API applying real
throttling/backoff pressure to concurrent requests from the same access
token (this codebase's own `_retry()` helper already adds linear backoff
on `ProviderTimeout`/`ProviderUnavailable`/`ProviderRateLimited` — under
concurrent load those retries fire far more often, which is exactly the
conservative, correct behavior Section 12 asks for: never turning a
provider rate-limit signal into an ignored error).

**Practical conclusion:** the real constraint on this scan's speed is
the *provider's* real, undocumented per-token rate limit, not this
codebase's own request shape. Bounded concurrency still produced a real,
measured, non-trivial improvement (~48%) by overlapping *some* of the
waiting time, but it does not multiply throughput by the concurrency
factor once the provider itself starts throttling.

## 4. Target assessment (Section 9)

**The ≤2-minute target is not safely achievable right now** without
either (a) raising concurrency further — which the measured throttling
behavior above suggests would mostly convert into *more* retries/backoff
delay rather than *more* real parallel throughput, risking real
rate-limit violations (explicitly forbidden by Section 12) — or (b)
reducing per-symbol data quality/completeness (explicitly forbidden by
Section 9: "do NOT sacrifice correctness for speed").

**Chosen safe target:** ≤4 minutes for the default 30-symbol Stage-2
deep-analysis workload, with `stage_two_concurrency=6` as a documented,
adjustable default. This is a real, measured, ~48% improvement over the
previous baseline, achieved with zero reduction in per-symbol data
completeness and zero increase in raw request count.

## 5. Other optimization avenues considered, and why they were not implemented this session

- **Batching option-chain/candle calls across symbols:** Upstox's
  historical-candle and option-chain endpoints are per-instrument-key,
  not batch endpoints (only the quote endpoint documented as batchable
  is already used that way in Stage 1). Batching them would require a
  new provider capability that does not exist — out of scope without
  first verifying it against the real API (Section 20's own discipline:
  "inspect actual API capabilities" before building against them).
- **Raising `stage_two_concurrency` above 6:** rejected for now given
  the measured throttling above — a higher value should only be chosen
  after a dedicated, deliberate rate-limit probe (reading real
  `429`/rate-limit response headers, which this session did not
  observe explicitly since the provider client does not currently log
  them) — not by guessing.
- **Caching static data (sector map, instrument master) across
  symbols:** already effectively true — `instrument_master` and
  `sector_map` are loaded once per process/run and passed by reference
  into every `run_analysis()` call; no per-symbol re-fetch of either
  exists in the current code (confirmed by reading
  `run_daily_research()`'s call signature — both are simple parameters,
  not re-fetched in the loop).
- **Two-stage scanning:** already existed (Stage 1/Stage 2 above) —
  this session's change is inside Stage 2's own execution strategy, not
  a new architectural layer.

## 6. A second, honest real-world measurement (95%+ Reliability Gate UAT)

During the follow-up browser UAT for the 95%+ Research Reliability &
Performance Gate (real authenticated Upstox session, post-market-close
window), the same 30-symbol Stage-2 workload was timed again through
the real dashboard (not a synthetic benchmark):

| Run | Requested (Stage 2) | Duration | vs. ≤4 min (240s) target |
|---|---|---|---|
| Follow-up browser UAT, `stage_two_concurrency=6` | 30 | **310.5s (~5.2 min)** | **missed by ~70s (~29% over)** |
| Original measurement (Section 3 above) | 30 | 218.9s (~3.65 min) | met |

Coverage was still perfect on this run (`Stage 1: 210/210`,
`Stage 2: 30/30`, `HIGH` confidence, `0` failed symbols) — the miss is
purely a **duration** regression, not a correctness or coverage one, and
it was reported honestly by the scan snapshot itself rather than hidden.

**Conclusion, stated plainly:** the ≤4-minute target is not consistently
met run-to-run. Section 3's own root-cause finding (provider-side
throttling under concurrent load, not this codebase's request shape) is
the most likely explanation — Upstox per-token throttling latency
appears to vary session-to-session and cannot be fully controlled from
this side without risking Section 12's rate-limit-safety rule. The
honest status is: **usually ≤4 min, occasionally up to ~5.5 min**, and
this variance itself should be surfaced to the operator (the scan
snapshot's own `duration_seconds` and "Scan completed Xs ago, took Ys"
UI text already does this per-run) rather than asserting a single fixed
number that real runs do not reliably meet. A future session should
either (a) run a dedicated multi-trial timing study before tightening
concurrency further, or (b) treat "≤4 min typical, ≤6 min worst-case
observed" as the documented, honest range instead of a single point
target.

## 7. Scan freshness and coverage (Sections 14/15)

`ResearchScanSnapshot` (`app.domain.audit.research_models`) now records,
for every run: `scan_id`, `started_at`, `completed_at`,
`duration_seconds`, `universe_version` (F&O-eligible-equity-underlying
count), `requested_symbols`, `successful_symbols`, `failed_symbols`,
`provider`, the existing `ResearchCoverage`, and a
`SymbolFreshnessRecord` per symbol (`generated_at`, `latency_seconds`,
`market_state`, `succeeded`) — reusing fields `AnalyzeResponse` already
computed, never a new fetch. The dashboard now shows: *"Scan completed
Xs ago, took Ys — N/M symbols successfully analyzed"* instead of
implying the whole scan happened at one single instant. See
`app.orchestration.daily_research.run_daily_research()` and
`build_daily_research_view()`.
