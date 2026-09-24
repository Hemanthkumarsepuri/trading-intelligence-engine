# Sprint 3.2 — Historical Validation Contract

## Objective

Make every research observation — in particular every historical-replay observation — answer, in machine-readable form, *"which evidence classes were actually available when this observation was generated?"*, so that **absent evidence can never be mistaken for evaluated evidence**. An UNAVAILABLE class must not read as NEUTRAL, false, no-change, bullish, bearish, confirmed or invalidated.

This sprint does **not** create historical option-chain, futures or news data.

## Baseline Commit

`48e679efdd1f2a629451606d745c405af7172f88` (Sprint 3.1 — Evidence Integrity), branch `phase-3-historical-validation`, working tree clean at start.

## Historical Data Reality

Available today: historical M15 price data, historical NIFTY 50 context (only when index candles are in the local store), supported technical price evidence, replay, timing classification, deterministic confirmation/invalidation, MFE/MAE, +1/+3/+5 session outcomes, supported price-action patterns.

Not available today (not created here): historical option chains, option OI/ΔOI/IV/bid-ask/Greeks/liquidity/contract viability, futures OI/basis/migration, news archive/transmission.

## Problem

Replay already degraded gracefully without a chain (Phase 3 gap-closure), but the fact "this stream did not exist at this instant" was only recoverable indirectly: a single `derivatives_evidence_available` boolean (chain only), UNKNOWN evidence rows, and free-text warnings. Nothing recorded, per observation and per evidence class, whether a stream was absent, stale, insufficient, evaluated-and-neutral, or conflicting — and the replay dataset's `neutral_or_unknown_evidence` list puts NEUTRAL and UNKNOWN rows together, so a reader could not tell "stream absent" from "evaluated, no direction".

## Architecture Decision

- **New typed model, derived not duplicated.** `app/domain/options/evidence_availability.py` defines `EvidenceClass`, `AvailabilityState`, `EvidenceClassAvailability` and the immutable `EvidenceAvailability` container. It is computed once per analysis by `assess_evidence_availability()` from facts the pipeline already established at `as_of` (candle currency, EMA history status, chain snapshot presence/currency, futures quote presence/currency, news fetch outcome, index-context presence) and the evidence matrix's own group verdicts. It reuses the pipeline's existing currency gates (`candles_are_current`, `chain_is_current`, `futures_are_current`) — it does not re-derive freshness, and it does not replace `DataStream`/`FreshnessLabel`/`StreamFreshness` (which describe *live stream health*; they cannot express "evaluated, empty" vs "absent", e.g. a fetched-but-empty news list is labelled UNKNOWN there).
- **Placement: nested immutable object on the observation.** `ResearchObservation.evidence_availability: EvidenceAvailability | None`. A nested frozen model keeps the five-class record as one inspectable provenance unit, avoids adding five flat fields, and is directly reusable by forward capture (live observations get the same record from the same function). `None` means *not recorded* (pre-sprint observation) and is deliberately not an UNAVAILABLE claim.
- **Flow, no second path.** `analyze_symbol()` → `report.evidence_availability` → `VisualData.evidence_availability` → observation builders copy it verbatim (`build_research_observation`, `build_price_only_observation`) → `capture_knew_then()` records it in the replay dataset's "knew then" block. Nothing is recomputed downstream.
- **Not a voting group.** An evidence class is not an `EvidenceGroup`; it adds nothing to the matrix. CONFLICTING is judged only against the existing *voting* group of the class (`OPTIONS_CHAIN` → `OPTIONS_OI`, never the non-voting `OPTIONS_IV`), so the Sprint 3.1 policy is used, not bypassed.
- **Legacy bool retained.** `derivatives_evidence_available` stays (pattern aggregation reads it). A test asserts it agrees with `OPTIONS_CHAIN` for replay observations.
- **Provider capability flags** (`ProviderCapabilities`) are used only to word the `reason` ("provider has no option-chain history…" vs "no option-chain snapshot…"). State comes from the observed facts, never from the capability declaration.

## Evidence Availability Model

`EvidenceClass`: `PRICE`, `MARKET_CONTEXT`, `OPTIONS_CHAIN`, `FUTURES`, `NEWS`.

`AvailabilityState`:

| State | Meaning |
|---|---|
| `AVAILABLE` | current, evaluated evidence (a NEUTRAL verdict is still AVAILABLE) |
| `UNAVAILABLE` | the stream produced nothing at this instant (no provider history / failed fetch / no quote) |
| `INSUFFICIENT` | data exists but too little to evaluate (PRICE: EMA history not evaluable) |
| `STALE` | data exists but failed its freshness gate and is withheld from voting |
| `CONFLICTING` | current and evaluated, but the class's own voting group disagrees internally |

Each entry also carries a short, deterministic `reason` (fixed strings — never exception text or timestamps — so identical inputs serialize identically). The record contains exactly one entry per class, in enum order.

## Available Evidence

In replay: `PRICE` (from locally persisted M15 candles, as_of-bounded), and `MARKET_CONTEXT` when index candles for NIFTY 50 (etc.) exist in the local store. In live analysis every class is derived from the live facts exactly as in replay.

## Unavailable Evidence

In price-only replay: `OPTIONS_CHAIN`, `FUTURES`, `NEWS` are `UNAVAILABLE` (provider declares no such history). `MARKET_CONTEXT` is `UNAVAILABLE` when the local store has no index candles (true of the repo's sample RELIANCE data). Nothing is synthesized for any of them.

## UNKNOWN vs NEUTRAL Semantics

- **UNAVAILABLE ≠ NEUTRAL.** For an absent stream the matrix rows stay `UNKNOWN`; a regression test runs the real replay pipeline and asserts every `OPTIONS_OI`, `OPTIONS_IV`, `FUTURES` and `NEWS_EVENT` row is UNKNOWN (never NEUTRAL, never BULLISH/BEARISH), and that `contradicted_by(matrix)` (availability says no vote possible while the matrix shows a directional group verdict) is empty.
- **NEUTRAL** = evaluated, no direction → the class stays `AVAILABLE`. **STALE** = data existed but failed freshness → `STALE`. **CONFLICT** = available evidence disagreed → `CONFLICTING`. Fetched-but-empty news is `AVAILABLE`; only a failed/absent news fetch is `UNAVAILABLE`.
- Existing live semantics (row directions, `withhold_stale_stream_rows`, decision engine, research states) are unchanged.

## Files Changed

- `app/domain/options/evidence_availability.py` — new: enums, immutable models, `assess_evidence_availability()`, `contradicted_by()`.
- `app/orchestration/options_intelligence_report.py` — `evidence_availability` report field.
- `app/orchestration/options_intelligence_pipeline.py` — computes it in the final assembly step (same facts as `_assemble_stream_freshness`).
- `app/orchestration/visual_data.py` — `VisualData.evidence_availability`, copied verbatim.
- `app/domain/audit/research_models.py` — `ResearchObservation.evidence_availability` (default `None`).
- `app/orchestration/daily_research.py` — both observation builders copy it verbatim from the response.
- `app/orchestration/replay_dataset.py` — `capture_knew_then()` records it (additive key; `DATASET_VERSION` unchanged, row top-level keys unchanged).
- `tests/unit/orchestration/test_tomorrow_watch.py` — one `# type: ignore[arg-type]` on `VisualData.model_construct(**dict)`; the new field would otherwise add one mypy error to a pre-existing typing artifact.
- New tests (below) and this document.

## Tests Added

`tests/unit/options/test_evidence_availability.py` (13) and `tests/integration/orchestration/test_historical_validation_contract.py` (11), covering the 15 required properties:

1. Price-only replay records PRICE AVAILABLE. 2–4. Chain, futures, news recorded UNAVAILABLE. 5–6. UNAVAILABLE does not become NEUTRAL or a vote (real pipeline matrix check + invariant). 7–8. **Existing replay unchanged**: on a 25-observation synthetic reclaim scenario, the observations (minus `observation_id` and the new field) plus their +1D confirmation/invalidation outcomes hash to the value captured by running the identical scenario on the Sprint 3.1 commit in a pristine worktree (`8e369f8f…`); a second scenario on the real RELIANCE sample (15 observations) was identical before/after as well. 9. as_of: replaying a store truncated at an observation's timestamp yields the identical observation and availability. 10. Same as_of ⇒ identical availability. 11. Live analysis (run after a replay in the same process) reports chain/futures AVAILABLE — no inherited UNAVAILABLE. 12. Observation Watch snapshot is identical with/without the contract in the payload. 13. Round-trip through `JsonlResearchOutcomeRepository` and JSON. 14. Legacy JSONL lines load with `evidence_availability=None`, explicitly not UNAVAILABLE. 15. No-lookahead suites pass.
Also: STALE/INSUFFICIENT distinct from UNAVAILABLE; CONFLICTING only from a voting group; Sprint 3.1 IV-skew cannot make the chain CONFLICTING; the voting-group set is unchanged; immutability; replay dataset "knew then" carries the contract; an observation built from a response with no contract records `None` rather than inventing one.

## Tests Executed

Targeted (options, research_truth, orchestration unit+integration, data, audit, scripts: 1615 tests), full pytest, Ruff, `mypy app --strict`, `tests/safety`, all `lookahead` tests, broker-execution lock, LLM unit tests, and the Sprint 3.1 M-1 tests.

## Test Results

- Full pytest: **2250 passed**, 0 failed (2226 before this sprint + 24 new).
- Ruff: all checks passed. `mypy app --strict`: no issues in 169 files.
- `tests/safety`: 67 passed. Lookahead tests: 53 passed. Broker lock + LLM: 33 passed. M-1 tests (`test_evidence_voting_policy`, `test_evidence_dependency`, `test_stale_stream_withholding`): 29 passed.

## Failures Encountered

1. A new unit test asserted `EvidenceClass` names are disjoint from `EvidenceGroup` names and failed.
2. `mypy app` reported a `comparison-overlap` error on `data_state != INSUFFICIENT_HISTORY` in the pipeline.
3. Ruff import-order errors (I001) in two modified files.
4. `mypy tests` count rose from 84 to 85.
5. Process error of mine: during a mutation check (deliberately breaking `news_fetch_failed` to prove the tests catch it) I reverted the file with `git checkout`, which discarded all of my pipeline edits, not only the mutation.

## Root Causes

1. Wrong test premise: `FUTURES` legitimately exists in both enums. The invariant that matters is that availability adds no voting group.
2. The pipeline returns early on `INSUFFICIENT_HISTORY`, so that state is unreachable at the assembly point; the real INSUFFICIENT case is unevaluable EMA history (early-session bars).
3. Import placement only.
4. `VisualData.model_construct(**dict[str, object])` yields one arg-type error per distinct field type; the new field added a type.
5. Wrong revert command on a file that also held real changes.

## Fixes Applied

1. Replaced with an assertion that `VOTING_GROUPS` is unchanged and `OPTIONS_IV` is still excluded. 2. PRICE `INSUFFICIENT` is now driven by `a.ema_status != OK`. 3. `ruff --fix`. 4. Single targeted ignore at the call site (the mypy-on-tests count is now below the baseline, since that one line accounted for many pre-existing errors). 5. Restored the file from the backup taken immediately before the mutation, verified the diff (24 insertions, 1 deletion) and re-ran the contract tests (11 passed) and, subsequently, the complete validation. The mutation itself was caught by the test as intended.

## No-Lookahead Verification

The contract is a pure function of facts already bounded by `as_of` (candles via `HistoricalReplayProvider`/`CandleRepository.query(as_of)`, the as_of-bounded chain/futures/news/context fetches). It reads no clock, no live state, and does not depend on provider behavior after `as_of`. `test_availability_is_determined_at_as_of_and_ignores_future_data` proves the observation and availability at bar T are identical whether or not later candles exist in the store. All 53 lookahead tests and `tests/safety` pass.

## Sprint 3.1 Regression Verification

`VOTING_GROUPS` is unchanged and asserted; `OPTIONS_IV` remains non-voting; a test proves an IV-skew lean opposing OI evidence cannot make `OPTIONS_CHAIN` CONFLICTING; the Sprint 3.1 tests (`test_evidence_voting_policy`, 23 cases) all pass. UNAVAILABLE classes are not voting groups.

## Safety Verification

Broker execution remains impossible (`tests/safety/test_broker_execution_lock.py` passes; no order/broker code touched). LLM boundaries unchanged (LLM unit tests pass; no LLM module touched; the contract is deterministic and not LLM-influenced). Stale evidence remains excluded from voting; UNKNOWN/INSUFFICIENT semantics intact.

## Serialization Verification

`EvidenceAvailability` round-trips through `model_dump_json`/`model_validate_json`, and a full `ResearchObservation` round-trips through `JsonlResearchOutcomeRepository` with the contract preserved. Enum values serialize as their string names.

## Backward Compatibility

Existing persisted observations have no `evidence_availability` key and load with `None` ("not recorded"). They are **not** backfilled: recomputing it would require re-running analyses, which cannot be done without look-ahead risk, and inferring it from the old boolean would fabricate a five-class record. Consumers must treat `None` as unknown-provenance, not as UNAVAILABLE. Live observations now also carry the contract (additive; no live evidence semantics changed).

## Known Limitations

- The replay dataset's `neutral_or_unknown_evidence` list still groups NEUTRAL and UNKNOWN rows into one list of strings. The new `evidence_availability` block disambiguates it, but the list itself is unchanged (schema change deferred).
- `MARKET_CONTEXT` is only AVAILABLE in replay when index candles are in the local store.
- Chain quality issues other than stale/dead snapshots (e.g. wide spreads) leave the class AVAILABLE; those remain visible in the existing chain-quality/freshness records.
- The contract is per-observation provenance; pattern aggregation and reports do not yet segment by it (the existing `derivatives_evidence_available` segmentation continues to work).
- 55 mypy errors remain in `tests` (pre-existing, not gated); `mypy app --strict` is clean.

## Deferred Work

Everything else from the Phase 3 audit remains deferred and untouched: API authentication, SYSTEM_HALTED, dependency cleanup, historical option-chain/futures/news providers, ML, new indicators, UI changes, aggregation segmentation by availability, and splitting the dataset's NEUTRAL/UNKNOWN list.

## Definition of Done

- [x] Typed, immutable, machine-readable availability contract on observations
- [x] Chain, futures and news recorded UNAVAILABLE in price-only replay; PRICE AVAILABLE
- [x] UNAVAILABLE never NEUTRAL/vote/confirmation/invalidation; distinct from STALE/INSUFFICIENT/CONFLICTING
- [x] Existing replay, outcomes, Observation Watch and live semantics unchanged (byte-identical baseline comparison)
- [x] as_of respected; deterministic; serialization and legacy loading verified
- [x] Full pytest, Ruff, mypy strict, safety, no-lookahead, broker lock, LLM boundary, M-1 all green
- [x] One focused commit, pushed normally

## Final Commit

The single commit containing this document, `feat: formalize historical evidence availability`, on top of `48e679e`. A commit cannot embed its own hash; see `git log` on `phase-3-historical-validation`.

## Final Status

**COMPLETE.** Explicitly:

- Historical option-chain replay was **NOT** implemented.
- Historical futures replay was **NOT** implemented.
- Historical news replay was **NOT** implemented.
- No synthetic data was introduced (the only synthetic candles are the pre-existing test fixtures).
- No new paid provider (or any new provider) was introduced.
- Sprint 3.3 was **NOT** started.
