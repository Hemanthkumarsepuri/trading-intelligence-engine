# TIRE Operator UAT — Real Authenticated Dashboard Test

This is a real browser UAT against the live dashboard (`uvicorn app.api.main:app`, `http://127.0.0.1:8000`), using the real, already-configured `UPSTOX_ACCESS_TOKEN` — not a description of expected behavior, not a unit test, not a claim made from reading code. Every quoted string below was read directly out of the live DOM during this session (2026-09-09, ~15:15–15:30 IST, market open) via `cursor-ide-browser`. Screenshots and raw panel text were captured and are quoted verbatim where relevant.

**Prior known limitation being closed here:** `docs/TIRE_GREEN_READINESS.md` §1 stated "browser UAT was not executed against a live authenticated dashboard in this session." This document is that execution.

## What was actually tested

| # | Symbol | Category (per the required test mix) | Method |
|---|--------|----------------------------------------|--------|
| 1 | RELIANCE | Highly liquid large-cap F&O stock | Full manual workflow (Steps 1–13) via the real ANALYZE box |
| 2 | KAYNES | Volatile mid-cap, active options, but with a weaker-liquidity contract outcome | Full manual workflow |
| 3 | NIFTY | Index, highly active options, weekly expiry | Full manual workflow — this is what surfaced the futures-gap defect below |
| 4 | M&M | Surfaced by the real Daily Market Research scanner (not hand-picked) | Read scanner output only, did not re-run the full manual workflow |
| — | 210-symbol F&O universe | Full-universe scan (`RUN RESEARCH` with a blank symbol box) | Timed, to get a real performance number |

Also exercised: `RUN RESEARCH` (Daily Market Research full-universe scan), auto-refresh, "MORE DETAILS / EXPERT VIEW" expansion, chart SVG rendering, watch-next panel, and the underlying multi-source price-consistency check.

**Not exercised in this session** (out of scope for what already exists, or requires conditions not present today): IPO Intelligence flow, WATCHLIST compare, LOAD HISTORY / research-outcome checkpoints (none due yet today), Qwen (not wired into this codebase at all yet — see Gap Analysis), and a genuinely stale/degraded/closed-market session (today's session was live).

## UAT Table

| Symbol | Research State | What developing? | Evidence clear? | Confirmation clear? | Invalidation clear? | Freshness clear? | Contract analysis useful? | Problems |
|---|---|---|---|---|---|---|---|---|
| RELIANCE | CONFIRMATION_PENDING | Yes — named pattern `FUTURES_STRUCTURE`, narrowing futures premium, plainly worded | Yes — bearish PCR-change + global-headwind both shown with real numbers | Yes — explicit CONFIRM IF text | Yes — explicit INVALIDATE IF text + a numeric level (1300) | Yes — per-stream freshness table, M15 correctly marked STALE while quote/chain stay usable | Yes — CE/PE both show LTP, spread, OI, ΔOI, volume, IV, delta, liquidity=EXCELLENT, decay=FAVORABLE | None material |
| KAYNES | CONFIRMATION_PENDING | Yes — `FUTURES_STRUCTURE`, widening futures discount | Yes | Yes | Partial — invalidation level (3800) shown, but "no actionable candidate was selected" so the condition text is a placeholder, not a real invalidation rule | Yes — quote flagged DEGRADED at 2m19s age, correctly | Real finding: "no sufficiently liquid option candidate exists for this bias" — the engine correctly refused to force a contract, but the top-line "what is missing" text still generically says "price structure and/or option-chain confirmation are incomplete," which is misleading for this specific case (see Defect 2) | See Defect 2 |
| NIFTY | CONFLICT | Correctly reported as `NONE` — "absence of a named pattern is not a directional conclusion" | Yes | Yes (CONFIRM IF re-stated for CONFLICT) | Yes | Yes | N/A (CONFLICT correctly routed to NO_TRADE, no contract pushed) | Real finding: Futures panel said only "no futures resolved for this underlying" with **zero reason**, even though the engine had already computed and logged the exact reason internally — see Defect 1 (fixed during this session) |
| M&M (via scanner) | — | "the preferred direction is supported by the evidence matrix, but the real M15 regime, trend, and VWAP do not yet confirm it" | Yes | Yes (M15/VWAP confirmation named explicitly) | Implicit via watch-next, not restated per-candidate on the scanner card itself | Badge said `STALE` at the top of the whole scan (see Defect 3 — scan duration) | Not evaluated in this pass (would require opening M&M's own analysis) | See Defect 3 |

## Ten-question answer, symbol by symbol (RELIANCE, the cleanest real case)

1. **What is developing?** "Futures basis vs spot changed vs a comparable prior observation" — named pattern `FUTURES_STRUCTURE`. Answerable directly from the Research Context panel, first thing on the page.
2. **Why is it developing?** "futures premium to spot is narrowing — conventionally read as cooling participation, unconfirmed." Present, and correctly hedged ("unconfirmed").
3. **What evidence independently supports it?** PCR(OI) change (0.79→0.61, corroborated by CE OI +29.9% vs PE OI +0.5%) + Global Context (4/4 inputs headwind). Both named, both with real numbers, both explicitly labeled "unconfirmed"/"context."
4. **What evidence conflicts?** The CE-vs-PE panel explicitly states "0 independent group(s) bullish, 2 bearish" — the absence of conflict is itself stated, not left implicit.
5. **What is stale/missing?** M15 candles explicitly marked STALE with a list of exactly which downstream calculations were withheld (`m15_trend`, `rolling_vwap_position`, `intraday_regime`, `breakout_confirmation`). This is a genuinely good, specific answer — not a vague "some data is old."
6. **What would confirm it?** "Current M15 structure and chain positioning remain compatible with the basis change." Present as its own labeled line.
7. **What would invalidate it?** "Basis change reverses on a comparable observation, or independent groups CONFLICT," plus a numeric underlying level (1300, tied to real CE OI concentration).
8. **Is the underlying already extended?** Not explicitly flagged as EXTENDED for RELIANCE (day move was small) — this is correct behavior (EXTENDED state only fires ≥6% day move per the documented precedence), but there is no on-screen answer to "how far are we from that threshold?" for a stock that is NOT extended. This is a minor usability gap (Medium, not High — see Gap Analysis).
9. **Is the option contract suitable?** Yes, directly: LTP, spread%, OI, ΔOI, volume, IV, delta, liquidity tier (EXCELLENT), decay tier (FAVORABLE), DTE (20 days) are all present for both CE and PE, with contract quality explicitly separated from directional edge ("DIRECTIONAL EDGE and CONTRACT QUALITY are different questions").
10. **Enough info for a disciplined decision?** For RELIANCE: yes. A real user could read the Quick View top-to-bottom and have research state, why, evidence, conflict, missing data, confirm/invalidate, and contract quality in under a minute, without leaving the page. For KAYNES/NIFTY: partially — see Defects 1 and 2.

## Defects found (real, evidence-based)

### Defect 1 — Futures panel gave no reason when it had one (FIXED this session)

**Where:** `app/api/static/index.html` (Futures/Global Context panel), `app/orchestration/visual_data.py` (`FuturesVisual`).

**What happened:** Querying NIFTY (a real index with a weekly options expiry) showed:
> `Futures — no futures resolved for this underlying`

with no explanation. The engine, however, had already computed and stored the exact reason in `report.data_warnings`:
> `"no futures contract found for NIFTY at expiry 2026-09-15 (may be a weekly-only expiry with no matching monthly futures contract)"`

That sentence existed only inside the 24,000+ character "Detailed Audit Report" text block, behind a collapsed `<details>` element — the operator had to know to open "MORE DETAILS / EXPERT VIEW" and search a wall of text to find the same fact already sitting right next to the "no futures resolved" line they were looking at.

**Category:** This is exactly the kind of "TIRE forces mental integration" gap called out in the request — except worse: the information wasn't just scattered, it was effectively undiscoverable without already suspecting the answer.

**Fix implemented:** `FuturesVisual` now carries a `no_futures_reason` field, populated from the already-computed `report.data_warnings` (no new computation, no invented text — copies the exact existing sentence). The dashboard's Futures panel renders it as a second line ("Why: …") directly under "no futures resolved for this underlying." Verified live against real NIFTY data after the fix:
> `Futures — no futures resolved for this underlying — Why: no futures contract found for NIFTY at expiry 2026-09-15 (may be a weekly-only expiry with no matching monthly futures contract)`

Test added: `test_futures_visual_surfaces_the_real_no_futures_reason` in `tests/integration/orchestration/test_visual_data.py`. Full regression: 1721 passed, Ruff clean, mypy --strict clean (141 files).

### Defect 2 — "What is missing" text is generic even when the real reason is contract liquidity, not price structure (HIGH PRIORITY, not fixed this session)

**Where:** `app/domain/options/development.py` (`DevelopmentNarrative.what_is_missing`), rendered via `options_intelligence_report.py`.

**What happened:** For KAYNES, the top-line "What is missing" under DEVELOPING OBSERVATION said:
> "Price structure and/or option-chain confirmation are incomplete."

But the Final Assessment panel's real WHY, for the same analysis, was:
> "no sufficiently liquid option candidate exists for this bias"

These are two different questions answered inconsistently: the top-line narrative talks about *price/chain confirmation*, while the actual blocking reason is *contract liquidity*. A user reading only the top of the page (as the product's own information hierarchy encourages) would form the wrong mental model of what to wait for.

**Why not fixed this session:** `what_is_missing` is generated per-`DevelopmentPattern` (`FUTURES_STRUCTURE`/`OI_MIGRATION`/`RELATIVE_STRENGTH`) independently of contract-liquidity gating, which happens later in the pipeline. Reconciling the two requires either (a) making the development-narrative layer aware of the contract-gating outcome, or (b) demoting/qualifying the narrative's "what is missing" text whenever a downstream liquidity/contract gate is the actual controlling reason. Given "do not blindly add features," this needs a deliberate small design decision (which field wins, and how to word the disagreement honestly) rather than a rushed one-line patch — recommended as the next P0 fix.

### Defect 3 — Full-universe Daily Research scan takes minutes, not seconds (HIGH PRIORITY, documented not fixed)

**Measured directly this session:** Clicking "RUN RESEARCH" with a blank symbol box (default universe = 210 real F&O equities) took approximately **7 minutes** wall-clock from click to rendered result (page snapshot timestamps: click ~15:47:51 IST wall time in this session's own logs → results visible ~15:54:46). A second, concurrent direct call to the same endpoint was still running past 3 minutes when abandoned, suggesting requests do not run with enough concurrency/parallelism to make a second overlapping scan practical, and that provider-side rate limiting and/or per-symbol serial fetches dominate the runtime.

**Why this matters for the actual product goal:** Section 33's "what deserves my attention today" market-overview workflow is supposed to be the entry point to a research session. A 7-minute wait before that question can be answered at all is a real, measured usability problem for "sit down and research the market" — not a hypothetical one. This is squarely a Section 30 (Performance/Cost) finding: **measure first, then decide whether concurrency (not new infrastructure) is the fix** — increasing the pipeline's internal concurrency for independent per-symbol Upstox calls is the most likely lever, not Redis/Kafka/microservices, which the spec explicitly rules out.

**Not fixed this session** because changing fetch concurrency touches provider rate-limit behavior and needs its own dedicated verification (a change here that trips Upstox's real rate limits would be worse than the current honest slowness) — recommended as the top P1 item for the next work session, with a live-timed before/after measurement as the acceptance test.

### Defect 4 — Extension distance (Question 8) has no on-screen answer when the stock is NOT extended (MEDIUM)

For a symbol like RELIANCE that is not in the EXTENDED research state, there is no visible "day move so far" vs. "the ≥6% EXTENDED threshold" comparison anywhere in Quick View. A user has to infer "not extended yet" purely from the absence of an EXTENDED badge, rather than seeing e.g. "day move +0.4% of the 6% threshold that would flag EXTENDED." This is a real answerability gap for Question 8 in the non-extended case specifically (the extended case itself, when it fires, is well labeled).

### Low-priority / cosmetic observations (not acted on, per "do not fix cosmetic before research-critical")

- The Daily Research scan's own top badge said `STALE` immediately after a fresh scan completed, because by the time a ~7-minute scan finishes, its own `generated_at` is already older than the freshness threshold for a "live" scan. This is an honest side-effect of Defect 3, not a separate bug — fixing Defect 3 fixes this automatically.
- `KAYNES` and other non-index symbols correctly show a real futures contract; only genuinely weekly-only index expiries hit Defect 1's underlying condition, so the blast radius of the ORIGINAL (pre-fix) defect was narrower than "every futures gap" — but NIFTY/BANKNIFTY are exactly the highest-volume, most commonly researched instruments, so the fix still had real value.

## BLOCKERS

None found. Every symbol tested produced a research state, a compatibility decision, and (where applicable) contract detail — the system never silently failed or returned an unusable/blank page for a real symbol.

## HIGH PRIORITY

1. Defect 2 — reconcile "what is missing" narrative text with the real controlling gate reason (contract liquidity vs. price/chain confirmation) when they disagree.
2. Defect 3 — full-universe Daily Research scan takes ~7 minutes; needs a measured concurrency fix, not new infrastructure.

## MEDIUM

3. Defect 4 — no on-screen "distance to EXTENDED threshold" when a symbol is not yet extended.
4. (Not a defect, a real capability gap) — there is currently no whole-market "what deserves my attention" view independent of running the full 210-symbol scan; the only entry points are "analyze one symbol I already chose" or "wait ~7 minutes for the full scan." See Gap Analysis below.

## LOW

5. Daily Research's own freshness badge reads STALE immediately after a long scan finishes — cosmetic consequence of Defect 3, will self-resolve once Defect 3 is fixed.

## FIXED THIS SESSION

- Defect 1 (Futures panel reason) — implemented, tested (1 new integration test), full regression green (1721 passed), Ruff clean, mypy --strict clean, and re-verified live against real NIFTY data in the browser after the fix.

---

## Gap analysis — where TIRE still forces the user to mentally combine information

This is the "do not stop at manual UAT" comparison requested: what does the *architecture* already have vs. what the *actual on-screen workflow* forces the user to reconcile by themselves.

1. **Chart ↔ Options ↔ Futures ↔ Sector ↔ News agreement is not summarized anywhere as a single relationship statement.** The Research Context panel (DEVELOPING OBSERVATION) already does this *for the specific named pattern* (e.g. `FUTURES_STRUCTURE`'s "why it matters" line explicitly ties basis change to a conventional reading). But when the pattern is `NONE` (as with NIFTY's CONFLICT state), there is no equivalent "here is why these streams disagree" one-line synthesis — the user has to open Evidence Convergence/Adversarial Analysis and read the individual bull/bear lists themselves. A CONFLICT-specific one-line evidence-relationship summary (still not a score — literally listing which two groups disagree and on what) would close this without adding a hidden ranking.
2. **Contract suitability and directional research state are correctly kept separate (per Section 26's explicit requirement) — this is a strength, verified live**, not a gap: KAYNES showed a case where the directional narrative kept talking about price-structure confirmation while the actual decision was driven by contract liquidity (Defect 2), and RELIANCE showed a case where both directional narrative and contract quality were computed and shown independently and correctly. The separation itself works; the wording reconciliation (Defect 2) is the actual gap, not the architecture.
3. **There is no whole-market "what deserves my attention today" entry point that completes in research-usable time.** The only two ways into the system today are (a) already knowing which symbol to type, or (b) waiting ~7 minutes for the full 210-symbol scan. Section 33's Market Overview page does not exist yet as a fast, always-current view — this is real, unbuilt product surface, not a defect in existing code.

## What was NOT built this session, and why

Per the request's own instruction ("Do NOT blindly add features... prove first, then implement only improvements that materially increase usefulness"), this session intentionally stopped at:

- Real UAT execution (done, above).
- One concrete, UAT-motivated, fully tested fix (Defect 1).
- Honest documentation of the remaining defects and gaps, prioritized by actual research impact, not novelty.

The following sections of the original request remain **design/architecture work for a future session**, not implemented here, because building them now — before the P0/HIGH PRIORITY defects above are resolved — would be exactly the "add features before proving the core works" anti-pattern the request explicitly warns against:

- Personal Market Universe policy document + Market Scanner pipeline redesign (Sections 9–11)
- Multi-timeframe chart system beyond the current M15 chart (Sections 12–13)
- Historical Market Store / local OHLCV persistence layer (Section 14)
- 5paisa provider implementation + `docs/PROVIDER_CAPABILITY_MATRIX.md` (Sections 15–18) — a first-pass capability read of 5paisa's public Xstream API docs was done during Phase 0 (live/historical candles, option chain, Greeks, and order/execution APIs are documented; historical candles are confirmed **not available for expired/illiquid option contracts** per 5paisa's own developer forum, a real limitation worth recording before integration) but no code was written against it, since Upstox already covers every capability this codebase currently uses and no unmet research need has been identified yet (Section 31's own gate: "what exact research question does it solve that existing sources cannot?").
- Local Qwen 2.5 refinement layer (Sections 20–22) — not wired into this codebase at all today; no evidence yet that the deterministic engine's own text output is insufficient, which is the precondition the request itself sets for adding this layer.
- Personal Decision Journal / Outcome Analysis / Learning Engine (Sections 23–25) — the existing `research_outcome.py` (+1/+3/+5 session checkpoints, `ResearchOutcomeStatus`) is real infrastructure already partially serving this need for the *automated* researched-candidate flow; a *manually-entered personal reasoning* journal (Section 23's "my own written reasoning" field) does not exist yet and was not added without first confirming, in a real usage session, that the existing outcome tracking is insufficient for the user's own workflow.

These are listed here as an honest backlog, not silently dropped.

---

## Final report (per the request's Section 43 format)

### A. Operator UAT result
Executed live against `http://127.0.0.1:8000` with the real, configured Upstox token. RELIANCE, KAYNES, and NIFTY were run through the full manual workflow; the Daily Research scanner was run against the real 210-symbol F&O universe and timed. See the UAT table and ten-question walkthrough above for the actual quoted output.

### B. Current architecture
Preserved in full — see Phase 0 inspection above (provider abstraction in `app/data/providers/base.py`, research-state/development-pattern engine in `app/domain/options/`, freshness/evidence-dependency machinery, orchestration in `app/orchestration/`). No architectural changes made.

### C. Defects discovered
Four, listed above with exact files and quoted real output (Defects 1–4).

### D. Changes implemented
One: `FuturesVisual.no_futures_reason` (`app/orchestration/visual_data.py`) + rendering in `app/api/static/index.html`'s Futures panel, surfacing the engine's own already-computed `data_warnings` reason instead of a bare "no futures resolved." One new integration test added and passing.

### E. Provider capability matrix
Not created as a formal document this session — see "What was NOT built" above for why, and the informal 5paisa capability notes gathered during Phase 0 (public Xstream docs: market data, historical candles [live scrips only, not expired options], option chain, Greeks, market depth all documented; order placement/modification/cancellation also exist in their API but remain out of scope for this read-only project regardless of provider).

### F. Market universe design
Not redesigned this session. The existing `list_fo_eligible_equity_underlyings()` (210 real NSE F&O equities, indices excluded and separately queryable) already implements a transparent, non-arbitrary universe — confirmed live this session via the Daily Research scan's own "Universe: 210 F&O equities" coverage line.

### G. Historical-data design
Not built this session. Today's system fetches candles per-request from Upstox live (no local persistence layer) — confirmed by reading `app/data/providers/upstox_provider.py` and by the ~7-minute full-scan timing, which is consistent with no local caching being used across the 210-symbol scan.

### H. Chart architecture
Confirmed live: one M15 intraday chart per analysis, real SVG rendering with EMA9/21/50 + VWAP overlays and support/resistance bands (verified `document.querySelectorAll('#price-chart-wrap svg').length === 1` for RELIANCE). No 1Y/3Y/5Y/10Y/weekly/monthly views exist yet — this is unbuilt product surface (Section 12), not a defect.

### I. Research workflow
Confirmed live end-to-end for RELIANCE (see the ten-question walkthrough): type a symbol → Research State → Developing Observation → Evidence → Confirm/Invalidate → Freshness → Contract Quality, all visible in Quick View without needing Expert View, for the clean case. KAYNES and NIFTY needed Expert View for full context (NIFTY specifically needed it before this session's fix).

### J. Personal journal design
Not built this session. Existing `research_outcome.py` infrastructure (session +1/+3/+5 checkpoints) is real and already running (confirmed: "LIVE OUTCOME TRACKING" panel present and populated with a real audit ID for each analysis run this session), but there is no manually-entered personal-reasoning journal yet.

### K. Qwen design
Not present in this codebase at all. `LLM_PROVIDER`/`OLLAMA_*` settings exist in `.env.example` as configuration placeholders only — no Qwen integration code exists to describe.

### L. Security
Confirmed unchanged and intact this session: `assert_broker_execution_disabled()` still gates startup, no `place_order`/`modify_order`/`cancel_order` anywhere in `app/`, `BROKER_ORDER_EXECUTION_ENABLED=false` in the real running config (`settings.broker_order_execution_enabled == False`, checked directly this session), TRADEABLE still renders as "COMPATIBLE (NOT AN ORDER)."

### M. Tests
`pytest`: **1721 passed**, 0 failed, 0 skipped, 0 collection errors (1720 baseline + 1 new this session). `ruff check app tests`: all checks passed. `mypy app --strict`: success, 141 source files.

### N. Browser UAT
**EXECUTED** — this document is the record. Real dashboard, real Upstox data, real symbols, screenshots and raw DOM text captured, one real defect found and fixed with a regression test, re-verified live post-fix.

### O. Remaining limitations (honest)
- Defect 2 (misleading "what is missing" text under contract-liquidity gating) is real and unfixed.
- Defect 3 (~7-minute full-universe scan) is real, measured, and unfixed — the single biggest usability gap found this session relative to the product's own "what deserves my attention today" goal.
- No local historical data store, no multi-timeframe charts beyond M15, no 5paisa integration, no Qwen integration, no manual decision journal — all real gaps relative to the full long-term vision, none blocking today's core research question-answering for a single symbol.
- Only one genuinely stale/closed-market session state and one CONFLICT state were observed live this session (market was open throughout); DATA_INSUFFICIENT, NO_TRADE-from-other-causes, and EXTENDED were not observed live (existing adversarial unit tests cover them synthetically, but that is not the same as a live observation).

### P. GREEN STATUS

**CONDITIONAL GREEN.**

Not full GREEN because: two HIGH PRIORITY defects (Defect 2, Defect 3) are documented but unfixed, and the personal-research-backbone capabilities (universe/scanner UI, historical store, multi-timeframe charts, provider expansion, journal, Qwen) remain largely unbuilt beyond what already existed before this session. Upgraded from the prior CONDITIONAL GREEN specifically because the one previously-open blocker on the safety/trust axis — "browser UAT has not been executed" — is now closed with real evidence, and the one concrete defect that UAT surfaced was fixed and verified live, not just patched and assumed correct.
