# TIRE Phase 3 — Historical Intelligence + Early Opportunity Validation

> **STATUS (forensic certification of `1fc5303`, 2026-09-25): historical
> replay is NOT certified for historical truth. Open defect C-1 — lookahead.**
> Upstox M15 timestamps mark the bar's **open** (the real series runs
> 09:15–15:15 IST). Replay sets each observation's `as_of` to that open
> timestamp, and `HistoricalReplayProvider._latest_candle()` queries with
> `end=as_of + 1s`, so the replay *quote* is that bar's **close**: a price
> that exists only 15 minutes after T0. The candle series itself is correctly
> bounded (the repository query is end-exclusive), but every quote-derived
> input — day change / relative strength, the EXTENDED threshold,
> pre-breakout proximity, the reclaim reference price, regime price, and the
> context index quotes — can see the future. All persisted replay
> observations, the replay dataset and `GET /api/research/patterns?source=replay`
> are affected. Statements below that replay is "no-lookahead" describe the
> design intent and the `data_timestamp <= as_of` filter, not a certified
> property. See `docs/certification/FORENSIC_1fc5303.md` and
> `docs/product/PRODUCT_CONTRACT.md` (principle P4).

Date: 13 September 2026 (mechanism), gap-closure same day. Branch:
`phase-3-historical-validation`. Baseline: `07f3b09` (GREEN). This
document describes what actually exists in the running codebase — never
a forward-looking design.

## 0. What Phase 3 found before writing any code

Before building anything, this phase audited the existing GREEN baseline
and found it already implements most of what a "Phase 3" brief would
normally ask for:

- `ResearchObservation` (`app.domain.audit.research_models`) — an
  immutable "what TIRE knew at that moment" record, never called a trade.
- Session-based (never calendar-day) outcome tracking at **+1/+3/+5
  trading sessions** (`ResearchOutcomeCheckpoint`, `ResearchProgression`),
  with MFE/MAE excursion, level-broken, and breakeven-reached facts.
- `TimingStage` (VERY_EARLY/EARLY/DEVELOPING/CONFIRMING/CONFIRMED/
  MATURE/ALREADY_MOVED/EXTENDED) — earliness classification, already more
  granular than a typical brief asks for.
- Named patterns with explicit evidence requirements
  (`app.domain.options.early_opportunity`).
- A working RESEARCH HISTORY UI/API (`GET /api/research/history`,
  `GET /api/research/{id}/outcome`) and a personal research journal.
- No-lookahead enforced structurally: every repository Protocol requires
  `as_of` and documents "filter out any record with `data_timestamp >
  as_of`, unconditionally" (`app/persistence/interfaces.py`).

What did **not** exist: a way to run the real intelligence engine against
a **chosen past instant** using only locally persisted historical data —
genuine historical REPLAY, as opposed to live observation + forward
outcome tracking. This document covers that gap, and its subsequent
closure. Everything above is untouched.

## 1. Architecture: one intelligence engine, two inputs

```
                 analyze_symbol() / run_analysis()
                       (SAME business logic;
                    see Sections 4-5 for what
                    changed in how it degrades)
                              ^
                 provider: AnalysisProvider  <-- Protocol, not a
                              |                  concrete class
              +---------------+----------------+
              |                                |
        UpstoxProvider                HistoricalReplayProvider
        (live network calls,          (local CandleRepository only;
         full ProviderCapabilities)    ProviderCapabilities says so)
```

`app.orchestration.options_intelligence_pipeline.AnalysisProvider` is a
`typing.Protocol` carrying exactly the six calls `analyze_symbol()`/
`run_analysis()` make on `provider` (`get_market_status`, `get_quote`,
`get_ohlcv`, `get_news`, `get_chain`, `get_quotes`), plus a
`capabilities: ProviderCapabilities` declaration (Section 4).
`UpstoxProvider` already implements every method structurally, so
loosening `analyze_symbol`'s/`run_analysis`'s parameter type from the
concrete `UpstoxProvider` class to this Protocol was a **pure typing
change** — it changed no live behavior (the full pre-existing 1927-test
suite, plus ruff and `mypy --strict`, passed unchanged after that
specific change, verified before the gap-closure work below began).

`app.data.providers.historical_replay_provider.HistoricalReplayProvider`
is the second implementation. It is never registered in the live
provider registry — it is constructed only by
`app.orchestration.historical_replay`.

## 2. Historical data layer

Reused, not rebuilt: `app.persistence.jsonl_file.JsonlCandleRepository`
already existed (implementing `persistence.interfaces.CandleRepository`)
but was not wired into anything that produces analysis. Each record
already carries exactly the Phase-3-required provenance:

```
symbol (instrument_id) | timeframe | timestamp (freshness.data_timestamp)
open | high | low | close | volume | provider | received_at (freshness.received_timestamp)
```

`query()`'s own contract: `start <= data_timestamp < end AND
data_timestamp <= as_of` — no-lookahead is enforced at the storage layer,
not trusted to every caller to remember. This was already covered by
`tests/integration/data/test_persistence_jsonl.py::
test_candle_query_excludes_records_beyond_as_of`.

**Backfilling real history**: `scripts/fetch_upstox_historical.py` +
`scripts/csv_candle_loader.py` already existed for pulling real M15
candles from Upstox's actual `/v3/historical-candle` endpoint into a CSV,
and `data/historical_replay/RELIANCE_M15.csv` is a real sample (2026-07-29
through 2026-08-27) already checked into the working tree (gitignored —
`data/` is never committed). `load_candles_from_csv()` loads that CSV
straight into `Candle` objects a `JsonlCandleRepository` can `save()`.

**What this does NOT do**: download years of data for 210 symbols. The
Phase-3 replay tooling operates on whatever a caller has explicitly
backfilled for a controlled symbol/date range — matching Section 5's "do
not build a huge data warehouse" instruction.

## 3. Session-aware, no-lookahead replay

`app.orchestration.historical_replay.replay_symbol_session(symbol,
session_date, ...)`:

1. Rejects a non-trading `session_date` (`is_trading_day()`).
2. Reads that session's own real M15 bars from the local
   `CandleRepository` once, purely to know which real timestamps to walk
   — the same structure already proven by `scripts/replay_core
   .run_replay()` (`visible = candles[:i+1]`).
3. For each bar, sets the replay clock to that bar's own timestamp
   (`HistoricalReplayProvider.advance_to()`) and calls the **real**
   `run_analysis()` — the identical function a manual query or the live
   daily researcher uses — with `as_of` = that bar's timestamp.
   **Open defect C-1:** that timestamp is the bar's *open*, and the replay
   quote is built from the same bar's *close* — 15 minutes of lookahead
   (see the status note at the top of this document).
4. Tries the SAME contract-selecting gate the live shortlist uses first
   (`collect_gated_candidates()` / `build_research_thesis()` /
   `build_research_observation()`, verbatim from
   `app.orchestration.daily_research`) — this succeeds whenever real
   historical chain data genuinely exists (e.g. a session this system
   already captured live going forward). Only when that gate finds
   nothing does the price-only gate (Section 5) run, and only because no
   chain evidence exists to select a contract from.

Every observation is tagged `source="REPLAY"` (an additive field on
`ResearchObservation`; every observation ever written before this phase
implicitly defaults to `"LIVE"`).

`replay_symbol_window(symbol, start_date, end_date, ...)` calls the above
once per real trading session in the range — the Section 15 "selected
historical window" mode. Whole-universe replay is deliberately not
implemented (Section 15's own instruction).

Replay observations are written to their **own, separate** repository/
files — never the live `research_observations.jsonl` — so a replay run
can never contaminate or be confused with the live GREEN system's own
data (Section 41).

## 4. The capability model — how a missing chain stops being fatal

`analyze_symbol()`'s option-chain fetch stage used to treat ANY
`ProviderError` as fatal for the whole analysis, unconditionally. That
made sense for one specific case (a live provider that genuinely
supports chain history hitting a real, transient failure) and made no
sense for another (a replay provider that structurally never had chain
history for this instant, by design, every single time). The gap-closure
distinguishes the two via an explicit, structural capability declaration
— never by checking a provider's class name:

```python
class ProviderCapabilities(BaseModel):        # app/data/providers/base.py
    historical_candles: bool = True
    historical_option_chain: bool = True
    historical_futures: bool = True
    historical_news: bool = True
```

`UpstoxProvider.capabilities` is the all-`True` default (live behavior
literally cannot change — there is no code path that reads this and
finds anything different from before). `HistoricalReplayProvider
.capabilities` declares `historical_option_chain=False`,
`historical_futures=False`, `historical_news=False` (genuinely,
permanently true: Upstox has no historical option-chain endpoint, no way
to reconstruct a past futures quote beyond what a local candle store
happens to hold, and no historical news archive).

At the option-chain fetch stage, on a `ProviderError`:

- `provider.capabilities.historical_option_chain is True` → **unchanged,
  fatal**, exactly as before this phase (`report.error` set, analysis
  aborts). This is the ONLY path a live `UpstoxProvider` call can ever
  take when its real fetch fails.
- `provider.capabilities.historical_option_chain is False` → **never
  fatal**. `report.derivatives_history_available = False` (a new,
  additive field on `OptionsIntelligenceReport`), a `data_warning` is
  recorded, `report.option_chain` stays honestly `None`, and every
  option-chain-dependent stage downstream (OI/PCR/ATM, IV context,
  temporal evidence, anomaly detection, candidate generation, contract
  comparison) is skipped and set to the exact same "no data" shape those
  functions would themselves produce for a genuinely empty/thin live
  chain — never a new sentinel, never a fabricated zero passed off as a
  real measurement.

`decide()` (`app.domain.options.decision_engine`) gained one new
keyword-only parameter, `derivatives_evidence_available: bool = True`.
Every pre-existing call site (and every LIVE call site since) passes
nothing and gets **byte-for-byte identical behavior** to before this
parameter existed — this was verified, not assumed: the full 1956-test
pre-existing suite passed unchanged after this edit. When explicitly
`False`, `decide()` skips ONLY the "no viable option candidate → NO_TRADE"
short-circuit; the same dimension-counting logic then runs on
`setup_quality`/`risk_quality` alone. Because `option_quality`/
`liquidity_quality`/`risk_quality` are still mathematically forced
`INSUFFICIENT` with zero real candidates, `TRADEABLE` (which requires 3+
`STRONG` dimensions out of 4) is **structurally impossible** without a
real contract — the worst this can ever produce is `WATCH`, never a
recommendation to trade an option this system never actually evaluated.
`determine_blockers()` gained the same parameter, used only to correct
the CONTRACT_UNUSABLE blocker's wording ("historical option-chain
evidence unavailable for this instant" instead of "no sufficiently
liquid option candidate exists" — Section 20: never describe a
structural absence as an observed illiquidity defect).

`classify_development()` (`app.domain.options.development`, unmodified)
already never selects `OI_MIGRATION` or `FUTURES_STRUCTURE` without real
migration/basis-change data — both are `None` when there is no chain, so
those two patterns simply never get selected, exactly like a real live
report with a completely dead OI/futures signal. `PRE_BREAKOUT_COMPRESSION`,
`FAILED_BREAKDOWN_RECLAIM`, `RELATIVE_STRENGTH`, and
`RELATIVE_STRENGTH_ROTATION` are all already computed from
price/technical/relative-strength evidence alone, so they work
unmodified in the no-chain case. This was verified empirically, not just
by reading the code: a scan across ~90 real bars of the sample RELIANCE
data produced only `NONE`/`PRE_BREAKOUT_COMPRESSION`/`RELATIVE_STRENGTH`
patterns, and the derived `research_state` distribution was
`{CONFLICT: 47, WATCH: 32, EARLY_SETUP: 14, CONFIRMATION_PENDING: 4}` —
zero fabricated `TRADEABLE`s, zero `OI_MIGRATION`/`FUTURES_STRUCTURE`.

## 5. The price-only observation gate

`_gate()`/`build_research_thesis()`/`build_research_observation()` (the
live shortlist path) fundamentally require a SELECTED option contract
(`DirectionComparisonVisual`/`ContractAssessmentView`) — that's still
`None` with no chain, so that path still correctly returns nothing. A
second, smaller function, `build_price_only_observation()`
(`app.orchestration.daily_research`, called only from
`replay_symbol_session()` when the contract-based gate finds nothing),
builds the SAME `ResearchObservation` model directly from
`response`/`response.visual` — no second evidence computation, no
second decision engine, only a different (contract-free) assembly step:

- `response.error is not None` → no observation (an analysis failure is
  an analysis failure, chain or no chain).
- Market state in the same `_STALE_MARKET_STATES` set `_gate()` already
  checks → no observation.
- `v.convergence` not `CONVERGENCE_BULLISH`/`CONVERGENCE_BEARISH` → no
  observation (CONFLICT or INSUFFICIENT_EVIDENCE; Section 11's Case D).
- `v.development.pattern == "NONE"` → no observation (Section 12: "WATCH
  + pattern NONE is never a developing setup" — the exact rule
  `classify_research_bucket()` already enforces live).
- Otherwise: a real `ResearchObservation` with `direction` from the
  matrix's own convergence, `early_stage_state` = the SAME
  `response.research_state` (EARLY_SETUP/WATCH) `derive_research_state()`
  already computed price-only (not the contract-aware
  `classify_early_stage_state()` vocabulary, which needs liquidity/decay
  context this observation doesn't have), `research_confidence` fixed at
  `"WEAK"` (never a false confidence claim when an entire evidence axis
  is missing), `selected_right`/`selected_strike`/
  `contractual_expiry_breakeven` all honestly `None`,
  `derivatives_evidence_available=False`, and `missing_evidence`
  combining the real, pattern-specific missing-confirmation text
  (`development.what_is_missing`, e.g. "Option-chain positioning and/or
  futures structure have not independently confirmed") with an explicit
  note that historical derivatives evidence itself is unavailable.

`ResearchObservation.selected_right`/`.selected_strike` became `str |
None` (previously required `str`) — additive-safe: every existing
caller (the live path) already supplies a real value explicitly, so
nothing changes for it. Two new additive fields:
`derivatives_evidence_available: bool = True` (the authoritative "was a
real contract ever evaluated" signal) and `missing_evidence: str | None
= None`.

**Verified end to end against the real sample data**, not just unit
fixtures: replaying RELIANCE's real 2026-08-25 session produces genuine
`PRE_BREAKOUT_COMPRESSION` / `EARLY_SETUP` / `BULLISH` observations, each
with `selected_right=None`, `derivatives_evidence_available=False`, and a
truthful thesis ("Price is compressing near a real opposing level while
structure has not yet broken.") that never claims derivatives
confirmation (`tests/integration/orchestration/test_historical_replay.py::
test_real_historical_data_produces_genuine_price_only_observations`).

### 5a. 95% sprint, Sprint 1 -- real technical levels, not just chain levels

`nearest_level_kind`/`nearest_level_value` used to be `None` for every
price-only observation, because the lookup reused
`report.support_levels`/`resistance_levels` (chain-OI-derived only,
honestly empty with no chain). The pipeline already separately computes
real candle-based `technical_levels` internally
(`technical_price_levels()` + `vwap_ema_levels()`, used for
`level_classifications` and the `PRE_BREAKOUT_COMPRESSION` proximity
check) — now also stored directly on the report
(`OptionsIntelligenceReport.technical_levels`) and exposed via
`VisualData.support_resistance.technical_only` (`_build_support_resistance()`,
unmodified). `build_price_only_observation()` reads the nearest real
technical level on the thesis's opposing side from there
(`_nearest_opposing_technical_level()`) instead of the always-empty
chain-only lookup — verified against the real sample data: real
resistance values (e.g. `1306.14`, `1311.96`) now populate every
`PRE_BREAKOUT_COMPRESSION` observation's `nearest_level_value`, not
`None`. No second technical-analysis engine — same functions, called
once, stored/exposed twice for two real consumers.

## 6. Outcome horizons + confirmation/invalidation split

`app.orchestration.outcome_horizons` — the REPLAY-specific complement to
`app.orchestration.research_outcome`'s live +1/+3/+5-session sweep. That
sweep must re-run `analyze_symbol()` at real wall-clock time because live
future data doesn't exist yet when an observation is made; for a replay
observation, the "future" candles already sit on disk the moment the
observation is generated, so every horizon is computed once, directly
from that series — no separate later sweep.

Five horizons: `PLUS_30M`, `PLUS_1H` (straight offsets from
`generated_at`), `PLUS_1D`/`PLUS_3D`/`PLUS_5D` (real trading **sessions**,
reusing `research_outcome.target_trading_session_date()` — never calendar
days). Each horizon reports, only when the local candle series genuinely
reaches that far (`data_sufficient`, never a partial/guessed number
otherwise): `subsequent_high/low/close`, `max_favorable_move_pct`,
`max_adverse_move_pct`.

Confirmation and invalidation are two **separate** deterministic
outcomes, per Sections 18-19 (the existing live sweep folds an analogous
pair of facts into one `progression` axis; this module keeps them apart):

- `ConfirmationOutcome`: `CONFIRMED` / `NOT_CONFIRMED` / `UNKNOWN` — CONFIRMED
  when EITHER the window's real high/low reached the option's own
  `contractual_expiry_breakeven` (contract-based observations) OR price
  broke THROUGH the observation's own recorded nearest opposing level in
  the thesis's favorable direction (available for price-only observations
  too, since Sprint 1 -- see `_opposing_level_broken_through()`). Every
  named pattern's own documented `confirm_if` text agrees ("price holds
  beyond the nearby opposing level").
- `InvalidationOutcome`: **honestly `UNKNOWN` always**, for now. 95% sprint
  correctness fix: the earlier version of this module fed "opposing level
  broken" into `InvalidationOutcome` -- backwards (a BULLISH thesis's
  resistance breaking above IS the setup working, not failing; caught by
  `tests/unit/orchestration/test_pattern_aggregation.py` before it could
  mislabel a real breakout as "invalidated"). This architecture tracks
  only that ONE confirmation-relevant opposing level, never a separate
  "structure that must NOT break" invalidation-side level -- a real
  INVALIDATED/NOT_INVALIDATED determination needs that second level,
  which doesn't exist yet. Never guessed from an unrelated fact (Section
  7 of the 95% sprint brief: "Otherwise: UNKNOWN. Never guess.") -- a
  real, named, scoped follow-up.

`UNKNOWN` (never a forced binary) whenever the original observation
didn't record a breakeven/level to check in the first place.

## 7. Measured performance -- 95% sprint, Sprint 2b fix (~27x)

**Before**: the 8-test replay integration suite (100-546 real local bars
across various session/window combinations, each a full `run_analysis()`
pipeline call) measured **481s** (8 minutes) wall-clock; a real
100-bar/4-session window replay measured **308.3s** (~3.1s/bar).

**Root cause, found by profiling `analyze_symbol()` for one real bar
with `cProfile`, not guessed**: `cProfile` showed 3.0 of 3.2 real seconds
inside the asyncio event loop's I/O wait
(`_overlapped.GetQueuedCompletionStatus` on Windows) -- i.e., the process
was genuinely SLEEPING, not computing. `PipelineConfig`'s default retry
policy (`chain_fetch_attempts=3`/`chain_fetch_backoff_seconds=1.0`,
`underlying_quote_fetch_attempts=2`/`...backoff_seconds=1.0`, used by
`options_intelligence_pipeline._retry()`) exists to ride out a REAL live
provider's transient network blips. `HistoricalReplayProvider`'s
`ProviderUnavailable` is never transient -- every chain-fetch attempt
(the main one, plus one per comparable expiry in the term-structure
stage) sleeps a full `backoff_seconds` between guaranteed-to-fail
retries, for zero possible benefit.

The `JsonlCandleRepository` full-file-rescan hypothesis this section
originally suspected was investigated FIRST (Sprint 2b's own
`CachedCandleRepository`, `app/persistence/caching.py`) and measured to
have **no detectable effect** on this dataset (~550 candles is too small
for file I/O to matter) -- kept anyway since it is real, correct,
harmless, and documented to matter more for a larger local candle store
(Section 10's own "in-memory caching where safe"), but it was NOT the
actual bottleneck. The real fix (`historical_replay
._DEFAULT_REPLAY_CONFIG`, `chain_fetch_attempts=1`,
`underlying_quote_fetch_attempts=1` -- "try once, fail fast," used only
when a caller doesn't supply their own `PipelineConfig`) changes nothing
about what a replay run can determine (a permanently-unavailable stream
stays unavailable either way), only how much wall-clock time is spent
finding that out.

**After**: the same 100-bar/4-session window replay measures **11.2s**
(~0.1s/bar) with byte-for-byte identical results (same 32 observations,
same pattern, same `missing_evidence` coverage -- verified, not assumed).
The 8-test replay integration suite now measures **21.5s**. The full
project test suite (2002 tests as of this sprint) now runs in **83s**
end to end, down from needing the slow replay suite run separately.

## 8. Testing

- `tests/unit/data/test_historical_replay_provider.py` — deterministic
  exchange status, no-lookahead through `get_ohlcv`/`get_quote`, correct
  previous-session-close reconstruction, honest `ProviderUnavailable` for
  news/chain, replay-clock-required guard.
- `tests/unit/orchestration/test_outcome_horizons.py` — target-timestamp
  arithmetic (including session-skip-the-weekend), data-sufficiency
  honesty, real excursion math (bullish and bearish), confirmation and
  invalidation classification.
- `tests/unit/orchestration/test_price_only_observation.py` — Section
  25's Cases A-E (valid price pattern, no-named-pattern, stale/failed/
  missing-visual, conflict/insufficient-evidence, bearish-with-pattern),
  plus a direct assertion that a price-only thesis never claims
  derivatives confirmed the setup.
- `tests/unit/options/test_decision_engine.py` /
  `tests/unit/options/test_research_blocker.py` — new
  `derivatives_evidence_available` cases: the default reproduces
  unchanged live behavior; `TRADEABLE` is mathematically unreachable
  without a contract; a genuine CONFLICT still wins even without
  derivatives; blocker wording is corrected only when the flag is
  explicitly `False`.
- `tests/integration/orchestration/test_historical_replay.py` — real
  `run_analysis()` path, non-trading-day/unresolvable-symbol errors,
  full-session bar-walk count, reproducibility, window-mode session
  enumeration, the honest zero-observations case (flat synthetic data),
  and — the actual proof — real observations from the real sample CSV.
- `tests/unit/orchestration/test_pattern_aggregation.py` — pure counting
  (exclusion of no-pattern observations, exact grouping, missing-outcome
  defaults to PENDING never dropped, deterministic sort order), the
  outcome-translation adapter's every branch (pending/insufficient/
  follow-through/no-follow-through, and the "FAILED_SETUP never produced"
  correctness guarantee), and one real end-to-end run over a JSONL-backed
  repository.
- `tests/unit/data/test_caching.py` — `CachedCandleRepository` hits the
  backing repository exactly once across many repeated queries, returns
  results identical to the uncached repository, preserves no-lookahead,
  and invalidates correctly on write.
- Full pre-existing suite (1927 tests before this phase) + this phase's
  own new tests = **2002 tests, full suite, one run, 83s** (down from
  needing the slow replay integration file run separately) + ruff +
  `mypy --strict` re-verified green after every change in this phase,
  including every gap-closure and correctness-fix edit.

## 10. Pattern aggregation -- 95% sprint, Sprint 2

`app.orchestration.pattern_aggregation` -- "when this named pattern
appeared historically, what happened afterward?" A lightweight,
read-only, descriptive summary over already-persisted
`ResearchObservation`s and their already-computed outcomes -- reuses
`ResearchOutcomeStatus` (`PENDING`/`FOLLOW_THROUGH_OBSERVED`/
`NO_FOLLOW_THROUGH`/`FAILED_SETUP`/`INSUFFICIENT_OUTCOME_DATA`) verbatim
as ONE shared vocabulary for both live and replay observations, rather
than inventing a second one. `aggregate_by_pattern()` is pure (no I/O,
trivially testable); two small adapters resolve each source's own real
outcome mechanism (`outcome_for_live_observation()` over real persisted
checkpoints; `outcome_for_replay_observation()` over
`outcome_horizons`'s own facts at the +5-session reference horizon).
`ResearchObservation` gained one new additive field, `pattern: str |
None`, populated verbatim from `ResearchThesisView.developing_pattern`
(live) / `DevelopmentNarrativeView.pattern` (replay) -- the one field
this aggregation groups by, never parsed from `thesis` free text.
`FAILED_SETUP` is reachable only for an observation that carries a real
recorded invalidation-side level; for every other pattern
`InvalidationOutcome` stays honestly `UNKNOWN` and `FAILED_SETUP` is
never produced (Section 6). Two patterns carry such a level today:
`PRE_BREAKOUT_COMPRESSION` (its own supporting structure) and, since
18 Sep 2026, `FAILED_BREAKDOWN_RECLAIM` (the reclaimed level itself --
Section 13). Never a probability, win rate, or confidence -- exact counts
only (`tests/unit/orchestration/test_pattern_aggregation.py`).

## 11. Replay caching layer -- 95% sprint, Sprint 2b

`app.persistence.caching.CachedCandleRepository` -- a read-through,
in-memory cache wrapping any real `CandleRepository`, scoped to one
caller-held instance (`replay_symbol_session()` constructs one per
session). Investigated as the suspected replay-performance bottleneck
(Section 7); measured to have no detectable effect on this dataset (the
real bottleneck was retry/backoff sleep, not file I/O -- see Section 7),
but kept: it is real, correct (no-lookahead preserved -- every call still
filters to its own `as_of`; `tests/unit/data/test_caching.py`), harmless,
and will matter more once a local candle store is large enough for
`JsonlCandleRepository`'s per-call full-file re-read to become
measurable on its own.

## 12. What this phase deliberately did NOT build

Per explicit scope agreement during this phase (documented, not silently
dropped):

- A genuine INVALIDATED/NOT_INVALIDATED determination (Section 6) --
  needs a second, distinct "structure that must NOT break" level this
  architecture doesn't track yet (only the confirmation-relevant
  opposing level). Honestly `UNKNOWN` in the meantime, never guessed.
- Market-regime and sector-conditional analysis (a further breakdown of
  pattern aggregation by regime/sector -- `PatternAggregate` already
  carries the real `symbols` a reader can cross-reference manually).
- Any UI surface for replay results or pattern aggregation (the
  mechanism is proven at the orchestration layer; wiring
  `POST /api/research/replay` / a pattern-aggregation endpoint is a
  small, separate follow-up once a UI consumer is actually wanted).
  **Superseded:** the PATTERN HISTORY and COMPARE HISTORICAL
  OBSERVATIONS views were built in the 17 Sep 2026 session and are
  reachable from the HISTORY nav.
- 5paisa. ML of any kind. Any change to Qwen (untouched, unused by any
  code in this phase).
