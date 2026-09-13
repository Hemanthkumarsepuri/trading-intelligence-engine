# TIRE Phase 3 — Historical Intelligence + Early Opportunity Validation

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
fixtures: replaying RELIANCE's real 2026-08-25 session produces 12
genuine `PRE_BREAKOUT_COMPRESSION` / `EARLY_SETUP` / `BULLISH`
observations, each with `selected_right=None`,
`derivatives_evidence_available=False`, and a truthful thesis
("Price is compressing near a real opposing level while structure has
not yet broken.") that never claims derivatives confirmation
(`tests/integration/orchestration/test_historical_replay.py::
test_real_historical_data_produces_genuine_price_only_observations`).

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

- `ConfirmationOutcome`: `CONFIRMED` / `NOT_CONFIRMED` / `UNKNOWN` — did
  the window's real high/low actually reach the option's own
  `contractual_expiry_breakeven`.
- `InvalidationOutcome`: `INVALIDATED` / `NOT_INVALIDATED` / `UNKNOWN` —
  did the window's real high/low actually break the observation's own
  `nearest_level_kind`/`nearest_level_value`.

`UNKNOWN` (never a forced binary) whenever the original observation
didn't record a breakeven/level to check in the first place — which is
ALWAYS the case for a price-only observation's confirmation outcome (no
contract, so no breakeven exists to check; honestly `UNKNOWN`, never
fabricated as CONFIRMED or NOT_CONFIRMED). A price-only observation's
`nearest_level_kind`/`nearest_level_value` are also currently `None`
(Section 9's known follow-up below), so its invalidation outcome is
likewise honestly `UNKNOWN` today rather than a genuine
INVALIDATED/NOT_INVALIDATED determination.

## 7. Measured performance (honest, not optimized)

The replay integration test suite (9 tests, walking 20-546 real local
bars across various session/window combinations, each a full
`run_analysis()` pipeline call) measures roughly 5-8 minutes wall-clock
on this machine. `JsonlCandleRepository` re-reads and re-parses its
entire file on every `query()`/`latest()` call (documented as its own
known trade-off in `persistence/jsonl_file.py`'s module docstring:
"adequate for this product's real write rate... revisit if that
changes") — replay is the first caller that queries it dozens of times
per session walk rather than a few times per live analysis, so this cost
is now visible where it wasn't before. Not optimized this phase (Section
38 asks for "small controlled jobs," which this still is at
single-symbol/single-window scale) — a real, worthwhile follow-up if
replay is used at larger scale.

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
- Full pre-existing suite (1927 tests before this phase; 1956 with this
  phase's new fast unit tests, run separately from the slower replay
  integration file) + ruff + `mypy --strict` re-verified green after
  every change in this phase, including every gap-closure edit.

## 9. What this phase deliberately did NOT build

Per explicit scope agreement during this phase (documented, not silently
dropped):

- **Technical (candle-based) support/resistance is not yet exposed on a
  price-only observation's `nearest_level_kind`/`nearest_level_value`.**
  `report.support_levels`/`resistance_levels` (used by both the live
  contract-based gate and, currently, the price-only gate's level
  lookup) are chain-OI-derived only; the pipeline already separately
  computes real candle-based `technical_levels`
  (`technical_price_levels()` + `vwap_ema_levels()`, used internally for
  `level_classifications` and for the `PRE_BREAKOUT_COMPRESSION`
  pattern's own proximity check), but that series isn't yet threaded
  through to `VisualData`/`build_price_only_observation()`. The
  practical effect: a price-only observation's invalidation outcome
  (Section 6 above) is honestly `UNKNOWN` today rather than a genuine
  INVALIDATED/NOT_INVALIDATED read — never wrong, just less precise than
  it could be. A real, named, scoped follow-up (add a `technical_levels`
  field to the report and `VisualData`, then use it in the price-only
  gate's level lookup instead of the chain-only `support_resistance`).
- Aggregate pattern/quality reporting (Sections 22-23); market-regime and
  sector-conditional analysis (Sections 26-27).
- Any UI surface for replay results (Section 32 says expose API
  structure only, not a UI, and this phase did not add new HTTP
  endpoints either — the mechanism is proven at the orchestration layer;
  wiring `POST /api/research/replay` etc. is a small, separate follow-up
  once a UI consumer is actually wanted).
- 5paisa. ML of any kind. Any change to Qwen (untouched, unused by any
  code in this phase).
