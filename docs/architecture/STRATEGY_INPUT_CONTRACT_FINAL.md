# Strategy Input Contract — Final

Status: **Design decision document — awaiting owner approval. No production code accompanies this document.** Labels used throughout: `[A]` explicit repository specification, `[B]` strong architectural inference, `[C]` design proposal requiring owner approval, `[D]` unsupported/unresolved.

**This document reverses the prior session's `TechnicalRequirement` / `Sequence[TechnicalSnapshot]` proposal (`STRATEGY_BOUNDARY_RESOLUTION.md` §5, §7) after genuine adversarial review, not a re-approval of it.** The reasoning is in §5–§9.

---

## 1. Executive Verdict

**HOLD** for implementation. This document proposes a materially simpler technical-input architecture than the prior session reached, with a specific, evidenced case for why the simpler design carries fewer long-term correctness risks. Still `[C]` throughout — owner approval required before any code.

---

## 2. Verified Repository State

```
pytest -q                  -> 154 passed
ruff check .                 -> All checks passed!
mypy --strict app tests      -> Success: no issues found in 82 source files
```
`domain/technical/` unchanged — 10 fact-only modules. Confirmed by direct read this session: `series.py`'s `bounded_series()` already raises `InvalidCandleSeriesError` if any candle's `instrument_id`/`timeframe` doesn't match the requested parameters (lines 68–73) — this fact is load-bearing for §21's answer to "can timeframes be accidentally mixed."

---

## 3. Authoritative Evidence

Re-confirmed from the same sources cited in `STRATEGY_BOUNDARY_RESOLUTION.md` §4 — not re-quoted in full here to avoid duplicating that document; the new evidence this session adds is the direct code inspection above (§2), and the project's own explicitly stated priority ordering (ARCHITECTURE.md's original brief, restated in this task's own §21 header): *"NOT optimizing for... maximum abstraction... fastest implementation."* This is treated as `[A]` — an explicit, standing project priority — and used directly in §5's reasoning.

---

## 4. Design Goals (restated, unchanged)

Deterministic replay, no-look-ahead, provider neutrality, strategy isolation, explainability, auditability, prevention of threshold leakage, prevention of accidental future-data use, prevention of generic BUY/SELL interpretation, independent strategy comparison, backtestability without modifying the technical engine.

---

## 5. TechnicalRequirement — Adversarial Review (reversal)

Full comparison of the task's options A–F, plus one it doesn't name (G, below), against ownership, coupling, type safety, duplicate information, timeframe ambiguity, replay determinism, strategy versioning, testability, extensibility, leakage risk:

**A. `Sequence[TechnicalRequirement]` (the prior session's proposal).** A two-phase design: the strategy *declares* `(timeframe, periods)` pairs; orchestration *fulfills* the declaration by pre-assembling `TechnicalSnapshot`s and handing them to `detect_setup`. On this session's re-examination, this has a genuine structural weakness the prior session did not weigh: **it is possible for `required_technical_inputs()`'s declaration to drift from what `detect_setup`'s own logic actually reads from the supplied sequence** — nothing prevents a strategy's two methods from silently disagreeing, and such a disagreement would be a *silent* correctness bug (the strategy would simply operate on the wrong periods/timeframes it happened to receive, no crash, no obvious signal). Introduces a new type (`TechnicalRequirement`). Requires `Sequence`, not `Mapping`, precisely because one timeframe can legitimately need multiple period-sets (correctly identified last session) — but this list-scanning API is exactly the ergonomic cost of the two-phase design.

**B. `Sequence[TechnicalSnapshotPeriods]` alone (no timeframe pairing).** Incomplete as stated — doesn't say which periods apply to which timeframe. Rejected as under-specified.

**C. Strategy declares only timeframes; orchestration owns period resolution.** Rejected: orchestration cannot resolve periods without either (i) an invented default (violates the project's standing "no baked-in defaults" rule, established since the Technical Engine milestone) or (ii) some other strategy-supplied declaration — collapsing back into A or G with extra indirection and no benefit.

**D. Strategy receives orchestration/config-selected precomputed snapshots.** Rejected: has the same defaults problem as C, and additionally risks a strategy silently consuming snapshots it never asked for if a *different* strategy's registered needs change the shared precompute set — a real replay-reproducibility risk (the "same" strategy could see a different precompute set across two runs for reasons entirely external to its own code).

**E. Strategy-specific bespoke input-spec object per strategy.** Functionally collapses into A with less standardization — orchestration would need per-strategy-type handling rather than one uniform contract. Not a meaningful improvement.

**G. (this document's proposal, not named in the task's A–F list) — single-phase design: the strategy declares only `required_timeframes() -> Sequence[Timeframe]`; receives raw `candles: Mapping[Timeframe, Sequence[Candle]]` (one canonical candle series per timeframe — no duplication risk here, since raw candles, unlike indicator snapshots, have no period dimension to create ambiguity); computes its own `TechnicalSnapshot`(s) internally, inline, by calling the existing `assemble_technical_snapshot()`/individual `calculate_*`/`compute_*` functions itself, with whatever periods it chooses, as many times as it wants, on any of its declared timeframes.**

**Why G is recommended over A** `[C]`:
- **No declare/use drift is possible** — there is only one place periods are chosen (inside `detect_setup`'s own code), so there is nothing to disagree with.
- **Simpler**: one declared method (`required_timeframes`) instead of two (`required_technical_inputs` + `detect_setup`), no new type.
- **`Mapping[Timeframe, Sequence[Candle]]` is safe where `Mapping[Timeframe, TechnicalSnapshot]` was not** — a raw candle series has no period dimension, so there is exactly one canonical value per key, unlike indicator snapshots (correctly rejected last session for exactly this reason).
- **Replay identity simplifies**: if periods are strategy-code-internal, `(strategy_name, strategy_version)` — already established fields on `Setup` — fully determines what periods were used, since they're baked into the versioned code, not a separate runtime declaration that could itself need independent versioning.
- **Matches the project's own stated priority** (§3): *"NOT optimizing for... maximum abstraction"* — the two-phase design is the more sophisticated-looking option, not the lower-risk one, once the drift risk above is accounted for.
- **Existing validation already defends the main risk this design could introduce** — see §21.

This is **not** presented as `[A]`. It is a considered reversal of the prior session's own proposal, `[C]`, requiring the same owner approval A would have needed.

---

## 6. Final Technical-Input Architecture

```python
def detect_setup(
    self,
    *,
    market_state: MarketState,
    candles: Mapping[Timeframe, Sequence[Candle]],
    as_of: datetime,
) -> Setup | None:
    # Inside its own body, the strategy may call, e.g.:
    #   snapshot_5m = assemble_technical_snapshot(
    #       candles[Timeframe.M5], instrument_id=..., timeframe=Timeframe.M5,
    #       as_of=as_of, periods=<the strategy's own chosen periods>,
    #   )
    # as many times, on as many declared timeframes, with as many different
    # period sets, as it needs — no external declaration of periods exists.
    ...
```

`candles` contains exactly the timeframes the strategy declared via `required_timeframes()`, each a full, already as_of-bounded, already-normalized candle series for that timeframe — sourced from the same `HistoricalProvider`/live-provider path already proven safe, unchanged.

---

## 7. TechnicalSnapshot Identity

`TechnicalSnapshot`'s existing self-describing fields (`instrument_id`, `timeframe`, `as_of`, `periods`) remain valuable for **audit logging of what a strategy actually computed**, and for the existing `bounded_series()` mismatch-detection this document leans on (§21) — but under §5/§6's design, they are **not needed to disambiguate a shared external collection**, because no such collection exists. A strategy holds whatever `TechnicalSnapshot` objects it computed as ordinary local variables; there is nothing to look up or collide. This directly answers this task's §6 question: identity is sufficient *because the problem it would need to resolve doesn't arise in this design*, not because the existing fields solve it after the fact.

---

## 8. Timeframe Contract

`required_timeframes() -> Sequence[Timeframe]` — a strategy declares exactly the timeframes it needs (any subset of the five already-established timeframes: 5m/15m/30m/1h/Daily, per ARCHITECTURE.md §10 — no new timeframe set invented here). Orchestration fetches/assembles a candle series for each declared timeframe and supplies exactly those via `candles: Mapping[Timeframe, Sequence[Candle]]`. Different strategies may declare entirely different, non-overlapping, or overlapping timeframe sets independently — no shared state, no interference.

**Lookback depth** — a real gap this document surfaces (not resolved by `required_timeframes()` alone, since it says *which* timeframes, not *how much history*): see §9.

---

## 9. Period Contract, and the Lookback-Depth Question

**Periods**: owned entirely by the strategy's own code (§5/§6), never orchestration, never `domain/technical`. `domain/technical`'s existing "no baked-in defaults" discipline is fully preserved — nothing changes there.

**Lookback depth** — how many candles per timeframe should orchestration fetch, if it doesn't know what periods a strategy will use? Two options, both `[C]`:
- **(i) Recommended: a generous, configurable default lookback per timeframe, fetched regardless of strategy-specific needs.** Under-fetching degrades *safely*, not dangerously: every `calculate_*`/`compute_*` function already refuses to fabricate a value when given insufficient history, reporting `INSUFFICIENT_HISTORY`/`INSUFFICIENT_DATA` instead (proven, tested throughout `domain/technical`). A strategy that receives fewer candles than it would like simply produces `Setup | None → None` more often — not a wrong answer, a missed opportunity, which this project's own original mission statement explicitly ranks far below correctness (*"NO TRADE is a successful result... The objective is NOT maximum number of trades"*). The exact default window size is `[D]` — not invented here.
- (ii) A narrower `required_lookback() -> Mapping[Timeframe, int]` declaration (candle *count* only, not full periods) — more precise, avoids over-fetching, but is one more declared method for a benefit this project's own stated priorities suggest isn't necessary.

**Recommendation: (i).** Not certain, flagged for owner choice.

---

## 10. MarketState Contract (re-checked, refined)

Re-examined ARCHITECTURE.md §5's exact text (*"instrument, OHLCV series, LTP, VWAP, breadth"*) against this session's new evidence. **Refined conclusion, `[C]`, replacing the prior session's flat "exclude OHLCV" recommendation:** "OHLCV series" most plausibly refers to the **current/running bar's O/H/L/C/V** (a single current-instant summary, analogous to a `Quote`), not a historical multi-candle series — this reading is consistent with `MarketState` being a current-instant-facts object (§21's own list already assigned it) and cleanly avoids duplicating the `list[Candle]` historical-series responsibility that already exists and is passed directly everywhere in this codebase. This is a refinement, not a certainty — the alternative (a true historical series field) remains `[D]`, unresolved, and would need explicit owner confirmation given the duplication risk.

Unchanged from the prior session, restated: **must not contain** `TechnicalSnapshot`, any indicator interpretation, strategy signals/thresholds, MTF interpretation, options/regime/news interpretation, `BUY`/`SELL`/`CE`/`PE`, risk sizing, trade decisions, confidence. `as_of`/staleness fields `[A]`-grounded (§21), exact shape `[D]`. Per-instrument vs. per-timeframe scope remains `[D]`.

---

## 11. StrategyDefinition Contract (revised)

```python
class StrategyDefinition(Protocol):
    name: str
    version: str

    def required_timeframes(self) -> Sequence[Timeframe]: ...
    def evidence_requirements(self) -> list[EvidenceCategory]: ...

    def detect_setup(
        self,
        *,
        market_state: MarketState,
        candles: Mapping[Timeframe, Sequence[Candle]],
        as_of: datetime,
    ) -> Setup | None: ...

    def invalidation_rule(self, setup: Setup) -> InvalidationCondition: ...
    def risk_reward_policy(self) -> RiskRewardPolicy: ...
```

| Method | Why it exists | Owner | Receives | Returns | Deliberately excludes | Deterministic/replayable |
|---|---|---|---|---|---|---|
| `required_timeframes` | Tells orchestration what candle data to fetch | Strategy | — | `Sequence[Timeframe]` | Periods, lookback depth (§9) | Yes — pure declaration |
| `evidence_requirements` | Which named evidence categories are mandatory (missing-evidence-forces-no-setup rule) | Strategy | — | `list[EvidenceCategory]` | — | Yes |
| `detect_setup` | The interpretation step itself | Strategy | `market_state`, `candles`, `as_of` | `Setup \| None` | Any raw provider/network access (still true — `candles` are already-fetched, already-normalized domain objects) | Yes, if implemented without wall-clock reads (a requirement on future implementations, testable the same way `domain/technical`'s own source-scan tests already work) |
| `invalidation_rule` | Strategy declares intent, not the actual number | Strategy | `setup` | `InvalidationCondition` | Actual price computation (Risk Engine's job) | Yes |
| `risk_reward_policy` | Strategy declares policy, not the actual number | Strategy | — | `RiskRewardPolicy` | Actual R:R computation (Risk Engine's job) | Yes |

Two independent strategies can implement this Protocol with zero shared state, zero coordination requirement, and no risk of one influencing the other's inputs — each receives its own `candles` mapping scoped to its own declared timeframes.

---

## 12. MTF Ownership

Unchanged from `STRATEGY_BOUNDARY_RESOLUTION.md` §8, restated for this revised interface: `mtf_context` remains absent from the generic Protocol. A strategy wanting MTF reasoning calls `assemble_technical_snapshot`/`compute_ema_alignment`/`compute_vwap_position` for each of its declared timeframes (all now trivially available via `candles`) and combines them *inside its own `detect_setup` body* — no generic component ever assigns a per-timeframe direction or combines directions into `FULL_ALIGNMENT`/`PARTIAL_ALIGNMENT`/`CONFLICT`/`REGIME_UNCLEAR`. The narrowly-scoped "mechanical combinator" idea (§10 of the prior document) remains unendorsed, flagged only as a possible future utility, `[D]`.

---

## 13. EvidenceCategory

Unchanged, `[D]` — left open. No new evidence found this session bearing on this question.

---

## 14. Setup Contract

Unchanged from `STRATEGY_BOUNDARY_RESOLUTION.md` §10 — not re-implemented, not re-litigated here; that document's field-by-field classification and `structural_basis` recommendation stand.

---

## 15. InvalidationCondition

Unchanged boundary from the prior document (§11 there): strategy declares intent/rule, Risk Engine computes the actual level and independently validates risk. Exact field shape remains `[D]`.

---

## 16. RiskRewardPolicy

Unchanged (§12 of the prior document): minimum contract directly supported by text is `minimum_acceptable_rr` + `target_derivation_method`; everything else `[D]`.

---

## 17. as_of Semantics

- `detect_setup` receives `as_of` explicitly `[C]` (§11).
- Every entry in `candles: Mapping[Timeframe, Sequence[Candle]]` must be bounded to the same `as_of` — **already structurally guaranteed** if orchestration sources each series via `bounded_series()`/`HistoricalProvider`, exactly as today; no new enforcement mechanism needed.
- `MarketState` must share the same `as_of` — `[C]`, restated.
- Staleness: inherited unchanged from `DataQualityGate`.
- Replay: since `detect_setup` only ever receives already-fetched `Candle`/`MarketState` objects (never touches a provider itself), replay reduces to supplying the same objects via `HistoricalProvider` instead of a live provider — the exact "same code path, different provider" principle this project has enforced since the Technical Engine milestone, now extended one layer up without modification.
- No wall clock, no future information, no implicit "now" — restated as a hard requirement for any future concrete implementation.

---

## 18. Replay Identity

Per §5's reasoning, this is **simpler than the prior session's proposal**: `(strategy_name, strategy_version, market_state, candles, as_of)` fully determines `detect_setup`'s output, because periods/timeframes are either declared (`required_timeframes`, capturable directly) or baked into the versioned strategy code itself (periods — no separate runtime record needed). `strategy_name` + `strategy_version` **must** be treated as load-bearing replay identity `[B]` — a version bump is required whenever any internal parameter (including periods) changes, a discipline requirement for whoever eventually implements strategy versioning, not enforced by any code today since none exists yet.

---

## 19. Options / Regime / News Extensibility

Unchanged from the prior document — no placeholder `Context` object created. When these domains produce real facts, `StrategyDefinition`'s interface would need a **new, separately-scoped extension** (most plausibly: additional `required_*` declaration methods mirroring `required_timeframes`'s pattern, e.g. a future `required_option_chains()`, each independently fetchable/optional) — not solved in advance here.

---

## 20. Dependency Graph (final)

```
data/normalization
        ↓
   domain/market → MarketState
        ↓
  Candle series (per instrument/timeframe, existing, provider-neutral, unchanged)
        ↓
MarketState + candles[required timeframes] + as_of
        ↓
StrategyDefinition.detect_setup()
        │
        └─→ calls domain/technical's existing pure functions directly
             (assemble_technical_snapshot / calculate_* / compute_*)
        ↓
      Setup | None
        ↓
  Risk Engine (independent recomputation, never trusts strategy's declared policy alone)
        ↓
  Trade Gate (deterministic AND-gate)
```

**Verified against the task's own checklist:**
- `domain/technical` does not depend on strategy — confirmed; the dependency runs the other way (strategy imports/calls `domain/technical`), which is the normal, expected, one-way layered direction, not a violation of ARCHITECTURE.md §6's rule (that rule constrains what `domain/*` may import, not what may import `domain/*`).
- `MarketState` does not depend on strategy — confirmed, unchanged.
- Strategy does not depend on provider implementation — confirmed; `candles`/`market_state` are already-normalized domain objects by the time a strategy sees them.
- Risk does not redefine technical facts — confirmed, unchanged boundary (§15).
- Trade gate does not reinterpret strategy facts — confirmed, unchanged.
- Orchestration coordinates rather than interprets — **arguably strengthened** by this revision: orchestration's job shrinks to "fetch candles for declared timeframes, assemble `MarketState`, call `detect_setup`" — it no longer needs to correctly interpret and fulfill a strategy's period declarations (a form of computation-triggering-on-the-strategy's-behalf that the prior design implied), which is a cleaner "coordinate, don't interpret" separation.

---

## 21. Accuracy / Correctness Review — the Task's Ten Questions

| # | Question | Answer |
|---|---|---|
| 1 | Can it leak future information? | No — `candles` are as_of-bounded via the same proven mechanism as today. |
| 2 | Different results for the same historical input? | Not structurally, if `detect_setup` avoids wall-clock reads (a future implementation requirement). |
| 3 | Can two strategies accidentally influence each other? | No — each receives its own `candles` mapping; no shared mutable state in this design. |
| 4 | Can technical facts be accidentally interpreted globally? | No — nothing outside a given strategy's own `detect_setup` body ever calls `domain/technical` for interpretation. |
| 5 | Can strategy configuration change historical meaning without versioning? | Only if `strategy_version` isn't bumped — a discipline requirement (§18), not yet enforced by code. |
| 6 | Can a missing fact be silently treated as neutral? | No — `INSUFFICIENT_HISTORY`/`INSUFFICIENT_DATA` propagate unmodified through anything a strategy computes, exactly as already tested. |
| 7 | Can different timeframes be accidentally mixed? | **Already defended by existing, tested code** — `bounded_series()` raises `InvalidCandleSeriesError` if any candle's `instrument_id`/`timeframe` doesn't match the requested parameters (verified this session, `series.py` lines 68–73). |
| 8 | Can different period configurations be accidentally mixed? | Does not apply in the dangerous sense — no shared external collection exists to mix up (§5/§7). |
| 9 | Can provider-specific behavior leak into strategy logic? | No — `Candle` objects are already fully normalized, provider-neutral. |
| 10 | Can the architecture support strict replay? | Yes — §17/§18. |

No "yes" requiring further mitigation was found.

---

## 22. Rejected Design Log

| Alternative | Status | Reasoning |
|---|---|---|
| `MarketState` containing `TechnicalSnapshot` | Rejected | Contradicts ARCHITECTURE.md §22's sequencing (assembled before `domain/technical` runs) |
| Generic directional-vote engine | Rejected | `DIRECTIONAL_VOTE_FINAL_GATE.md`, unchanged |
| Generic MTF directional engine | Rejected | Same, §12 |
| `Mapping[Timeframe, TechnicalSnapshot]` | Rejected (prior session) | One-snapshot-per-timeframe limit, redundant keying |
| **`Sequence[TechnicalRequirement]` + pre-assembled `Sequence[TechnicalSnapshot]`** | **Rejected this session** | Two-phase declare/fulfill drift risk (§5) — this session's central reversal |
| Generic bundled "Context" object (options/regime/news fields now) | Rejected | No producer exists; placeholder abstraction |
| Hard-coded technical periods anywhere in `domain/technical` | Rejected | Standing "no baked-in defaults" rule, unchanged |
| Strategy-specific logic inside `domain/technical` | Rejected | Fact-only boundary, unchanged, verified this session |
| `MarketState` embedding a full historical OHLCV series | Refined, not flatly rejected | Now: "current bar only" is the recommended reading (§10), a full series remains rejected as duplicative |
| Premature options/regime/news placeholders | Rejected | §19 |
| `required_lookback()` as a mandatory declaration | Deferred, not rejected | §9 — a defensible alternative to the recommended generous-default approach, owner's choice |

---

## 23. Unresolved Owner Decisions

1. Approve/amend this session's revised `StrategyDefinition` interface (§11) — supersedes, does not merely add to, the prior session's proposal.
2. Lookback-depth policy: generous default (recommended) vs. explicit `required_lookback()` declaration (§9).
3. `MarketState`'s exact field list, including the refined "current bar only" OHLCV reading (§10).
4. Everything still open from `STRATEGY_BOUNDARY_RESOLUTION.md` §19 (`EvidenceCategory` vocabulary, `InvalidationCondition`/`RiskRewardPolicy` field shapes, `structural_basis`'s future, the MTF-combinator question) — unaffected by this session's changes, still open.

---

## 24. Implementation Prerequisites

1. Owner resolves §23, at minimum #1 and #2.
2. `MarketState` gets a concrete, approved field list.
3. The revised `StrategyDefinition` Protocol is implemented as an **interface only** — no concrete strategy.
4. A generous-default lookback policy (or `required_lookback()`) is decided and specified before any orchestration code fetches candles on a strategy's behalf.
5. Only then: a first concrete `StrategyDefinition`, subject to STRATEGY_FRAMEWORK.md's existing replay-review requirement.

---

## 25. Final Gate

**HOLD.**
