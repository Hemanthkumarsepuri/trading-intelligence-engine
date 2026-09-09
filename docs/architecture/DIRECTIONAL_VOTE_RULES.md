# Directional Vote Rules

Status: **Superseded in part by [`DIRECTIONAL_VOTE_FINAL_GATE.md`](DIRECTIONAL_VOTE_FINAL_GATE.md) (adversarial review).** That document found this one's `BULLISH`/`BEARISH` labeling of the EMA/MACD/VWAP relational comparisons below to be inconsistent with this project's own `structure_facts.py` precedent (a threshold-free comparison must be labeled descriptively — e.g. `ASCENDING`/`ABOVE` — not with a directional/trading-conclusion label, without separate justification). The comparison *logic* and *periods analysis* in §4/§6/§7 below remain accurate and useful evidentiary material; their `BULLISH`/`BEARISH` *conclusions* do not — read `DIRECTIONAL_VOTE_FINAL_GATE.md` §5–§7 for the corrected classification. This document is kept, not deleted, per the minimum-correction principle applied throughout this gate sequence.

Status (original): **Specification for owner review.** Extends [`DIRECTIONAL_VOTE_GATE.md`](DIRECTIONAL_VOTE_GATE.md) and [`DIRECTIONAL_VOTE_SPECIFICATION.md`](DIRECTIONAL_VOTE_SPECIFICATION.md) with concrete, per-category rule proposals. **No production code accompanies this document.** Every rule below is labeled with its evidentiary status — directly-supported / design-inference / owner-decision-required — per this milestone's explicit instruction not to present inference as approved architecture.

---

## 1. Purpose

Close as much of the O1–O3 gate as can be closed **without inventing an arbitrary numeric threshold**, and explicitly mark FACT-ONLY/DEFERRED wherever no such closure is possible. This document does not implement anything; it is the specification a future, separately-approved milestone would build against.

---

## 2. Current Architecture Boundary (re-verified this session)

```
pytest -q                  -> 131 passed
ruff check .                 -> All checks passed!
mypy --strict app tests      -> Success: no issues found in 76 source files
```

`domain/technical/` remains fact-only (8 modules, unchanged). `engines/`, `orchestration/`, `domain/{signals,risk,options,news,regime,behavior}` remain empty or nonexistent, as before. This document proposes nothing that would move interpretation into `domain/technical` — every rule below is specified as belonging to `engines/setup_detection.py`'s future pipeline, consuming `TechnicalSnapshot`/`StructureFacts` as opaque inputs, never modifying them.

---

## 3. Approved Category Classification

| Category | Rule specifiable without invented threshold? | Status |
|---|---|---|
| EMA | **Yes** — relational, uses architecture-given periods | Rule specified below (§4) |
| MACD | **Partially** — relational logic is threshold-free; its own periods are not architecture-given | Rule specified conditionally (§6) |
| VWAP | **Yes, structurally** — but has an unmet precondition (session-bounding) | Rule specified, marked CONDITIONAL (§7) |
| RSI | **No** — every candidate rule requires an invented numeric band | FACT-ONLY / DEFERRED (§5) |
| ATR | N/A — not directional by design | Non-directional, risk/context input (§8) |
| Relative Volume | N/A — not directional by design | Confirmation-only (§9) |
| Structure (HH/HL/LH/LL) | **No** — any confirmation-count rule is an invented threshold | FACT-ONLY / DEFERRED (§10) |

---

## 4. EMA Rule Decision

**Evidence basis:** ARCHITECTURE.md §9 states `EMA(9/21/50)` as the chosen periods for the Trend category — **directly supported**, not inferred. This is the only category where the architecture supplies enough structure (three named periods) to build a rule using pure relational comparison, no invented numeric cutoff.

**Options considered** (per this task's §6):

- *Price relative to EMA* — rejected: three EMAs exist (9/21/50); nothing says which one is "the" reference, so picking one would itself be an invented choice.
- *Fast EMA vs. slow EMA (two-period crossover)* — workable, but uses only 2 of the 3 given periods, arbitrarily discarding one; also produces a binary read (no distinct "unclear" state) whereas a 3-EMA read can.
- *EMA slope* — rejected: requires an undefined lookback ("previous" = how many bars back?), which is itself an invented parameter with no textual grounding.
- *Multiple EMA alignment (all three periods, ordinal comparison)* — uses all three given periods with no discarding, produces a genuine three-state read, and is structurally identical in kind to the swing-point `>`/`<`/`==` comparisons already implemented in `structure_facts.py` (a pure ordering test, no magic constant).

**Recommended rule (design inference, grounded directly in §9's given periods — not yet owner-approved):**

```
Input fields: EMA(9), EMA(21), EMA(50) — all for the same instrument/timeframe/as_of
Bullish  (+1): EMA(9) > EMA(21) > EMA(50)               [full ascending alignment]
Bearish  (-1): EMA(9) < EMA(21) < EMA(50)               [full descending alignment]
Neutral   (0): any other ordering (e.g. EMA(9) > EMA(21) but EMA(21) < EMA(50))
                — the three EMAs disagree with each other; no coherent trend read exists,
                  by the same reasoning §10 uses to make disagreeing timeframes CONFLICT
                  rather than a blended average
Insufficient data: any of the three EMA calls reports IndicatorStatus.INSUFFICIENT_HISTORY
                    -> vote = INSUFFICIENT_DATA (not 0 — see §11)
Conflict condition: N/A at this layer (conflict is what "Neutral" already captures for a
                     single category; cross-category/cross-timeframe conflict is §13/§14's concern)
Single or multiple indicators: three EMA calls (same category, three periods) — one vote
Timeframe-independent: yes — the rule is a pure ordering test with no timeframe-specific
                        constant; nothing about it changes meaning across 5m vs Daily
Thresholds fixed or configurable: no numeric threshold exists in this rule at all — only
                                    the three periods (9/21/50) are parameters, and those
                                    are already fixed by §9, not by this document
```

**Evidence required before promotion from UNVALIDATED:** a replay run across at least one trending and one ranging historical window (see §15) showing the vote does not fire near-constantly `NEUTRAL` (which would indicate the three periods are too close together for this instrument/timeframe combination) nor near-constantly non-neutral (which would indicate the opposite). No specific pass/fail percentage is proposed — see §15/§16 on why.

---

## 5. RSI Rule Decision

**Explicit conclusion: FACT-ONLY / DEFERRED.** No generic RSI directional rule is specified in this document.

**Why, in detail (per this task's explicit invitation to reach this conclusion):**

- *Midpoint (RSI > 50 = bullish)* — the single most common convention in general TA literature, and exactly the kind of "conventional but arbitrary" rule this task's brief warns against adopting merely because it's common. No repository text anchors 50 (or any other value) as meaningful for this project.
- *Overbought/oversold bands (30/70, or 45/55)* — even less defensible: these bands are themselves contested in general practice (values from 20/80 to 40/60 all appear in different sources) and nothing here picks one.
- *Regime dependence, explicitly analyzed:* RSI's midpoint/band interpretation is known to behave differently in trending vs. ranging markets (in a strong trend, RSI can sit above 70 or below 30 for extended periods without reversing — the "overbought stays overbought" problem). This project has no regime classifier yet (`domain/regime` doesn't exist), so there is no way to even condition an RSI rule on regime state today, which removes one plausible path to making a defensible generic rule.
- *Divergence (price makes a new high, RSI doesn't)* — potentially the most information-rich RSI pattern, but requires comparing RSI values across two different points in time tied to swing points, which compounds structure's own unresolved confirmation-rule gap (§10) on top of an RSI threshold gap. Not specifiable until both are resolved, if ever.

**Conclusion:** unlike EMA, RSI has no architecture-given structure (like three named periods) that permits a threshold-free relational rule — every path to an RSI vote requires picking an arbitrary number. RSI remains a **fact** (`RSIResult.value`) until a specific `StrategyDefinition`, validated through replay, supplies its own threshold as strategy-scoped configuration (§6 of `DIRECTIONAL_VOTE_SPECIFICATION.md`'s Option C).

---

## 6. MACD Rule Decision

**Evidence basis:** ARCHITECTURE.md §9 lists "MACD" under Momentum with **no periods specified** — unlike EMA's explicit `9/21/50`. This asymmetry matters and is treated explicitly below.

**Double-counting analysis (mandatory per §14):** MACD's own internal values are correlated with each other, not independent:
- `histogram = macd_line - signal_line` (already true by construction in `momentum.py`) — so **"histogram sign > 0" and "macd_line > signal_line" are the exact same test expressed two ways**, not two pieces of evidence. Using both would silently double-weight one fact.
- `macd_line`'s zero-line position (`macd_line > 0`, meaning `EMA(fast) > EMA(slow)`) is a different test, but is **highly correlated with the EMA-alignment rule in §4** — both are fundamentally asking "is a shorter-term average above a longer-term average." If MACD's fast/slow periods were ever set equal to two of EMA's three periods (e.g. 9 and 21), the zero-line test and part of the EMA-alignment test would become near-redundant restatements of each other.

**Recommended rule, if MACD is approved for generic interpretation (design inference):**

```
Input fields: MACDResult.macd_line, MACDResult.signal_line — ONLY these two; histogram and
              zero-line position are deliberately excluded (see double-counting analysis above)
Bullish  (+1): macd_line > signal_line
Bearish  (-1): macd_line < signal_line
Neutral   (0): macd_line == signal_line (exact equality; rare but well-defined, not a gap)
Insufficient data: MACDResult.status == INSUFFICIENT_HISTORY -> vote = INSUFFICIENT_DATA
Single or multiple indicators: one MACD call, two of its three output fields — one vote
Timeframe-independent: yes, same reasoning as EMA — pure comparison, no timeframe-specific constant
Thresholds fixed or configurable: the COMPARISON itself has no invented threshold. The
                                    fast/slow/signal PERIODS it operates on are a separate,
                                    still-open parameter (see below) — conflating "the rule
                                    needs no threshold" with "the rule needs no configuration
                                    at all" would be incorrect.
```

**Open item this document does not resolve:** MACD's fast/slow/signal periods are not given anywhere in the repository (unlike EMA's). This document does **not** propose values (e.g. the conventional 12/26/9) — doing so would be exactly the "conventional but arbitrary" invention this task prohibits. **Owner decision required:** either supply MACD's periods explicitly (analogous to how §9 already supplies EMA's), or defer MACD alongside RSI until they are. The macd-vs-signal comparison *rule* is ready; the *periods it runs on* are not.

---

## 7. VWAP Rule Decision

**Evidence basis:** existing `structure.py` VWAP implementation, unchanged by this document, computes VWAP over exactly the candle series it's given and explicitly does **not** perform a session reset (its own docstring states this, and this document does not touch that implementation).

**Rule, if approved (design inference):**

```
Input fields: VWAPResult.current_price, VWAPResult.value (the VWAP itself)
Bullish  (+1): current_price > vwap_value
Bearish  (-1): current_price < vwap_value
Neutral   (0): current_price == vwap_value
Insufficient data: VWAPResult.status == INSUFFICIENT_HISTORY -> vote = INSUFFICIENT_DATA
                    (this includes the zero-total-volume case, per structure.py's own logic)
```

**Why this is marked CONDITIONAL, not simply approved:** the comparison itself is threshold-free (structurally identical to EMA/MACD's relational tests). But its **meaning** depends entirely on what candle window was passed in. "Price above a 3-day VWAP" and "price above today's intraday VWAP" are different, non-interchangeable signals, and the current architecture has **no mechanism yet that guarantees session-bounded input** — that's explicitly a future orchestration-layer responsibility (per the VWAP implementation's own documented convention, restated in `DIRECTIONAL_VOTE_SPECIFICATION.md` §15.2). **This rule must not be promoted to production use until the calling layer that supplies session-bounded candles exists and is verified** — the rule can be specified today; it cannot be safely evaluated as directional evidence today, because there's no guarantee of *what* it would be evaluating.

---

## 8. ATR Decision

**Verified directly from the repository, not inferred:** ARCHITECTURE.md §9 places ATR under "Volatility" only. RISK_AND_BEHAVIOR.md's risk-engine function list (`stop_loss_reference()`, position-sizing design) is exactly where ATR-derived distances are the expected kind of input (stop distance, position-size denominator) — a risk/context role, not a directional one. No document anywhere pairs ATR with bullish/bearish language.

**Decision: ATR is non-directional.** No vote is created. **Where it will later be consumed:** `engines/risk_engine.py` (stop distance, volatility-adjusted sizing) and potentially `domain/regime` (volatility-regime classification, per §5's module table: `domain/regime` "classifies... high/low volatility") — both outside this milestone's scope, both already-documented future homes, neither invented here.

---

## 9. Relative Volume Decision

**Verified directly from the repository, not inferred:** ARCHITECTURE.md §9's own text: *"Volume: Relative volume vs N-period average, VWAP deviation... Confirms breakout/breakdown validity."* This is the single most concretely-worded category in the entire specification — the architecture already states relative volume's role as confirmation, in these exact words.

**Decision: relative volume produces no independent bullish/bearish vote.** How it will later be consumed: as a **confirmation gate** on top of a direction already established by other evidence — e.g. the (not-yet-specified, see §12) breakout/breakdown classification's own stated requirement that "volume confirms (relative volume above threshold)" before a breakout is classified as valid rather than `BREAKOUT_UNCONFIRMED` (§9's existing text). Relative volume answers "is this move believable," never "which way is the move" — it has no directional sign of its own to contribute to a `-1/0/+1` vote, consistent with how the primitive itself (`RelativeVolumeResult.ratio`) is a magnitude, not a signed value.

---

## 10. Structure Decision (O3)

**Options compared, per this task's §12:**

- **(A) Structure remains fact-only.** No synthesis; `StructureFactsResult`'s comparisons stand as-is.
- **(B) Generic structure confirmation rule** (e.g. "N consecutive HH/HL = bullish structure"). Rejected. Reasons, addressing every consideration this task lists:
  - *Swing confirmation latency:* a swing point is inherently unconfirmable until `right_bars` candles after it — a confirmation-count rule built on top compounds this lag further, since it needs *multiple* already-lagged points.
  - *Fractal sensitivity:* the swing points feeding this rule are themselves parameterized by `left_bars`/`right_bars`, chosen by the caller with no architecture-given default (unlike EMA's periods). A confirmation-count rule would be a second layer of unanchored parameterization stacked on a first, compounding arbitrariness rather than resolving it.
  - *Timeframe/noise:* how many HH/HL should "count" plausibly differs by timeframe and instrument volatility — exactly the kind of context-dependence this task's §5 warns a universal fixed rule cannot honestly absorb.
  - *No textual anchor exists anywhere* — unlike EMA's given periods or relative volume's stated confirmation role, nothing in any document proposes even an approach to counting, let alone a number.
- **(C) Strategy-specific structure interpretation.** A given `StrategyDefinition` could reasonably declare "for *my* setup, 2 consecutive HH is sufficient," as a validated, replay-tested, strategy-scoped choice — categorically different from inventing a *generic* rule, because it is scoped, owned, and evidenced by one strategy's own replay history rather than presented as universally true.

**Recommendation: Option A, with Option C as the only sanctioned future path.** Structure stays permanently fact-only at the generic layer. No confirmation rule is proposed, approved, or scaffolded by this document. If and when a specific strategy wants to build on structure facts, that interpretation belongs entirely inside that `StrategyDefinition`, validated through that strategy's own replay run — never inserted into `engines/setup_detection.py`'s generic pipeline as if it applied universally.

---

## 11. Missing / Insufficient Data Semantics

**Explicit rule, applying uniformly to every category above:** a vote has exactly four possible states, not three:

```
BULLISH  = +1
NEUTRAL  =  0
BEARISH  = -1
INSUFFICIENT_DATA  (a distinct, non-numeric state — never encoded as 0)
```

This extends §9's literal `-1/0/+1` framing with an explicit fourth state (reflected as a minimal, direct clarification already added to ARCHITECTURE.md §9 in this session — see §17 below). The rationale is not new: it is the same "missing ≠ 0, missing ≠ neutral" principle already enforced structurally throughout `domain/technical` (verified: `TechnicalSnapshot`'s own tests assert `INSUFFICIENT_HISTORY` is never collapsed into a fabricated value). A vote computed from a category whose underlying `IndicatorStatus` is `INSUFFICIENT_HISTORY` **must** report `INSUFFICIENT_DATA`, never `0` — collapsing the two would let missing evidence silently vanish into a future average/count without a trace, exactly the failure mode this whole project is structured to prevent.

Per-category behavior when the underlying primitive cannot compute:

| Category | Underlying condition | Vote outcome |
|---|---|---|
| EMA | Any of EMA(9)/EMA(21)/EMA(50) is `INSUFFICIENT_HISTORY` | `INSUFFICIENT_DATA` |
| MACD | `MACDResult.status == INSUFFICIENT_HISTORY` | `INSUFFICIENT_DATA` |
| VWAP | `VWAPResult.status == INSUFFICIENT_HISTORY` (includes zero-volume case) | `INSUFFICIENT_DATA` |
| RSI | N/A — no vote exists (§5); fact remains `None`/`INSUFFICIENT_HISTORY` as already implemented | Not applicable |
| ATR | N/A — non-directional (§8) | Not applicable |
| Relative Volume | N/A — non-directional (§9) | Not applicable |
| Structure | N/A — fact-only (§10) | Not applicable |

`NOT_APPLICABLE` (distinct from `INSUFFICIENT_DATA`) is the correct label for categories that structurally never produce a vote at all (ATR, Relative Volume, Structure, RSI) — these are not "missing," they are "not in scope for voting," a different and equally important distinction this task asked to be made explicit.

---

## 12. Confidence Decision

Restated, unchanged from `DIRECTIONAL_VOTE_SPECIFICATION.md` §7/§4, because nothing found in this pass changes the underlying evidence: **no formula is invented.** The safest architecture is to **defer confidence entirely** for every rule proposed in this document — none of §4/§6/§7's votes carry a confidence value. If the owner later wants one, the interface should be defined first (a typed field with an explicit, stated semantic) separate from any specific number, and populated only once a semantic is chosen and justified — not before.

**Kept explicitly separate, as required:**
- **Data Quality** (`DataQualityGate`) — precondition for even attempting a vote; never blended into the vote's value.
- **Directional Evidence** (this document's `-1/0/+1/INSUFFICIENT_DATA`) — what a category says, nothing about how sure it is.
- **Confidence** — undefined, deferred, not computed by anything in this document.
- **Strategy Confidence** — not addressed here at all; belongs entirely to future `StrategyDefinition` design, out of scope.

---

## 13. Double-Counting Policy

Beyond the MACD-internal analysis in §6, the cross-category correlation this task's §14 specifically asks about:

**EMA (§4) and MACD (§6) are correlated, not independent.** Both derive from EMA smoothing of the same price series; both are, at root, measuring "is a shorter-term average above a longer-term average." They are not *mathematically forced* to agree at every bar (MACD's signal line lags differently than a raw third EMA does, so their exact flip points differ), but they share the same underlying data and the same broad concept ("trend"). **This document does not combine them.** Per ARCHITECTURE.md §9's own text, combination is explicitly `engines/signal_engine.py`'s job (§13), not this layer's — and per the explicitly preserved principle (this task's §14, echoing ARCHITECTURE.md throughout), no opaque `score > 70 = BUY` mechanism is proposed anywhere in this document. Each category's vote is reported independently; whether/how correlated votes should be weighted differently from independent ones when eventually combined is squarely a signal-engine design question, out of scope here, and is flagged for whoever specifies that engine to treat EMA and MACD as correlated evidence, not two independent confirmations.

---

## 14. MTF Dependency

Unchanged from `DIRECTIONAL_VOTE_SPECIFICATION.md` §10, restated for completeness — this document adds categories' vote definitions but does not change the dependency chain or touch MTF alignment itself:

```
TechnicalSnapshot(instrument, timeframe, as_of)     [implemented, unchanged]
        ↓
per-timeframe directional evidence                   [this document specifies EMA/MACD/VWAP;
                                                        leaves RSI/Structure fact-only]
        ↓
MTF alignment: FULL_ALIGNMENT / PARTIAL_ALIGNMENT /   [state names and combination logic
               CONFLICT / REGIME_UNCLEAR               already defined, §10 of ARCHITECTURE.md —
                                                        NOT implemented, NOT touched here]
        ↓
setup detection → strategy interpretation → risk gate  [unchanged, out of scope]
```

**Re-confirmed, not weakened:** conflicting timeframes cannot be silently averaged into `BUY`/`SELL`. Nothing in this document's per-category rules changes that guarantee — if anything, having explicit `INSUFFICIENT_DATA`/`NOT_APPLICABLE` states (§11) makes an accidental average even less mechanically possible, since a naive `sum(votes)/len(votes)` would need to actively ignore or mis-handle non-numeric states rather than silently including them.

---

## 15. Replay Validation Requirements

For **every** rule specified in this document (EMA §4, MACD §6 pending periods, VWAP §7 pending session-bounding), before any is used outside a replay experiment:

**What must be measured** (not performance thresholds — measurement categories only, per this task's explicit instruction not to invent pass/fail numbers):
- Vote-state distribution over the historical window (how often BULLISH/BEARISH/NEUTRAL/INSUFFICIENT_DATA each occur) — to catch a rule that's degenerate (always one state) before it reaches any downstream consumer.
- Vote stability under `bounded_series()`'s existing no-look-ahead guarantee, re-confirmed per-rule the same way the existing primitives-level and snapshot-level safety suites already do (future candles must not change a fixed-`as_of` vote — mechanically guaranteed already by every rule above being a pure function of `TechnicalSnapshot`/its sub-results, which already carry that guarantee, but must still be tested explicitly at the vote layer once it exists).
- Agreement/disagreement rate between EMA's and MACD's votes (§13's correlation, measured rather than assumed).

**What failure modes must be inspected:** a rule that never leaves `NEUTRAL` (too insensitive to be useful) or one that's almost never `NEUTRAL` (too sensitive, likely noise-driven) are both failure signatures worth flagging during replay review — described qualitatively here, not gated by an invented numeric bound.

**What regimes/instruments/timeframes must be covered:** at minimum one clearly-trending historical window and one clearly-ranging one (by inspection, since no `domain/regime` classifier exists yet to label these automatically), across more than one instrument and more than one of the five architecture-defined timeframes (5m/15m/30m/1h/Daily) — a rule validated only on one narrow slice is not evidence it generalizes.

**What constitutes sufficient evidence for human approval:** this document does not define a number (e.g. "N replay runs" or "X% agreement") — consistent with STRATEGY_FRAMEWORK.md's own position that this is set from real data in front of the reviewer, not pre-guessed.

---

## 16. Anti-Overfitting Requirements

- **Train/validation/test separation:** any future replay-based *parameter selection* (e.g. if MACD's periods are eventually chosen empirically rather than specified outright) must reserve a held-out historical window never used during selection, evaluated only once, after the choice is fixed — standard discipline this document requires be followed, without prescribing exact date ranges (an owner/implementer decision at that time).
- **Repeated replay on the same sample:** re-running replay against the same historical window repeatedly while nudging a parameter until results "look good" is data snooping — the rules in §4/§6/§7 are deliberately parameter-light (EMA's periods are given, not tuned; MACD/VWAP's open items are period *selection*, not threshold *tuning*) specifically to minimize the surface area exposed to this risk.
- **Look-ahead:** already structurally prevented — every rule above is defined purely in terms of already-computed `TechnicalSnapshot`/sub-result fields, which are already `as_of`-bounded and covered by existing dedicated no-look-ahead test suites; no new look-ahead surface is introduced by any rule in this document.
- **Survivorship bias:** relevant once real instrument selection for replay begins (e.g. only replaying against currently-listed, currently-liquid instruments would bias results) — flagged as a requirement for whoever builds the replay evaluator, not addressed further here since no replay evaluator exists yet.
- **Regime dependence:** addressed in §15 (must cover trending and ranging windows) rather than assumed away.
- **Threshold selection discipline:** none of this document's approved rules (§4, §6's comparison, §7's comparison) contain an invented numeric threshold to begin with — the one open numeric-ish choice (MACD's periods) is flagged in §6 as requiring an explicit owner decision, not a replay-optimized one, specifically to avoid this exact overfitting path.

---

## 17. UNVALIDATED / PROMOTED Lifecycle

```
Rule specified (this document)
        ↓
UNVALIDATED  — exists only as documentation + (in a future milestone) config/interface,
               never consumed by any live or paper-trading path
        ↓
Replay evaluation (§15) — measurement only, no invented pass/fail bar
        ↓
Anti-overfitting review (§16) — confirms the evaluation itself wasn't contaminated
        ↓
Human approval — a process step per STRATEGY_FRAMEWORK.md, not automated
        ↓
PROMOTED — usable inside a StrategyDefinition's detect_setup(); still not paper-trading
           or real-money eligible until that StrategyDefinition itself separately
           accumulates paper-trading evidence (ARCHITECTURE.md §20, restated in
           DIRECTIONAL_VOTE_SPECIFICATION.md §13)
```

Nothing in this document promotes any rule. Every rule in §4/§6/§7 remains `UNVALIDATED` after this document is published — that status change is itself a future, separate, evidence-based decision.

---

## 18. Explicit Rejected Alternatives

- **A single universal fixed-threshold rule for every category** (this task's Option A, unqualified) — rejected wherever it would require inventing a magic number (RSI, structure); accepted only in the narrow form of threshold-free relational comparisons using already-given parameters (EMA, MACD's comparison logic, VWAP's comparison logic).
- **Timeframe-specific threshold tables** (Option B) — not adopted; none of the approved rules need timeframe-specific tuning since none contain a numeric threshold to begin with. Would become relevant only if a future numeric-threshold rule (e.g. an eventual RSI band) were approved with timeframe-varying values — not proposed here.
- **A weighted composite score across categories** — explicitly rejected throughout, consistent with every prior document in this gate sequence and with ARCHITECTURE.md's own stated rejection of `score > 70 = BUY`-style systems.
- **RSI midpoint/band rule** — considered and explicitly rejected (§5) for lacking any textual anchor.
- **Generic structure confirmation-count rule** — considered and explicitly rejected (§10) for compounding two layers of unanchored parameterization.
- **Deriving confidence from data-quality metadata** — considered and explicitly rejected (§12) as conflating two categorically different concepts.

---

## 19. Open Decisions

**Owner decisions still required, in priority order:**

- **P1.** Approve or reject the EMA alignment rule (§4) — the one rule with the strongest textual grounding.
- **P2.** Approve or reject the MACD comparison rule (§6), **and separately** supply MACD's fast/slow/signal periods (currently unspecified anywhere) if approved.
- **P3.** Approve or reject the VWAP rule (§7) as CONDITIONAL — and separately confirm the orchestration-layer session-bounding precondition will exist before this rule is ever evaluated against real data.
- **P4.** Confirm RSI stays FACT-ONLY/DEFERRED (§5) — or supply an explicit, owner-justified RSI rule this document declined to invent.
- **P5.** Confirm structure stays FACT-ONLY/DEFERRED at the generic layer (§10) — strategy-specific structure interpretation remains available regardless of this answer.
- **P6.** Confirm ATR (§8) and Relative Volume (§9) as non-directional — both have strong textual support already; this is a confirmation, not expected to be contentious.

No implementation should begin against **any** approved rule until its corresponding decision above is made explicitly, in writing, by the project owner — not inferred from silence.

---

## 20. Final Gate Recommendation

**HOLD** — for implementation. This document closes the *specification* gap for EMA, MACD (partially), VWAP (conditionally), ATR, and Relative Volume, and explicitly declines to close it for RSI and Structure, marking both `FACT-ONLY / DEFERRED` rather than inventing a rule to force closure. Per this task's own stop condition — *"If you discover that a safe, testable directional rule cannot be specified without introducing an arbitrary strategy assumption, DO NOT invent one"* — that condition was met for RSI and Structure, and honored. The gate moves from HOLD to GO only after §19's owner decisions are made; this document is the input to that decision, not the decision itself.
