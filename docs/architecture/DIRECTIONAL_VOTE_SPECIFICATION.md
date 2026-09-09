# Directional Vote Specification

Status: **HOLD.** This document is a deeper audit of the gate first recorded in [`DIRECTIONAL_VOTE_GATE.md`](DIRECTIONAL_VOTE_GATE.md) (2026-08-27) — it does not supersede that document, it extends it with a category-by-category classification and an explicit boundary table the prior pass did not build. No production code accompanies this document.

---

## 1. Executive Decision

**HOLD.** No document in this repository specifies what makes a technical fact "bullish" or "bearish." This is verified below by direct text extraction and repository search, not assumed. The gate closes only when the project owner answers the questions in §17 — this document does not answer them.

---

## 2. Current Verified Repository State

Verified fresh in this session:

```
pytest -q                  -> 131 passed
ruff check .                -> All checks passed!
mypy --strict app tests     -> Success: no issues found in 76 source files
```

Repository-wide search (`app/`, `tests/`) — file-count of matches, not just presence:

| Term | Files matched | What's actually there |
|---|---|---|
| `BULLISH` / `BEARISH` / `NEUTRAL` / `CONFLICT` | 0 | — |
| `confidence` | 2 | A docstring stating `TechnicalSnapshot` has none, and a test asserting the field must not exist |
| `directional` | 2 | Same pattern — docstring + test, both stating absence |
| `score` | 3 | Docstrings/tests stating "never an opaque score" |
| `threshold` | 6 | All `ProviderHealthTracker.failure_threshold` (circuit-breaker retry count — unrelated to trading) or docstrings explicitly declining to invent one |
| `setup` | 0 | — |
| `signal` | 5 | All MACD's standard `signal_line`/`signal_period` (a textbook indicator term) |
| `FULL_ALIGNMENT` / `PARTIAL_ALIGNMENT` / `REGIME_UNCLEAR` | 0 | — |
| `place_order` / `modify_order` / `cancel_order` | 0 | — |
| `BUY` / `SELL` | 0 | — |
| `recovery` / `revenge` / `target_recovery` | 0 | — |

`app/domain/{signals,risk,options,news}/`, `app/engines/`, `app/orchestration/`: confirmed 0-byte `__init__.py` only. `app/domain/regime/` and `app/domain/behavior/` confirmed still nonexistent as directories. `app/domain/technical/` confirmed to contain exactly the eight fact-only modules from the prior milestone (`series`, `trend`, `momentum`, `volatility`, `structure`, `volume`, `snapshot`, `structure_facts`) — no ninth file, no interpretation code added since the last gate.

---

## 3. Facts vs. Interpretation vs. Decision

Restated precisely because this document leans on the distinction throughout:

| Statement | Layer | Exists today? |
|---|---|---|
| `RSI(14) = 62` | Fact | Yes (`momentum.py`) |
| `swing_high[2] > swing_high[1]` → `HIGHER_HIGH` | Fact | Yes (`structure_facts.py`) |
| "RSI = 62 is bullish" | Interpretation | **No — the gate this document is about** |
| "RSI bullish + EMA bullish + MACD bullish → CE" | Decision | No, and structurally cannot exist without the Interpretation layer first |
| An order is placed | Execution | Permanently out of scope (`SAFETY_LOCK.txt`) |

---

## 4. D1 — Definition of "Directional Vote"

**§9's exact text:** *"each indicator category emits a `-1/0/+1` directional vote with a confidence."*

**Reading of the encoding:** the only coherent reading, given the surrounding text's consistent use of "bullish"/"bearish"/"neutral" vocabulary throughout §9–§13, is:

```
-1 = bearish evidence
 0 = neutral / no directional evidence
+1 = bullish evidence
```

No alternative encoding is proposed or supported anywhere in the docs (e.g. nothing suggests `-1/0/+1` means something else like "weak/medium/strong"). This part is **not ambiguous** — it is simply unimplemented. What is unspecified is not the *meaning of the three values*, but the **rule that assigns any given indicator reading to one of them**.

**A vs B vs C, evaluated against existing text only:**

- **(A) Generic interpretation of technical facts** — a single rule set applied uniformly regardless of strategy. Partially supported: §9 frames the vote as a property of the "indicator category" itself ("each indicator category emits..."), suggesting one canonical interpretation per category, not one per strategy. But STRATEGY_FRAMEWORK.md's evidence-gating language ("indicator thresholds... belong to specific `StrategyDefinition` implementations") argues against a single hardcoded rule set with no strategy input at all.
- **(B) Strategy-specific interpretation** — each `StrategyDefinition` defines its own vote rules independently. Contradicted by STRATEGY_FRAMEWORK.md's own `detect_setup(self, market_state: MarketState, mtf_context: MTFAlignment)` signature: `mtf_context` is a parameter *handed to* the strategy, not computed by it — meaning at least the MTF-level directional read is produced once, generically, upstream of any individual strategy. Pure Option B does not match this contract.
- **(C) A separate evidence layer, parameterized by strategy** — a shared computational *mechanism* whose *thresholds* are strategy-supplied configuration, not different code per strategy. This is the only option consistent with both texts simultaneously: §9's "each category emits a vote" (one mechanism) and STRATEGY_FRAMEWORK.md's "thresholds belong to strategies, evidence categories are declared per-strategy as mandatory/contributing" (strategy-supplied parameters).

**Recommendation, grounded only in the above:** **Option C** — a shared vote-computation mechanism (living where §7 of `DIRECTIONAL_VOTE_GATE.md` already placed it: `engines/setup_detection.py`'s own pipeline, upstream of individual strategies) whose actual threshold values are supplied as explicit, versioned, strategy-scoped configuration rather than hardcoded in that mechanism or duplicated per-strategy in code. This is not a new conclusion — it matches `DIRECTIONAL_VOTE_GATE.md` §6's synthesis, restated here with the A/B/C framing this task asked for.

---

## 5. D2 — Category-by-Category Classification

Every cell below is either directly sourced from ARCHITECTURE.md/STRATEGY_FRAMEWORK.md or marked `UNSPECIFIED`. No cell is a guess presented as fact.

| Category | Fact (already implemented) | Directional? | Possible Vote | Threshold Needed? | Threshold Owner | Replay Required? |
|---|---|---|---|---|---|---|
| EMA | `EMAResult.value` (single value) or a relationship between two EMA calls | UNSPECIFIED — §9 lists "Trend: EMA... ADX" as a category, implying a vote exists, but never states the relationship rule (e.g. EMA9 vs EMA21) | -1/0/+1, if a rule is supplied | Yes | UNSPECIFIED (per D1: recommended strategy-scoped config, not decided) | Yes (STRATEGY_FRAMEWORK.md "Replay Requirements") |
| RSI | `RSIResult.value` | UNSPECIFIED — §9 lists RSI under "Momentum," implying a vote, but no value/band is stated anywhere | -1/0/+1, if a rule is supplied | Yes | UNSPECIFIED | Yes |
| MACD | `MACDResult.macd_line/signal_line/histogram` | UNSPECIFIED — same pattern; §9 lists MACD as a momentum category | -1/0/+1, if a rule is supplied | Yes | UNSPECIFIED | Yes |
| ATR | `ATRResult.value` | UNSPECIFIED whether directional at all — see §9 of this document (D6) for the dedicated analysis; §9 of ARCHITECTURE.md lists ATR only under "Volatility," never under a directional category | Likely none — no doc ever pairs ATR with -1/0/+1 language | N/A if non-directional | N/A if non-directional | N/A if non-directional |
| VWAP | `VWAPResult.value/deviation/deviation_pct` | UNSPECIFIED — §9 lists VWAP under "Structure," alongside swing S/R, not under a named directional category, but the deviation-from-VWAP concept (price above/below) is a natural directional signal in common TA practice; nothing in this repo states that rule | -1/0/+1, if a rule is supplied | Yes | UNSPECIFIED | Yes |
| Relative Volume | `RelativeVolumeResult.ratio` | UNSPECIFIED whether directional at all — see D6 | Likely none on its own — §9 explicitly frames volume as confirming breakout validity, not as an independent directional vote | N/A if confirmation-only | N/A if confirmation-only | N/A if confirmation-only |
| Swing Structure (`HH`/`LH`/`EQUAL_HIGH`/`HL`/`LL`/`EQUAL_LOW`) | `StructureFactsResult.high_comparisons/low_comparisons` | UNSPECIFIED — see D5, no confirmation-count rule exists to turn a sequence of comparisons into a directional read | -1/0/+1, if a confirmation rule is supplied | Yes (a *count/pattern* threshold, not a numeric one) | UNSPECIFIED | Yes |

Row-by-row answers to the nine sub-questions this task posed are folded into the table above and D5/D6 below rather than repeated seven times; every category's answer to "can the generic Technical Engine decide it?" is **no** — `domain/technical` is confirmed (§2, and its own tests) to contain no interpretation vocabulary, and per the corrected ARCHITECTURE.md §5, it must not.

---

## 6. D3 — Threshold Location

Five candidate locations evaluated, no numeric value proposed for any of them:

| Location | Verdict | Why |
|---|---|---|
| `domain/technical` | **Rejected** | Confirmed pure-fact-only by its own tests (`model_fields` scanned for forbidden direction/score fields); ARCHITECTURE.md §5 (corrected) explicitly forbids cross-timeframe or interpretive logic here. A threshold living here would be indistinguishable from a fabricated fact. |
| `engines/setup_detection.py` | **Correct home for the *mechanism*, not the *value*** | This is where §9 says votes are eventually combined and where D1's Option C mechanism belongs — but the mechanism reading a threshold is different from the mechanism defining one. |
| `StrategyDefinition` | **Correct home for *which* thresholds a given strategy uses, and for strategy-specific ones** | STRATEGY_FRAMEWORK.md: "indicator thresholds... belong to specific `StrategyDefinition` implementations." |
| Strategy configuration (external, versioned) | **Correct home for the *numeric values themselves*** | Consistent with Addendum A7's stated principle: thresholds "are only loosened based on paper-trading evidence" — implies they are data (config), not code, so they can change without a code change/review cycle each time evidence updates them. |
| Replay experiment configuration | **Correct home during validation, before any threshold is trusted** | STRATEGY_FRAMEWORK.md's "Replay Requirements" section: a strategy "is not eligible to run in real-time mode until it has been exercised through `replay/`... and reviewed." A candidate threshold set lives here first, is promoted to strategy configuration only after passing that review. |

**Design principle stated explicitly, as this task requires:** the architecture should make it structurally impossible for a threshold to enter `domain/technical` — this is already true today (verified by the boundary tests on `TechnicalSnapshot`/`StructureFacts`) and should remain a standing invariant for any future engine work, not something to re-verify manually each milestone.

---

## 7. D4 — Confidence Semantics

**`CONFIDENCE SEMANTICS NOT DEFINED`** for the per-category vote (§9's "-1/0/+1 with a confidence" — the word appears, nothing defines it). Partially and only qualitatively defined for the aggregate: ARCHITECTURE.md §13, `SignalScore.confidence: float # separate from composite — reflects data completeness`. This is a *type hint plus one sentence*, not a formula, scale, or worked example, and it explicitly applies only to the aggregate, not to any individual category's vote confidence.

**Explicit separation this task requires — data quality vs. directional evidence vs. strategy confidence:**

| Concept | Where it exists today | What it measures |
|---|---|---|
| **Data quality** | `DataQualityGate` (`data/validation/quality_gate.py`) — fully implemented | Whether the *input* is trustworthy at all (fresh, complete, structurally valid, provider healthy) |
| **Directional evidence confidence** | Not implemented; named in §9 only | How strongly one category's raw fact supports a direction — **undefined** |
| **Strategy/aggregate confidence** | Named in §13 only, one sentence | "Data completeness" per §13's own words — but this is explicitly about *evidence being present*, not about *conviction in a trading direction* |

**These must not be conflated**, exactly as this task's brief states, and nothing in the current repository conflates them — `DataQualityGate` never touches a directional value, and no code path derives a percentage "trading confidence" from freshness/completeness/provider-agreement. This document does not invent such a relationship and explicitly recommends none be invented without owner sign-off, because "fresh + complete + two providers agree" is a statement about *whether the inputs can be trusted*, not about *whether the market is about to move in a given direction* — collapsing the two would silently launder data-quality assurance into an unearned trading conviction.

**Safest architecture, given the above:** treat confidence as **deferred entirely** until a computed form is justified by evidence (this was already the leading candidate in `DIRECTIONAL_VOTE_GATE.md` §9, restated here as the recommendation, not a new option). If and when the owner chooses to define it, `DataQualityGate`'s output should remain a *precondition* for even considering a directional vote (no vote should be computed from data that failed the gate) — not an *input* blended into that vote's confidence value.

---

## 8. D5 — Structure Interpretation Ownership

No document defines when `HIGHER_HIGH`/`HIGHER_LOW` sequences become "bullish structure," "reversal," or "consolidation." `structure_facts.py`'s own docstring states this explicitly and declines to invent a confirmation-count rule (e.g. "2 HH + 2 HL = bullish"), and this audit found no ARCHITECTURE.md or STRATEGY_FRAMEWORK.md text that supplies one either.

**Future ownership, by the same reasoning as D1/D3:** the *mechanism* that would synthesize a sequence of `StructureFactsResult` comparisons into a structure verdict belongs alongside the other generic-vote mechanisms (`engines/setup_detection.py`'s pipeline) — but the *rule* (how many consecutive HH/HL, over what window, tolerating how many counter-moves) is exactly the kind of threshold D3 assigns to strategy configuration, validated through replay before use. No such rule exists today; none is proposed here.

---

## 9. D6 — ATR and Relative Volume

**ATR:** ARCHITECTURE.md §9 places ATR under "Volatility" only — never under a directional category, and never paired with bullish/bearish language anywhere in the repository. Separately, RISK_AND_BEHAVIOR.md's `stop_loss_reference()`/position-sizing design (Phase-0 architecture, risk engine) is the kind of place ATR-derived distances are expected to feed as a **risk input** (stop distance, position sizing denominator), not as a directional vote. Given the repository's own categorization: **ATR is UNSPECIFIED as directional and has clear textual support as a risk/volatility input.** No rule anywhere assigns it a vote.

**Relative Volume:** ARCHITECTURE.md §9's own "Conflict handling" prose states relative volume's role explicitly: *"Volume: Relative volume vs N-period average, VWAP deviation... Confirms breakout/breakdown validity."* This is the clearest textual anchor of any category in this whole analysis — the architecture already describes relative volume as **confirmation**, not as an independent directional vote. **Recommendation, directly sourced (not invented): relative volume should not participate in the -1/0/+1 vote set at all; it should remain a confirmation/context signal consumed by whatever eventually implements breakout classification (D3's "structure/breakout mechanism"), exactly as §9 already describes it.** This is the one place in this document where the existing text is concrete enough to state a recommendation with confidence rather than marking the cell fully `UNSPECIFIED` — but it is still a recommendation for the owner to confirm, not an implemented rule.

---

## 10. D7 — MTF Dependency

Confirmed again this session: `domain/technical` remains strictly single-timeframe (§2's search; `TechnicalSnapshot`'s own tests). The required input chain for MTF alignment, once it can be built, is exactly:

```
TechnicalSnapshot(instrument, Daily,  as_of=T)  ─┐
TechnicalSnapshot(instrument, 1H,     as_of=T)  ─┤
TechnicalSnapshot(instrument, 15M,    as_of=T)  ─┼─→  per-timeframe directional evidence  ─→  MTF alignment
TechnicalSnapshot(instrument, 5M,     as_of=T)  ─┘        (D1/D2's unresolved gate)         (§10, already specified)
```

Every `TechnicalSnapshot` call already shares the same `as_of` contract (proven by the existing replay-equivalence test) — nothing about the MTF layer changes the no-look-ahead requirement, it only adds *more calls* to an already-safe primitive. **Not implemented; not proposed for implementation here.**

**CONFLICT cannot become BUY/SELL by weighted averaging:** re-confirmed. ARCHITECTURE.md §10's own worked example (daily+1h bullish, 15m+5m bearish → `CONFLICT`) is stated as a discrete state, explicitly contrasted with a weighted-average approach the document rejects by name. Nothing in this audit's findings weakens that — if anything, the fact that the *inputs* to alignment (per-timeframe directional evidence) don't exist yet makes it structurally impossible today to build a weighted-average shortcut even by accident, since there is nothing to average.

---

## 11. D8 — StrategyDefinition / Risk / Trade Gate Boundary

Built from RISK_AND_BEHAVIOR.md's exact function list (re-read in full this session) cross-referenced against STRATEGY_FRAMEWORK.md's `StrategyDefinition` Protocol:

| Rule | Generic Technical | Setup Engine | StrategyDefinition | Risk | Trade Gate |
|---|---|---|---|---|---|
| RSI interpretation (value → vote) | No (fact only) | Mechanism, per D1 | Supplies which threshold (D3) | No | No |
| EMA relationship (vote) | No (fact only) | Mechanism, per D1 | Supplies which threshold | No | No |
| MACD interpretation (vote) | No (fact only) | Mechanism, per D1 | Supplies which threshold | No | No |
| Structure confirmation (D5) | No (fact only) | Mechanism, per D5 | Supplies which threshold | No | No |
| Breakout threshold (volume % + S/R significance) | No | Mechanism (partially specified, §9) | Supplies which threshold | No | No |
| Volume confirmation | No | Consumed as confirmation input, per D6 | — | No | No |
| Entry condition (`trigger_level`, `structural_basis`) | No | — | **Yes** — `detect_setup()` is where this is decided (STRATEGY_FRAMEWORK.md) | No | No |
| Invalidation | No | No | Declares *which structural rule* (`invalidation_rule()`) | Computes the *actual price* (`invalidation_level()`, RISK_AND_BEHAVIOR.md) | Consumes the result (`NO_STOP_LOSS` check) |
| Target | No | No | Declares *method/minimum R:R* (`risk_reward_policy()`) | Computes the *actual zones* (`target_zones()`) — independently re-validated, per STRATEGY_FRAMEWORK.md's own text, never trusting the strategy's stated policy alone | Consumes `RISK_REWARD_VALID` |
| Position sizing | No | No | **No role** — RISK_AND_BEHAVIOR.md's `position_size()` takes only `AccountRiskConfig` + the computed levels, never a strategy input | **Yes**, exclusively | Consumes `RISK_LIMITS_VALID` |

The two rows worth calling out explicitly, since they're not simple single-owner rows: **invalidation** and **target** are each split between a strategy declaring *intent/method* and the risk engine independently computing the *actual number* — this split is stated directly in both source documents (STRATEGY_FRAMEWORK.md: *"a strategy's declared policy is a stated intent, not a substitute for the deterministic recomputation"*), not inferred here. **Position sizing has no strategy input path at all** — this is the structural mechanism, already documented before this audit, that makes "recover ₹30,000" impossible to wire in even accidentally: there is no parameter in `position_size()`'s design for a desired outcome, only account-level risk config and already-computed structural levels.

---

## 12. D9 — Replay Validation Lifecycle (conceptual only — not built here)

```
candidate rule (threshold/vote definition, owner-supplied, per D3)
        ↓
historical replay — HistoricalProvider + bounded_series(), already proven safe (131/131 tests,
        dedicated no-look-ahead + replay-equivalence suites) — no new mechanism required
        ↓
performance / failure analysis — NOT specified anywhere in this repo what "acceptable" looks like;
        no performance threshold exists or is proposed here
        ↓
behavioral analysis — cross-check against RISK_AND_BEHAVIOR.md's Part 2 detectors once they exist,
        to catch a rule that would systematically trigger FOMO/overtrading-shaped patterns
        ↓
approval — a human review step; STRATEGY_FRAMEWORK.md frames this as "a process requirement...
        not (yet) an automated CI check"
        ↓
production strategy — only after paper-trading evidence accumulates "statistically meaningful"
        (STRATEGY_FRAMEWORK.md declines to pre-define this number; set from real data later)
```

No performance thresholds are proposed by this document. The lifecycle's *shape* is derivable from existing text; its *acceptance criteria* are not, and are explicitly left for the owner/evidence, matching STRATEGY_FRAMEWORK.md's own stated position.

---

## 13. D10 — Paper Trading Validation Boundary

ARCHITECTURE.md §20 is precise about the entry point: `TradeCandidate (passed gate) → paper_trading/tracker.py`. This is a load-bearing detail for this gate: **paper trading only ever receives a candidate that has already passed the full deterministic trade gate** — meaning an unvalidated directional rule cannot reach paper trading directly. The correct reading of the lifecycle is therefore:

- **Replay (§12 above)** is where a *directional rule* moves from `UNVALIDATED` toward usable — it is rule-level, historical, fast-iterating, and already fully supported by existing infrastructure.
- **Paper trading** is where a *complete strategy* (already built on replay-validated rules, already passing the trade gate deterministically) accumulates live-timed, consequence-free evidence — it is strategy-level, not rule-level, and does not exist yet.
- **Real execution** remains a third, separate state, gated by `SAFETY_LOCK.txt`'s explicit preconditions and, per Addendum A7, a distinct Phase 8 review — untouched by anything in this document.

The three states — analysis-only, paper trading, real execution — are already architecturally separate in the existing documents (§20, `SAFETY_LOCK.txt`, Addendum A7's phase table) and nothing found in this audit blurs that separation. No paper-trading code is proposed here.

---

## 14. Safety Invariants

All checked against the current repository state in §2; none are affected by this document, since it changes no production code.

---

## 15. Explicit Unresolved Questions

1. The exact vote rule per category (EMA/RSI/MACD/VWAP/structure) — completely unspecified.
2. Whether VWAP deviation participates in the vote set or is confirmation-only like relative volume (D6 resolved relative volume with high confidence from text; VWAP's own categorization in §9 is more ambiguous — it sits under "Structure" alongside swing S/R, not clearly directional or clearly confirmation-only).
3. The structure confirmation-count rule (D5).
4. Confidence's computed form, if any is ever adopted (D4).
5. The breakout volume-confirmation number and S/R-significance rule (carried over from `DIRECTIONAL_VOTE_GATE.md`, unchanged by this audit).
6. Whether Option C's "strategy-scoped configuration" should be a single shared config schema across all future strategies or genuinely per-strategy free-form config — not addressed by any existing document.

---

## 16. Documentation Corrections Made

**None.** The one stale cross-reference noted in `DIRECTIONAL_VOTE_GATE.md` §4 (§13's "Phase 1" phrase predating the Addendum A7 renumbering) was re-confirmed present and is, again, not corrected here — it does not affect this document's coherence, and this task's instructions caution against broad cleanup. It remains logged in the prior document for whoever next touches §13.

---

## 17. Exact Decisions Required From the Project Owner

**BLOCKING** (implementation cannot proceed on any directional-interpretation work without these):

- **O1.** Per-category vote rule content — at minimum EMA, RSI, MACD, and a decision on whether VWAP deviation is in or out of the vote set (§15.2).
- **O2.** Confirm or reject this document's D6 recommendation that ATR is non-directional (risk input) and relative volume is confirmation-only, not part of the vote set.
- **O3.** The structure-confirmation rule (D5) — or an explicit decision to leave structure permanently fact-only with no synthesized verdict.

**NON-BLOCKING** (narrow further work once O1–O3 are answered, but don't block starting on them):

- **O4.** Confidence semantics (D4) — "deferred entirely" is a complete, valid answer requiring no further owner action.
- **O5.** Breakout's missing volume threshold and S/R-significance rule (D3/D5) — independent of O1–O3; breakout classification can be deferred as a whole without blocking generic per-category voting.
- **O6.** Whether strategy-scoped configuration is a shared schema or per-strategy free-form (§15.6) — an implementation-shape question, answerable once O1 exists to shape it.

---

## 18. Recommended Next Implementation Milestone

**None, until at least O1–O3 are answered.** If and when they are, the recommended next milestone — not started here, not scaffolded here — would be the generic vote-computation mechanism in `engines/setup_detection.py` (Option C's shared mechanism from D1), reading its threshold values from owner-supplied configuration rather than any hardcoded rule, tested first and exclusively through replay per D12/D9's lifecycle, before any live or paper-trading exposure. Until O1–O3 are answered, the correct next action for this project is **more replay/data-foundation hardening, or explicit owner specification work — not code against this gate.**
