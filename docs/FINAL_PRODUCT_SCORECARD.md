# TIRE — Final Product Scorecard

Date: 15 September 2026. Branch: `phase-3-historical-validation`.

Each capability is scored **independently**, against what was actually
measured this sprint (live Upstox, a real completed full-universe scan, a
real browser session, real Qwen HTTP probes) — not against source
inspection. Percentages are deliberately absent: they would be invented
precision. Evidence for every row is in
`docs/FINAL_95_PRODUCT_COMPLETION_REPORT.md`.

| Capability | Status | Basis |
| --- | --- | --- |
| Live data | **GREEN** | Live Upstox probe 200 OK; full 210-symbol scan ran end to end against the real API |
| Data quality | **GREEN** | Freshness gating, `MARKET_CLOSED_LATEST_DATA` correctly applied all run; 1 symbol honestly failed as `INSUFFICIENT_HISTORY` rather than being silently dropped |
| Options intelligence | **GREEN** | Evidence matrix, chain/OI/IV analysis, convergence & CONFLICT states all exercised live (RELIANCE → CONFLICT, KAYNES → DATA_INSUFFICIENT) |
| Futures intelligence | **GREEN** | `FUTURES_STRUCTURE` selected as a real named pattern on live data (CROMPTON, DABUR) |
| Technical structure | **GREEN** | Real candle-derived levels thread into observations; S/R, VWAP, EMA, RSI all present |
| Sector context | **GREEN** | Sector Data GREEN in live status; relative-strength vs index available (`index_relative_strength_available: true`) |
| News/context | **CONDITIONAL** | Wired and fetched inside Stage 2, but News stream reports YELLOW; no independent second source |
| Early opportunity discovery | **GREEN** | Named pattern genuinely required (`early_opportunity.py:328`); 0 manufactured EARLY_SETUPs in the live scan |
| Whole-market scanner | **CONDITIONAL** | Full 210 universe screened (210/210 Stage 1) — but the Stage-2 survivor cap truncated **175** matching names, so only 30 were deep-analyzed. Honest and clearly surfaced, still a real coverage limit |
| Contract quality | **GREEN** | Full field set exposed (bid/ask/spread/OI/ΔOI/volume/IV/greeks/liquidity/decay/DTE); KAYNES correctly reported "contract is not usable for reliable options research" |
| Historical replay | **GREEN** | No-lookahead, reproducible, price-only observations real; ~308 s → ~11.2 s on the measured case, not regressed |
| Historical outcomes | **GREEN** | CONFIRMATION and INVALIDATION are genuinely separate; all five horizons on real trading sessions; `UNKNOWN` where no safe rule exists |
| Pattern aggregation | **GREEN (capability) / CONDITIONAL (evidence)** | Now reachable end to end — API + UI — and structurally incapable of emitting a rate. The *sample* is 6 named observations, 0 determined: honest, but proves nothing yet |
| Research journal | **GREEN** | "What TIRE knew then" and "what happened after" are separate records in separate stores; never rewritten |
| Decision memory | **GREEN** | Personal journal reused (no second system): observation, reasoning, expected confirm/invalidate, actual outcome |
| Qwen | **CONDITIONAL** | All five required cases pass over real HTTP, fail-open and bounded — but LM Studio is **down**, and at the measured ~0.13 tok/s this hardware cannot serve a 600-token response inside any sane budget. `QWEN HARDWARE LIMITED` |
| UX | **GREEN** | WHAT / WHY / MISSING / CONFIRM / INVALIDATE render on a real symbol page; technical detail stays expandable |
| Browser UAT | **CONDITIONAL** | HOME, DISCOVER, RELIANCE, KAYNES, NIFTY, EXPLAIN SIMPLY, HISTORY, PATTERN HISTORY all verified in a real headed browser with **zero console errors** — but a true 390 px viewport could not be exercised (the extension's window resize did not take effect); mobile verified only via responsive CSS and container structure |
| Performance | **CONDITIONAL** | Stage 1: **7.1 s for 210 symbols** (excellent). Stage 2: **468.9 s for 30 symbols** (~15.6 s/symbol wall at concurrency 6) — the real bottleneck, and the reason the survivor cap exists |
| Safety | **GREEN** | Zero order/execution paths repo-wide; runtime fail-closed lock; no tracked secrets, models, or debug artifacts; no shell/eval path |
| Provider redundancy | **RED** | `classify_provider_conflict()` exists and is correct, but **no live pipeline calls it** and no second credential exists. `/api/health` honestly reports `secondary_provider: "not_wired"` |

## Overall

### CONDITIONAL GREEN

TIRE performs the complete intended loop end to end on real market data,
and it does so without a single manufactured claim. It is genuinely
usable today as a personal Indian-market research backbone.

Three honest limitations keep it from unqualified GREEN, none of which is
a correctness or safety defect:

1. **Provider redundancy is absent (RED).** Single feed, no
   cross-validation in the live path. Blocked on a credential, not on code.
2. **Stage-2 depth is capped.** The full universe is *screened*, but only
   30 names are *deep-analyzed* per run because Stage 2 costs ~15.6 s per
   symbol. 175 matching names went unanalyzed in the measured run.
3. **Historical evidence is too thin to learn from yet.** The machinery
   for pattern learning is complete and correct; it has 6 named
   observations and 0 determined outcomes to learn from. That is a
   matter of elapsed time, not of missing capability.

None of these is hidden from the operator: each is stated on the surface
that depends on it.
