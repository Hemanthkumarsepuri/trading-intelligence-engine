# Directional Vote — Final Gate (Adversarial Review)

Status: **HOLD, with a course correction — the fact-only path §17/§19 identified has since been implemented.** See `app/domain/technical/{ema_alignment,vwap_position}.py`: pure `ASCENDING`/`DESCENDING`/`MIXED` and `ABOVE`/`BELOW`/`AT` facts, no `BULLISH`/`BEARISH` vocabulary, following exactly the scope this document approved and no more (the generic directional-vote layer itself remains on HOLD, unimplemented, and unrecommended). This document adversarially re-examines `DIRECTIONAL_VOTE_RULES.md`'s proposals rather than ratifying them, per this milestone's explicit charge not to preserve a prior proposal merely because it exists.

---

## 1. Executive Verdict

**HOLD for a generic "directional vote" layer as originally conceived.** The adversarial review below finds that `DIRECTIONAL_VOTE_RULES.md`'s EMA/MACD/VWAP proposals — which labeled threshold-free relational comparisons `BULLISH`/`BEARISH` — were **internally inconsistent with this project's own established treatment of Structure Facts**, and that inconsistency, once corrected, dissolves most of what a generic "vote" layer would even do. The corrected architecture is **fact-only technical outputs, with all directional/trading interpretation owned exclusively by `StrategyDefinition`** — Option 3 in this task's framing, detailed in §14. This is a narrower, safer conclusion than the prior document reached, reached by holding the prior document to the same standard it already applied to Structure Facts and finding it hadn't met that standard for EMA/MACD/VWAP.

A **separate, smaller, much-lower-risk opportunity** is identified as a byproduct of this correction: purely *descriptive* (non-directional-labeled) relationship facts for EMA and VWAP could be added to `domain/technical` — as siblings to `structure_facts.py`, not as a new interpretive layer — without requiring any of the trading-assumption sign-off a "vote" would need. This is flagged as a candidate for a future, separately-authorized milestone, **not implemented here**.

---

## 2. Repository Evidence (re-verified this session, not carried over)

```
pytest -q                  -> 131 passed
ruff check .                 -> All checks passed!
mypy --strict app tests      -> Success: no issues found in 76 source files
```

`domain/technical/` confirmed unchanged (8 modules). `structure_facts.py` re-read in full for this review — its exact stated principle: *"This module deliberately stops here. It does NOT synthesize these pairwise facts into an 'uptrend'/'downtrend'/'bullish structure'/'reversal' verdict... inventing one here would smuggle a strategy assumption into a facts layer."* This sentence is the standard the rest of this document holds `DIRECTIONAL_VOTE_RULES.md` against.

---

## 3. The Core Adversarial Finding

`DIRECTIONAL_VOTE_RULES.md` proposed, for EMA: *"`EMA(9) > EMA(21) > EMA(50)` → Bullish (+1)"*. Compare this directly to `structure_facts.py`'s actual, already-implemented rule: *`current.high > previous.high` → `HIGHER_HIGH`* — with the explicit, deliberate choice **not** to further label a sequence of `HIGHER_HIGH`s as "bullish structure."

Both are pure, threshold-free ordinal comparisons of already-computed numbers. The only difference between them is **vocabulary**: one used a purely descriptive label (`HIGHER_HIGH`), the other used a directional/trading-conclusion label (`BULLISH`). Labeling a comparison `BULLISH` rather than `ASCENDING` is not a mathematical difference — it is an assertion that the comparison is *evidence for future price direction*, which is an empirical trading claim requiring justification (replay evidence) that a purely descriptive fact does not require. `DIRECTIONAL_VOTE_RULES.md` applied the stricter, correct standard to Structure Facts and a looser, inconsistent standard to EMA/MACD/VWAP. **This is the finding that drives every conclusion below.**

---

## 4. Primary Question — A vs B vs C vs D

Evaluated against the correction in §3, and against this task's explicit optimization target (minimize arbitrary assumptions, overfitting, correlated double-counting, regime dependency, hidden strategy behavior, look-ahead risk, false confidence, architecture coupling — not "choose the most sophisticated option"):

- **(A) Generic directional interpretation** — this is exactly what §3 found unjustified. Rejected.
- **(B) Strategy-specific interpretation, from raw facts, with nothing generic in between** — safe, but discards the legitimate, already-precedented middle step of computing a shared *descriptive* relationship (exactly what `structure_facts.py` already does for swings) once per fact set rather than recomputing it inside every future strategy.
- **(C) Hybrid: generic objective evidence + strategy-specific thresholds** — closest to correct, but "objective evidence" must be defined narrowly (see §5) or it silently readmits §3's problem under a new name.
- **(D) Facts-only technical layer, all interpretation inside strategies** — the safest option, and correct for every category that cannot support even a descriptive relational fact (RSI, structure's sequence-level synthesis, ATR-as-directional, relative-volume-as-directional).

**Conclusion: a refined Option C that is functionally close to D.** The generic layer may compute *descriptive, non-directionally-labeled* relationship facts (EMA ordering, VWAP position) exactly as it already does for swing points — never a `BULLISH`/`BEARISH` label. Everything past that description is strategy-owned. This maps directly onto this task's own §15 "Option 3" (`TechnicalSnapshot → Objective directional evidence → Strategy-specific interpretation → MTF/setup logic`), with "objective directional evidence" read strictly as *orientation without trading verdict* (`ASCENDING`/`DESCENDING`, not `BULLISH`/`BEARISH`) — the same reading `structure_facts.py` already uses for `HIGHER_HIGH`/`LOWER_HIGH`.

---

## 5. EMA — Adversarially Re-examined

Answering each challenge posed:

- **Why 9/21/50? Specified or merely an example?** ARCHITECTURE.md §9's table column is literally headed "Chosen indicators" — `EMA(9/21/50)` is a direct textual specification of *which periods to compute*, not an example. This part of the prior document's grounding is solid and unchanged.
- **Does EMA alignment represent direction or merely trend structure?** **Trend structure only** — this is §3's finding applied. The ordering of three EMAs is a fact about the current shape of the moving-average stack, exactly analogous to a fact about the shape of a swing sequence. Calling it "direction" was the error.
- **Does price-vs-EMA provide different information?** Yes, and it introduces a new ambiguity the alignment rule doesn't have: which of the three EMAs is "the" reference for a single price comparison is unspecified (§4 of `DIRECTIONAL_VOTE_RULES.md` already flagged and rejected this for the same reason) — not revisited further here, still rejected.
- **Is EMA slope necessary?** No — still rejected, same undefined-lookback problem as before, unaffected by this review.
- **Does this become strategy logic?** Under the corrected framing, *interpreting* the alignment fact as bullish/bearish is now explicitly strategy logic, by design — that's the fix.
- **Does it work identically on Daily/1H/15M/5M, and in trending/ranging regimes?** As a *descriptive fact* ("the three EMAs are ascending"), yes — the fact is true or false independent of context, exactly like `HIGHER_HIGH` is. As a *directional claim*, this question was never answerable without regime data this project doesn't have yet (`domain/regime` doesn't exist) — which is precisely why the directional claim, not the fact, is being deferred.
- **Does it duplicate MACD evidence?** Yes — addressed in §11, unaffected by the relabeling; correlation is a property of the underlying numbers, not the label attached to them.

**Corrected classification: FACT-ONLY.** A descriptive `EMAAlignment` fact (`ASCENDING`/`DESCENDING`/`MIXED`/`INSUFFICIENT_DATA`) is well-specified, threshold-free, and — unlike a directional vote — requires no owner sign-off on a trading assumption to become implementable; it requires only a scope-expansion decision (§13). No `BULLISH`/`BEARISH` label is approved at this layer.

---

## 6. MACD — Adversarially Re-examined

- **Periods are unspecified — does this alone block implementation?** Yes, independent of everything else in this document. Even under the corrected descriptive-fact framing (§5's logic applied identically: `macd_line`-vs-`signal_line` as `ABOVE_SIGNAL`/`BELOW_SIGNAL`/`AT_SIGNAL`, not `BULLISH`/`BEARISH`), the comparison still needs *some* fast/slow/signal periods to exist at all, and none are given anywhere in the repository. This is a distinct, more basic blocker than the labeling issue — fixing the label doesn't fix the missing periods.
- **Does the line-vs-signal comparison alone represent direction?** No — same correction as EMA: it represents a relationship between two computed numbers, nothing more, until a strategy says otherwise.
- **Redundant with EMA-derived evidence? Should zero-line/histogram matter?** Histogram is mathematically identical to the line-vs-signal comparison (already established, unchanged). Zero-line position (`macd_line > 0`) is highly correlated with EMA alignment (both ask "is a short average above a long average") — including both as separate facts would risk exactly the double-counting this task warns against; zero-line position remains excluded, as previously concluded.
- **Better treated as a strategy-level feature entirely?** Given the periods are unspecified regardless of labeling, yes for now — there is nothing safe to compute generically until periods are supplied, so it may as well remain entirely a strategy-level concern (a strategy that wants MACD can specify its own periods internally) until/unless the owner supplies periods for a shared computation.

**Corrected classification: FACT-ONLY / DEFERRED**, for a compound reason — the labeling correction (§3) removes the directional claim, but the missing-periods problem independently blocks even the descriptive-fact version. Unlike EMA, MACD has no clear, low-risk path forward without an owner decision on periods first.

---

## 7. VWAP — Adversarially Re-examined

- **Valid for every timeframe? Meaningful for Daily/1H/15M/5M?** As a mathematical comparison, `close > vwap(over whatever candles were supplied)` is always well-defined regardless of timeframe — its *well-definedness* doesn't depend on session-bounding. Its *usefulness as directional evidence* does. This is the same fact/interpretation split as §3 and §5.
- **Does multi-day VWAP make the interpretation misleading?** Yes, for *interpretation* — not for the underlying comparison's correctness as a stated fact. "Price is above the VWAP computed over the exact candles given" is true or false regardless of what those candles were; whether that's a *meaningful* observation is entirely dependent on what candles were given, which is a consumption-time concern, not a computation-time one.
- **Should VWAP be intraday-only? Who guarantees session-bounded input? Can generic Setup Detection safely assume that guarantee?** No such guarantee exists anywhere in the architecture today (re-confirmed: no orchestration/scheduler code exists at all). **No, nothing may assume it.**
- **Does VWAP therefore belong to strategy-specific interpretation?** The *directional claim* ("above VWAP means bullish") — yes, entirely, and doubly so because of the unmet session-bounding precondition on top of the general labeling correction. The *descriptive fact* ("price is above/below/at the VWAP computed from the given candles") can be computed generically, exactly like EMA's descriptive fact, **carrying forward, unmodified, the existing VWAP implementation's own documented caveat** that the caller controls session-windowing.

**Corrected classification: FACT-ONLY.** No `BULLISH`/`BEARISH` label. A `VWAPPosition` descriptive fact (`ABOVE`/`BELOW`/`AT`/`INSUFFICIENT_DATA`) is well-specified and computable today, with the session-bounding caveat carried forward explicitly to any consumer — the existing `structure.py` VWAP implementation is **not modified**, per this task's explicit instruction.

---

## 8. RSI — Confirmed Unchanged

No relational structure exists for RSI the way EMA has three given periods to compare against each other — RSI has only one given period (14), so there is no threshold-free comparison available even under the corrected descriptive-fact framing (a second RSI period would itself be an invented parameter). **FACT-ONLY / DEFERRED, confirmed, for a cleaner reason than before:** it isn't just that a directional label would be premature — there is no comparison to make at all without inventing a second parameter.

---

## 9. Structure — Confirmed Unchanged

Unaffected by §3's correction, because `structure_facts.py` already applied the correct standard from the start — this is the module the rest of this document is calibrated against. The sequence-aggregation problem (how many `HIGHER_HIGH`s constitute "confirmed structure") remains exactly as unresolved and exactly as correctly left unresolved as `DIRECTIONAL_VOTE_RULES.md` §10 found. **FACT-ONLY, confirmed, no change.**

---

## 10. ATR — Confirmed Unchanged

Re-verified: ARCHITECTURE.md §9 places ATR under "Volatility" only, never paired with directional language anywhere. Under the corrected framing, is there even a *descriptive* orientation fact for ATR (e.g. "ATR increasing")? No — that would require an undefined lookback window, the same problem that blocked EMA slope (§5) and RSI's own slope variant. **Non-directional, confirmed. No fact or vote of any kind proposed.** Consumption remains `engines/risk_engine.py` (stop distance, sizing) and potentially `domain/regime` (volatility-regime classification) — both pre-existing, documented future homes, not proposed here.

---

## 11. Relative Volume — Confirmed Unchanged

Re-verified: ARCHITECTURE.md §9's own text states relative volume "confirms breakout/breakdown validity" — the clearest textual anchor in this entire review, unaffected by §3's correction since no directional claim was ever proposed for it. **Confirmation-only, confirmed.** High relative volume is not turned into bullish or bearish direction by anything in this document.

---

## 12. Correlated Evidence — Rigorous Review

EMA-ordering, MACD-line-vs-signal (once periods exist), and VWAP-position are all derived from smoothed/weighted price averages of the same underlying candle series — they are **correlated, not independent**, regardless of whether they are labeled as facts or votes. This was already noted in `DIRECTIONAL_VOTE_RULES.md` §13 and is **not weakened by this review** — if anything, the correction strengthens the case against combining them: since interpretation is now explicitly a `StrategyDefinition` concern rather than a generic-engine concern, it is even more important that no generic layer ever assembles these facts into anything resembling a combined score, because doing so would be constructing exactly the kind of `EMA=30%, MACD=30%, RSI=20%, VWAP=20%` weighted system this task explicitly forbids inventing, and which no document anywhere supports with actual weights. **No weighting scheme is proposed, approved, or implied by this document.** Any future combination is a `StrategyDefinition`'s own responsibility to justify and replay-validate, on its own terms, for its own purposes — never a generic default.

---

## 13. Missing-Data Semantics

The four-state discipline from `DIRECTIONAL_VOTE_RULES.md` §11 carries forward unchanged in *shape*, relabeled to match the corrected, non-directional vocabulary:

```
For a descriptive relationship fact (e.g. EMA ordering):
    ASCENDING / DESCENDING / MIXED   — three well-defined comparison outcomes
    INSUFFICIENT_DATA                — a distinct, non-numeric fourth state,
                                        never collapsed into any of the three above
```

`0`/`NEUTRAL`/`MIXED` must never be used to mean "data unavailable" — restated, unweakened, from ARCHITECTURE.md §9's own clarifying sentence (added in the previous milestone in this gate sequence). `NOT_APPLICABLE` remains the correct label for categories that structurally never produce any comparison at all (RSI, ATR, Relative Volume, Structure's synthesized verdict) — distinct from `INSUFFICIENT_DATA`, which means "would have computed something, but didn't have enough history."

---

## 14. Confidence — Confirmed Deferred

Unchanged. No formula invented. Directional evidence — now understood to mean *descriptive facts*, not trading votes — can and does exist without any claim about statistical reliability. If anything, deferring confidence is *more* clearly correct under the corrected model: a purely descriptive fact ("EMAs are ascending") doesn't need a confidence value any more than `HIGHER_HIGH` does — confidence, if it is ever defined, belongs entirely at the strategy-interpretation layer where an actual trading claim is finally being made, not at the fact layer.

---

## 15. MTF Architecture Decision

Given §4–§11, the dependency chain from `DIRECTIONAL_VOTE_SPECIFICATION.md` §10 is revised:

```
TechnicalSnapshot(instrument, timeframe, as_of)        [implemented, unchanged]
        +
EMA/VWAP descriptive relationship facts (proposed,      [NOT implemented — future,
  NOT implemented here — see §17)                        separately-authorized milestone]
        ↓
StrategyDefinition.detect_setup(market_state, mtf_context)  [consumes facts, assigns
                                                               directional meaning — the
                                                               ONLY place that happens]
        ↓
MTF alignment (FULL_ALIGNMENT/PARTIAL_ALIGNMENT/CONFLICT/REGIME_UNCLEAR)  [§10, unchanged,
                                                                            still not built]
        ↓
Setup → Risk → Trade Gate                                [unchanged, out of scope]
```

This is a **meaningful revision** from the prior documents' chain, which placed a generic "directional evidence" step *before* `StrategyDefinition`. Under the corrected model, `StrategyDefinition` is not handed a pre-interpreted directional read — it is handed descriptive facts (technical snapshot values, plus optionally the new relationship facts) and does the interpreting itself. `MTFAlignment` as a *parameter into* `detect_setup` (per STRATEGY_FRAMEWORK.md's existing signature) still implies *some* shared, pre-strategy computation exists for combining timeframes — but what feeds that computation is now understood to be facts, not votes, pushing more of the interpretive work than previously assumed into a place this document has not resolved (see §19, O-7). **`CONFLICT` cannot become `BUY`/`SELL` by weighted averaging — re-confirmed, strengthened, not weakened:** there is now even less generic machinery in the picture that could accidentally perform such an average, since the generic layer no longer produces anything with a sign to average in the first place.

---

## 16. Strategy Boundary (revised)

| Rule | Generic Technical (`domain/technical`) | StrategyDefinition | Risk | Trade Gate |
|---|---|---|---|---|
| EMA ordering (descriptive fact) | **Yes**, if authorized (§17) | Consumes and interprets | No | No |
| EMA "bullish/bearish" claim | **No — moved here from the prior document** | **Yes, exclusively** | No | No |
| MACD relationship (descriptive fact) | Blocked on periods (§6) | Consumes and interprets, once unblocked | No | No |
| VWAP position (descriptive fact) | **Yes**, if authorized (§17) | Consumes and interprets, respecting session-bounding caveat | No | No |
| RSI interpretation | No (fact only, unchanged) | Yes, exclusively | No | No |
| Structure interpretation | No (fact only, unchanged) | Yes, exclusively | No | No |
| Entry condition, invalidation, target, position sizing | Unchanged from `DIRECTIONAL_VOTE_SPECIFICATION.md` §11 | — | — | — |

The one substantive change from the prior boundary table: EMA/MACD/VWAP's *directional claims* move from "Setup Engine, generic" to "StrategyDefinition, exclusively" — everything else in that prior table is unaffected.

---

## 17. Replay Validation — Simplified by the Correction

This is a direct, positive consequence of §3's finding, not merely a restatement: **a purely descriptive fact requires no trading-performance replay validation at all.** "EMA(9) > EMA(21) > EMA(50)" is arithmetically exact — there is nothing to validate about whether the comparison is *correct*, the same way there's nothing to validate about whether `HIGHER_HIGH` correctly describes two swing prices. It needs ordinary unit tests (deterministic, hand-verifiable — exactly the standard already applied to every existing `domain/technical` primitive), not replay-based performance evidence.

**Replay validation remains fully required, unweakened, for exactly one thing: any `StrategyDefinition`'s claim that a fact (or combination of facts) predicts price direction.** That lifecycle is unchanged from `DIRECTIONAL_VOTE_RULES.md` §15–17 (`UNVALIDATED → REPLAY VALIDATED → PAPER TRADING → OWNER APPROVAL → production`), restated here as still binding, still requiring: multiple historical regimes (trending/ranging, by inspection since no regime classifier exists), multiple instruments, multiple timeframes, out-of-sample separation, no repeated-replay-until-it-looks-good parameter tuning, and human approval before promotion — no invented win-rate or performance number, consistent with every prior document in this sequence.

**What this means concretely:** adding `EMAAlignment`/`VWAPPosition` facts to `domain/technical` (§17 below) needs only an owner **scope decision** ("yes, compute this too") — a much lower bar than the trading-assumption sign-off a directional vote would need, because nothing about a fact's correctness depends on market behavior.

---

## 18. Anti-Overfitting Controls

Unchanged in substance from `DIRECTIONAL_VOTE_RULES.md` §16 — train/validation/test separation, no repeated-sample tuning, look-ahead already structurally prevented, survivorship bias flagged for the eventual replay evaluator, regime coverage required. The corrected model **reduces** the overfitting surface area further: since no generic layer computes a directional claim, there is no generic parameter (a threshold, a weight) left for anyone to inadvertently tune against historical data at the generic layer — that risk is now fully contained within whichever `StrategyDefinition` chooses to make a claim, where STRATEGY_FRAMEWORK.md's existing replay-review requirement already applies per-strategy.

---

## 19. Owner Decision Matrix

| Category | Proposed artifact | Architecture-supported? | Safe to implement now? | Decision |
|---|---|---|---|---|
| EMA (descriptive fact) | `EMAAlignment`: `ASCENDING`/`DESCENDING`/`MIXED`/`INSUFFICIENT_DATA` | Directly supported (periods given, §9) | Yes, pending scope authorization only | **FACT-ONLY** (ready pending O-1) |
| EMA (directional claim) | `BULLISH`/`BEARISH` label | Not supported — §3's finding | No | **DEFER to StrategyDefinition, permanently at this layer** |
| MACD | Any relationship fact or claim | Blocked — periods unspecified | No | **DEFER** (needs O-2 first) |
| VWAP (descriptive fact) | `VWAPPosition`: `ABOVE`/`BELOW`/`AT`/`INSUFFICIENT_DATA` | Directly supported (computation), caveat design-inference | Yes, with session-bounding caveat documented, pending scope authorization | **FACT-ONLY** (ready pending O-1) |
| VWAP (directional claim) | `BULLISH`/`BEARISH` label | Not supported | No | **DEFER to StrategyDefinition, permanently** |
| RSI | Any | Not supported — no relational structure exists | No | **DEFER** (unchanged) |
| ATR | Any directional artifact | Explicitly contradicted by §9's own categorization | No | **REJECT** (as directional; confirmed non-directional risk input) |
| Relative Volume | Any directional artifact | Explicitly contradicted by §9's own text | No | **REJECT** (as directional; confirmed confirmation-only) |
| Structure | Confirmation-count synthesis | Not supported | No | **DEFER** (unchanged) |

---

## 20. Explicit Rejected Alternatives

- **Generic `BULLISH`/`BEARISH` labeling of EMA/MACD/VWAP relationships** — the central rejection of this document, per §3.
- **A weighted composite of correlated facts** — rejected, §12, consistent with every prior document.
- **RSI midpoint/band rule, generic structure confirmation-count rule** — rejected, unchanged from `DIRECTIONAL_VOTE_RULES.md`.
- **Treating "objective directional evidence" (this task's Option 3 wording) as license to reintroduce directional labels at the generic layer** — considered and rejected; "objective" is read strictly as *orientation without trading verdict*, matching `structure_facts.py`'s own precedent, not as permission to relabel §3's finding away.
- **Option 1** (`TechnicalSnapshot → DirectionalVote → MTF Alignment`) — rejected as the primary architecture; it's what `DIRECTIONAL_VOTE_RULES.md` implicitly assumed, and §3 found that assumption unjustified.

---

## 21. Required Documentation Changes

**One minimum correction made in this session:** a short supersession note added to the top of `DIRECTIONAL_VOTE_RULES.md`, pointing to this document, without rewriting or deleting that document's content — its EMA/MACD/VWAP sections remain useful evidentiary material (the comparison logic, the periods analysis) even though their `BULLISH`/`BEARISH` labeling conclusion is superseded here. No other document required correction; `ARCHITECTURE.md`'s §9 clarification from the prior milestone (missing-data ≠ `0`) remains accurate and unaffected by this review.

---

## 22. Final Gate

**HOLD.**

Not because the specification is incomplete in the way prior HOLDs meant it — this review found the prior specification's central mechanism (a generic directional-vote layer) to be architecturally unjustified, not merely unapproved. GO is not appropriate for something this review recommends not building. A narrower, lower-risk path (descriptive relationship facts for EMA/VWAP, §17/§19) is identified and could reasonably reach GO on a much smaller, separate owner decision (a scope-expansion nod, not a trading-assumption approval) — but that is a new, distinct proposal for a future milestone to evaluate on its own, not an implicit GO granted here.
