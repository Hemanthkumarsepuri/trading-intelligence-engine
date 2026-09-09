# Directional Vote Specification Gate

Status: **HOLD — specification insufficient for implementation.** Architecture decision document, not an implementation plan. No production code accompanies this document; none should be written against it until the project owner resolves the decisions in §16.

Supersedes/extends: ARCHITECTURE.md Addendum B4 (2026-08-27), which first recorded this gate at a higher level. This document is the detailed audit that Addendum B4 promised.

---

## 1. Current Verified Architecture State

Re-verified fresh in this session, project `.venv`, before writing this document:

```
pytest -q                    -> 131 passed
ruff check .                  -> All checks passed!
mypy --strict app tests       -> Success: no issues found in 76 source files
```

Repository-wide search (`app/`, `tests/`) for `BULLISH|BEARISH|NEUTRAL|directional vote|confidence|threshold|score|signal|setup|FULL_ALIGNMENT|PARTIAL_ALIGNMENT|CONFLICT|REGIME_UNCLEAR`:

| Term | Found in code (`app/`)? | Where |
|---|---|---|
| `BULLISH`/`BEARISH`/`NEUTRAL` | No | Only in docstrings/tests *stating these must not exist* |
| `confidence` | No | Only in docstrings ("no confidence score") and a test's forbidden-field list |
| `threshold` | No (trading sense) | Only `ProviderHealthTracker.failure_threshold` — an unrelated circuit-breaker concept |
| `score` | No | Only in docstrings ("never an opaque score") |
| `signal` | No (trading sense) | Only MACD's standard `signal_line`/`signal_period` — a textbook indicator term, not a trade signal |
| `setup` | **Zero matches anywhere in `app/` or `tests/`** | — |
| `FULL_ALIGNMENT`/`PARTIAL_ALIGNMENT`/`REGIME_UNCLEAR` | **Zero matches anywhere in `app/` or `tests/`** | Documentation-only, in `ARCHITECTURE.md` |

**Conclusion:** no directional-vote logic, no MTF alignment logic, no setup-detection logic, and no hidden threshold logic exists anywhere in the codebase. `BROKER_ORDER_EXECUTION_ENABLED=false` confirmed in `.env.example` and `app/config/settings.py`; no `place_order`/`modify_order`/`cancel_order` anywhere. This matches the milestone report's claims — verified independently here, not assumed.

`app/domain/technical/` currently contains exactly: `series.py`, `trend.py`, `momentum.py`, `volatility.py`, `structure.py`, `volume.py`, `snapshot.py`, `structure_facts.py` — all single-timeframe, all fact-only, confirmed by the same search above finding no interpretation vocabulary inside them.

---

## 2. Why the Directional-Vote Boundary Exists

Every layer built so far (Data Foundation, Technical Engine, TechnicalSnapshot, StructureFacts) is a *pure computation of an already-well-defined mathematical quantity* — an EMA value, a swing-price comparison. Those have one correct answer, independent of opinion. A directional vote ("is RSI=62 bullish?") is categorically different: it requires a **threshold decision with financial consequences**, and no such decision has evidence behind it yet (no replay run, no paper-trading data). Building it now would mean encoding an unvalidated trading opinion as if it were a fact — exactly the failure mode this whole project's phase-gating exists to prevent (ARCHITECTURE.md Addendum A7: signal-engine thresholds are explicitly the *last* deterministic engine built, tuned only against replay/paper evidence that doesn't exist yet).

---

## 3. Facts vs. Interpretations vs. Decisions

| Layer | Example | Status |
|---|---|---|
| **Fact** | `EMA(9) = 24,812.50` | Implemented (`trend.py`) |
| **Fact** | `swing_high[2] (24,900) > swing_high[1] (24,850)` → `HIGHER_HIGH` | Implemented (`structure_facts.py`) |
| **Interpretation** | "EMA9 > EMA21 means bullish" | **Not specified anywhere. Not implemented.** |
| **Interpretation** | "Daily=bullish + 1h=bullish + 15m=bearish + 5m=bearish → `CONFLICT`" | Discrete-state *shape* specified (§10); the per-timeframe bullish/bearish inputs to it are not |
| **Decision** | "This is a valid CE candidate" (`Setup`) | Shape specified (STRATEGY_FRAMEWORK.md); cannot be produced without the Interpretation layer existing first |
| **Decision** | An order is placed | **Permanently out of scope for this system** (SAFETY_LOCK.txt) |

These must never be conflated: `EMA value` → `EMA interpretation` → `directional vote` → `MTF alignment` → `setup` → `trade candidate` → `trade approval` → `order` is a strict one-way chain. Nothing built so far crosses from Fact into Interpretation. This document's entire purpose is to determine what would be required to take that one step — and to conclude that the requirement is not yet met.

---

## 4. Existing Specification, Extracted Verbatim

**ARCHITECTURE.md §9** (Technical Analysis Architecture): *"each indicator category emits a `-1/0/+1` directional vote with a confidence, not a raw number consumed downstream. `engines/signal_engine.py` — not the technical engine itself — is where votes are combined... The technical engine's job stops at 'what does each category say, independently, with what confidence.'"*
→ Specifies the **output shape** of a vote (`-1/0/+1` + confidence) and that **combination** happens in `signal_engine.py`. Does **not** specify where votes are **produced**, or the rule that produces them.

**ARCHITECTURE.md §10** (Multi-Timeframe Engine): defines the discrete alignment states precisely — `FULL_ALIGNMENT | PARTIAL_ALIGNMENT | CONFLICT | REGIME_UNCLEAR` — and the rule for *combining already-known* per-timeframe reads into one of these four labels. Does **not** specify how a single timeframe's read becomes "bullish"/"bearish"/"neutral" in the first place.

**ARCHITECTURE.md §13** (Signal Engine): `SignalScore.confidence: float # separate from composite — reflects data completeness`.
→ The **only** place any confidence semantic is stated at all, anywhere in the docs — and it applies to the *aggregate* `SignalScore.confidence`, not the per-category vote confidence from §9. Even here: qualitative anchor only ("reflects data completeness"), no scale, no formula, no bounds, no worked example.

**ARCHITECTURE.md §13** also states: *"Composite scoring formula and category weights are not invented ad hoc here — they are a Phase 1 implementation task done against historical/paper data."* This phrase predates the Addendum A7 phase renumbering, which moved Signal Engine to **Phase 5**. Minor stale cross-reference — noted, not fixed here (out of this document's scope; flagged for a future docs pass).

**STRATEGY_FRAMEWORK.md**, `StrategyDefinition.detect_setup(self, market_state: MarketState, mtf_context: MTFAlignment) -> Setup | None`:
→ **Load-bearing finding.** `mtf_context: MTFAlignment` is a parameter *passed into* `detect_setup`, not something a strategy computes itself. This means MTF alignment (and by necessary implication, the per-timeframe directional read it's built from) is produced **once, generically, upstream of and shared across every `StrategyDefinition`** — not owned by individual strategies. This directly narrows the Option A/B/C question in §6 below, even though it resolves nothing about the rule content.

**STRATEGY_FRAMEWORK.md**, closing section: *"Concrete entry rules, indicator thresholds, R-multiple targets... belong to specific `StrategyDefinition` implementations built and evidenced in Phase 3 and Phase 5, not to this framework document."*
→ Confirms indicator thresholds are strategy-validated, evidence-gated content — never invented by whoever happens to be implementing the plumbing.

**RISK_AND_BEHAVIOR.md**: no mention of directional voting, confidence, or MTF alignment — entirely downstream of a `Setup` already existing (`invalidation_level()`, `entry_zone()` all take a setup as given).

**PROVIDERS.md**: no relevance to this gate.

---

## 5. Specification Matrix

| Component | Specified? | Exact rule exists? | Safe to implement? | Why |
|---|---|---|---|---|
| Per-category vote output shape (`-1/0/+1` + confidence) | Yes (§9) | N/A — shape, not rule | Shape only, not the rule | The *container* is defined; what fills it is not |
| Per-category vote **rule** (which EMA relationship, which RSI value, etc. → vote) | **No** | **No** | **No** | Zero textual specification anywhere; would require invented thresholds |
| Vote **production ownership** (which module computes it) | Partially, by inference | N/A | — | §5 (now fixed) + STRATEGY_FRAMEWORK.md's `detect_setup` signature together imply a generic, pre-strategy step, most consistent with living in `engines/setup_detection.py`'s own pipeline — but this is inference from two documents' shapes, not a direct statement |
| Vote **combination** (per-category votes → one read) | Yes, location only (§9: `engines/signal_engine.py`) | No (no formula) | No | Combination formula unspecified |
| MTF alignment **states** | Yes, precisely (§10 table) | Yes, for the *combination* rule (agree/disagree logic across timeframes) | Only if inputs already exist | The 4-state combination rule is genuinely specified; it just has no valid input yet |
| MTF alignment **ownership** | Yes (§10, §5-fixed, §6) | — | — | Unambiguously `engines/setup_detection.py`, not `domain/technical` |
| Structure interpretation (HH/HL → "uptrend") | No | No | No | No confirmation-count rule exists (how many HH/HL = "confirmed") |
| Breakout: close-beyond-level rule | Yes (§9: close not wick) | Yes | Partially | One of four required pieces is concrete |
| Breakout: hold-confirmation window | Yes (§9: 1 full subsequent candle) | Yes | Partially | Second concrete piece |
| Breakout: volume-confirmation threshold | Named, not quantified (§9: "relative volume above threshold") | No | No | The word "threshold" appears; no number does |
| Breakout: S/R "level" selection/significance | No | No | No | Requires a structure-significance synthesis this project has explicitly declined to invent (Addendum B3) |
| `Setup` object shape | Yes, precisely (STRATEGY_FRAMEWORK.md) | Yes, as a container | No | Depends on `MTFAlignment` (blocked) and `domain/options`/`domain/regime` (don't exist) |
| Strategy-specific thresholds | Explicitly deferred to evidence (STRATEGY_FRAMEWORK.md, Addendum A7) | No, by design | No, by design | This is the one row where "not yet specified" is the *architecture's own intent*, not a gap to close |
| Confidence semantics (aggregate) | Qualitative only (§13: "data completeness") | No (no scale/formula) | No | Cannot compute a `float` from a sentence |
| Confidence semantics (per-category) | No | No | No | Not mentioned anywhere beyond the §9 shape declaration |
| Replay-based validation requirement | Yes, precisely (STRATEGY_FRAMEWORK.md "Replay Requirements") | Yes, as a process gate | N/A | Applies once a strategy exists to validate — not itself a blocker, a downstream requirement |

---

## 6. Evaluation of Options A / B / C

### Option A — Generic technical vote rules (hardcoded thresholds in a shared layer)

Rejected as originally framed (hardcoded numeric thresholds baked into a shared, non-strategy module) — this is precisely the un-evidenced strategy-parameter invention Addendum A7, STRATEGY_FRAMEWORK.md, and this whole gate exist to prevent. **However**, the *shape* of Option A — a generic (non-strategy-specific) computation step — is partially correct per §4's `mtf_context: MTFAlignment` finding: something generic must exist to produce a per-timeframe read before any `StrategyDefinition` runs. What's rejected is Option A's *hardcoded content*, not its *existence as a pipeline stage*.

### Option B — Strategy-specific interpretation only

Consistent with STRATEGY_FRAMEWORK.md's evidence-driven, per-strategy threshold philosophy for *strategy-specific* decisions (trigger level, structural basis, entry/invalidation specifics). **Inconsistent**, on its own, with the same document's `detect_setup(market_state, mtf_context: MTFAlignment)` signature — `mtf_context` is handed to the strategy, not derived by it, so *pure* Option B (every interpretation choice made independently per-strategy, nothing shared) does not match the existing contract. Full ownership cannot rest with individual strategies.

### Option C — Configurable/unvalidated rules

Structurally sound as a **holding pattern**: config-supplied thresholds, explicitly marked unvalidated, usable only in replay — this defers invention to the project owner (config values they supply) rather than to whoever implements the plumbing, and keeps unvalidated output out of any real decision path. Does not, by itself, resolve *what* the thresholds should be — it only resolves *how* to keep them safely quarantined once someone supplies them. Could be combined with either A's generic-stage-shape or B's strategy-specific layer.

### Synthesis

The evidence points to a **hybrid**, not a single option:
- A generic, shared, non-strategy-specific pipeline stage — most likely living inside `engines/setup_detection.py` (matching the already-corrected §5 and the module responsibility table) — is structurally implied to produce the per-timeframe read and `MTFAlignment` that `StrategyDefinition.detect_setup` receives as an input. This is Option A's *shape*, without Option A's hardcoded content.
- Individual `StrategyDefinition` implementations own strategy-specific thresholds, trigger conditions, and setup-triggering logic on top of that shared input. This is Option B, exactly as documented.
- Whatever numeric content either layer needs remains Option C's territory: explicit, owner-supplied, replay-validated configuration — never invented by an implementer.

This synthesis resolves **ownership location**. It resolves **nothing** about rule content, which remains entirely unspecified (§5) and is the actual blocker.

---

## 7. Recommended Ownership of Directional Rules

| Concern | Recommended owner | Basis |
|---|---|---|
| Per-timeframe directional read (the generic step) | `engines/setup_detection.py` (or a small module it owns) | §5 fix + `detect_setup(mtf_context=...)` signature |
| MTF alignment combination | `engines/setup_detection.py` | §10, unambiguous already |
| Per-category vote *rule content* | Project owner, as explicit config/spec, not any implementer | STRATEGY_FRAMEWORK.md's evidence-gating philosophy |
| Strategy-specific setup triggering | Individual `StrategyDefinition` implementations | STRATEGY_FRAMEWORK.md |
| Confidence formula/scale | Project owner (undetermined even in concept — see §9 below) | No existing anchor beyond one qualitative sentence |

---

## 8. Proposed Minimal Contract (target shape only — not implemented, not to be implemented until §16 is resolved)

Derived from §9's own vocabulary and §13's `SignalScore` shape — not invented independently:

```python
class CategoryVote(BaseModel):
    category: str              # matches an evidence category from ARCHITECTURE.md §13
    direction: Literal[-1, 0, 1]   # verbatim from §9's own "-1/0/+1" language
    confidence: ...             # TYPE UNRESOLVED — see §9 below; cannot be typed yet
    rule_id: str                # which specified, versioned rule produced this — required for
                                 # audit ("what did the system know at T") and for
                                 # STRATEGY_FRAMEWORK.md's replay-validation requirement
    supporting_facts: ...       # reference back to the specific TechnicalSnapshot/StructureFacts
                                 # field(s) this vote was derived from
```

This is presented to make the gap concrete, not as a specification to build against. The `confidence` field cannot even be typed correctly today (§9 below) — that alone is sufficient to keep this at HOLD.

---

## 9. Confidence Semantics — Explicitly Unresolved

The six candidate forms named in this task's brief (numeric / ordinal / categorical / evidence-completeness / strategy-specific / deferred) were evaluated against §4's extracted text:

- **Evidence-completeness** is the only form with *any* textual anchor (§13: "reflects data completeness") — but only at the aggregate `SignalScore` level, and only as a sentence, not a formula. If the project owner picks a semantic with the least invention required, this is it — but the actual computation ("matched required facts ÷ total required facts"? weighted? something else?) is still entirely unspecified and must be supplied, not inferred.
- **Numeric** (e.g., a float in [0,1]) is implied by `confidence: float` in the `SignalScore` sketch (§13) and in the LLM's `RedTeamResponse.confidence` (Addendum A6) — but a type hint is not a semantic. What `0.62` *means* is not stated.
- **Ordinal/categorical** (e.g., LOW/MEDIUM/HIGH) has no textual support anywhere but would be defensible as a more honest representation of genuinely unvalidated confidence than a false-precision float.
- **Strategy-specific** confidence is plausible given STRATEGY_FRAMEWORK.md's per-strategy evidence-requirements model, but no document states this explicitly.
- **Deferred entirely** (no confidence field until paper-trading data justifies one) is the most conservative option and has explicit textual support: Addendum A8's Language & Claims Policy already forbids treating `RedTeamResponse.confidence` as "a calibrated probability... until/unless it is empirically calibrated against paper-trading outcomes" — the same reasoning applies with at least equal force to a confidence value with *zero* empirical grounding at all.

**No numeric value, formula, or worked example may be invented to fill this gap.** This document does not select among the above — that selection is explicitly reserved for §16.

---

## 10. MTF Alignment Dependency

Confirmed: ownership is `engines/setup_detection.py`, not `domain/technical` (§5, corrected this session's predecessor). `TechnicalSnapshot` remains single-timeframe, verified structurally (its own tests scan `model_fields` for forbidden direction/score fields). The architecture's discrete-state model (`FULL_ALIGNMENT`/`PARTIAL_ALIGNMENT`/`CONFLICT`/`REGIME_UNCLEAR`) is explicitly preserved and explicitly **not** replaceable by a weighted average — §10's own worked example (daily bullish + 5m bearish) is stated to resolve to `CONFLICT`, never a blended score, and nothing in this gate's analysis weakens that. MTF alignment cannot be implemented before §6/§9's rule-content gap closes, because it has no per-timeframe input to combine.

## 11. Structure Interpretation Boundary

`StructureFacts` (`HIGHER_HIGH`/`LOWER_HIGH`/`EQUAL_HIGH`/`HIGHER_LOW`/`LOWER_LOW`/`EQUAL_LOW`) remain literal, pairwise, threshold-free comparisons — confirmed by source inspection and by the existing test that scans for forbidden trend/reversal vocabulary. No confirmation-count rule (e.g. "2 HH + 2 HL = uptrend") exists in any document, and none is proposed here.

## 12. Breakout/Reversal Dependency

Partially specified, precisely as detailed in §5's matrix rows: the close-beyond-level condition and the one-candle hold-confirmation window are concrete; the numeric volume-confirmation threshold and the S/R-level significance/selection rule are not. Both missing pieces must be resolved — and the S/R significance rule specifically requires the structure-synthesis decision this project has already declined to invent (Addendum B3) — before any breakout/breakdown/false-breakout code can be written.

## 13. Replay Validation Requirements

Unconditional, regardless of how §16 is resolved: any future directional rule must be evaluable as `historical candles → as_of=T → TechnicalSnapshot(T) → interpretation → outcome`, using the existing `HistoricalProvider`/`bounded_series()` contracts already proven in this codebase (131/131 tests, dedicated no-look-ahead and replay-equivalence suites). No new persistence or provider mechanism is implied by anything in this document. STRATEGY_FRAMEWORK.md's existing "Replay Requirements" section already states this as a precondition for any `StrategyDefinition` reaching real-time — this document adds nothing new here, it only confirms nothing contradicts it.

## 14. Safety Invariants

All 19 invariants listed in this task's brief were checked against the current repository state in §1 and hold true. None are weakened by this document, because this document changes no production code.

## 15. Explicit STOP Conditions (all currently active)

1. No per-category vote rule is specified — STOP on implementing any vote.
2. No confidence semantic beyond one qualitative sentence exists — STOP on typing or computing any confidence value.
3. MTF alignment has no valid input to combine — STOP on implementing alignment classification.
4. Breakout/breakdown is missing two of four required pieces (volume threshold, S/R significance) — STOP on implementing breakout logic.
5. `Setup` construction additionally requires `domain/options`/`domain/regime`, neither of which exists — STOP regardless of the above.

None of these are resolved by this document. They are resolved only by explicit project-owner decisions (§16) followed by a future, separately-reviewed implementation gate.

## 16. Decisions Required From the Project Owner

The smallest set that unlocks further movement — each is independent; answering a subset unlocks only the corresponding work:

**D1 — Confirm or override the ownership synthesis in §6/§7.** (Default if unanswered: treat §6's synthesis as provisionally correct, but still do not implement anything against it until D2–D4 are also answered.)

**D2 — Per-category vote rule content**, including explicitly answering the brief's own open sub-questions: is relative volume/ATR a directional vote at all, or context-only (confirmation/volatility modifiers with no independent direction)? Acceptable answers include "defer indefinitely, revisit after replay tooling exists" — that is a valid decision, not a non-answer.

**D3 — Confidence semantics**: pick one of §9's evaluated forms (or explicitly defer), and supply the actual formula/scale if a computed form is chosen. "Defer entirely, no confidence field yet" is an acceptable, arguably the most defensible, answer.

**D4 — Breakout's two missing pieces**: a numeric volume-confirmation threshold, and an S/R-level significance/selection rule (or explicit deferral of breakout detection as a whole, independent of D2).

Until at least D2 is answered, no directional interpretation code should be written. D1/D3/D4 gate narrower slices of the same boundary.
