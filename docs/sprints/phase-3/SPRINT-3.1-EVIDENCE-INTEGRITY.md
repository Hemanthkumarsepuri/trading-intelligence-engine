# Sprint 3.1 — Evidence Integrity

## Objective

Close finding **M-1** (evidence double-counting) from the Phase 3 forensic audit, and nothing else. **This sprint fixes M-1 only.**

## Baseline Commit

`94f80c9e67793470bf2032396b241f9efafecc95` on `phase-3-historical-validation` (working tree clean at start).

## Problem Identified

`OPTIONS_IV` could produce a directional vote independently of `OPTIONS_OI`, although both come from the same option-chain snapshot. A directional `IV skew` row (BULLISH/BEARISH) formed a second "independent" directional group beside OI evidence. Consequences:

- `overall_convergence()` could reach CONVERGENCE from OI + IV-skew alone, or manufacture a CONFLICT when IV skew opposed OI.
- `supporting_group_count()` (decision-engine `supporting_evidence_count`, and so `evidence_quality`) counted two "independent" groups for one observation.

## Root Cause

The policy existed only as documentation. `evidence_dependency.DEPENDENCIES` declared `OPTIONS_IV` as `directional=False, may_vote=False`, but nothing read `may_vote`. `EvidenceMatrix.group_verdict()` computed a verdict for every group from its rows' directions, and `row_iv_skew()` legitimately emits BULLISH/BEARISH. The declaration and the enforcement were disconnected.

## Architecture Decision

One source of truth, one enforcement point:

- **Policy:** `VOTING_GROUPS` / `group_may_vote()` in `app/domain/options/evidence_matrix.py`. It lives there because `evidence_dependency` imports `EvidenceGroup` from that module, so the reverse import would be circular.
- **Derived, not duplicated:** every `may_vote` in `DEPENDENCIES` is now `group_may_vote(<group>)`. The table can no longer disagree with enforcement (a test also asserts this for all groups).
- **Enforcement:** `EvidenceMatrix.group_verdict()` returns `NON_DIRECTIONAL` for any non-voting group. `group_verdicts()`, `overall_convergence()`, `supporting_group_count()`, `build_quality_assessment()`, direction analysis and the adversarial group-conflict check all read through it, so they are fixed without per-caller changes.
- **Row-level consumers:** `EvidenceMatrix.voting_rows(direction)` (used by `supporting_count()`) keeps raw "supporting rows" lists (adversarial bull/bear case, candidate supporting/contradicting evidence, no-trade explanation) consistent with the vote.
- **Observation preserved:** `row_iv_skew()` is unchanged and still reports its BULLISH/BEARISH observation; it simply cannot vote. Making IV an independent voter would require deliberately editing `VOTING_GROUPS`.

Voting groups: `UNDERLYING_PRICE_STRUCTURE`, `FUTURES`, `OPTIONS_OI`, `GLOBAL`, `RELATIVE_STRENGTH`. Non-voting: `OPTIONS_IV`, `LIQUIDITY`, `DATA_QUALITY`, `NEWS_EVENT` (the last three already only emitted NEUTRAL/UNKNOWN, so no behavior change for them).

## Files Changed

- `app/domain/options/evidence_matrix.py` — `VOTING_GROUPS`, `group_may_vote()`, enforcement in `group_verdict()`, `voting_rows()`, `supporting_count()`.
- `app/domain/options/evidence_dependency.py` — `may_vote` derived from `group_may_vote()`.
- `app/domain/options/adversarial_analysis.py` — bull/bear case use `voting_rows()`.
- `app/orchestration/options_intelligence_pipeline.py` — candidate supporting/contradicting evidence use `voting_rows()`.
- `app/orchestration/options_intelligence_report.py` — no-trade explanation supports/invalidate lists respect the policy.
- `tests/unit/options/test_evidence_voting_policy.py` — new.
- `tests/unit/options/test_decision_engine.py`, `tests/unit/options/test_direction_analysis.py` — existing fixtures updated (see Fixes).
- `docs/sprints/phase-3/SPRINT-3.1-EVIDENCE-INTEGRITY.md` — this document.

## Tests Added

`tests/unit/options/test_evidence_voting_policy.py` (23 cases, built from the real `row_pcr_change()` and `row_iv_skew()` builders):

- Same snapshot + OI directional + IV-skew directional ⇒ `supporting_group_count == 1`, IV group `NON_DIRECTIONAL` (the exact M-1 reproduction), and decision-engine `supporting_evidence_count == 1`.
- IV skew opposing OI does not create a CONFLICT; IV skew alone yields INSUFFICIENT_EVIDENCE.
- Non-voting rows excluded from raw supporting count and adversarial cases.
- Legitimate cases: OI + each of `UNDERLYING_PRICE_STRUCTURE`/`FUTURES`/`GLOBAL`/`RELATIVE_STRENGTH` still count as 2 independent groups; independent disagreement is still CONFLICT; intra-OI conflict is still CONFLICTING; IV-skew row still reports its observation; stale-chain withholding/UNKNOWN semantics preserved.
- Single source of truth: for every `EvidenceGroup`, `DEPENDENCIES.may_vote == group_may_vote()` and the matrix verdict matches; every group has exactly one dependency entry.

## Tests Executed

Targeted evidence tests; `tests/unit/options` + `tests/research_truth` + options pipeline integration; full pytest suite; Ruff; mypy; `tests/safety` (including all no-lookahead tests); broker-execution lock and LLM unit tests.

## Test Results

- Before the fix: 14 of the 23 new tests failed (5 behavioral assertions reproducing M-1; 9 on the missing policy API), 9 passed (legitimate controls).
- After the fix: new file 23/23 pass. Options/research-truth/pipeline: 770 passed.
- Full suite: **2226 passed**, 0 failed.

## Failures Encountered

1. After the fix, `test_quality_tiers_evidence_quality_strong_when_convergent_with_three_plus_supporting` failed (`supporting_evidence_count` 2, expected ≥3).

## Root Causes

1. The test (and 5 others) used `OPTIONS_IV` as one of "three independent groups". They encoded the M-1 defect itself, not an invariant.

## Fixes

Fixtures were re-pointed to a genuinely independent voting group so each keeps its original intent: `OPTIONS_IV` BULLISH → `GLOBAL` BULLISH (3 places in `test_decision_engine.py`, 3 in `test_direction_analysis.py`), and in the conflict-honesty test the bearish `OPTIONS_IV` → `FUTURES`. No assertion was weakened or removed. Full suite re-run afterward: green.

## Regression Results

Full pytest: 2226 passed. Ruff: `All checks passed!`. `mypy app` (strict, per `pyproject.toml`): `Success: no issues found in 168 source files`.

## Safety Verification

`tests/safety`: 67 passed, including the broker-execution lock. Broker execution remains impossible (`SAFETY_LOCK.txt` unchanged; no order/broker code touched). LLM unit tests and output boundaries: 33 passed with the broker lock tests; no LLM module changed. The diff touches only evidence-voting domain code, two report/pipeline list builders, and tests.

## No-Lookahead Verification

All no-lookahead tests in `tests/safety` pass (46 selected, 0 failed). The change is a pure function of already-computed rows; it reads no clock, fetches nothing and touches no time-indexed data.

## Performance Impact

Negligible: one frozenset membership check per group/row evaluation. Full suite ran in ~88 s.

## Known Limitations

- Voting policy is a static set; there is no runtime configuration (intentional).
- `IV skew` observations still appear in row-level displays (matrix table, "what we know", "options market showing") labeled with their group, as observations only. Their directional label is not a vote.
- `mypy tests` reports 84 pre-existing errors (identical count on the baseline commit, none in the new test file); the strict target is `app`, which is clean. Not addressed here.

## Deferred Findings

All other Phase 3 audit findings are deferred and untouched, including API authentication, SYSTEM_HALTED, dependency cleanup, historical replay changes, and historical option-chain providers. Sprint 3.2 was not started.

## Definition of Done

- [x] Regression test reproduced M-1 and failed before the fix
- [x] Passes after the fix; legitimate cases retained
- [x] One authoritative voting policy; dependency map derived from it
- [x] Full pytest, Ruff, mypy (`app`), safety and no-lookahead green
- [x] Broker execution and LLM boundaries unchanged
- [x] One focused commit, pushed normally

## Final Commit

The single commit that contains this document, `fix: enforce evidence group voting policy`, on top of `94f80c9`. A commit cannot embed its own hash; see `git log` on `phase-3-historical-validation`.

## Final Status

**COMPLETE — M-1 closed.** This sprint fixes M-1 only.
