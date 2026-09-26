# TIRE Product Contract

This is the canonical product contract for TIRE (Indian Market Options
Intelligence Terminal). Every phase of work is measured against it. A
principle is marked ENFORCED only when the implementation enforces it and a
test would fail if it stopped doing so. Status reflects the code as of the
forensic certification of `1fc5303`
([`docs/certification/FORENSIC_1fc5303.md`](../certification/FORENSIC_1fc5303.md))
plus Phase 0, which changed no application code.

## Product Identity

TIRE is an evidence-first research intelligence system for Indian markets.

It is:
- read-only
- research-oriented
- time-aware
- evidence-first
- auditable
- provenance-aware
- outcome-aware

It is NOT:
- a broker
- an execution platform
- a tips service
- a BUY/SELL engine
- a probability engine
- a confidence-score engine
- an expected-return engine
- a gambling system
- a black-box prediction engine
- a generic chatbot

TIRE discovers developing market structures, records exactly what was
knowable at each observation time (T0), explains why a research state
exists, preserves evidence provenance, identifies missing and conflicting
evidence, tracks confirmation and invalidation, and evaluates what happened
afterward — without ever modifying what it recorded at T0.

## Status vocabulary

| Status | Meaning |
|---|---|
| **ENFORCED** | Implemented on every known path, protected by tests; no open defect. |
| **PARTIAL** | Enforced on some paths; at least one open defect violates it. |
| **NOT ENFORCED** | No implementation enforces it. |
| **PROCESS** | A way of working, enforced by the phase gate rather than by code. |

A GREEN test suite does not by itself make a principle ENFORCED: several
open defects below sit behind passing suites that do not exercise them.

## 18 Core Principles

| ID | Principle | Invariant | Enforcement | Tests | Status | Defect |
|----|-----------|-----------|-------------|-------|--------|--------|
| P1 | Evidence before intelligence | Every research state, pattern and conclusion is derived from `EvidenceMatrix` rows and facts computed from timestamped provider data at `as_of`; nothing is asserted without an evidence row behind it. | `analyze_symbol()` builds the matrix before `build_quality_assessment()` / `decide()` / `derive_research_state()` / `classify_development()`; patterns read only computed facts. | `tests/research_truth/test_research_truth.py`, `tests/unit/options/test_development.py`, `tests/unit/options/test_decision_engine.py` | PARTIAL | H-5 (a fabricated basis-change fact produces the FUTURES_STRUCTURE pattern) |
| P2 | Intelligence before AI | The deterministic engine produces every research object; the LLM only paraphrases objects that already exist and is absent from the decision, capture and outcome paths. | `app/llm` is imported only by `app/api/main.py` for `/api/qwen/health` and `/api/research/explain`; fail-open. | `tests/integration/orchestration/test_forward_loop_certification.py` (LLM import closure; adapter-raises test), `tests/unit/llm/test_qwen_narrative.py` | ENFORCED | — |
| P3 | Validation before expansion | No new capability is added on top of an uncertified one; each phase passes its gate (tests, adversarial checks, docs, commit) before the next starts. | Phase gate rule in the integrity programme. | Phase certification reports | PROCESS — currently **not met** | C-1 (historical validation not certified); live certification not performed; browser certification blocked |
| P4 | No lookahead | No analysis, observation or outcome at instant T reads data that did not exist at T — including bars whose interval ends after T. | Repository contract `data_timestamp <= as_of`; `bounded_series()`; end-exclusive candle queries; outcome windows bounded to the horizon close. | `tests/safety/test_no_lookahead.py`, `tests/safety/test_technical_no_lookahead.py`, `tests/safety/test_audit_journal_no_lookahead.py`, `tests/integration/orchestration/test_forward_loop_certification.py` | PARTIAL — **OPEN** | C-1 (replay quote = the close of a bar stamped at its open: 15-minute lookahead). The suites check `ts <= as_of`, not bar duration. |
| P5 | Immutable T0 | A persisted `ResearchObservation` (and its forward-capture provenance, contract identity and evidence availability) is never rewritten; its id is a deterministic hash of contract identity and T0. | Frozen pydantic models; append-only JSONL with no update/delete; `save_observation_once`; `forward_observation_identity`; `verify_forward_observation`. | `tests/integration/orchestration/test_forward_capture.py`, `tests/integration/orchestration/test_forward_loop_certification.py` | ENFORCED (forward loop, single process) | Watch T0 is immutable once stored but client-asserted: M-5 |
| P6 | Outcomes never modify T0 | Checkpoints are separate append-only records; computing, sweeping or retrying an outcome writes nothing to the observation. | Separate `research_outcome_checkpoints.jsonl`; `save_checkpoint_once`; outcome engine only reads the observation. | `tests/integration/orchestration/test_outcome_progression.py`, `tests/integration/orchestration/test_outcome_scheduler.py`, `tests/integration/orchestration/test_forward_loop_certification.py` | ENFORCED | — |
| P7 | Evidence groups prevent double counting | Correlated rows from one source count as one group vote; non-voting groups (OPTIONS_IV, liquidity, data quality, news) never add support or direction anywhere — backend or UI. | `VOTING_GROUPS` / `group_may_vote()` single policy; `supporting_group_count()`; `overall_convergence()` per group. | `tests/unit/options/test_evidence_voting_policy.py`, `tests/unit/options/test_evidence_matrix.py`, `tests/unit/options/test_evidence_dependency.py` | PARTIAL | H-4 (UI `summarizeGroupDirection` counts raw rows incl. `options_iv`); L-1 (BOTH_SIDES_STRONG label uses row counts) |
| P8 | Stale evidence cannot vote | A stream that fails its freshness requirement contributes no direction and is shown as withheld; research state becomes CONFIRMATION_PENDING when a core stream is not current. | `withhold_stale_stream_rows`; `data_is_current` on price rows; `derive_research_state()` precedence. | `tests/unit/options/test_stale_stream_withholding.py`, `tests/unit/market/test_data_state.py`, `tests/unit/options/test_evidence_availability.py` | PARTIAL — **OPEN** | H-3 (market-context quotes have no freshness gate; MARKET_CONTEXT cannot be STALE); M-2 (closed-session last prints count as current) |
| P9 | Missing evidence is not neutral evidence | Absent history, a missing prior, missing future data or an unavailable stream is UNKNOWN / INSUFFICIENT / UNAVAILABLE — never NEUTRAL, STABLE, "no change" or "no follow-through". | `row_pcr_change` / `classify_price_oi` / `classify_basis_change` INSUFFICIENT without a prior; `EvidenceAvailability`; outcome `data_sufficient`. | `tests/integration/orchestration/test_options_intelligence_pipeline.py` (`test_pcr_change_row_is_unknown_on_first_run_then_real_on_second`), `tests/integration/orchestration/test_outcome_progression.py` (`test_missing_future_data_is_insufficient_and_never_neutral_confirmed_or_invalidated`) | PARTIAL | H-5 (a missing prior underlying quote is replaced by the current one instead of reading INSUFFICIENT); M-6 (no comparability window for priors) |
| P10 | AI is never market truth | No LLM output sets research state, freshness, confirmation, invalidation, direction, or any persisted record. | LLM output is display-only and never persisted; not imported by domain/capture/outcome modules. | `tests/integration/orchestration/test_forward_loop_certification.py`, `tests/unit/llm/test_qwen_narrative.py` | ENFORCED | M-4 affects what the LLM may *display*, not truth — see P12–P15 |
| P11 | No broker execution | No code path can place, modify or cancel an order. | No order code in `app/`; provider issues GETs only; allowlisted provider paths; startup refuses `BROKER_ORDER_EXECUTION_ENABLED=true`; `SAFETY_LOCK.txt`. | `tests/safety/test_broker_execution_lock.py`, `tests/safety/test_provider_endpoint_allowlist.py` | ENFORCED | — |
| P12 | No BUY/SELL recommendation | No response, UI element or narrative instructs the user to buy or sell. | Deterministic vocabulary has no BUY/SELL; Qwen output filter (`_PROHIBITED_KEYS`, `_CLAIM_RE`, `_FORBIDDEN_PROSE_RE`). | `tests/integration/api/test_daily_research_api.py` (`test_research_daily_no_forbidden_language_or_credential_leak`), `tests/unit/llm/test_qwen_narrative.py` | PARTIAL | M-4 (dict-valued Qwen fields bypass the filter) |
| P13 | No confidence percentage | No numeric confidence is emitted. (`research_confidence` is an ordinal evidence-quality label, never a percentage.) | Deterministic outputs carry no confidence number; Qwen filter. | `tests/unit/llm/test_qwen_narrative.py`, `tests/integration/api/test_daily_research_api.py` | PARTIAL | M-4 |
| P14 | No probability claims | No probability, likelihood or chance is emitted anywhere. | Pattern aggregation has only integer counts (no float field); Qwen filter. | `tests/unit/orchestration/test_pattern_aggregation.py`, `tests/unit/llm/test_qwen_narrative.py` | PARTIAL | M-4 |
| P15 | No expected-return claims | No expected return, gain, target or profit claim is emitted. | Decay/breakeven text is labelled "pure expiry arithmetic, not a profitability forecast"; Qwen filter. | `tests/integration/api/test_live_operation_api.py`, `tests/unit/llm/test_qwen_narrative.py` | PARTIAL | M-4 |
| P16 | No fabricated certainty | TIRE never presents a fact, change, confirmation or T0 it did not genuinely observe. | Fail-closed capture; honest INSUFFICIENT outcomes; LAST OBSERVED labelling. | `tests/integration/orchestration/test_forward_capture.py`, `tests/integration/api/test_dashboard_api.py` (`test_analyze_market_closed_never_fabricates_a_decision`) | PARTIAL | H-5 (fabricated basis widening); H-4 (UI directional tally from non-voting rows); M-2 (CONFIRMED_SETUP from closed-session data); M-5 (client-asserted Watch T0) |
| P17 | Prefer UNKNOWN / INSUFFICIENT / CONFLICT over invention | Where a fact cannot be established, the system says so instead of degrading silently to a default. | `derive_research_state()` precedence (DATA_INSUFFICIENT > CONFLICT > CONFIRMATION_PENDING); availability states; same-bar ambiguity → INSUFFICIENT. | `tests/unit/options/test_decision_engine.py`, `tests/integration/orchestration/test_outcome_progression.py` (`test_same_bar_ambiguity_is_insufficient_outcome_data_never_a_guess`) | PARTIAL | M-7 (calendar silently becomes weekend-only outside 2026); H-3 (stale context reads AVAILABLE) |
| P18 | Every research conclusion is traceable to timestamped evidence | Each observation carries per-stream provenance (label, source timestamp, retrieval time, source) and a reference to its full evidence snapshot; records are never lost or corrupted. | `ForwardCaptureProvenance.streams`; `audit_id` → `AnalysisSnapshot`; `audit_research_dataset`. | `tests/integration/orchestration/test_forward_capture.py`, `tests/integration/orchestration/test_forward_loop_certification.py` | PARTIAL | Sprint 3.6 M1 (`audit_id` not required at capture); H-2 (concurrent appends corrupt/lose market-data and journal records); M-5 (Watch T0 has no server-side provenance) |

**Summary:** ENFORCED 5 (P2, P5, P6, P10, P11) · PARTIAL 12 (P1, P4, P7, P8,
P9, P12–P18) · PROCESS, not met 1 (P3). No principle is claimed GREEN beyond
what the implementation enforces.

## Operational contract

Properties the product depends on that are not themselves research principles.

| Property | Invariant | Status | Defect |
|---|---|---|---|
| API authentication | Every route that changes state requires an authenticated operator. | **NOT ENFORCED** | H-1 |
| Persistence integrity | Every JSONL store survives concurrent writers within the process and malformed lines without data loss or read failure. | PARTIAL (research observation/checkpoint stores only) | H-2 |
| Cross-process safety | Two processes sharing a data root cannot corrupt or duplicate records. | NOT ENFORCED (single process assumed) | — (known limitation) |
| Kill switch | `SYSTEM_HALTED=true` halts research routes, Discover and the sweep. | **NOT ENFORCED** | M-3 |
| Trading calendar | Session arithmetic fails closed for any date the official holiday file does not cover. | NOT ENFORCED | M-7 |
| Outcome vocabulary | One definition of FOLLOW_THROUGH / FAILED_SETUP across forward and replay. | NOT ENFORCED (sources kept separate) | M-8 |
| Deterministic test baseline | The full suite gives the same result on any real date. | ENFORCED since Phase 0 (`pinned_api_clock`, `tests/integration/api/test_api_clock_pinning.py`) | M-1 — resolved in Phase 0 |

## Certification status

| Certification | Status |
|---|---|
| Forensic (repository) | `1fc5303`: **RED** — see `docs/certification/FORENSIC_1fc5303.md` |
| Historical replay | **NOT certified for historical truth** (C-1) |
| Live market | **NOT PERFORMED** |
| Browser (Playwright) | **BLOCKED** — Playwright MCP unavailable |

## Open defect register

| ID | Severity | Summary | Status |
|---|---|---|---|
| C-1 | CRITICAL | Historical replay lookahead (quote = close of the bar stamped at its open) | OPEN |
| H-1 | HIGH | No API authentication on state-changing routes; GET scans not session-gated | OPEN |
| H-2 | HIGH | Concurrent in-process JSONL appends corrupt unlocked stores; readers raise | OPEN |
| H-3 | HIGH | Market-context quotes vote without a freshness gate | OPEN |
| H-4 | HIGH | UI tally counts non-voting/correlated rows as direction | OPEN |
| H-5 | HIGH | Fabricated futures basis change raises research state | OPEN |
| M-1 | MEDIUM | Test time bomb (fixed expiry + real clock) | RESOLVED in Phase 0 (tests only) |
| M-2 | MEDIUM | CONFIRMED_SETUP from closed-session data | OPEN |
| M-3 | MEDIUM | `SYSTEM_HALTED` not wired | OPEN (runbook corrected) |
| M-4 | MEDIUM | Qwen claim filter bypass via non-string fields | OPEN |
| M-5 | MEDIUM | Watch T0 client-asserted; invalid symbol can mint a watch | OPEN |
| M-6 | MEDIUM | No comparability window for prior chain/futures observations | OPEN |
| M-7 | MEDIUM | Calendar covers 2026 only; no fail-closed | OPEN |
| M-8 | MEDIUM | Forward vs replay outcome vocabularies differ | OPEN |
| L-1 | LOW | BOTH_SIDES_STRONG label counts raw rows | OPEN |
| L-2 | LOW | Unused dependencies; false pypdf comment in `pyproject.toml` | OPEN |
| L-3 | LOW | `technical_price_levels` has no `as_of` | OPEN |
| L-4 | LOW | No fsync on research stores | OPEN |
| L-5 | LOW | Futures-only staleness does not force CONFIRMATION_PENDING (documented) | OPEN |

## Changing this contract

A principle's status may only be raised when the defect that holds it back is
fixed **and** a test that fails on the defect is committed with the fix. A new
defect that violates a principle lowers its status in the same change that
records the defect.
