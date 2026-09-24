# Sprint 3.3 — Forward F&O Observation Capture

## Objective

Establish the forward half of TIRE's Phase 3 research loop: turn a validated *live* F&O analysis into an immutable, provenance-backed `ResearchObservation` recording what TIRE genuinely knew at T0, deduplicated and restart-safe, ready for the existing +1/+3/+5 session outcome checkpoints. This is a recording step. It is not a backtest, prediction, order system, paper-trading system or recommendation engine, and it reconstructs no historical option-chain/futures/news data.

## Certified Baseline

Branch `phase-3-historical-validation`, HEAD `60344982b4b1e90f1297a7acf59c1777e14a5f5a` (Sprint 3.2), working tree clean at start. Sprint 3.1 = `48e679e` (Evidence Integrity), Sprint 3.2 = `6034498` (Historical Validation Contract).

## Architecture Inspection

Traced, not assumed:

- **Live analysis authority.** `run_analysis()` → `analyze_symbol(as_of=…)` produces an `AnalyzeResponse` whose `generated_at` *is* the `as_of` the analysis ran at. In the daily scan each symbol's `as_of` is the scan start advanced by wall-clock elapsed time (`symbol_as_of`).
- **How observations were built/persisted before this sprint.** `run_daily_research()` ranks candidates, then for **every** shortlisted candidate called `build_research_observation()` and `outcome_repository.save_observation()` **unconditionally**: no session check (an observation was written on a Sunday from last-observed data with `source="LIVE"`), no identity/expiry check, no deduplication, and a random uuid `observation_id`. `ResearchObservation` had **no observed expiry, no contract instrument key, no persistence timestamp** and no data-timestamp provenance.
- **Persistence.** `JsonlResearchOutcomeRepository` (`research_observations.jsonl` + `research_outcome_checkpoints.jsonl`): append-only, no update/delete, no torn-write handling (not added — out of scope).
- **Outcomes.** `sweep_due_research_outcomes()` / `due_research_checkpoints()` key off `observation_id` and `generated_at`; trading sessions come from `trading_calendar`.
- **Session semantics.** `classify_session_window()` (OPEN/PRE_OPEN/CLOSED) and `research_session_mode` (LIVE/PRE_MARKET/POST_MARKET/CLOSED); `observation_kind` is `LIVE` only for OPEN, else `LAST_OBSERVED`.
- **What canonical analysis already carries** (and is therefore copied, not re-fetched): `visual.option_chain.expiry` (the *observed* chain expiry), `visual.freshness.streams` (per-stream label, source data timestamp, retrieval time, source), `visual.futures.instrument_key`, `visual.evidence_availability` (Sprint 3.2), `market_state`. What it dropped: the option leg's `security_id` and the underlying instrument key.
- **Watch.** `WatchRecord`/`ObservationSnapshot` are a separate user-facing longitudinal store built from analyze payloads; untouched.

## Live Analysis Capture Path

```
run_analysis()  ->  AnalyzeResponse (source of truth)
  -> rank_candidates() / build_research_observation()          (unchanged)
  -> assess_forward_capture()    identity + provenance copied from the same response;
                                 every rule enforced by verify_forward_observation()
  -> repository.save_observation_once()   persist iff no record with that id exists
  -> CaptureOutcome (CAPTURED | DUPLICATE | INELIGIBLE | ERROR) recorded on DailyResearchResult
```

There is no capture-specific decision, evidence, pattern or contract logic; nothing is fetched to enrich a record.

## T0 Definition

T0 = the observation's own `generated_at` = the analysis `as_of` (existing semantics). Three timestamps stay distinct: **T0** (`generated_at`); **source data timestamps**, per stream in `forward_capture.streams` (verbatim; `None` stays `None`; the option chain's is an HTTP receipt time and is labelled as the stream's own fact, never as an exchange matching-engine time); and **`persisted_at`** (`forward_capture.persisted_at`). T0 is never the persistence time, and a T0 later than `persisted_at` is rejected (model validator and gate).

## Capture Eligibility

Data integrity only — never a score, confidence, probability, expected return or research-state value. All rules live in one function, `verify_forward_observation()`, used both as the pre-persistence gate and later as the integrity check:

| Area | Rule (reason code) |
|---|---|
| Session | `classify_session_window(T0)` must be OPEN + `research_session_mode` LIVE (`T0_NOT_IN_LIVE_SESSION`), the stored session record must match (`SESSION_RECORD_MISMATCH`), and the analysis's `MarketDataState` must be LIVE_SNAPSHOT/LIVE_STREAMING (`MARKET_STATE_NOT_LIVE`) — `MARKET_CLOSED_LATEST_DATA` is never promoted to LIVE |
| Causality | T0 tz-aware and ≤ `persisted_at` |
| Identity | symbol; underlying instrument key; option right ∈ {CE, PE}; strike > 0; direction BULLISH/BEARISH; the contract must exist in the observed T0 chain (`CONTRACT_NOT_IN_OBSERVED_CHAIN`); analysis not failed, has visual, has T0, symbol consistent |
| Expiry | observed T0 chain expiry present (`OBSERVED_EXPIRY_MISSING`) and not before T0's IST date (`EXPIRY_BEFORE_T0`) |
| Evidence | Sprint 3.2 `evidence_availability` present; `OPTIONS_CHAIN` AVAILABLE or CONFLICTING at T0 (`OPTION_CHAIN_NOT_CURRENT_AT_T0`) |
| Integrity | recomputed identity equals `observation_id` (`IDENTITY_MISMATCH`) |

Not gated: the value of the research state. WATCH / EARLY_SETUP / CONFIRMATION_PENDING / CONFIRMED_SETUP are captured identically and recorded verbatim. Capture is downstream of the existing shortlist gate, which supplies a direction and a selected contract; observations without a selected contract (CONFLICT / DATA_INSUFFICIENT / no-direction) cannot be represented by `ResearchObservation` today and are therefore not captured — the *policy* is state-agnostic, the *model* is not (see Known Limitations).

## Observation Identity

`observation_id = sha256("TIRE-FWD-V1" | symbol | underlying key | right | normalized strike | observed expiry | contract key | T0 (UTC ISO))[:32]`. Same contract at the same T0 ⇒ same id (retry, refresh, restart, persistence retry cannot mint a second record; two independent analyses of the same T0 agree). Same contract at a different T0 ⇒ different id (legitimately separate observations). `persisted_at` is not part of the identity. Strike, right, expiry and contract key are inside it, so tampering with any of them is detectable (`IDENTITY_MISMATCH`).

## Immutability Model

Layered: (1) `ResearchObservation` and every nested model (`ForwardCaptureProvenance`, `StreamProvenance`, `EvidenceAvailability`) are frozen pydantic models; (2) the repository has no update/delete and `save_observation_once()` never overwrites — the first persisted record is final (a test attempts hostile rewrites of expiry, strike, option type, spot/premium and evidence availability under the same T0 identity; all are refused and the file bytes are unchanged); (3) outcomes are separate `ResearchOutcomeCheckpoint` records that never write back into T0; (4) identity binds the contract fields, so an altered record fails verification.

## EvidenceAvailability Handling

Copied verbatim from the analysis (`visual.evidence_availability`), never recomputed at capture (a test replaces `assess_evidence_availability` with a function that raises; capture still succeeds). `OPTIONS_CHAIN: AVAILABLE` means "available to TIRE at T0". It is not a voting group and adds no vote; the Sprint 3.2 enums/states are unchanged.

## Provenance

`ForwardCaptureProvenance`: capture policy version, session window/mode at T0, analysis `market_state`, `persisted_at`, underlying/contract/futures instrument keys, observed expiry, and per-stream `StreamProvenance` (stream, freshness label, source data timestamp, retrieval time, source string) for underlying quote, M15 candles, option chain, futures, macro, news — copied from `StreamFreshness`. The provider is identified by each stream's `source` string (e.g. `upstox /v2/option/chain`); nothing is invented. To make the contract identity available from canonical analysis, two additive optional fields were added to the visual layer: `ChainLegView.instrument_key` and `OptionChainVisual.underlying_instrument_key` (both from data the pipeline already held).

## Persistence

Existing JSONL journal, extended: `JsonlResearchOutcomeRepository.save_observation_once()` (also added to the `ResearchOutcomeRepository` protocol). No database, Redis or service was introduced. Forward observations appear in the existing Research Journal / history views via the same `research_observations.jsonl`; no second journal.

## Deduplication

`save_observation_once()` scans the file for the id (substring pre-filter, then a full parse and id compare) under a process lock, and appends only if absent, returning whether it wrote. A duplicate returns `DUPLICATE`, writes nothing, and the retry's later `persisted_at` is not recorded.

## Restart Safety

The duplicate check reads the file, not process memory. Tested: capture → construct a fresh repository on the same directory (no in-memory state) → same capture attempt → `DUPLICATE`, still exactly one line. The lock covers atomicity within one process; cross-process writers are not supported (one API process owns the journal).

## Historical vs Forward Separation

No new stored "kind" field. `forward_capture` is non-`None` only for forward captures; `observation_capture_kind()` derives `FORWARD_LIVE_CAPTURE` / `HISTORICAL_REPLAY` (`source="REPLAY"`) / `LEGACY_LIVE_UNVERIFIED` (pre-Sprint-3.3 live records). A model validator forbids `forward_capture` on any observation whose `source != "LIVE"`, so a replay observation can never masquerade as live. Replay output is byte-identical to the Sprint 3.1 baseline (the Sprint 3.2 golden test still passes; it now additionally asserts `forward_capture is None` for every replay observation). Nothing is backfilled.

## Outcome Compatibility

No outcome semantics changed. A test captures a forward observation, finds it via `query_observations_due_for_sweep()`, runs the existing `sweep_due_research_outcomes()` for +1 session, and confirms the checkpoint is recorded against the same `observation_id` while the stored T0 record is unchanged.

## Watch Integration

Untouched. `WatchRecord` and `ResearchObservation` remain separate. A test shows the Watch snapshot built from an analyze payload is identical with or without the new identity fields; all watch tests pass.

## Files Changed

- New: `app/orchestration/forward_capture.py` (policy, identity, gate/verify, capture service, inspection view).
- `app/domain/audit/research_models.py` (`StreamProvenance`, `ForwardCaptureProvenance`, `ResearchObservation.forward_capture` + validator).
- `app/orchestration/daily_research.py` (capture wiring; `DailyResearchResult.forward_capture`).
- `app/persistence/interfaces.py`, `app/persistence/jsonl_file.py` (`save_observation_once`).
- `app/orchestration/visual_data.py` (two additive identity fields).
- `app/api/main.py` (read-only `GET /api/research/{observation_id}/capture`).
- Tests: new `tests/integration/orchestration/test_forward_capture.py`; `test_historical_validation_contract.py` (one added assertion: replay `forward_capture is None`).
- This document.

## Tests Added

31 tests in `test_forward_capture.py` covering the 30 required properties plus the read-only inspection endpoint (kind, provenance, verification; 404; writes nothing): valid capture; evidence availability present/copied/not recalculated; T0 preserved and distinct from `persisted_at`; immutability (attribute assignment and hostile same-T0 rewrites of expiry/strike/right/spot/premium/availability); identity binding of strike, option type, expiry and contract key; no information after T0; duplicate, different-T0, restart and cross-analysis determinism; fail-closed on closed market state, out-of-session T0 (Saturday, after close, before open, pre-open), missing/invalid identity, expired contract, missing/stale evidence; round-trip; replay/legacy distinguishability; outcome flow; research state unchanged with/without capture and under a repository failure; closed-market daily run captures nothing; Watch unchanged; Sprint 3.1 voting policy and Sprint 3.2 availability semantics unchanged; no broker/LLM/scoring dependency in the capture module. Test data is the repo's existing live-shape mock transport fixture; nothing was fabricated as market history. Mutation checks confirmed the session gate, dedupe and deterministic identity are each actually exercised.

## Test Results

- Full pytest: **2281 passed**, 0 failed (2250 before + 31).
- Ruff: all checks passed. `mypy app --strict`: no issues in 170 files. `mypy app tests`: 55 errors, identical to the pre-sprint baseline of 55 (none in the new file).
- `tests/safety` 67 passed; lookahead tests 53 passed; research_truth + evidence voting/availability/dependency 153 passed; replay/dataset/outcome/data suites 314 passed; watch tests 99 passed; LLM + broker-lock 33 passed.

Failures encountered and fixed during the sprint: (1) the Sprint 3.2 golden-hash test failed because replay dumps now contain the new `forward_capture` key — the test now asserts it is `None` and excludes it (the hash is otherwise unchanged); (2) a first version of my "no scoring language" test scanned prose and matched the module's own docstring — replaced with an AST check of imports/identifiers; (3) `mypy tests` rose by 25 from untyped fixtures in the new test file — annotated, back to 55.

## Live Verification

**Not performed.** The market was closed at the time of the run (POST_MARKET) and the environment has no Upstox credentials, so no live read-only capture could be made; a LIVE observation was not faked. Live behavior is covered by deterministic fixtures through the real `run_analysis()` path. A live one-underlying certification remains to be run during an open session.

## No-Lookahead Verification

The record holds only the analysis output at T0: stream timestamps are ≤ T0 (tested), `observed_expiry` is the T0 chain's, identity excludes `persisted_at`, and no field is recomputed after T0. A later analysis with different market data cannot alter the stored record. All 53 lookahead tests pass.

## Safety Verification

No order/broker code added (tested by AST over the module; broker-execution lock tests pass). No LLM is in the capture path; capture eligibility, T0, identity, expiry, availability and research state are all deterministic. No new endpoint writes anything (the one new route is a read-only GET).

## Sprint 3.1 Regression

`VOTING_GROUPS` unchanged and asserted; `OPTIONS_IV` still not a voting group; the Sprint 3.1 test files pass.

## Sprint 3.2 Regression

`EvidenceAvailability` classes/states unchanged, immutable and round-tripped; the replay golden baseline is unchanged.

## Known Limitations

- **Behavior change to flag:** the daily scan previously persisted every shortlisted candidate regardless of session; it now persists only eligible forward captures. In a closed market nothing is persisted (results are reported on `DailyResearchResult.forward_capture`). This is intentional but reduces the number of stored observations versus before.
- Only contract-selected shortlist candidates (with a direction) can be captured; CONFLICT / DATA_INSUFFICIENT / no-direction analyses are not, because `ResearchObservation` requires a direction and a selected contract. Capturing those would need a model change (deferred).
- The scan's per-symbol `as_of` advances by wall-clock time, so a symbol analyzed a few microseconds after 15:30:00 IST is CLOSED and not captured.
- No cross-process locking and no torn-write handling on the JSONL file (pre-existing).
- The requested-vs-observed contract distinction does not arise in the scan path (no requested contract); only the observed contract/expiry is recorded.
- The provider is recorded through per-stream source strings, not a separate provider-name field.
- Live certification not performed (market closed).

## Deferred Work

Outcome aggregation, pattern statistics, historical option/futures/news providers, capturing non-directional (CONFLICT/DATA_INSUFFICIENT) observations, torn-write/locking hardening, a live certification run, API authentication, SYSTEM_HALTED, dependency cleanup, UI work — all untouched.

## Definition of Done

- [x] One capture path consuming canonical analysis; no second engine
- [x] Explicit, single-rule-set, fail-closed eligibility
- [x] Deterministic identity; persistence-backed dedupe; restart-safe
- [x] Immutable T0 with provenance and Sprint 3.2 availability; T0 / data / persisted timestamps distinct
- [x] Forward vs replay vs legacy distinguishable; replay unaffected
- [x] Existing outcome, Watch, voting-policy and availability behavior unchanged
- [x] Full pytest, Ruff, mypy strict, safety, lookahead all green
- [x] One focused commit, pushed normally

## Final Commit

The single commit containing this document, `feat: capture immutable forward fno observations`, on top of `6034498`. A commit cannot embed its own hash; see `git log` on `phase-3-historical-validation`.

## Final Status

**COMPLETE.** Explicitly:

- No historical option-chain data was fabricated.
- No synthetic F&O history was introduced.
- No paid provider was added; no new provider was added.
- No ML was added; no prediction engine was added.
- No order execution was added.
- No profitability metrics were added; no pattern ranking was added.
- Sprint 3.4 was **NOT** started.
