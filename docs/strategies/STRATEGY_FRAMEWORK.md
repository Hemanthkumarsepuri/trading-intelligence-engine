# Strategy Framework

Status: **Setup-detection subset implemented 2026-08-27 (Strategy Foundation Implementation milestone).** `MarketState` (`app/domain/market/market_state.py`), `Setup`/`StrategyDefinition` (`app/domain/strategy/contracts.py`), and the first concrete strategy, `ema_vwap_alignment` v0.1.0 (`app/domain/strategy/ema_vwap_alignment.py`, documented in [`EMA_VWAP_ALIGNMENT_STRATEGY.md`](EMA_VWAP_ALIGNMENT_STRATEGY.md)), all exist as code. `evidence_requirements()`/`invalidation_rule()`/`risk_reward_policy()` remain unimplemented — see "Unresolved Blockers" below, updated to reflect what's now resolved vs. still open. Does **not** define any *other* specific strategy's parameters — per the original review, additional strategies' parameters are defined only once evidenced by Phase 2 (replay) and Phase 5 (signal engine) (Architecture Addendum A7).

Cross-references: [Architecture](../architecture/ARCHITECTURE.md) §11–§13, Addendum A7; [Risk & Behavior](../risk/RISK_AND_BEHAVIOR.md).

---

## Supersession Note (2026-08-27)

This document's original `detect_setup(market_state, mtf_context: MTFAlignment)` signature is **superseded by the interface below**, the end point of three adversarial architecture reviews that this document was never updated to reflect: [`STRATEGY_BOUNDARY_RESOLUTION.md`](../architecture/STRATEGY_BOUNDARY_RESOLUTION.md) (rejected a pre-assembled `Mapping[Timeframe, TechnicalSnapshot]` input), [`STRATEGY_INPUT_CONTRACT_FINAL.md`](../architecture/STRATEGY_INPUT_CONTRACT_FINAL.md) (rejected a two-phase `TechnicalRequirement` declare/fulfill design over silent declare/use drift risk), and [`STRATEGY_INPUT_CONTRACT_DECISION.md`](../architecture/STRATEGY_INPUT_CONTRACT_DECISION.md) §D (the final, still-standing decision). The `mtf_context: MTFAlignment` parameter is removed outright — [`DIRECTIONAL_VOTE_FINAL_GATE.md`](../architecture/DIRECTIONAL_VOTE_FINAL_GATE.md) established that no generic, pre-strategy MTF interpretation may exist; a strategy wanting multi-timeframe reasoning computes and combines it itself, internally, from the raw candles below.

This note updates the *interface signature* only — the substance of §19's blocking items (below) is unchanged and this note does not resolve them.

## Strategy Interface

A "strategy" in this system is not a black box that outputs a signal — it is a named, versioned combination of:

```python
class StrategyDefinition(Protocol):
    name: str
    version: str

    def required_timeframes(self) -> Sequence[TimeframeRequirement]: ...
    def detect_setup(
        self, *, market_state: MarketState, candles: Mapping[Timeframe, Sequence[Candle]], as_of: datetime
    ) -> Setup | None: ...
    def evidence_requirements(self) -> list[EvidenceCategory]: ...
    def invalidation_rule(self, setup: Setup) -> InvalidationCondition: ...
    def risk_reward_policy(self) -> RiskRewardPolicy: ...
```

`required_timeframes()` declares which timeframes (and, via `TimeframeRequirement.minimum_candles`, how much history — `app/domain/market/timeframe_requirement.py`, already implemented) the strategy needs; orchestration fetches and hands back exactly those as an `as_of`-bounded `candles` mapping (`app/domain/market/candle_inputs.py`, already implemented). `detect_setup` is pure — it receives already-fetched, already-normalized raw candles and `MarketState`, and computes whatever `domain/technical` facts it needs *itself*, inline, calling `assemble_technical_snapshot()`/`calculate_*`/`compute_*` directly with its own chosen periods. It never fetches data itself, never receives a pre-interpreted directional read, and never calls the LLM.

**Implemented as code** (`app/domain/strategy/contracts.py`) — but only this `name`/`version`/`required_timeframes`/`detect_setup` subset. `evidence_requirements()`, `invalidation_rule()`, and `risk_reward_policy()` are deliberately **not** part of the implemented Protocol yet; see "Unresolved Blockers."

## Setup Definition

A `Setup` is a typed object, not a signal string:

**Implemented as code** (`app/domain/strategy/contracts.py`), with one field removed from the original draft below it:

```python
class Setup(BaseModel):
    strategy_name: str
    strategy_version: str
    direction: Literal["BULLISH", "BEARISH"]
    origin_timestamp: datetime          # when the setup condition first became true
    trigger_level: Decimal
    structural_basis: str                # e.g. "15m higher-low above VWAP, confirmed by 5m breakout"
```

`mtf_alignment: MTFAlignment` (present in the original draft) is **dropped, not merely deferred**: `DIRECTIONAL_VOTE_FINAL_GATE.md` §15/§19 (item O-7) found that `MTFAlignment` as a generic, pre-strategy type was never resolved and, per that document's own conclusion, may not belong as a required field at all if a given strategy never computes one. This is the smallest documented change to `Setup` warranted by that finding — the field is removed, not replaced with a placeholder type. A strategy wanting MTF reasoning computes and combines it entirely internally and may describe it in `structural_basis`.

## Evidence Requirements — NOT IMPLEMENTED

Intended design, unchanged from the original draft: every `StrategyDefinition` would declare which evidence categories (Architecture §13) it depends on, and which are mandatory vs. merely contributing, so "missing data forces low confidence" (Architecture §13) is enforceable per-strategy rather than only globally. Not implemented — `EvidenceCategory`'s vocabulary is undefined; see "Unresolved Blockers."

## Invalidation — NOT IMPLEMENTED

Intended design, unchanged: every `Setup` would carry an `InvalidationCondition` derived from market structure (Risk & Behavior doc, `invalidation_level()`), never an arbitrary percentage. Not implemented — `InvalidationCondition`'s field shape is undefined; see "Unresolved Blockers."

## Risk/Reward — NOT IMPLEMENTED

Intended design, unchanged: every `StrategyDefinition` would declare a `RiskRewardPolicy` (minimum acceptable R:R, target-derivation method); the risk engine would still independently recompute and validate R:R from the actual `Setup`. Not implemented — `RiskRewardPolicy`'s full field shape is undefined; see "Unresolved Blockers."

## Replay Requirements

A `StrategyDefinition` is not eligible to run in real-time mode until it has been exercised through `replay/` against a representative historical window and its `detect_setup` output reviewed for look-ahead leakage (Architecture Addendum A5) and for producing a sane rate of setups (neither near-zero nor implausibly frequent). This gate is a process requirement on whoever adds a new strategy, not (yet) an automated CI check — automating it is a reasonable Phase 5+ addition once there's more than one strategy to compare.

## Paper-Trading Requirements

No `StrategyDefinition`'s output ever reaches a real-money conversation without first accumulating a statistically meaningful run in `paper_trading/` (Architecture §20, Addendum A8 on language/claims). "Statistically meaningful" is deliberately not pre-defined with a fixed trade count here — it depends on the strategy's expected frequency and will be set with real paper-trading data in front of us, not guessed now.

---

## Resolved and Unresolved (updated 2026-08-28, Strategy Foundation Implementation milestone)

**Resolved, now implemented as code:**

| Type | Resolution |
|---|---|
| `MarketState` | Implemented (`app/domain/market/market_state.py`) as a **narrower** container than ARCHITECTURE.md §5's full definition — `instrument_id`, `as_of`, and `quote: Quote` (reusing the existing, already-typed `Quote` model for LTP/previous-close/volume/OI/average-price). OHLCV-series, VWAP, and breadth are excluded, not guessed — duplication/no-producer reasoning recorded in that module's own docstring and in [`MARKETSTATE_PROPOSAL.md`](../architecture/MARKETSTATE_PROPOSAL.md) (left as historical record of the original full-scope analysis). |
| `MTFAlignment` | Resolved by **removal** — `Setup.mtf_alignment` is dropped from the implemented type (see "Setup Definition" above), per `DIRECTIONAL_VOTE_FINAL_GATE.md`'s own conclusion that it may not belong as a required field at all. |

**Still unresolved — no placeholder types substituted for any of these:**

| Type | Blocks | Status |
|---|---|---|
| `EvidenceCategory` | `evidence_requirements()` | No vocabulary defined anywhere — see [`STRATEGY_INPUT_CONTRACT_DECISION.md`](../architecture/STRATEGY_INPUT_CONTRACT_DECISION.md) §K |
| `InvalidationCondition` | `invalidation_rule()` | Boundary is clear (strategy declares intent, risk engine computes the number); exact field shape is not — same §K |
| `RiskRewardPolicy` | `risk_reward_policy()` | Only two fields textually named (`minimum_acceptable_rr`, `target_derivation_method`); full shape and `target_derivation_method`'s type are not — same §K |

The implemented `StrategyDefinition` Protocol (`app/domain/strategy/contracts.py`) covers only `name`/`version`/`required_timeframes`/`detect_setup` — the setup-detection subset that is fully resolved. Extending it with the three methods above is a future, separately-scoped decision once an owner supplies their types.

---

## What this document intentionally does not contain

Concrete entry rules, indicator thresholds, R-multiple targets, or any claim about expected win rate — those belong to specific `StrategyDefinition` implementations built and evidenced in Phase 3 and Phase 5, not to this framework document.
