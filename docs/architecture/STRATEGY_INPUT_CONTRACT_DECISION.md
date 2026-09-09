# Strategy Input Contract — Decision

Status: **HOLD, with a precisely-scoped exception.** No production code accompanies this document. Labels: `[A]` explicit repository specification, `[B]` strong architectural inference, `[C]` design recommendation, `[D]` owner decision required.

---

## A. Executive Decision

**HOLD** — for the full `StrategyDefinition` Protocol as previously proposed, because three of its five methods (`evidence_requirements`, `invalidation_rule`, `risk_reward_policy`) reference types (`EvidenceCategory`, `InvalidationCondition`, `RiskRewardPolicy`) that remain genuinely undefined after six rounds of review — not from lack of effort, but because no document anywhere supplies their field shapes, and inventing them here would be exactly the "invented trading assumption" this whole gate sequence exists to prevent.

**Not a flat HOLD, however.** This session resolves the technical-input mechanics (`required_timeframes`, the candle contract, lookback depth) to a level this document is prepared to call **ready for implementation as its own, narrower slice** — `MarketState`'s scope, the candle input contract, and the timeframe/lookback declaration — **if the owner wants to approve that subset independently** of the three still-blocked methods. This is offered as an option, not forced.

Would this document's author trust the *resolved* portion of this contract as the foundation for a strategy later replayed candle-by-candle, years on? For the resolved portion: yes, and the reasoning is below. For the full Protocol: not yet, for the three named reasons.

---

## B. Current Architecture (re-verified fresh this session)

```
pytest -q                  -> 154 passed, 0 failed, 0 skipped
ruff check .                 -> All checks passed!
mypy --strict app tests      -> Success: no issues found in 82 source files
```
Production files: 47, unchanged. `domain/technical/` confirmed fact-only by direct read of every "BULLISH/BEARISH/direction/signal/confidence" match in `app/` and `tests/` this session (§ below) — every hit is either prose explicitly stating the absence of interpretation, MACD's legitimate mathematical "signal line" terminology, or a test's forbidden-field assertion. Zero implementation matches.

**New finding this session, not previously flagged:** `grep -rn "model_config\|frozen"` across `app/domain/market/models.py` and every `app/domain/technical/*.py` file returns **zero matches** — no domain model (including `Candle`) declares `frozen=True`. Nothing today prevents in-place mutation of a `Candle` instance handed to a strategy. This is addressed in the threat model (§L, item 11) and the candle contract (§H).

---

## C. Evidence Classification

Applied throughout, per label definitions above. Prior documents' `[A]`-grounded citations (ARCHITECTURE.md §5/§6/§10/§21/§22, STRATEGY_FRAMEWORK.md, RISK_AND_BEHAVIOR.md) are not re-quoted in full here — see `STRATEGY_BOUNDARY_RESOLUTION.md` §4 and `STRATEGY_INPUT_CONTRACT_FINAL.md` §3 for the original citations, unchanged and still authoritative.

---

## D. Final Strategy Input Contract

```python
class TimeframeRequirement(BaseModel):
    """[C] — new this session, narrower than the previously-rejected
    TechnicalRequirement. Declares a timeframe and the MINIMUM candle count
    needed — never indicator periods (see §F for why this distinction is
    safe where a periods-declaration was not)."""
    timeframe: Timeframe
    minimum_candles: int


class StrategyDefinition(Protocol):
    name: str                                                    # [A]
    version: str                                                  # [A]

    def required_timeframes(self) -> Sequence[TimeframeRequirement]: ...  # [C]

    def evidence_requirements(self) -> list[EvidenceCategory]: ...        # [A] shape, [D] type — BLOCKING

    def detect_setup(
        self,
        *,
        market_state: MarketState,                                # [C] scope, [D] exact fields
        candles: Mapping[Timeframe, Sequence[Candle]],             # [C]
        as_of: datetime,
    ) -> Setup | None: ...                                         # [A] shape (Setup itself)

    def invalidation_rule(self, setup: Setup) -> InvalidationCondition: ...  # [A] shape, [D] type — BLOCKING
    def risk_reward_policy(self) -> RiskRewardPolicy: ...                    # [A] shape, [D] type — BLOCKING
```

**Repository-defined:** the overall five-method shape, `Setup`'s own fields (STRATEGY_FRAMEWORK.md).
**Recommended this session:** `TimeframeRequirement`, the `candles`/`market_state`/`as_of` parameter shape for `detect_setup`.
**Owner decision required, blocking:** `EvidenceCategory`, `InvalidationCondition`, `RiskRewardPolicy` field definitions.

---

## E. `required_timeframes()` Decision

- **Purpose:** tells orchestration what candle data to fetch before `detect_setup` can run.
- **Authority:** `[C]` **authoritative, not mere metadata** — orchestration must use it as the exclusive source of what to fetch; it is not descriptive-only.
- **Validation / undeclared-timeframe access:** `[C]` **structurally impossible, not runtime-checked.** If orchestration constructs `candles` containing *only* the keys a given strategy declared, then `candles[undeclared_timeframe]` raises Python's own `KeyError` immediately — no new enforcement code is needed; this falls out of the data structure itself.
- **Missing-timeframe behavior** (candles genuinely don't exist for a declared timeframe): `[C]` the key **remains present with an empty `Sequence[Candle]`**, never an absent key — "requested but zero available" is a different, distinguishable state from "never requested." An empty sequence flows into `assemble_technical_snapshot`/`calculate_*` and produces `INSUFFICIENT_HISTORY` via the already-proven, already-tested mechanism — no new failure path.
- **Extra-timeframe behavior:** not applicable under this design — orchestration only ever fetches what was declared, so there is no "extra" to handle.
- **Runtime behavior:** `[C]` **must be static and parameterless** — no market data, no `as_of`, nothing runtime-dependent may influence it, extending STRATEGY_FRAMEWORK.md's existing purity requirement on `detect_setup` to this method too. It cannot meaningfully "change during execution" because it has no execution-dependent state to change.
- **Versioning behavior:** `[B]` automatically captured — if a new strategy version changes its required timeframes, that's a property of the versioned code itself, consistent with `strategy_version` already being load-bearing for replay identity (§M).
- **Cross-strategy safety:** `[C]` **required implementation constraint, not yet built:** orchestration must hand each strategy its own `candles` mapping, scoped to only that strategy's own declared timeframes — never a shared, full-union mapping across all running strategies — otherwise one strategy could structurally "see" another's declared data (the exact cross-contamination risk named in this task's Step 4, Q12).
- **Same timeframe, different strategies:** `[C]` explicitly fine — two strategies may interpret the "same" 5m candle series with completely different internal indicator periods; no shared consensus is required or enforced, which is strategy isolation working as intended.

---

## F. Lookback Decision

**Resolved this session, replacing the prior document's open Option (i)/(ii) split with a specific recommendation.**

- **Option A (declare timeframe + lookback count):** `[C]` **recommended.**
- **Option B (strategy owns lookback fully internally, i.e. receives "all available history, unbounded below"):** considered and **rejected** — see the new threat identified below.
- **Option C (provider/orchestration decides lookback):** rejected, same reasoning as the prior document — requires either an invented system-wide default or collapses into Option A with extra indirection.

**Why Option A is safe here where the earlier periods-declaration (`TechnicalRequirement`) was rejected — the critical distinction:** the prior rejection was about *periods* drifting silently between declaration and use, because using the wrong period *still produces a number* — a silent wrong answer. A *lookback count* drifting (under-declaring) does **not** have this property: if a strategy declares fewer candles than its internal periods actually need, the shortfall is caught by the exact same, already-tested `INSUFFICIENT_HISTORY` mechanism every indicator function already implements — a loud, safe non-answer, never a silently wrong one. This asymmetry is the reasoning, not a re-approval of the earlier design under a new name.

**New threat identified this session, motivating rejection of "unbounded, all available history" (Option B):** if orchestration fetches *everything* available up to `as_of` with no fixed floor, two replay runs of the identical `(strategy, as_of)` pair are only guaranteed identical if the *entire* historical record available in the persistence layer is *also* identical at both run times. If older, pre-`as_of` history is ever backfilled or corrected between a live run and a later replay (a realistic operational scenario, not a contrived one), "all available" silently differs between runs even though nothing *after* `as_of` changed — a genuine, previously-unflagged reproducibility risk. **A fixed, strategy-declared minimum count is more reproducible than "everything," not less** — it only requires that *at least* the declared count existed at both times, a much weaker and more realistic assumption.

The exact `minimum_candles` value for any real, future strategy is not invented here — it is a mechanical consequence of whatever periods that strategy's own code uses internally, decided by whoever writes that strategy, not by this document or by any system-wide default.

---

## G. MarketState Specification

Unchanged in substance from `STRATEGY_INPUT_CONTRACT_FINAL.md` §10, restated with evidence-only fields:

| Field | Status |
|---|---|
| `instrument_id` | `[C]`, consistent with every other domain type |
| `as_of` | `[A]`-grounded (§21) |
| Freshness / `staleness_seconds` | `[A]`-grounded existence (§21), `[D]` exact shape ("per data type it's built from" implies nesting never shown) |
| LTP | `[A]`-named (§5), `[D]` type |
| VWAP | `[A]`-named (§5), `[D]` — likely duplicates `vwap_position.py`'s output if computed the same way; needs owner clarification on source |
| Breadth | `[A]`-named (§5), `[D]` — no producer, no type, anywhere |
| "OHLCV" | `[A]`-named (§5), `[C]` recommended reading: **current/running bar only**, not a historical series — a historical `list[Candle]` already exists as the standard representation passed directly everywhere in this codebase; embedding a second, independently-sourced copy inside `MarketState` risks two "same" series silently diverging. This is a recommendation, not a certainty — a genuine historical-series reading remains `[D]`. |

**Must not contain** (`[A]`, restated, unchanged): `TechnicalSnapshot`, RSI/EMA/MACD interpretation, strategy signals/thresholds, MTF interpretation, options/regime/news interpretation, `BUY`/`SELL`/`CE`/`PE`, risk sizing, trade decisions, confidence.

Per-instrument vs. per-timeframe scope: `[D]`, unresolved — if reduced to current-instant facts only (per the OHLCV recommendation), it plausibly needs no timeframe dimension, but this is inference, not confirmation.

---

## H. Candle Input Contract

`candles: Mapping[Timeframe, Sequence[Candle]]` — evaluated against every risk this task names:

- **Timeframe mapping:** safe, per §E — `Mapping` here does not have the ambiguity problem `Mapping[Timeframe, TechnicalSnapshot]` had, because a raw candle series has no period dimension; there is exactly one canonical series per `(instrument, timeframe, as_of)`.
- **Ordering:** `[A]`, inherited — every existing candle-producing path (`HistoricalProvider`, `InMemoryCandleRepository.query()`) already returns ascending-by-timestamp order, verified by existing tests.
- **As_of / boundedness:** `[C]` **must be guaranteed by whatever supplies `candles`, not re-filtered inside `detect_setup`.** The existing `bounded_series()` contract remains the sole authoritative filtering mechanism — restated explicitly per this task's Step 8 instruction not to create a second implementation. A strategy receiving already-bounded candles should never need to filter them again; if it calls `assemble_technical_snapshot`/`calculate_*` itself (§ per the prior document's design), those functions apply `bounded_series()` internally anyway, so even a defensively-unfiltered input would be safely re-bounded — belt-and-suspenders, not a second competing implementation.
- **Missing data:** §E, above — empty sequence, not an absent key, not an exception.
- **Malformed data:** `[A]`, inherited — `InvalidCandleSeriesError` (out-of-order, duplicate timestamps, mismatched instrument/timeframe) already propagates from `bounded_series()`; nothing in this contract catches or converts it.
- **Mutation expectations:** `[D]` — **genuine gap, newly surfaced this session (§B).** No domain model is `frozen=True`. Nothing today prevents a strategy from mutating a `Candle` it receives, which — if the same `Candle` instances are shared across multiple strategies (a plausible, efficient orchestration choice) — could let one strategy's bug corrupt another's input. **Recommendation `[C]`:** either (a) domain models should be marked `frozen=True` (a change to `app/domain/market/models.py`, explicitly **not made in this document** — production code, out of scope here), or (b) orchestration must supply each strategy an independent copy. Flagged as an owner decision, not resolved.
- **Extra timeframe:** not applicable, per §E.
- **Provider normalization / timezone consistency:** `[A]`, inherited — `data/normalization`'s single-normalizer-per-provider design and `DataFreshness`'s naive-datetime rejection already cover this; no new mechanism proposed or needed.
- **Accidental future candles:** `[A]`, inherited — `bounded_series()`'s `as_of` filtering, already proven by the dedicated no-look-ahead test suites across every existing `domain/technical` module.

---

## I. MTF Decision

Unchanged, restated for completeness: MTF reasoning lives entirely inside `StrategyDefinition.detect_setup()`. No generic component ever assigns a per-timeframe direction or combines directions into `FULL_ALIGNMENT`/`PARTIAL_ALIGNMENT`/`CONFLICT`/`REGIME_UNCLEAR` — established because those states are defined (§10 of ARCHITECTURE.md) in terms of directional reads that are themselves unavoidably strategy interpretation (`DIRECTIONAL_VOTE_FINAL_GATE.md`). **Named, honest limitation, not previously stated this plainly:** because MTF semantics are strategy-internal, two strategies may define "alignment" incompatibly, and there is no shared vocabulary for comparing "which strategy had better MTF alignment" across strategies — an accepted consequence of strategy isolation, not a defect, but worth naming so it isn't mistaken for an oversight later (this task's Step 9, "inability to compare strategies").

---

## J. Setup Contract Review

Unchanged from `STRATEGY_BOUNDARY_RESOLUTION.md` §10 — not modified here. `structural_basis: str` remains flagged as inconsistent with the now-established typed-evidence convention (`structure_facts.py`, `ema_alignment.py`, `vwap_position.py`), with a typed-evidence-reference alternative recommended `[C]`, `Setup` itself unchanged.

---

## K. Supporting Types — Blocker Status

| Type | Owner | Creates | Consumes | Facts or interpretation | Generic or strategy-specific | Defined before first strategy? | Status |
|---|---|---|---|---|---|---|---|
| `EvidenceCategory` | Unclear | Strategy (`evidence_requirements`) | Missing-evidence rule | Neither — a vocabulary/label type | `[D]` unresolved | **Yes — BLOCKING** | No field/vocabulary defined anywhere |
| `InvalidationCondition` | Strategy declares | Strategy | Risk Engine | Intent, not a number | Strategy-specific | **Yes — BLOCKING** | No field list anywhere |
| `RiskRewardPolicy` | Strategy declares | Strategy | Risk Engine | Intent, not a number | Strategy-specific | **Yes — BLOCKING** | Only two named concepts in prose (`minimum_acceptable_rr`, `target_derivation_method`), no full shape |
| `TradeCandidate` | `engines/trade_gate.py` | Trade Gate | Paper trading / audit | Decision | Generic | No — downstream of Setup Detection entirely | Zero definition anywhere; not this milestone's blocker |
| `EvidenceItem` | `domain/signals` | Signal Engine | `SignalScore` | Evidence | Generic | No — a different, later component | Zero definition; adjacent, not blocking |

**Three of five are genuine blockers to implementing the full `StrategyDefinition` Protocol as documented.** No placeholder classes are created for any of them in this document.

---

## L. Accuracy Threat Model

| # | Threat | Failure mode | Prevention | Detection | Covered today? | Remaining gap |
|---|---|---|---|---|---|---|
| 1 | Look-ahead | Result influenced by data after `as_of` | `bounded_series()` | Dedicated safety test suites | **Yes** | None |
| 2 | Future candle leakage | Same as above, candle-specific | Same | Same | **Yes** | None |
| 3 | Timeframe mismatch | Candles for wrong timeframe used | `InvalidCandleSeriesError` on mismatch | Existing tests | **Yes** | None |
| 4 | Period mismatch | Wrong indicator period silently used | N/A under this design — periods are strategy-internal, no external declaration to mismatch | Strategy's own unit tests (future) | **By design** | Only applies if a future strategy has internal bugs — out of this contract's scope |
| 5 | Missing candle handling | Silently treated as present/zero | `INSUFFICIENT_HISTORY`, never fabricated | Existing tests throughout | **Yes** | None |
| 6 | Duplicate timestamps | Silently accepted | `InvalidCandleSeriesError` | Existing tests | **Yes** | None |
| 7 | Out-of-order candles | Silently accepted | Same | Same | **Yes** | None |
| 8 | Strategy-version drift | Historical decision unreproducible | `strategy_version` as replay identity (§M) | Not yet enforced — no code exists | **Partially** | Versioning *discipline* is a future implementation requirement, not yet enforceable by any test |
| 9 | Provider normalization differences | Two providers report the "same" fact differently | Single-normalizer-per-provider design, `DataQualityGate` A4 cross-check | A4 (not live — no second provider yet) | **Partially** | Pre-existing, logged gap (Addendum A4), not this milestone's to close |
| 10 | Different timezone assumptions | Naive/ambiguous timestamps | `DataFreshness` rejects naive datetimes | Existing tests | **Yes** | None |
| 11 | Mutable inputs | One strategy corrupts another's shared data | **None today** | **None today** | **No — new finding, §B/§H** | Domain models not `frozen=True`; flagged, not fixed here |
| 12 | Hidden technical dependencies | Strategy silently reads global/shared indicator state | No shared mutable technical state exists anywhere | Code inspection | **Yes** | None |
| 13 | Hidden timeframe dependencies | Strategy accesses undeclared timeframe | `KeyError` (§E) | Structural (Python's own dict semantics) | **Yes, by design** | Requires orchestration to actually scope per-strategy dicts (§E) — not yet built |
| 14 | Strategy cross-contamination | Shared candle-mapping leaks across strategies | Per-strategy-scoped `candles` mapping (§E) | Future orchestration tests | **By design, not yet built** | Same as #13 |
| 15 | Generic interpretation leakage | `domain/technical` starts producing BULLISH/BEARISH | Verified this session by direct read of every match | Structural fact-only tests, forbidden-field scans | **Yes** | None |
| 16 | MTF semantic inconsistency | Strategies define "alignment" incompatibly | Accepted, named limitation (§I) | N/A — not a bug | **Accepted by design** | Cross-strategy comparison of MTF quality is out of scope, by design |
| 17 | Replay inconsistency | Historical backfill changes "available" data between runs | Bounded, declared lookback (§F), not "all available" | Future replay tests | **Addressed by this session's recommendation** | Not yet implemented |
| 18 | Undocumented thresholds | A number invented without justification | None invented in this document (verified by scan) | Repo-wide grep, this session | **Yes** | None |
| 19 | Silent fallback behavior | A failure quietly becomes a "valid" result | `KeyError`/`INSUFFICIENT_HISTORY`, both loud | Existing tests | **Yes** | None |
| 20 | Data-quality masquerading as neutral/valid | A gate failure reinterpreted as "no evidence" rather than "cannot evaluate" | `DataQualityGate` blocks a cycle entirely, upstream of this contract; `INSUFFICIENT_DATA`/`INSUFFICIENT_HISTORY` stay distinct from `0`/neutral throughout | Existing tests | **Yes** | None |

**One genuinely new, unaddressed gap surfaced by this audit: #11 (mutable inputs).** Everything else is either already covered by existing, tested mechanisms, or is an accepted, named design boundary (not a defect), or is explicitly deferred to future implementation work with the discipline requirement stated.

---

## M. Replay / Reproducibility

`(strategy_name, strategy_version, market_state, candles, as_of)` — **re-examined, still sufficient**, `[B]`, with one addition from this session: since lookback is now a strategy declaration (`TimeframeRequirement.minimum_candles`, §F) rather than "whatever was available," `candles` itself is now a *reproducible* quantity (bounded above by `as_of`, bounded below by the strategy's own declared minimum) rather than a potentially-drifting "everything available" set — strengthening, not weakening, this tuple's sufficiency.

**Strategy versioning scenario, worked through explicitly (this task's Step 15):** if version 2 of a strategy changes its internal EMA periods, its `required_timeframes()`'s declared lookback, or its MTF interpretation logic, **all of these are captured automatically by `strategy_version` changing**, because none of them are externally-declared runtime configuration — they are all strategy-code-internal (§E, §F). Historical decisions remain reconstructable *as long as the exact strategy code for the version that produced them is retained* (source control, not something this document designs) — no additional persisted configuration/versioning field is required beyond `strategy_name` + `strategy_version` themselves. **What must be persisted, conceptually, not implemented here:** the strategy's own source code per version (ordinary software version control), and, per `analysis_runs`' existing schema (ARCHITECTURE.md §7), which `(strategy_name, strategy_version)` pair produced a given `Setup` at a given `as_of`.

---

## N. Rejected Alternatives

| Alternative | Session | Reasoning |
|---|---|---|
| `MarketState` containing `TechnicalSnapshot` | Prior | Contradicts §22's sequencing |
| Generic directional-vote / MTF engine | Prior | `DIRECTIONAL_VOTE_FINAL_GATE.md` |
| `Mapping[Timeframe, TechnicalSnapshot]` | Prior | One-snapshot-per-timeframe limit |
| `Sequence[TechnicalRequirement]` + pre-assembled snapshots | Prior | Silent periods-drift risk |
| **"All available history, unbounded below" for lookback** | **This session** | **New: historical-backfill-instability threat (§F, #17)** |
| Generic bundled options/regime/news "Context" object | Prior | Placeholder abstraction, no producer |
| Hard-coded technical periods anywhere generic | Prior, restated | Standing rule |
| A full historical OHLCV series inside `MarketState` | Prior, restated | Duplicates existing `list[Candle]` responsibility |
| Placeholder classes for `EvidenceCategory`/`InvalidationCondition`/`RiskRewardPolicy`/`TradeCandidate`/`EvidenceItem` | This session | Explicitly declined — these remain the actual blockers, not papered over |

---

## O. Owner Decisions

**BLOCKING** (must be resolved before the full `StrategyDefinition` Protocol can be implemented):
1. `EvidenceCategory` field/vocabulary definition.
2. `InvalidationCondition` field definition.
3. `RiskRewardPolicy` field definition.

**NON-BLOCKING** (can proceed independently, on the owner's schedule, without holding up items 1–3):
4. `MarketState`'s exact field types (scope is now clear — §G).
5. Domain-model immutability (`frozen=True` or per-strategy copying) — §H, §L #11.
6. `Setup.structural_basis`'s long-term treatment (§J) — a future revision, `Setup` unchanged today.

**FUTURE** (not needed for the next implementation step at all):
7. `TradeCandidate` — downstream of full Setup Detection.
8. `EvidenceItem` / `SignalScore` — a later, separate component.
9. A shared, narrowly-scoped MTF combinator utility (§I) — explicitly not endorsed for building now.

---

## Final Verification (this session)

```
pytest -q                  -> 154 passed, 0 failed, 0 skipped
ruff check .                 -> All checks passed!
mypy --strict app tests      -> Success: no issues found in 82 source files
```
Production files unchanged (47). No new files under `app/`. Zero `StrategyDefinition`/`MarketState`/generic-directional-vote/generic-MTF implementation. Zero `place_order`/`modify_order`/`cancel_order`/`recovery`/`revenge`/`target_recovery`. `BROKER_ORDER_EXECUTION_ENABLED=false`, confirmed in both files. No look-ahead mechanism introduced. No new thresholds. No speculative business logic. Every `BULLISH`/`BEARISH`/`direction`/`signal`/`confidence` match in the repository was read directly this session (§B) — none are implementations.

---

## Final Gate

**HOLD** for the full `StrategyDefinition` Protocol.

If the owner wishes to proceed incrementally rather than wait for items O.1–O.3: `MarketState`, `TimeframeRequirement`, and the `candles`/`as_of` contract (§D–§H) are assessed as ready for implementation as their own, independently-useful, non-blocking slice — offered as an option, not a default, and not started in this document.
