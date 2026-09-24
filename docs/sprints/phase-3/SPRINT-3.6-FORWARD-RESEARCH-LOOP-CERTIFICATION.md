# Sprint 3.6 — Forward Research Loop Certification & Dataset Integrity

## Objective

Certify, as far as an automated suite can, that the complete forward research lifecycle is internally consistent, auditable and immutable — i.e. that TIRE can accumulate forward F&O observations **without rewriting, contaminating, duplicating or misrepresenting what it knew at T0**. No new intelligence, indicator, provider, ML, prediction, profitability or ranking was added. Two real integrity defects found during the audit were fixed (below); everything else is tests, an audit helper and documentation.

**Certification scope: SINGLE-PROCESS. CROSS-PROCESS ATOMICITY: NOT CERTIFIED. LIVE CERTIFICATION: NOT PERFORMED.**

## Certified Baseline

Branch `phase-3-historical-validation`, HEAD `521ddbe5dc4515259bba579d0a5de352ea3826e1` (Sprint 3.5; the hash in the sprint brief was one character short, its prefix matches), tree clean. Measured before changes: pytest **2346 passed**; Ruff clean; `mypy app --strict` clean (172 files); safety 67; no-lookahead 53; watch 99; historical replay + validation contract 21; forward capture 31; outcome progression 39; scheduler 26; `mypy app tests` 55 pre-existing errors.

## Current TIRE Research Architecture

`run_analysis()` (canonical, as-of bounded) → shortlist gate (`rank_candidates`, unchanged) → `build_research_observation()` → **forward capture gate** (`verify_forward_observation`, one rule set) → immutable `ResearchObservation` + `ForwardCaptureProvenance` → `save_observation_once()` (`research_observations.jsonl`) → **scheduled sweep** (`OutcomeSweepScheduler`, 15-min trigger) → canonical due-query and outcome engine → `save_checkpoint_once()` (`research_outcome_checkpoints.jsonl`) → `build_horizon_progress()` / `summarize_research_outcome()`. Observation Watch is a separate store; the LLM adapter and broker lock sit outside the whole chain.

## Complete Lifecycle

Traced end to end in code and covered by `test_complete_lifecycle_certification`: capture at T0 → ticks at +1h (nothing), +1 close, +1 close again (duplicate tick), +3 close +15 min, +5 close, +5 close +2 days, **with a fresh repository (simulated restart) on every tick and violent post-+1 data (3000/20 spikes)**. Result: the observation file is byte-identical to a fresh capture of the same analysis; exactly three checkpoint lines; each evaluated through its own close; +1's excursions never saw the spike, +3's did (legitimately); T0 availability/direction/state unchanged; all three horizons AVAILABLE; `audit_research_dataset` clean.

## T0 Integrity Contract

- T0 = `ResearchObservation.generated_at` = the analysis `as_of` (existing semantics); `persisted_at` (write time) and per-stream source timestamps stay separate.
- **Field audit** (`test_every_t0_field_is_classified…`): every field of `ResearchObservation`, `ForwardCaptureProvenance` and `StreamProvenance` is classified OBSERVED_AT_T0 / DERIVED_FROM_T0 / PROVENANCE / PERSISTENCE_METADATA, and the test fails if a field is added without classification. PERSISTENCE_METADATA is exactly `run_id` and `persisted_at`.
- **No future information** (`test_t0_holds_no_timestamp_later_than_t0…`): walking the whole dumped record, the only timestamp later than T0 is `forward_capture.persisted_at`; the observed expiry is not before T0's IST date; identity keys are present.
- **Capture fetches nothing**: `capture_forward_observation` takes no provider/client and the module imports nothing from `app.data`/`httpx`/`dashboard_service` (tested).
- **Identity**: the existing deterministic `observation_id` (symbol, underlying key, option type, strike, expiry, contract key, T0) already binds everything the brief lists and recomputes from the stored record (`IDENTITY_MISMATCH` otherwise). **No second fingerprint was added** — no integrity gap was found that one would close.

## Evidence Availability Contract

Constructed once by the analysis at T0, copied verbatim into the record, never recomputed by the scheduler, outcome engine, progression, Watch or history. `ResearchOutcomeCheckpoint` has no availability or contract-identity field (tested), and a sweep whose fresh analyses see a live chain leaves T0's `MARKET_CONTEXT: UNAVAILABLE` and `OPTIONS_CHAIN: AVAILABLE` exactly as recorded. It is not a voting group (class names disjoint from `EvidenceGroup`; `VOTING_GROUPS` asserted unchanged; `OPTIONS_IV` non-voting).

## Provenance Contract

Per-stream label / source data timestamp / retrieval time / source string are copied from the canonical analysis's `StreamFreshness` (verified verbatim in Sprint 3.3 tests); unknown stays `None`; the option-chain timestamp is an HTTP receipt time, not an exchange time. Raw T0 evidence values (option LTP, OI, ΔOI, IV, volume, bid/ask, PCR, futures, VWAP, levels) are **not** stored on the observation; they live in the immutable analysis snapshot referenced by `audit_id` — see Medium finding M1.

## Outcome Isolation

After +1/+3/+5 with adversarial data, the observations file is byte-identical and reloaded T0 equals the original; checkpoints only ever add a separate file (Sprints 3.4/3.5 and lifecycle test above).

## +1/+3/+5 Horizon Isolation

`test_a_horizon_is_identical_with_and_without_any_later_data` (parametrized): quiet data through the horizon under test and a violent 3000/20 series **only after it**; the checkpoint from data ending at the horizon must equal the checkpoint from data extending days beyond. A/B (+1 vs +2..+5), C/D (+3 vs +4/+5), E/F (+5 vs 3 further days) — every price field identical and `evaluated_through` equals that horizon's own close. (An earlier version of this test used spikes that saturated inside the +3 window and let a "+3 sees +4" mutation survive; it was rewritten and the mutation is now caught.)

## Calendar Contract

One definition (`outcome_sessions`): IST session date, canonical `next_trading_day`, close at 15:30 IST. Tested that replay's `horizon_target_timestamp` equals the forward `horizon_close_utc` and that forward due-ness flips exactly at that instant (±1 s) for +1/+3/+5 across: normal session, T0 after the close, T0 on a weekend, across a configured holiday, across a special session. **Defect fixed (H2):** a special session (the packaged calendar includes `SPECIAL:2026-11-08`, Muhurat) was treated as closing at 15:30 IST although it trades in the evening, so a horizon landing on it would have been due before it traded and been frozen permanently as insufficient. Its close is now the end of that IST day (`23:59:59`) — never earlier than the session ends. Special sessions cannot be captured live (fail-closed: not in the 09:15–15:30 live window) — documented limitation.

## Scheduler Contract

Certified via the Sprint 3.5 suite plus lifecycle/mutation tests here: triggers only the canonical sweep with the real clock; contains no due, outcome or persistence logic (AST); cannot fabricate `as_of` (future-`as_of` mutation fails 13 tests) or due status (early-due mutation fails); missed ticks and downtime are recovered; restart safe; no duplicate checkpoints; exceptions never escape and are reported by type only; a malformed persisted line now makes health `DEGRADED` with a `malformed_lines` count.

## Persistence Contract

- **Duplicates.** `save_observation_once` (by `observation_id`) and `save_checkpoint_once` (by observation + horizon) are file-backed and lock-guarded; a 16-call/8-thread race on one repository persists one record (**single-process guarantee: a threading lock**).
- **Defect fixed (H1): partial writes.** Reproduced: after a process kill mid-append (torn trailing line, no newline) (a) the *next* capture reported `CAPTURED` but its record was silently glued to the fragment and unreadable, and (b) `query_all_observations` and therefore the whole outcome sweep and history API raised. Minimal fix, no rewrite: `_JsonlStore.append_line` starts a new line when the file does not end in a newline; the research observation/checkpoint reads skip and **count** malformed lines (`count_malformed_lines`, surfaced in the sweep result and health) instead of failing; the once-checks ignore fragments. A torn checkpoint leaves its horizon uncaptured, so the next sweep recreates it (tested); a torn observation is a record that was never fully written.
- **Audit helper** (`audit_research_dataset`, read-only): counts valid/invalid forward observations (with reasons), legacy/replay records, duplicate observation ids, duplicate checkpoint keys, orphan checkpoints, checkpoints dated before the horizon they claim, malformed lines, and forward observations lacking an `audit_id`. Tested against deliberately corrupted files.
- **Not certified:** cross-process atomicity, filesystem-level transactions, fsync/durability, torn records in the *middle* of a file. Ordering: observations are read sorted by `generated_at`; checkpoints by `captured_at`.

## Dataset Quality Contract

A forward observation is **VALID_RESEARCH_OBSERVATION** iff: (1) it parses and its `observation_id` is unique in the journal; (2) `verify_forward_observation` returns nothing — tz-aware T0 ≤ `persisted_at`; T0 inside the OPEN/LIVE session with a live market state; symbol, underlying key, option right ∈ {CE, PE}, strike > 0, direction BULLISH/BEARISH; observed expiry not before T0's IST date; Sprint 3.2 availability present with `OPTIONS_CHAIN` AVAILABLE/CONFLICTING; identity recomputes to the id; (3) provenance is the copied analysis provenance; (4) persistence: readable, single, in the observations file. A checkpoint is valid iff it belongs to a persisted observation, is unique per horizon, and was captured no earlier than the horizon close it evaluates. **No confidence, probability, score, profit or prediction participates.** (`research_confidence`/`actionability` exist on the record as recorded labels and are deliberately unused by the contract.)

## Historical vs Forward Separation

Kinds derive from `(source, forward_capture)`: LIVE+capture = FORWARD_LIVE_CAPTURE, LIVE+none = LEGACY_LIVE_UNVERIFIED, REPLAY+none = HISTORICAL_REPLAY; REPLAY+capture is impossible (validator). Real replay output has `forward_capture is None` for every observation (tested). Mutations "replay marked as forward" (3 tests fail), "forward marked replay" (7) and "validator removed" (2) are caught.

## Watch Separation

Separate stores. Tested with real files: research ticks leave the watch file byte-identical; a Watch update leaves the observation and checkpoint files byte-identical; the watch modules reference none of the research observation/outcome write API (AST; mutation adding such a reference fails).

## LLM Safety

The import closure of the whole lifecycle (91 app modules) contains no `app.llm`; the narrative adapter imports no persistence/orchestration/audit module; and a test replaces every public adapter method with one that raises and runs a full capture + three-horizon sweep — nothing calls it. Mutation "LLM imported into the capture path" fails.

## Broker Safety

No lifecycle module defines or calls `place_order`/`modify_order`/`cancel_order`/`submit_order`; no route path contains "order"; the settings broker lock still refuses execution. Mutation adding a `place_order` to the scheduler module fails.

## Adversarial Tests

Each mutation was applied, the suites run, and the file restored:

| Mutation | Result |
|---|---|
| future data injected into T0 | caught (3 fail) |
| EvidenceAvailability overwritten during outcome | caught (5) |
| expiry replaced after T0 | caught (5) |
| strike replaced after T0 | caught (5) |
| T0 state rewritten | caught (5) |
| +1 sees +2 | caught (3) |
| +3 sees +4 | caught (1) — after strengthening the isolation test |
| duplicate observation persistence removed | caught (4) |
| duplicate checkpoint persistence removed, persistence layer only | **survives by design** — the sweep's own skip is an independent layer |
| ... at both layers | caught (7) |
| scheduler due logic moved early | caught (5) |
| replay marked as forward / forward marked replay / validator removed | caught (3 / 7 / 2) |
| Watch reaches the research store | caught (1) |
| LLM in the source-of-truth path | caught (1) |
| broker execution reachable | caught (1) |
| special-session fix reverted / torn-append fix reverted / tolerant reads reverted | caught (1 / 2 / 2) |

## Test Results

Full pytest **2378 passed**, 0 failed (2346 + 32 new). Ruff clean. `mypy app --strict` clean (172 files). `mypy app tests` 55 = baseline. Safety 67; no-lookahead 53; watch 101; LLM + broker lock 33. Regression suites (replay/validation contract, forward capture, outcome progression, scheduler, research truth, voting/availability) all pass; the replay golden hash is unchanged.

## Known Limitations

Single-process only; durable checkpoints need a persistent data volume; T0 is the analysis-start clock (M2); the dataset is gate-conditioned (M3); special sessions are approximated (M5); a torn record in the middle of a file is skipped and counted, not repaired.

## Blocking Findings

None remaining. (H1 below would have been blocking for "captured means persisted"; it was fixed.)

## High Findings

- **H1 (fixed):** torn JSONL tail silently lost the next captured record and broke reads/sweep. Residual: fragments are unrecoverable; cross-process atomicity uncertified.
- **H2 (fixed):** special-session horizons would have been permanently frozen as insufficient (first affected date in the packaged calendar: 2026-11-08).
- **H3 (open, deployment precondition):** checkpoint/observation durability requires `TIRE_DATA_ROOT` on a persistent Railway volume; nothing in `railway.toml`/`Procfile` provides one. Without it every deploy erases the research history. Not verifiable from the repository.

## Medium Findings

- **M1:** raw T0 evidence values (LTP, OI, ΔOI, IV, volume, bid/ask, PCR, futures, VWAP) are not on `ResearchObservation`; they are recoverable only through the analysis snapshot referenced by `audit_id`, and forward capture does not require `audit_id` (the test fixture has none). `audit_research_dataset.forward_without_audit_id` reports it. Recommended future gate, not implemented here (wide test ripple, behavior change).
- **M2:** T0 is the analysis-start `as_of`; live data is received after it (analysis latency, seconds) and the pipeline floors source timestamps to `as_of` (existing clock-skew handling). "Only information available at T0" is therefore exact to the analysis latency, not to the millisecond. Immaterial at session-close granularity.
- **M3:** capture is downstream of the shortlist gate (directional + selected contract). CONFLICT / DATA_INSUFFICIENT / no-direction analyses are never recorded, so the dataset is **selection-conditioned**: it can describe what happened after gated setups, not base rates. Any future aggregation must say so. (Not a correctness problem; `ResearchObservation` requires a direction and contract by design.)
- **M4:** outcome status vocabulary differs by source: forward summaries derive from state progression, replay from first-event price facts; a late-captured forward checkpoint withholds state fields, so its summary reads INSUFFICIENT even when price facts are determined. Never merge the two sources' counts.
- **M5:** special sessions cannot be live-captured (fail-closed) and their horizon close is the end-of-day approximation.

## Low Findings

- **L1:** legacy `neutral_or_unknown_evidence` (NEUTRAL and UNKNOWN in one list) exists only in the replay dataset "knew then" block; the forward record and lifecycle never touch it (tested) — no contamination path.
- **L2:** the live sweep does not filter `source != "LIVE"`; only reachable if repositories were mixed, which no code path does.
- **L3:** `scripts/run_daily_research_uat.py` builds its own repository instance on the live directory (separate process, separate lock).
- **L4:** checkpoints have no id; their logical key is (observation, horizon).
- **L5:** scheduler health is process memory and reads `NEVER_RAN` after a restart until the first tick.

## Deferred Findings

API authentication (and therefore a safe manual sweep trigger); SYSTEM_HALTED; cross-process/transactional JSONL hardening; dependency cleanup (`apscheduler`/`redis` unused); evidence-aware aggregation; historical option-chain/futures/news data; requiring `audit_id` at capture (M1).

## Live Certification Procedure

For the **first real OPEN session**; not executed here (no credentials, market closed).

1. **Preconditions.** Deployment (or local run) with a valid Upstox access token; a **persistent** `TIRE_DATA_ROOT`; nothing else writing to that directory; the branch at this commit.
2. **Environment variables.** `UPSTOX_ACCESS_TOKEN` (set), `TIRE_DATA_ROOT` (persistent path), `OUTCOME_SWEEP_ENABLED=true` (default), `OUTCOME_SWEEP_INTERVAL_SECONDS=900` (default), `BROKER_ORDER_EXECUTION_ENABLED` unset/false. Confirm `GET /api/health` shows `token_configured: true`, `broker_execution` disabled, `outcome_sweep.health` not `NOT_CONFIGURED`.
3. **Market session.** A normal trading day, between 09:30 and 15:15 IST (`session_window.session_window == "OPEN"`, `research_session_mode == "LIVE"`), not a special session.
4. **One-underlying scope.** `GET /api/research/daily?symbols=<ONE F&O SYMBOL>` — a single symbol, no whole-market scan.
5. **Expected live state.** The response `market_state` is `LIVE_SNAPSHOT`/`LIVE_STREAMING`; if the symbol yields no shortlisted candidate, nothing is captured (that is a valid outcome — pick another single symbol; do not lower any gate).
6. **Capture.** Note `observation_id` from `GET /api/research/history?symbol=<SYMBOL>`; `GET /api/research/{id}/capture` must return `verified: true`, `problems: []`, capture kind `FORWARD_LIVE_CAPTURE`, observed expiry equal to the chain's expiry, contract and underlying keys present, `evidence_availability` present.
7. **Persistence verification.** Copy `research_observations.jsonl`; run the audit: `python -c "import asyncio; from pathlib import Path; from app.persistence.jsonl_file import JsonlResearchOutcomeRepository as R; from app.orchestration.forward_capture import audit_research_dataset as a; print(asyncio.run(a(R(Path('<TIRE_DATA_ROOT>/persistence/research_outcomes')))))"` — expect `forward_valid=1`, `clean=True` (`forward_without_audit_id` should be 0 when the journal is wired).
8. **Restart verification.** Restart the process; the record is still readable; repeating the same request creates a *new* T0 (different id), never a duplicate of the first; `outcome_sweep.health` becomes `OPERATIONAL` after ~30 s.
9. **+1.** After the next trading session's 15:30 IST close (within ~15 min): `GET /api/research/{id}/outcome` → `horizons[0].state = AVAILABLE`, `checkpoints[0].evaluated_through` = that close, `captured_late = false`.
10. **+3.** Same after the third subsequent session (calendar: weekends/holidays do not count).
11. **+5.** Same after the fifth.
12. **T0 byte comparison.** `sha256sum research_observations.jsonl` at step 7 equals the hash after step 11 (the file may only grow by *other* observations; compare the observation's own line byte-for-byte).
13. **EvidenceAvailability comparison.** The record's `evidence_availability` at step 6 equals the one after step 11.
14. **Duplicate test.** Restart twice more and wait a full tick each time; the checkpoints file still has exactly one line per horizon; rerun the audit — `clean=True`.
15. **Final cleanup.** Nothing to delete (append-only). Record the audit output and the three checkpoint lines in the certification log; do not edit any journal file.

## Live Certification Status

**LIVE CERTIFICATION NOT PERFORMED** (market closed, no credentials). The automated suite certifies the architecture on the repository's mock-transport live-shape fixture only.

## Definition of Done

- [x] T0 field audit, immutability and no-future-information tests
- [x] Horizon isolation (A/B, C/D, E/F) with adversarial data
- [x] Restart/duplicate/missed-tick/torn-write behavior proven; two real defects fixed
- [x] Forward/replay, Watch, LLM, broker separation proven
- [x] Dataset quality contract defined and executable (`audit_research_dataset`)
- [x] All required mutations caught (one documented as defense-in-depth)
- [x] Full pytest, Ruff, mypy strict, safety, lookahead green
- [x] One focused commit, pushed normally

## Final Commit

The single commit containing this document, `certify: validate forward research loop integrity`, on top of `521ddbe`. A commit cannot embed its own hash; see `git log` on `phase-3-historical-validation`.

## Final Status

**CERTIFIED for single-process operation on fixtures — LIVE CERTIFICATION NOT PERFORMED.**

- Real live certification: **not performed**.
- Cross-process atomicity: **not certified**.
- Durable Railway volume: **required, unverified** (H3).
- API authentication gap: **remains** (no manual sweep trigger exists because of it).
- SYSTEM_HALTED: **not implemented**.
- JSONL hardening: **partial** — torn-tail isolation, tolerant counted reads; no cross-process or transactional guarantees.
- Historical F&O data: **not available** (no historical option chain, OI, IV, futures); replay remains price-only.
- Historical news: **not available**.
- Evidence-aware aggregation: **not implemented**.
- No new indicators, ML, prediction, profitability, win rate, ranking, provider or broker code was added.
- Sprint 3.7 was **NOT** started.
