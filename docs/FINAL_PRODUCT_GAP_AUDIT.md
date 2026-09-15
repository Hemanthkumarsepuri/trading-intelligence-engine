# TIRE — Final Product Gap Audit

Date: 15 September 2026. Branch: `phase-3-historical-validation`.
Baseline: `46646ff` (P0 historical-outcome invalidation) on top of
`4123cd5` → `1fa6c63` → `226019e` → `07f3b09`.

This audit was produced by **reading the implementation**, running the
real gates, and probing the live system — not by trusting the previous
sprint's report. Where the previous report was right, this says so and
names the evidence; where it overstated, this says that too.

## 0. Verified starting state (measured, not assumed)

| Check | Result |
| --- | --- |
| `pytest -q` | **2019 passed**, 1 warning, 48.5s |
| `ruff check .` | **All checks passed** |
| `mypy app --strict` | **Success: no issues found in 159 source files** |
| Working tree | 2 modified files (uncommitted Section 8 segmentation work), 1 untracked scan artifact |
| Upstox live probe | **200 OK** — `NSE_EQ:RELIANCE last_price 1235.3` |
| LM Studio / Qwen probe | **unreachable** — `/api/qwen/health` → `QWEN_TIMEOUT` in 2215 ms |
| `.env` tracked? | No — `git check-ignore` confirms `.gitignore:12` |
| Secrets/models/screenshots tracked? | None (`git ls-files` sweep clean) |

`app/` is 32,376 lines across 159 modules; 186 test files.

The previous sprint reported "2002 tests". The real number is now 2019 —
the 17 extra are this working tree's uncommitted segmentation tests. The
claim was accurate for its own commit.

## 1. What is genuinely already done (verified, not taken on trust)

These were spot-checked against source and are **real**, so they are not
re-listed as gaps:

- **Section 6/7 historical outcome correctness — GENUINELY CLOSED.**
  `outcome_horizons.py` tracks CONFIRMATION and INVALIDATION as two
  independent outcomes. `_opposing_level_broken_through()` (confirmation:
  the obstacle in the thesis's favourable direction giving way) and
  `_supporting_level_broken_through()` (invalidation: the thesis's own
  floor/ceiling giving way on the wrong side) are separate functions with
  separate geometry. The Section 6 trap — "price crossing an opposing
  resistance is NOT invalidation" — is explicitly handled and regression-
  tested. Invalidation is scoped to `PRE_BREAKOUT_COMPRESSION` only and
  returns `UNKNOWN` everywhere else rather than manufacturing a rule.
- **Section 7 horizons.** All five (+30m/+1h/+1/+3/+5 sessions) exist;
  session horizons use `target_trading_session_date()` (real NSE
  sessions, never calendar days) targeting that session's real 15:30 IST
  close. `test_outcome_horizons.py` covers all 7 required cases including
  the whipsaw ("independent outcomes") case.
- **Section 12 scanner failure transparency — ALREADY BUILT.** The
  DISCOVER UI renders universe count, `stage1_successful/stage1_attempted`,
  `stage2_successful/stage2_attempted`, coverage classification, the
  survivor-cap truncation count with its honest "provider-safety limit,
  not a claim they were uninteresting" wording, and an expandable
  per-category failure breakdown. This is exactly what Section 12 asks
  for, and it predates this sprint.
- **Section 14 contract quality — ALREADY FIRST-CLASS.**
  `ContractAssessmentView` exposes strike, right, moneyness, LTP, bid,
  ask, `spread_pct`, OI, ΔOI, volume, IV, delta/gamma/theta/vega,
  `liquidity_grade`, decay verdict and `decay_viability_ratio`; DTE is on
  the API schema.
- **Section 31 UI null-safety — effectively closed.** A full `.toFixed`
  sweep found no unguarded call on a nullable field: the two candidates
  (`snap.duration_seconds`, `lv.kind`) are both **non-nullable in their
  Pydantic models** (`ResearchScanSnapshot.duration_seconds: float`,
  `LevelLine.kind: str`), so neither is a real crash path. The genuine
  bug of this class was found and fixed last sprint (line ~2187).
- **Section 32 safety — clean.** A repo-wide sweep for `place_order`,
  `cancel_order`, `modify_order`, `transaction_type`, `execute_trade`,
  broker order endpoints returns **zero hits** in `app/`. The lock is
  runtime-enforced, not merely documented:
  `Settings.assert_broker_execution_disabled()` raises at startup.
  `/api/health` reports `broker_execution: "impossible"`.

## 2. Gaps found

### P0 — blocks truthful/useful operation

**None.** The single P0 carried into this sprint (Section 6 invalidation
correctness) was genuinely closed in `46646ff` and is verified above. No
new correctness or safety defect was found.

### P1 — major product capability missing

**P1-1 — Pattern aggregation has no API and no UI. It is library-only.**
`app/orchestration/pattern_aggregation.py` is correct, pure and well
tested, but **nothing in `app/` imports it** — a grep for importers
returns only its own tests and docstring cross-references. There is no
`/api/...` route and no HISTORY panel section. Section 8 says "make it
genuinely useful"; Sections 13/29 put "HISTORY — what happened to similar
situations?" on the primary research surface. As shipped, the operator
cannot reach this capability at all. **This is the highest-value
closeable gap in the product.** → fix.

**P1-2 — Section 8 segmentation is uncommitted and also unexposed.**
`aggregate_by_pattern_segmented()` plus the three real segment keys
(direction / timing stage / evidence completeness) exist in the working
tree with 17 passing tests, but share P1-1's problem: no caller. →
finish and expose with P1-1.

**P1-3 — Sample-size and derivatives honesty is not surfaced (Section
25/9).** The persisted live store holds **41 observations**, but 38 were
written before the `pattern` field existed (`pattern: None`) and are
therefore silently excluded from aggregation; only 3 carry a pattern, one
each of `PRE_BREAKOUT_COMPRESSION`, `OI_MIGRATION`, `FUTURES_STRUCTURE` —
i.e. **n=1 per pattern**. Zero observations carry an invalidation level.
Any aggregation surface must state this out loud rather than presenting
three singletons as "historical validation". Section 25 is explicit: "Do
not pretend that 32 observations prove profitability." → fix as part of
P1-1's surface, including the `PRICE_HISTORY_AVAILABLE` vs
`DERIVATIVES_HISTORY_AVAILABLE` split.

**P1-4 — The whole-market scan has never actually been run to
completion on the full universe (Section 10).** The only artifact
(`docs/_whole_market_scan_20260913.json`) shows universe 210, Stage-1
survivors 30, deep-analyzed 30, **551 s**, with 157 names truncated by
the survivor cap. The previous sprint acknowledged not doing this. →
**running live now**, results recorded in the final report.

**P1-5 — Qwen's five required cases (Section 20) have never been
exercised against the real endpoint.** LM Studio is currently down, which
makes Case B (unavailable) the live reality — already verified clean
(`QWEN_TIMEOUT` returned in 2.2 s, bounded, non-blocking). Cases A/C/D/E
need explicit verification. → verify.

### P2 — useful enhancement

- **P2-1 — Provider redundancy is code-ready but not wired.**
  `classify_provider_conflict()` exists, is tested, and correctly refuses
  to average disagreeing prints — but **no live pipeline calls it**, and
  `/api/health` honestly reports `secondary_provider: "not_wired"`.
  `DhanProvider` (356 lines) is implemented; 5paisa is a status
  placeholder only. The blocker is **credentials, not code**: `.env`
  carries only `UPSTOX_ACCESS_TOKEN`. Wiring a second provider blind,
  without a credential to validate against, would produce untested
  plumbing that claims a redundancy the system does not have — worse than
  the current honest `not_wired`. See §3.
- **P2-2 — Stage-2 scan latency.** ~11–18 s/symbol at concurrency 6.
  Benchmarked in the final report; optimised only if a *verified*
  bottleneck appears (Section 26: never by reducing evidence, universe or
  freshness).

### DEFER — not necessary for the 95% target

5paisa endpoint build-out, GIFT Nifty, BSE dual listing, Level-2/tick
data, ML/prediction, broker execution, paid vendors, distributed
infrastructure, DB migration, full UI redesign, larger local models,
multi-agent orchestration. All explicitly out of scope per Section 39.

## 3. Section 16 determination — can 5paisa provide immediate redundancy?

**No, and it should not be attempted this sprint.** Section 16 asks for a
determination, not an implementation. The determination:

1. Redundancy requires a **second live credential**. The operator's
   `.env` has one token (Upstox). No 5paisa or Dhan credential exists, so
   nothing built against either could be validated against real data this
   sprint.
2. A second provider already exists in code (`DhanProvider`). If the
   operator ever wants redundancy, **Dhan is the shorter path than
   5paisa** — the adapter, normalizer and settings are already written;
   only `DHAN_CLIENT_ID`/`DHAN_ACCESS_TOKEN` are missing.
3. The conflict primitive is already correct and already refuses to
   average. The missing piece is purely the second feed.

Recommendation: leave `secondary_provider: not_wired` honest, document
the Dhan-first path, and score provider redundancy **RED** rather than
dress it up. Fabricating redundancy is precisely the failure mode this
product is built to avoid.

## 4. Work executed this sprint

Only P1 work that can be completed and genuinely validated:

1. **P1-1 + P1-2 + P1-3** — expose pattern aggregation as a real product
   capability: read-only API + HISTORY UI section, segmented, with
   explicit sample-size and derivatives-availability honesty.
2. **P1-4** — run the real full-universe scan; record every metric
   Section 10 asks for; audit the top results per Section 11.
3. **P1-5** — exercise Qwen Cases A–E.
4. Complete browser UAT matrix (Section 30), safety audit (Section 32),
   final gates, scorecard and report.

Nothing in this sprint weakens an existing correctness rule, invents
evidence, or replaces deterministic analysis with AI.
