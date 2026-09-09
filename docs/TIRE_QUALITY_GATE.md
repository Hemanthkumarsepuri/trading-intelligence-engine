# TIRE — 95%+ Research Reliability & Performance Gate

This report covers the session that took TIRE from **CONDITIONAL GREEN**
(post real-authenticated browser UAT, `docs/TIRE_OPERATOR_UAT.md`) through
a dedicated 95%+ research-truth quality gate: fixing the real KAYNES-style
misleading-explanation defect generically, profiling and optimizing the
210-symbol daily scan, building a canonical research-truth test suite, and
re-running real authenticated browser UAT to verify the fixes live.

## 1. Current GREEN status

# **CONDITIONAL GREEN**

Not full GREEN, and this report will not claim it is. Reasons it is not
full GREEN, stated plainly:

- The daily-scan performance target (≤4 min) was **met in one measured
  run (218.9s) and missed in another (310.5s)** — see Section 5. Duration
  is provider-throttling-dependent and not yet a number TIRE can
  guarantee run-to-run.
- Sections 16–24 of the governing prompt (whole-market discovery
  categories without ranking, multi-timeframe research hierarchy, local
  historical-data layer, 5paisa provider abstraction + cross-checking,
  local Qwen 2.5 integration, personal decision journal, personal
  outcome analysis) are **not implemented this session** — see Section
  15 (Remaining limitations). These are real, acknowledged gaps, not
  hidden ones.
- The pre-existing `rank_candidates()` "TOP OPPORTUNITIES #1/#2/#3"
  shortlist (from an earlier phase) uses a deterministic, fully
  explainable tie-break tuple (decision tier → supporting-group count →
  liquidity tier → decay tier → required move → breakeven distance →
  headroom) with **no hidden numeric score, probability, or confidence
  percentage** — so it does not violate Section 17 — but it is still an
  ordinal ranking, which is in tension with Section 16's "no ranking, only
  categories" instruction for the *next* iteration of the scanner. Not
  changed this session (out of the committed scope below); flagged here
  rather than silently left as-is.

What **is** solid enough to call GREEN on its own:

- The KAYNES-style misleading-primary-explanation defect: fixed
  generically (not a KAYNES-specific patch) and re-verified live.
- The research-truth test suite: 105/105 (100%), 0 zero-tolerance
  failures.
- Full regression: 1841/1841 pytest, ruff clean, mypy --strict clean.
- Safety zero-tolerance items: 0 failures, including a real regression
  this session's own browser UAT found and fixed (Section 4).
- Scan coverage/freshness reporting: honest, per-run, never claims a
  7-minute scan happened at one instant.

## 2. 95% research-quality score

**100.0%** (105/105 scenarios) in `tests/research_truth/`. Full
methodology, per-category breakdown, and per-dimension mapping to
Section 29's required targets: see `docs/TIRE_QUALITY_SCORECARD.md`.
This is a **research-truth** metric (correct freshness/state/blocker/
provenance representation), explicitly **not** a profitability or
prediction-accuracy metric (Section 1/30) — no outcome-accuracy claim is
made anywhere in this report.

## 3. Critical-zero-defect results

**0 failures** across all zero-tolerance categories (broker execution,
false BUY/SELL, hidden stale directional evidence, volume directional
voting, fabricated timestamps, future-data leakage, missing-data-as-
confirmation, cash-context-as-hidden-evidence, false LIVE labeling,
contradictory primary/secondary explanations). See
`TIRE_QUALITY_SCORECARD.md` "Zero-tolerance categories" for the full list
and how each is enforced.

## 4. KAYNES blocker fix — before/after

**Before** (real UAT finding, `docs/TIRE_OPERATOR_UAT.md` Defect 2): the
top-level narrative talked about price/chain confirmation being
incomplete while the real, more fundamental blocking reason was contract
illiquidity — a user reading only the Quick View could reasonably
conclude the wrong thing was missing.

**Fix implemented:** `app/domain/options/research_blocker.py` — a new,
generic `BlockerClass` enum with a documented, deterministic precedence
(`DATA_INSUFFICIENT` > `CONFLICT` > `CONTRACT_UNUSABLE` >
`CORE_STREAM_STALE` > `NO_DIRECTIONAL_BIAS` > `EXTENDED` >
`REQUIRED_CONFIRMATION_MISSING` > `NONE`), and a `BlockerAssessment`
(`primary`, `secondary`, `missing_confirmation`) computed purely from
already-existing `DecisionResult`/freshness/`DevelopmentNarrative`
fields — no new evidence rule, no hidden score. Wired into
`OptionsIntelligenceReport.render_text()` and the dashboard's Quick View
(`app/api/static/index.html`).

**After** (real browser UAT, this session, live dashboard, real Upstox
data):

```
SYMBOL: KAYNES
RESEARCH STATE: NO_TRADE
PRIMARY BLOCKER: CONTRACT_UNUSABLE
no sufficiently liquid option candidate exists for this bias
```

This is now generic (`BlockerClass.CONTRACT_UNUSABLE`, computed from
`assessment.option_quality`/`liquidity_quality`), not a KAYNES-specific
string match — any symbol with the same underlying condition gets the
same correct primary blocker.

**A second, real defect found by this session's own re-verification
UAT** (not present in the original KAYNES fix, found while checking
RELIANCE/NIFTY/BANKNIFTY): when `CONFLICT` was primary and
`CONTRACT_UNUSABLE` was secondary, the secondary blocker's explanation
text was a verbatim, wrong copy of the `CONFLICT` explanation (root
cause: both branches reused `decision.reasoning`). This is exactly a
Section 4 "no contradictory explanations" violation, just in the
secondary slot. Fixed the same session, re-verified live:

```
PRIMARY BLOCKER: CONFLICT
evidence matrix convergence is CONFLICT -- no coherent bias to act on

Also true right now (secondary, not the main reason):
• CONTRACT_UNUSABLE: no sufficiently liquid option candidate exists for this bias
```

A permanent zero-tolerance regression scenario now guards this exact
failure mode (`tests/research_truth/test_research_truth.py`,
`safety.secondary_blocker_explanation_never_duplicates_a_different_blockers_text`).

## 5. Scan performance — before/after

| | Symbols (Stage 2) | Duration | Notes |
|---|---|---|---|
| Original baseline (`TIRE_OPERATOR_UAT.md`) | 30 | ~7 min (~420s) | Fully sequential Stage 2 |
| This session, fix #1 measurement | 30 | 218.9s (~3.65 min) | Bounded concurrency (`stage_two_concurrency=6`) |
| This session, follow-up browser UAT measurement | 30 | **310.5s (~5.2 min)** | Same code, same concurrency setting — real run-to-run variance |

Fix: `run_daily_research()` Stage 2 now runs with `asyncio.Semaphore`-
bounded concurrency (default 6) via `asyncio.gather()`, never unbounded
— see `docs/TIRE_SCAN_PERFORMANCE.md` for full root-cause analysis
(provider-side per-token throttling under concurrent load, not this
codebase's request shape) and why ≤2 min was rejected as unsafe.

**Honest target status:** ≤4 min was met in the first measurement and
missed by ~29% in the second, real, later measurement. The honest
conclusion (documented in `TIRE_SCAN_PERFORMANCE.md` Section 6) is
**"usually ≤4 min, occasionally up to ~5.5 min"** — a range, not a
guaranteed number — until a dedicated multi-trial timing study is done.
This report is not rounding that up to "target met."

## 6. Scan coverage

Real, measured, this session's browser UAT run:

```
Universe: 210 F&O equities
Stage 1: 210 / 210 evaluated
Stage 2: 30 / 30 evaluated
HIGH -- 210/210 symbols reliably evaluated. The full eligible universe was reliably screened this run.
Scan completed 0s ago, took 310.5s -- 30/30 symbols successfully analyzed
```

**100% coverage, 0 failed symbols** in the run measured for this report.
`ResearchScanSnapshot` (`app/domain/audit/research_models.py`) makes
partial failure visible by construction whenever it does occur (never
silently reported as "complete") — see `tests/integration/orchestration/test_daily_research.py::test_scan_snapshot_reports_honest_coverage_and_timing`.

## 7. Freshness

`ResearchScanSnapshot.per_symbol_freshness` (a `SymbolFreshnessRecord`
per symbol: `generated_at`, `latency_seconds`, `market_state`,
`succeeded`) plus the scan-level `started_at`/`completed_at`/
`duration_seconds` mean the dashboard can say (and does say) *"Scan
completed Xs ago, took Ys"* instead of implying a multi-minute scan
happened at one instant. Per-symbol freshness labeling
(`classify_freshness_label`) was independently verified never to claim
`LIVE` for stale/unavailable/provider-down data (zero-tolerance,
`tests/research_truth`).

## 8. Provider status

Only Upstox is wired end-to-end this session. **5paisa was not
inspected or integrated** — Section 20's `docs/PROVIDER_CAPABILITY_MATRIX.md`
and the common provider abstraction remain future work (see Section 15).
No cross-provider disagreement handling exists yet because there is only
one live provider.

## 9. Historical-data status

**Not implemented this session.** No local historical OHLCV persistence
layer (Section 19) exists yet; all historical analysis (trend, ATR,
relative strength, structure) still reads directly from
provider-fetched candles per-request, per the pre-existing architecture.

## 10. Chart status

Unchanged this session. The existing M15 chart and multi-timeframe
hierarchy work described in Section 18 (long-term/swing/short-term,
provider-native vs. locally-resampled labeling) was **not** built this
session — flagged as a real gap.

## 11. Qwen status

**Not evaluated this session.** No local Qwen 2.5 integration exists;
TIRE continues to operate fully deterministically without it, so its
absence has zero impact on any of the numbers in this report.

## 12. Personal journal status

**Not implemented this session** (Sections 23/24 — Personal Decision
Journal, Personal Outcome Analysis). Explicitly deferred per the
governing prompt's own ordering ("implement after the core scan/research
workflow is reliable") — this session's scope was the reliability work
that gates it.

## 13. Browser UAT — actual execution result

Real authenticated browser session against the live dashboard
(`http://127.0.0.1:8010/`, real Upstox account/token, market-closed
post-close window on 9/9/2026), performed with the `cursor-ide-browser`
tooling — not claimed without execution:

| Symbol | Research state | Primary blocker | Notes |
|---|---|---|---|
| KAYNES | `NO_TRADE` | `CONTRACT_UNUSABLE` — "no sufficiently liquid option candidate exists for this bias" | Defect A fix confirmed live |
| RELIANCE | `CONFLICT` | `CONFLICT` — "evidence matrix convergence is CONFLICT" | Secondary `CONTRACT_UNUSABLE` text bug found + fixed live (Section 4) |
| NIFTY | `CONFLICT` | `CONFLICT` | Futures price honestly reported `n/a (as of n/a)` when unavailable — no fabricated value |
| BANKNIFTY | `CONFLICT` | `CONFLICT` | Secondary blocker text re-verified correct after fix |
| Daily scan (210-symbol universe) | — | — | `210/210` Stage 1, `30/30` Stage 2, `HIGH` confidence, 310.5s duration, 0 failed symbols |

Quick View answered the ten required questions (research state, what's
developing, primary blocker, supporting evidence, conflicts, missing
data, confirm-if, invalidate-if, freshness, contract quality) for every
symbol tested. `TRADEABLE`/`WATCH`/`NO_TRADE` never rendered as an order
button or execution affordance anywhere observed. Provider (`Upstox`)
and per-source timestamps were shown for every price figure, including
the honest `n/a` case for NIFTY futures.

## 14. Tests

```
pytest:            1841 passed, 0 failed, 0 skipped
ruff check app tests:  All checks passed
mypy app --strict:     Success: no issues found in 142 source files
tests/research_truth/: 105/105 scenarios (100.0%), 0 zero-tolerance failures
```

## 15. Remaining limitations

- Scan duration is not yet a number TIRE can guarantee run-to-run (218.9s
  vs. 310.5s across two real measurements) — provider-throttling-bound,
  not codebase-bound.
- No 5paisa integration, provider capability matrix, or cross-provider
  disagreement detection (Section 20/21).
- No local historical-data persistence layer (Section 19).
- No multi-timeframe chart hierarchy beyond the existing M15 chart
  (Section 18).
- No local Qwen 2.5 evaluation (Section 22).
- No Personal Decision Journal or Personal Outcome Analysis (Sections
  23/24) — deliberately deferred.
- The pre-existing "TOP OPPORTUNITIES #1/#2/#3" shortlist is still an
  ordinal ranking (deterministic, explainable, no hidden score — but
  still a ranking), in tension with Section 16's "categories, not
  ranking" direction for the next scanner iteration.
- The research-truth suite (105 scenarios) is necessarily a sample of
  possible real-market conditions, not exhaustive — it should keep
  growing as new real defects are found (exactly as this session did
  with the secondary-blocker regression).

## 16. Next highest-value improvement

**Run a dedicated multi-trial scan-timing study** (5–10 real runs,
varying time of day) to replace the current single-point ≤4-min target
with an honestly-measured distribution (e.g., p50/p90/max), and decide
from real data whether `stage_two_concurrency` should be tuned per
session or left fixed. This directly resolves the one open item keeping
this report at CONDITIONAL GREEN rather than GREEN, and is a
prerequisite for trusting any future Section 16 "whole-market discovery"
UX that depends on scan latency feeling predictable to the operator.
