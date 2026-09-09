# TIRE — 95%+ Research Reliability & Performance Quality Scorecard

This scorecard is computed from the canonical suite in
`tests/research_truth/test_research_truth.py` — **105 explicit scenarios**
covering data freshness/completeness, options/contract quality, evidence
convergence, session context, cash/breadth context, research-state
correctness, and safety. It is a **research-truth** metric (does TIRE
correctly represent freshness/completeness/state/blocker/provenance?),
**not** a prediction-accuracy or profitability metric — see Section 1/30
of the governing prompt for why those are deliberately different
questions this scorecard does not answer.

Methodology: every scenario is a pure, deterministic assertion against
real domain functions (`classify_freshness_label`, `assess_liquidity`,
`determine_blockers`, `derive_research_state`, `classify_session`,
`classify_sample_breadth`, `decide`, etc.) — no mocked/faked
"correctness," no manual judgment calls. `correct_cases / total_valid_cases`
is computed directly by
`test_overall_pass_rate_and_zero_tolerance_categories`, which prints the
per-category breakdown and fails the build if any zero-tolerance case
fails, independent of the aggregate percentage (Section 6's explicit
requirement that a single systematic failure class can matter more than
the headline number).

## Headline result (measured, this session)

```
TIRE research-truth suite: 105/105 passed (100.0%)
  CASH:            10/10  (100.0%)
  DATA:            17/17  (100.0%)
  EVIDENCE:        16/16  (100.0%)
  OPTIONS:         18/18  (100.0%)
  RESEARCH_STATE:  17/17  (100.0%)
  SAFETY:          15/15  (100.0%)
  SESSION:         12/12  (100.0%)
```

**Overall research-truth score: 100.0%** (≥95% target: **met**).

This number should be read as "100% of the *defined* scenarios this
session was able to enumerate," not as "TIRE is 100% correct in every
possible real-market case." The suite is a floor, not a ceiling — see
Section 15 (Remaining limitations) below and in `TIRE_QUALITY_GATE.md`.

## Per-dimension breakdown (Section 29 targets)

| Dimension | Target | Measured | Status |
|---|---|---|---|
| Research truth (overall) | ≥95% | 100.0% (105/105) | MET |
| Data freshness correctness | ≥95% | 100.0% (17/17 DATA scenarios; freshness-specific SAFETY scenarios also 100%) | MET |
| Research-state correctness | ≥95% | 100.0% (17/17 RESEARCH_STATE scenarios) | MET |
| Explanation correctness | ≥95% | 100.0% (blocker-explanation scenarios across OPTIONS/RESEARCH_STATE/SAFETY, including the regression case below) | MET |
| Contract-quality correctness | ≥95% | 100.0% (18/18 OPTIONS scenarios) | MET |
| Provenance correctness | ≥95% | 100.0% (price-source/freshness provenance scenarios in DATA/SAFETY) | MET |
| Scan completeness | report actual % | **100%** this run (`Stage 1: 210/210`, `Stage 2: 30/30`, 0 failed symbols) — see caveat below | REPORTED |
| Critical safety defects | 0 required | **0** (15/15 SAFETY scenarios pass, all zero-tolerance) | MET |
| False confirmation | 0 target | **0** found (no scenario or browser UAT observation showed a confirmed/tradeable claim without real supporting evidence) | MET |
| False BUY/SELL behavior | 0 required | **0** (`FinalDecision` vocabulary has no BUY/SELL member; verified by `safety.no_buy_sell_member_anywhere_in_final_decision` and by grep for order-execution functions in `app/`) | MET |

**Scan completeness caveat:** 100% coverage was achieved in the run
measured for this scorecard, but coverage is a per-run measurement, not
a guaranteed constant — a provider outage or rate-limit event could
honestly produce a lower number on a different run. The scan snapshot
mechanism (`ResearchScanSnapshot`) is what makes that variability
visible instead of hidden; see `docs/TIRE_SCAN_PERFORMANCE.md` Section 6
for a real run that met coverage but missed the *duration* target.

## Zero-tolerance categories (Section 7) — 0 failures required

All of the following are covered by dedicated, zero-tolerance-flagged
scenarios in the suite, and **all currently pass**:

- Broker execution disabled by default, and tampering is caught by a runtime assertion
- No `BUY`/`SELL` member anywhere in `FinalDecision`
- Stale directional evidence never votes (hidden or otherwise)
- Volume never votes directionally on its own
- Missing data is never treated as confirmation
- Cash/breadth context never becomes a hidden voting `EvidenceGroup`
- No false `LIVE` labeling of stale/unavailable/provider-down data
- No fabricated future timestamps in evidence-row detail examples
- No probability/confidence-score member in the decision vocabulary
- No order-execution functions anywhere under `app/`
- **No contradictory primary explanations** (`DATA_INSUFFICIENT` primary blocker text never uses confirmatory language)
- **No secondary blocker explanation duplicates a different blocker class's text** — this is a real regression case (see below)

## A real defect found and fixed during this session's own browser UAT

While re-running browser UAT against the live dashboard after the
primary-blocker fix, RELIANCE/NIFTY/BANKNIFTY (all `CONFLICT` this
session, since all three also had an illiquid contract) showed:

```
PRIMARY BLOCKER: CONFLICT
evidence matrix convergence is CONFLICT -- no coherent bias to act on

Also true right now (secondary, not the main reason):
• CONTRACT_UNUSABLE: evidence matrix convergence is CONFLICT -- no coherent bias to act on   <- WRONG, duplicated text
```

Root cause: `determine_blockers()` reused `decision.reasoning` (the
*overall* decision's reasoning, which describes whatever `decide()`
actually keyed off) as the `CONTRACT_UNUSABLE` blocker's own
explanation, instead of a explanation specific to that blocker class.
When `CONFLICT` was also true, `decision.reasoning` described `CONFLICT`
— so the "secondary" `CONTRACT_UNUSABLE` line silently repeated the
`CONFLICT` explanation verbatim, which is exactly the "contradictory
explanation" failure mode Section 4 of the governing prompt describes,
just in the *secondary* slot rather than the primary one.

Fix: `CONTRACT_UNUSABLE`'s explanation is now built independently from
`decision.reasoning`, always describing the actual contract-quality fact
(illiquidity vs. general contract-quality insufficiency). A permanent
regression scenario (`safety.secondary_blocker_explanation_never_duplicates_a_different_blockers_text`,
zero-tolerance) was added to the suite, and the live fix was
re-verified in the browser against RELIANCE (see `TIRE_QUALITY_GATE.md`
Section 4).

## Suite composition

105 scenarios across 7 required categories (DATA, OPTIONS, EVIDENCE,
SESSION, CASH, RESEARCH_STATE, SAFETY), all enforced by
`test_suite_has_at_least_100_scenarios_across_all_required_categories`
(fails the build if the suite ever shrinks below 100 or drops a
category). This does **not** replace any pre-existing test — it is an
additive, dedicated suite under `tests/research_truth/`.
