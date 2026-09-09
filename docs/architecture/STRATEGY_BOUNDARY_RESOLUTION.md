# Strategy Boundary Resolution

Status: **Design decision document — awaiting owner approval.** No production code accompanies this document. Every decision below is labeled `[A]` explicitly specified by the repository, `[B]` strong architectural inference, `[C]` design proposal requiring owner approval, or `[D]` unsupported/unresolved. Nothing labeled `[B]`/`[C]`/`[D]` should be read as already-approved architecture.

This document adversarially re-examines the `StrategyDefinition` proposal from the prior audit (`Mapping[Timeframe, TechnicalSnapshot]`) rather than ratifying it, per this milestone's explicit instruction — and finds a real flaw in it (§7).

---

## 1. Executive Verdict

**HOLD** for implementation. This document resolves the design questions with labeled, evidence-grounded proposals and corrects one flaw found in the prior session's proposal, but every proposal here requires owner sign-off before any code is written — none of it is presented as already-approved.

---

## 2. Current Verified Architecture

Re-verified fresh this session:
```
pytest -q                  -> 154 passed
ruff check .                 -> All checks passed!
mypy --strict app tests      -> Success: no issues found in 82 source files
```
`domain/technical/` — 10 fact-only modules, `TechnicalSnapshot` confirmed to already carry `instrument_id`, `timeframe`, `as_of`, `periods`, plus the seven primitive results (read directly from `snapshot.py`, not assumed). No `StrategyDefinition`, `MarketState`, `Setup`, `MTFAlignment` anywhere in code. `domain/{options,regime,news,behavior}` absent or empty. `engines/`, `orchestration/` empty. `BROKER_ORDER_EXECUTION_ENABLED=false`.

---

## 3. Contradictions `[A]`

Both directly re-confirmed by text, unchanged from the prior audit:

1. ARCHITECTURE.md §22: `MarketState` is assembled *before* `domain/technical` runs — but STRATEGY_FRAMEWORK.md's `detect_setup(market_state, mtf_context)` has no parameter for technical facts at all.
2. ARCHITECTURE.md §10 defines `MTFAlignment`'s states in explicitly directional terms ("daily bullish," "HTF agrees") — which presupposes a per-timeframe directional read that `DIRECTIONAL_VOTE_FINAL_GATE.md` established must be strategy-owned, not generic. Nothing upstream of a strategy can legitimately produce a valid `MTFAlignment` to hand it.

A third, minor inconsistency `[A]`: `detect_setup`'s documented signature omits `as_of`, despite it being a mandatory parameter on every other function in this codebase without exception.

---

## 4. Evidence From Source Documents

Directly quoted, not paraphrased, for the load-bearing citations:

- ARCHITECTURE.md §5: *"domain/market — Assembles `MarketState` (instrument, OHLCV series, LTP, VWAP, breadth) from normalized data."*
- ARCHITECTURE.md §21: *"each `MarketState` carries `as_of` and `staleness_seconds` per data type it's built from."*
- ARCHITECTURE.md §22: *"before `domain/market` assembles a `MarketState`... before `domain/technical`/`domain/options` run."*
- STRATEGY_FRAMEWORK.md: `detect_setup` is *"pure — it consumes already-computed `domain/technical` and `domain/options` outputs... It never fetches data itself and never calls the LLM."*
- STRATEGY_FRAMEWORK.md: `RiskRewardPolicy` — *"minimum acceptable R:R, target-derivation method."*
- STRATEGY_FRAMEWORK.md: `InvalidationCondition` — *"derived from market structure (Risk & Behavior doc, `invalidation_level()`), never an arbitrary percentage."*
- RISK_AND_BEHAVIOR.md: `invalidation_level()` → *"price — The structural level that, if breached, proves the thesis wrong... derived from market structure (e.g. beyond the swing low/high that defined the setup)."*

---

## 5. Proposed StrategyDefinition Contract `[C]`

```python
class TechnicalRequirement(BaseModel):
    """What one (timeframe, period-set) computation a strategy needs.
    Multiple requirements MAY share a timeframe (see §7) — this is why
    `required_technical_inputs` returns a sequence of these, not a mapping
    keyed by timeframe alone."""
    timeframe: Timeframe
    periods: TechnicalSnapshotPeriods


class StrategyDefinition(Protocol):
    name: str
    version: str

    def required_technical_inputs(self) -> Sequence[TechnicalRequirement]: ...

    def evidence_requirements(self) -> list[EvidenceCategory]: ...

    def detect_setup(
        self,
        *,
        market_state: MarketState,
        technical: Sequence[TechnicalSnapshot],
        as_of: datetime,
    ) -> Setup | None: ...

    def invalidation_rule(self, setup: Setup) -> InvalidationCondition: ...
    def risk_reward_policy(self) -> RiskRewardPolicy: ...
```

Every `TechnicalSnapshot` in `technical` MUST have `.as_of == as_of` — a required invariant `[C]`, not yet enforced anywhere since nothing implements this yet; whoever builds the orchestration layer that assembles this sequence must enforce it, and whoever tests a concrete `StrategyDefinition` should assert it the same way `bounded_series()`'s invariants are already asserted today.

No `mtf_context` parameter — removed per §8. `detect_setup` now receives `as_of` explicitly, closing the inconsistency in §3.

---

## 6. MarketState Exact Proposed Contract `[C]`, with `[D]` gaps marked

| Field | Status | Note |
|---|---|---|
| `instrument_id: str` | `[C]` | Consistent with every other domain type's convention |
| `as_of: datetime` | `[A]`-grounded, `[C]` for exact placement | §21 states this exists |
| `staleness_seconds` (or a richer per-field freshness structure) | `[A]`-grounded, `[D]` for exact shape | §21 says "per data type it's built from" — implies nesting, never shown |
| `last_price` / LTP | `[C]` | §5 names "LTP"; type/precision (Decimal, matching `Quote.last_price`) inferred from existing convention |
| `previous_close` | `[D]` | Not named in §5, plausible but unconfirmed |
| `vwap` | `[C]`, with a caveat | §5 names "VWAP" as a MarketState field — **this likely duplicates `vwap_position.py`'s `VWAPPositionResult.vwap_value`**, a domain/technical fact computed from candles. Recommend MarketState's VWAP, if kept, be a simple current-value fact (e.g. the provider's own VWAP field on a `Quote`, if one exists) rather than a recomputation — needs owner clarification, not resolved here. |
| `breadth` | `[D]` | Named, never typed; "market breadth" as a concept has no producer anywhere in this codebase yet |
| **"OHLCV series"** | **`[C]` — recommend excluding entirely** | §5 names it, but `list[Candle]` is already the standard, direct representation passed to every `domain/technical` function independently. Embedding a duplicate OHLCV series inside `MarketState` risks two independently-fetched "same" series silently diverging. Recommend `MarketState` carry only *current-instant* facts (LTP, previous close, today's running O/H/L/C, breadth), never a historical series — the historical series stays exactly where it already lives, passed directly. |

**Must NOT contain** (`[A]`, restated from this task's own explicit list, and consistent with every finding so far): `TechnicalSnapshot`, any RSI/MACD/EMA interpretation, strategy signals, options/regime/news interpretation, trade decisions, confidence, `BUY`/`SELL`/`CE`/`PE`, position sizing.

**Per-instrument or per-timeframe?** `[D]` — unresolved. If `MarketState` is reduced to current-instant facts only (per the OHLCV recommendation above), it plausibly needs no timeframe dimension at all (LTP/breadth aren't timeframe-scoped concepts) — but this is a `[C]` inference, not confirmed by any document.

---

## 7. TechnicalSnapshot Input Flow — Adversarial Correction `[C]`

The prior session's proposal (`technical: Mapping[Timeframe, TechnicalSnapshot]`) is **found to be flawed on adversarial review**, not merely re-approved:

- `TechnicalSnapshot` already carries its own `timeframe` **and** `periods` fields (confirmed by direct read of `snapshot.py` this session) — keying an outer `Mapping` by `Timeframe` duplicates information already inside each value.
- A `Mapping[Timeframe, TechnicalSnapshot]` can hold **at most one** snapshot per timeframe. A strategy legitimately might want two different period-sets on the *same* timeframe (e.g. a fast EMA pair for entry timing and a slower pair for trend context, both on 5m) — the Mapping design cannot express this at all.

**Corrected recommendation: `technical: Sequence[TechnicalSnapshot]`** (this task's option B), each entry fully self-describing via its own `timeframe`/`periods`/`as_of` fields — no redundant, limiting outer key. `required_technical_inputs()` returns `Sequence[TechnicalRequirement]` (§5) for the same reason — a strategy can declare as many `(timeframe, periods)` pairs as it needs, including repeats on one timeframe.

Full option comparison, evaluated against ownership/typing/replayability/determinism/strategy-independence/extensibility/testability/coupling/timeframe-correctness:

| Option | Verdict |
|---|---|
| A. `Mapping[Timeframe, TechnicalSnapshot]` | **Rejected on adversarial review** — see above |
| B. `Sequence[TechnicalSnapshot]` | **Recommended** `[C]` — no information loss, no artificial one-per-timeframe limit, each item self-describing, clean for replay (a flat list of complete, already-typed objects) |
| C. Strategy-specific bundled context object | Deferred, unchanged from prior audit — would need options/regime/news fields that have no producer yet; building it now risks exactly the placeholder-abstraction problem this task prohibits |
| D. `MarketState` embeds technical facts | **Rejected**, unchanged — directly contradicts §22's sequencing (§3) |

`required_technical_inputs()`'s necessity, re-examined adversarially: could a strategy instead just receive raw candles and call `assemble_technical_snapshot()` itself inline, with no declarative method at all? This is a real, simpler alternative. It was **not chosen**, because a declarative "what do I need" method is what makes strategy input requirements *inspectable without running the strategy* — directly useful for the replay-identity requirement in §13, and for orchestration to de-duplicate identical requests across multiple strategies. This is a real cost/benefit trade-off, not a free win — flagged explicitly as `[C]`, not the only defensible design.

---

## 8. MTF Ownership Model

Unchanged conclusion from the prior audit, restated with the task's exact five options addressed:

1. Remain strategy-internal concept — **yes, primarily this** `[C]`.
2. Become a strategy-owned value object — **yes, as the output of a strategy's own internal computation**, if a strategy chooses to compute one at all; nothing requires it to.
4. Removed entirely from the generic interface — **yes, done in §5's proposed signature** (`mtf_context` parameter removed).
3. A narrowly-scoped mechanical combinator (taking already-strategy-assigned per-timeframe labels as input, applying only §10's already-fully-specified combination table) — **plausible future utility, explicitly not endorsed for building now** `[D]`, flagged as a real slippery-slope risk if built carelessly (it must never itself decide any indicator's direction).

**Non-negotiable, re-stated `[A]`-grounded principle:** a generic utility must never decide whether EMA/RSI/MACD/etc. is bullish or bearish. Nothing in this document proposes that it should.

---

## 9. EvidenceCategory Decision

**`[D]` — left open.** §13's 15 named evidence categories (market regime, HTF trend, ... conflict penalty) are never confirmed to be the same vocabulary `StrategyDefinition.evidence_requirements()` is meant to return — they appear in prose for `SignalScore`, a different, not-yet-designed downstream component. Whether `EvidenceCategory` is an enum of exactly those 15, a superset, a strategy-specific open vocabulary, or something else entirely is unresolved by any document. Not invented here.

---

## 10. Setup Contract Issues

| Field | Type-defined | Meaning-defined | Producer-defined | Validation-defined | Status |
|---|---|---|---|---|---|
| `strategy_name`/`strategy_version` | ✓ `[A]` | ✓ | ✓ (the strategy itself) | — | Clear |
| `direction: Literal["BULLISH","BEARISH"]` | ✓ `[A]` | ✓ (a strategy's own conclusion) | ✓ (the strategy) | — | Clear — assignment rule is intentionally strategy-internal, not a gap |
| `origin_timestamp` | ✓ `[A]` | ✓ | ✓ | — | Clear |
| `trigger_level: Decimal` | ✓ `[A]` | ✓ | ✓ | — | Clear |
| `structural_basis: str` | ✓ type, `[D]` for continued appropriateness | Partial | ✓ | — | **See below** |
| `mtf_alignment: MTFAlignment` | `[D]` — type undefined, and per §8, may not belong as a required field at all if a given strategy never computes one | — | — | — | Open, tied to §8 |

`structural_basis: str` — re-examined per this task's four options: (A) keep string, (B) typed structural reference, (C) evidence collection, (D) other. **Recommendation `[C]`, requiring owner approval, `Setup` itself NOT changed here per instruction:** now that typed fact objects exist (`EMAAlignmentResult`, `VWAPPositionResult`, `StructureFactsResult`, `TechnicalSnapshot` sub-results), a free-text string is a weaker auditability guarantee than a small typed evidence collection referencing the specific fact objects/fields that justified the setup — machine-checkable, reproducible, consistent with this project's now-established typed-evidence convention throughout `domain/technical`. This is a recommendation for a future revision to `Setup`, not something to implement now.

---

## 11. InvalidationCondition Contract

**Boundary, `[A]`-grounded, precisely stated:** a `StrategyDefinition` declares *intent/rule* (which structural reference to use — e.g. "beyond the swing low that defined this setup's origin") via `invalidation_rule(setup) -> InvalidationCondition`. `engines/risk_engine.py` independently computes the *actual numeric level* via `invalidation_level()` (RISK_AND_BEHAVIOR.md), and independently validates the resulting risk. These are explicitly **not the same thing** — STRATEGY_FRAMEWORK.md's own text: *"a strategy's declared policy is a stated intent, not a substitute for the deterministic recomputation."*

`InvalidationCondition`'s exact field shape: `[D]`, unresolved. Plausibly a typed reference to a specific structural fact (e.g. a particular `SwingPoint` from `StructureFactsResult`) rather than a bare price — RISK_AND_BEHAVIOR.md's example ("beyond the swing low/high that defined the setup") supports a structural-reference shape over a numeric one, but no field list is given anywhere. Not invented here.

---

## 12. RiskRewardPolicy Contract

**Minimum contract directly supported by text:** `minimum_acceptable_rr` (a ratio) and a `target_derivation_method` (STRATEGY_FRAMEWORK.md's own two named concepts). Everything else — exact types, whether `target_derivation_method` is an enum or a callable reference, whether additional fields exist — is `[D]`, unresolved, not invented here.

---

## 13. as_of Semantics

- **Where does `as_of` enter strategy evaluation?** As an explicit parameter to `detect_setup` (§5's proposal — this closes §3's third inconsistency).
- **Does `StrategyDefinition` receive it explicitly?** Yes, per §5.
- **Must every `TechnicalSnapshot` share identical `as_of`?** **Yes — required invariant**, stated in §5, not yet enforced anywhere (nothing implements this yet).
- **Must `MarketState` share the same `as_of`?** Yes, same reasoning.
- **How is stale data represented?** Inherited unchanged from the existing `DataQualityGate`/freshness mechanism — nothing new needed or proposed here.
- **How does replay reconstruct exactly the same state?** Via the existing `HistoricalProvider`/`bounded_series()` as_of-bounded reads, already proven safe and already replay-tested for `TechnicalSnapshot` — `MarketState`, once built, would need the same treatment (a future implementation requirement, not resolved here).
- **No wall clock, no future information, no implicit "now":** restated as a hard requirement on any future concrete `StrategyDefinition` implementation, exactly as already enforced (by source-scanning tests) for every `domain/technical` module today — the same testing discipline must extend to strategy code once it exists.

---

## 14. Replay Determinism

Deterministic strategy input identity, per this task's explicit ask, consists of: `strategy_name`, `strategy_version`, the exact `Sequence[TechnicalSnapshot]` used (each self-describing its own `periods`/`timeframe`/`as_of` — no separate record needed, a real benefit of §7's corrected design), `MarketState`, and `as_of`. **`strategy_name` + `strategy_version` must become part of the eventual audit/replay identity** `[B]` — two versions of "the same" strategy could produce different `Setup`s from identical inputs if the strategy's own code changed, and STRATEGY_FRAMEWORK.md already carries both fields on `Setup` itself, so this is a direct consequence of an existing decision, not a new invention.

---

## 15. Dependency Graph

```
data/normalization
        ↓
domain/market  (MarketState — current-instant raw facts only, §6)
        ↓ (independent)                    domain/technical (TechnicalSnapshot, unchanged, per (timeframe, periods) pair)
        └───────────────────┬───────────────────────┘
                             ↓
     StrategyDefinition.detect_setup(market_state, technical: Sequence[...], as_of)
                             ↓
                           Setup
                             ↓
               engines/risk_engine.py  (independent recomputation)
                             ↓
                     engines/trade_gate.py
```
`domain/options`/`domain/regime`/`domain/news` remain outside this graph — see §17.

---

## 16. Ownership Matrix

| Responsibility | Owner |
|---|---|
| Calculations, comparisons, descriptive facts | `domain/technical` (unchanged, verified fact-only) |
| Interpretation, direction assignment, setup recognition, trigger logic, strategy-specific MTF reasoning, strategy-specific structural interpretation | `StrategyDefinition` |
| Actual risk numbers, actual invalidation level, actual target, position sizing, independent risk validation | Risk Engine |
| Deterministic final eligibility gate | Trade Gate |

No layer's responsibility is reassigned by this document — this table restates, does not alter, the boundary established across the prior five gate documents.

---

## 17. Options / Regime / News — No Placeholder Objects

Per this task's explicit instruction, **no placeholder `Context` object is created for future extensibility.** `MarketState` and the technical-input flow (§5–§7) are scoped to exactly what exists today. When `domain/options`/`domain/regime`/`domain/news` eventually produce real, typed facts, extending `StrategyDefinition`'s interface to receive them is a future, separately-scoped interface change — not solved in advance by an empty aggregate now. Each of those future domains will very likely face its own fact-vs-interpretation gate (ARCHITECTURE.md §11's options checks and threshold language already hint at this for options) before contributing anything beyond raw facts — flagged for whenever that work begins, not resolved here.

---

## 18. Rejected Alternatives

- `MarketState` embedding `TechnicalSnapshot` (Option D throughout) — directly contradicts §22.
- `Mapping[Timeframe, TechnicalSnapshot]` — adversarially rejected this session (§7) for the one-snapshot-per-timeframe limitation and redundant keying.
- A strategy-bundled "Context" object including options/regime/news fields now — rejected as a placeholder abstraction with no current producer.
- A generic MTF-alignment-producing layer, in any form — rejected, consistent with every prior gate document.
- `MarketState` carrying a full OHLCV series — recommended against (§6), to avoid duplicating the already-standard `list[Candle]` responsibility.

---

## 19. Unresolved Owner Decisions

1. Approve/amend the `StrategyDefinition`/`TechnicalRequirement` interface (§5, §7) — the core blocking decision.
2. `MarketState`'s exact field list, especially the VWAP-duplication question and whether it needs a timeframe dimension at all (§6).
3. `EvidenceCategory`'s vocabulary and relationship to `SignalScore` (§9) — left fully open.
4. `Setup.structural_basis`'s continued appropriateness as a free-text field vs. a typed evidence reference (§10) — recommendation given, `Setup` not changed.
5. `InvalidationCondition`/`RiskRewardPolicy` exact field shapes (§11, §12).
6. Whether a shared, narrowly-scoped MTF combinator utility is ever worth building, and how to keep it from becoming a generic interpretation layer by accident (§8).

---

## 20. Implementation Prerequisites (before any code)

1. Owner resolves §19's six decisions, at minimum #1.
2. `MarketState` gets a concrete, owner-approved field list (currently `[D]` in key places).
3. `TechnicalRequirement`, revised `StrategyDefinition` Protocol get implemented as **interfaces only** — no concrete strategy.
4. Only then: a first concrete `StrategyDefinition`, immediately subject to STRATEGY_FRAMEWORK.md's existing replay-review requirement.

---

## Final Gate

**HOLD.**
