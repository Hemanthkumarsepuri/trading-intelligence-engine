# Sprint 3.4 — Outcome & Research Progression

## Objective

Make TIRE reliably evaluate what happened *after* an immutable T0 `ResearchObservation`: T0 → +1 → +3 → +5 trading sessions → a descriptive, deterministic, auditable progression record — with the **same outcome semantics for historical replay and forward capture**, no future information leaking into an earlier horizon, and T0 never modified. This sprint connects, hardens and certifies the existing loop; it introduces no profitability, win-rate or predictive quantity.

## Certified Baseline

Branch `phase-3-historical-validation`, HEAD `d71282efd316baf12c25768a4930c4b0c0446fc1` (Sprint 3.3), tree clean. Measured before any change: pytest **2281 passed**; Ruff clean; `mypy app --strict` clean (170 files); `tests/safety` 67 passed; no-lookahead tests 53 passed; `mypy app tests` 55 pre-existing errors.

## Existing Outcome Architecture

- `ResearchObservation` (frozen, T0) and `ResearchOutcomeCheckpoint` (frozen, append-only, separate file) with `ResearchProgression`, `ResearchOutcomeStatus`, `ResearchOutcomeSummary`.
- **Replay:** `outcome_horizons.compute_price_path_outcome` — bounded to the target session's close, MFE/MAE, `ConfirmationOutcome`/`InvalidationOutcome`, first-crossing bars; `pattern_aggregation.outcome_for_replay_observation` resolved "first event wins" with same-bar ambiguity → `INSUFFICIENT_OUTCOME_DATA`.
- **Forward:** `research_outcome.sweep_due_research_outcomes` → `capture_research_outcome_checkpoint` (fresh `run_analysis` at `as_of`) → `save_checkpoint`.
- Sessions: `trading_calendar.next_trading_day`. Existing enums/models were reused; no parallel outcome model was created.

## Historical Replay Path

Replay observation → `compute_price_path_outcome(candles)` at each horizon. Behavior is unchanged: the Sprint 3.2 golden test (25 observations + their +1D confirmation/invalidation outcomes, hashed against the Sprint 3.1 baseline) still passes byte-identically. Its internals were refactored (below) to share one computation with the forward path.

## Forward Capture Path

Forward observation (Sprint 3.3) → `sweep_all_due_research_outcomes` (new library driver: `query_observations_due_for_sweep` → per observation `sweep_due_research_outcomes`) → per due horizon `capture_research_outcome_checkpoint` → `save_checkpoint_once`. Tracing this path found real defects against this sprint's invariants; they are fixed:

| # | Defect found in the forward path | Fix |
|---|---|---|
| D1 | A checkpoint became "due" once `as_of.date()` reached the target *date* — i.e. mid-session. Idempotency then froze a partial-session checkpoint permanently. | Due only when `as_of >=` the target session's **close** (`horizon_close_utc`) — the same instant replay's horizon ends at. |
| D2 | MFE/MAE, level-broken and breakeven were computed over every candle up to `as_of`, and `spot_at_checkpoint` was the live quote *now*. A late sweep of +1 saw +2…+4 data (**lookahead**). | Price facts now come from the shared bar computation, bounded to the horizon close; `spot_at_checkpoint` is the close at the horizon (replay's `subsequent_close`). |
| D3 | State-derived fields (`next_observed_early_stage_state`, `progression`) came from an analysis "now" but were presented as the horizon's. | Withheld (`None` / UNKNOWN, with a note) when `captured_late` (the next session had already opened) or when the horizon's price path is not determinable. |
| D4 | Session arithmetic used the UTC date of T0/`as_of`, and `target_trading_session_date` defaulted to the **empty** `NSE_HOLIDAYS` constant, silently bypassing the official holiday calendar the app loads at startup — so exchange holidays counted as sessions. | Single module `outcome_sessions` (IST session date; `holidays=None` defers to the canonical calendar). |
| D5 | Forward checkpoints had no confirmation/invalidation facts, so forward and replay did not share outcome semantics. | New additive checkpoint fields carrying the same `ConfirmationOutcome`/`InvalidationOutcome` values and first-crossing bars. |
| D6 | `save_checkpoint` was a plain append (overlapping sweeps could duplicate a horizon), and a failed re-analysis was persisted as if it were an outcome. | `save_checkpoint_once` (file-backed, keyed on observation + horizon); a failed re-analysis persists nothing and is retried. |

Also found, not fixed (out of scope): nothing in the application calls the sweep — no scheduler/endpoint runs it (see Deferred Work).

## T0 / Outcome Separation

Nothing in outcome code can write to T0: `ResearchObservation` and all nested models are frozen; outcomes live in a separate append-only file; the sweep only reads the observation. Tested adversarially: after a full +1/+3/+5 sweep with violent post-horizon data, the observations file is **byte-identical** and every named field (expiry, strike, option type, contract key, underlying/futures keys, spot, breakeven, levels, state, timing, pattern, direction, `generated_at`, evidence availability, capture provenance) is equal to the pre-sweep value.

## Trading Session Semantics

One definition for both paths (`app/orchestration/outcome_sessions.py`): the observation's session is the IST date of T0; +N is the N-th subsequent valid trading session via `next_trading_day`; weekends and exchange holidays never count. Fri 2026-08-28 → +1 Mon 08-31, +3 Wed 09-02, +5 Fri 09-04 (verified); with 09-01 configured as a holiday, +3 becomes Thu 09-03. A horizon's information boundary — and earliest availability — is the target session's close, 15:30 IST.

## +1 / +3 / +5 Semantics

Each horizon is computed independently from bars in `[T0, that horizon's close]`. A later checkpoint never overwrites or recomputes an earlier one; a horizon that already has a checkpoint is never re-evaluated. `RESEARCH_CHECKPOINT_SESSIONS_AHEAD` is unchanged.

## Confirmation Semantics

Unchanged and shared: CONFIRMED if the option's contractual breakeven was reached or the recorded opposing level was broken through in the thesis direction (first crossing bar recorded); NOT_CONFIRMED if neither in the window; UNKNOWN if neither level exists or data is insufficient.

## Invalidation Semantics

Unchanged and shared: INVALIDATED if the recorded supporting level (recorded only for the patterns that define one) was lost (first crossing bar recorded); NOT_INVALIDATED otherwise; UNKNOWN when no level was recorded or data is insufficient — never guessed. The single rule `resolve_first_event()` (extracted from the replay adapter, behavior identical) resolves them: the **earliest** event wins and a later reversal never rewrites it; both in the **same M15 bar** → `INSUFFICIENT_OUTCOME_DATA`.

## MFE / MAE Semantics

Existing convention preserved: BULLISH favorable = (window high − T0 spot)/T0 spot × 100, adverse = (T0 spot − window low)/T0 spot × 100; BEARISH mirrors it. They measure the **underlying's** price path from the T0 spot over `[T0, horizon close]` — research measurements, not option P&L, probability, expectancy or a score. The T0 contract is never replaced by today's expiry/strike/right; its identity is used only through its recorded breakeven and levels.

## Missing Data Semantics

Missing or short future data → `data_sufficient=False`; confirmation/invalidation UNKNOWN; MFE/MAE/close/level flags `None` (not `False`, not 0); `progression` UNKNOWN with state fields withheld; the horizon reads INSUFFICIENT. A missing horizon is never NEUTRAL, "no move", INVALIDATED or CONFIRMED. A horizon not yet reached is PENDING and produces no record.

## Partial Progression

`build_horizon_progress()` (derived, never persisted; exposed as `horizons` on the outcome detail view) reports each of +1/+3/+5 as `AVAILABLE`, `PENDING` (not yet closed, or closed and awaiting the sweep) or `INSUFFICIENT`. T0 + AVAILABLE +1 with PENDING +3/+5 is valid, is not a failure, and creates no placeholder checkpoints.

## Idempotency

`sweep_due_research_outcomes` skips horizons that already have a checkpoint, and `save_checkpoint_once` refuses a second checkpoint for the same (observation, horizon) — the first persisted checkpoint is final (a forged replacement is refused, including from a fresh repository instance). Running the sweep repeatedly, or two sweeps overlapping, yields exactly one record per horizon and one progression view.

## Restart Safety

The duplicate check reads the file. Tested: capture T0 → sweep +1 → restart the repository → sweep again (nothing new) → sweep +3 → restart → sweep again (nothing) → sweep +5: exactly one checkpoint per horizon (3 lines), T0 file byte-identical.

## No-Lookahead Verification

Adversarial tests: (a) pure — bars through +1 only versus the same bars plus violent +2…+5 data give an identical +1 result (`==` on the whole outcome); +3 is identical with or without +4/+5 data; an event during +2 is visible to +3 but not +1. (b) Forward — a +1 checkpoint swept late on Friday with a 3000/20 spike series after Monday has price facts identical to the on-time (Monday-close) sweep, for every price field, while the spike never appears in MFE. (c) A +3 checkpoint is identical with and without +4/+5 data. Mutation checks: removing the horizon bound fails 3 tests; reverting to date-based due fails 2; removing checkpoint dedupe fails the dedupe test. All 53 pre-existing lookahead tests pass.

## Files Changed

- New: `app/orchestration/outcome_sessions.py`.
- `app/orchestration/outcome_horizons.py` (`PriceBar`, `compute_price_path_outcome_from_bars`, `resolve_first_event`; candle entry point is a thin adapter; horizon target via `outcome_sessions`).
- `app/orchestration/research_outcome.py` (due rule, horizon-bounded checkpoint capture, sweep, `sweep_all_due_research_outcomes`, `build_horizon_progress`, detail-view `horizons`).
- `app/orchestration/pattern_aggregation.py` (uses `resolve_first_event`; behavior identical).
- `app/domain/audit/research_models.py` (additive checkpoint fields; `HorizonState`, `ResearchHorizonProgress`).
- `app/persistence/interfaces.py`, `app/persistence/jsonl_file.py` (`save_checkpoint_once`).
- Tests: new `tests/integration/orchestration/test_outcome_progression.py`; three existing assertions corrected (below).

## Tests Added

39 tests: session resolution +1/+3/+5, weekend and holiday exclusion (incl. the calendar-bypass regression), IST session date, due-at-close; MFE and MAE (bullish and bearish, exact values); first confirmation / first invalidation / earliest-wins / same-bar ambiguity / no event; missing data, missing spot, horizon not reached; no-lookahead (+1, +3, independence of horizons); determinism; T0 unmodified; replay/forward computation equivalence; forward: no early checkpoint, partial progression, pending vs awaiting-sweep, idempotency, restart safety, second-checkpoint refusal, round trip, legacy checkpoint loading, late-capture equivalence, +3 isolation, determinism, insufficient horizon, failed re-analysis retried, sweep-all, byte-level T0 immutability, availability not reinterpreted, forward/replay distinguishability, Sprint 3.1/3.2 invariants, no prediction/profit/broker identifiers in the outcome modules.

## Test Results

Full pytest **2320 passed**, 0 failed (2281 + 39). Ruff clean; `mypy app --strict` clean (171 files); `mypy app tests` 55 = baseline. Safety 67, lookahead 53, LLM + broker lock 33, watch 99, replay/dataset/aggregation/horizons 73, forward capture + outcome 81, research-truth + voting + availability 150 — all passing.

Existing tests corrected (each encoded a defect, not an invariant): `test_plus_one_session_becomes_due_once_monday_is_reached` and `test_outcome_summary_pending_when_due_but_not_yet_captured` treated 11:30 IST on the target day (mid-session) as "due"; `test_checkpoint_capture_succeeds_once_due_and_computes_real_facts` asserted the checkpoint spot equals the live quote "now" (the fixture's quote and candle series deliberately differ) — it now asserts the horizon close.
Failures during the sprint: a mis-designed +3 test of mine (datasets differed inside the window), fixed; and a genuine hole found by the missing-data test — `progression` still read `NO_FOLLOW_THROUGH` for a horizon with no price data — fixed by withholding state-derived fields when the price path is not determinable.

## Safety Verification

No order/broker/LLM code touched (AST check of the outcome modules; broker-lock and LLM tests pass). No probability, profit, win-rate, expectancy, hit-rate, Sharpe, ROI or alpha exists in the outcome modules. Deterministic throughout.

## Historical Regression

Golden replay baseline byte-identical; replay dataset, horizons, pattern-aggregation and historical-replay suites pass.

## Forward Capture Regression

The 31 Sprint 3.3 tests pass unchanged; forward observations remain `FORWARD_LIVE_CAPTURE` and replay `HISTORICAL_REPLAY` after outcome sweeps.

## Watch Regression

Untouched; all watch tests pass. Watch T0 and `ResearchObservation` T0 remain separate and outcome processing mutates neither.

## Known Limitations

- Nothing schedules the sweep (see Deferred Work); until something does, no forward outcome is computed automatically.
- State-derived progression is only meaningful when the sweep runs between the horizon session's close and the next session's open; later sweeps still record correct price facts but withhold state fields (documented on the checkpoint).
- A transient candle-fetch failure inside an otherwise successful analysis is recorded as an INSUFFICIENT horizon and, being append-only, is not retried (a wholly failed re-analysis *is* retried).
- Checkpoints persisted before this sprint have the new fields `None` ("not recorded") and were computed under the older rules; they are not reinterpreted.
- The forward path is bounded by the analysis's 10-day M15 lookback; a sweep run more than that long after T0 records INSUFFICIENT price facts.
- The checkpoint spot is now the horizon close rather than the live quote; on-time captures differ from before only where quote and last bar disagree.
- Cross-process locking and JSONL torn-write handling remain unimplemented (out of scope).
- Live certification not performed: the market was closed and no credentials are available.

## Deferred Work

A scheduled/API caller for `sweep_all_due_research_outcomes`; aggregation segmented by evidence availability; pattern statistics (explicitly not built); JSONL hardening; the items excluded by the sprint brief (historical providers, ML, auth, SYSTEM_HALTED, dependency cleanup, etc.).

## Definition of Done

- [x] Same session/horizon/MFE-MAE/confirmation/invalidation semantics for replay and forward
- [x] No future data in any horizon (adversarial tests + mutation checks)
- [x] T0 provably immutable (byte-level) after outcomes
- [x] Partial progression honest; no placeholders; missing data never neutral
- [x] Idempotent, restart-safe sweep; one checkpoint per horizon
- [x] Full pytest, Ruff, mypy strict, safety, lookahead, broker/LLM, watch, replay, forward-capture all green
- [x] One focused commit, pushed normally

## Final Commit

The single commit containing this document, `feat: complete research outcome progression`, on top of `d71282e`. A commit cannot embed its own hash; see `git log` on `phase-3-historical-validation`.

## Final Status

**COMPLETE.** Explicitly:

- No profitability metric was introduced.
- No win-rate metric was introduced.
- No predictive model was introduced.
- No historical options provider was added.
- No historical futures provider was added.
- No historical news provider was added.
- No synthetic market history was introduced (bars in tests are explicit fixtures, never presented as real history).
- No broker execution was introduced.
- **LIVE CERTIFICATION NOT PERFORMED** (market closed, no credentials).
- Sprint 3.5 was **NOT** started.
