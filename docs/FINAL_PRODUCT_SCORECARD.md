# TIRE — Final Product Scorecard

Date: 18 September 2026 (IST). Branch: `phase-3-historical-validation`.

Each capability is scored **independently**, against what was actually
measured this session — a real live Upstox whole-market scan, a real
45-symbol historical replay build, a real headed browser session at both
desktop and a genuine 387 px viewport, and real Qwen HTTP probes — not
against source inspection. Percentages are deliberately absent: they
would be invented precision. Evidence for every row is in
`docs/FINAL_MARKET_BACKBONE_RELEASE_REPORT.md`.

The previous edition of this file (15 Sep 2026) is superseded: three of
its rows were scored against numbers that no longer hold (Stage-2 cap of
30, 6 historical observations, an unverified mobile viewport).

| Capability | Status | Basis (measured this session) |
| --- | --- | --- |
| Live market data | **GREEN** | `GET /v2/market/status/NSE` 200 OK on the real token; two full 210-symbol scans completed end to end against the live API |
| Data quality | **GREEN** | Freshness gating applied all run (`MARKET_CLOSED_LATEST_DATA`); 0 failed symbols, 0 stale; 7 symbols honestly `DATA_INSUFFICIENT` rather than silently dropped |
| Options intelligence | **GREEN** | Chain/OI/IV/convergence exercised on 196 deep-analyzed symbols; 2 names rejected as `POOR_CONTRACT_QUALITY`, 141 as `NO_DIRECTIONAL_CONVERGENCE` |
| Futures intelligence | **GREEN** | Futures/basis inputs present per symbol; unchanged from the verified 15 Sep baseline |
| Technical structure | **GREEN** | Real candle-derived levels thread into every observation; the replay dataset's 2,610 rows each carry a real recorded confirm and invalidation level |
| Sector context | **GREEN** | Sector Data GREEN; `index_relative_strength_available: true`; 16 real NSE industries present in the historical dataset segmentation |
| News/context | **CONDITIONAL** | Wired and fetched inside Stage 2, News stream reports YELLOW, no independent second source. No historical news archive exists, so replay observations honestly carry `NEWS_DATA_UNAVAILABLE` |
| Early opportunity discovery | **GREEN** | Named pattern genuinely required; 23 EARLY_SETUP and 3 shortlisted out of 196 analyzed — no manufactured setups |
| Whole-market Stage 1 | **GREEN** | 210/210 screened in **0.78 s**; 0 truncated |
| Whole-market Stage 2 | **GREEN** | **196/196 Stage-1 candidates deep-analyzed, 0 deferred, 0 failed.** The fixed cap of 30 is gone; capacity is the provider's own published 30-min budget, which still had room for 566 more analyses after the run |
| Contract quality | **GREEN** | Full field set exposed; KAYNES still correctly reported as not usable for reliable options research |
| Historical replay | **GREEN** | 45 symbols × 124 sessions replayed with no lookahead; 139,497 bars in 1,228 s with 7 workers, 0 symbols failed |
| Historical outcomes | **GREEN** | CONFIRMATION and INVALIDATION remain separate facts, now with first-crossing timestamps; order decides the reference status, and an unresolvable same-bar double crossing returns `INSUFFICIENT_OUTCOME_DATA` rather than a guess. This fixed a defect that mislabelled 827 real episodes as failures |
| Pattern aggregation | **GREEN (capability) / CONDITIONAL (evidence)** | Reachable end to end — API + UI, with a live/replay source switch — and structurally incapable of emitting a rate. The sample is now **2,610 episodes, 2,280 determined** (was 6 named / 0 determined), but it is one pattern, price-only, over one 6-month window |
| Research journal | **GREEN** | "What TIRE knew then" and "what happened after" are separate records in separate stores; the dataset rows keep the same split and are browsable per observation |
| Personal learning loop | **GREEN** | Personal journal (no second system) plus the new COMPARE HISTORICAL OBSERVATIONS view, which shows the recorded rules and levels beside what price actually did |
| Qwen 2.5 | **CONDITIONAL** | All five operational paths verified over real HTTP, all fail safe; prohibited-claim detection materially hardened this session. The model itself is **hardware-limited**: 2 completion tokens in 43.8 s (~0.05 tok/s), so real generation times out and the deterministic explanation is what a user actually sees |
| Provider redundancy | **RED** | Unchanged and blocked on a credential, not on code. No 5paisa adapter, dependency or token exists; a Dhan adapter exists but is unwired and has no credential. `/api/health` and the UI honestly report `5paisa UNKNOWN` / `Dhan UNKNOWN` |
| UX | **GREEN** | WHAT / WHY / MISSING / CONFIRM / INVALIDATE render per candidate; coverage reads "210 requested → 210 Stage 1 → 196 candidates → 196 Stage 2 → 0 deferred"; a failing panel can no longer blank the page |
| Browser UAT | **GREEN** | HOME, DISCOVER, RELIANCE, KAYNES, NIFTY, EXPLAIN SIMPLY, HISTORY, PATTERN HISTORY and the new observations table all verified in a real headed browser, 0 JS errors |
| Mobile UAT | **GREEN** | Verified at a **genuine 387 px CSS viewport** with real media queries (not CSS inspection). Three real defects found and fixed; after: no horizontal page scroll, 0/102 buttons off-screen, 0 touch targets under 28 px, all 28 tables scrolling internally, 0 JS errors |
| Performance | **GREEN** | Whole-market scan **51.3 s** end to end (Stage 1 0.78 s, Stage 2 50.4 s, avg 1.46 s/symbol) for 196 deep analyses. A second run under concurrent model-loading load took 97.4 s — the honest variance band |
| Safety | **GREEN** | No order/execution path repo-wide; runtime fail-closed lock; no tracked secrets, model files or debug artifacts; no shell/eval path. Provider HTTP paths are now pinned to a read-only allowlist by an AST test |

## Overall

### CONDITIONAL GREEN

TIRE performs the complete research loop end to end on real market data,
explains why a name deserves attention, shows exactly what was and was
not analyzed, and now has a real historical sample to compare against —
without a single manufactured claim. It is usable today as a personal
Indian-market research backbone.

Two honest limitations keep it from unqualified GREEN. Neither is a
correctness or safety defect, and neither is hidden from the operator:

1. **Provider redundancy is absent (RED).** One feed, no
   cross-validation in the live path. `classify_provider_conflict()`
   exists and is correct but nothing calls it, because there is no second
   credential to call it with. Blocked on a credential, not on code.
2. **Qwen cannot actually generate on this hardware (CONDITIONAL).**
   Every failure path is safe and the deterministic explanation is
   complete, so nothing is lost — but the "available" path is, in
   practice, unreachable at ~0.05 tok/s.

Two further limitations are properties of the data rather than the
product, and are stated on the surfaces that depend on them:

- The historical sample is **one pattern**
  (`PRE_BREAKOUT_COMPRESSION`), **price-only**, over **one 6-month
  window**. No historical option chain or futures data exists to buy or
  fetch, so derivatives-dependent patterns remain unevaluable. The counts
  are descriptive history, not a validated edge.
- Stage 1 admits **196 of 210** names. Because every candidate is now
  deep-analyzed within budget, this costs coverage nothing today, but
  Stage 1 is a data-quality and eligibility filter far more than an
  attention filter. If the universe or the per-symbol cost grew, this
  would start to bind.
