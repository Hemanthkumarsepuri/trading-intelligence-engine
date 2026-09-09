# Strategy: `ema_vwap_alignment` v0.1.0

Status: **Implemented, NOT replay-validated.** Per `STRATEGY_FRAMEWORK.md`'s "Replay Requirements" section, this strategy is not eligible for real-time use until exercised against a representative historical window and reviewed for a sane setup rate — that review has not happened yet; this document records what was built, not a performance claim.

Code: `app/domain/strategy/ema_vwap_alignment.py`.

## What it uses

Two existing, already-implemented, fact-only `domain/technical` outputs, on a single timeframe (`Timeframe.M15`):

- `compute_ema_alignment()` over periods `(9, 21, 50)` — ARCHITECTURE.md §9's own textually-specified Trend periods.
- `compute_vwap_position()` — no threshold, a pure comparison.

No RSI/MACD/ATR/relative-volume/structure input, and no multi-timeframe combination — single timeframe only, chosen specifically to avoid needing any MTF-combination logic in this first strategy.

## Its rule (this strategy's own interpretation — `[C]`, not a general trading claim)

```
BULLISH setup  <=>  EMA(9,21,50) alignment == ASCENDING  AND  price ABOVE 15m VWAP
BEARISH setup  <=>  EMA(9,21,50) alignment == DESCENDING AND  price BELOW 15m VWAP
no setup       <=>  either fact is INSUFFICIENT_HISTORY, MIXED/AT, or the two disagree
```

`ema_alignment.py` is explicit that `ASCENDING`/`DESCENDING` describe a numeric EMA-value sequence only, not "price is rising." This strategy's mapping of that sequence to `BULLISH`/`BEARISH` is made here for the first time — a design choice, not a restatement of an existing fact.

## Required timeframes / lookback

`required_timeframes() -> [TimeframeRequirement(timeframe=M15, minimum_candles=50)]` — 50 is exactly EMA(50)'s own minimum viable series length (`ema_series()` needs `len(values) >= period`), not an arbitrary margin.

## Setup fields

`trigger_level` = latest bounded candle's close. `origin_timestamp` = latest bounded candle's timestamp — this strategy reports "the condition holds as of this instant," not "the first instant it became true" (which would need an unspecified look-back-scan rule). `structural_basis` is a descriptive string naming the timeframe, periods, and both fact states.

## Replay / no-look-ahead guarantees

Same mechanism as every other module in this codebase — `detect_setup()` never reads the clock, receives only already-`as_of`-bounded `candles`, and delegates all indicator math to `bounded_series()`-backed functions. Proven by:
- `tests/safety/test_ema_vwap_alignment_no_lookahead.py` — future-candle invariance, wall-clock invariance, forbidden-call/vocabulary source scans.
- `tests/integration/strategy/test_ema_vwap_alignment_replay_equivalence.py` — `InMemoryCandleRepository → HistoricalProvider → normalization → strategy` produces identical `Setup`s to direct bounded computation, for both the BULLISH and BEARISH cases.

## Deliberately deferred

`evidence_requirements()`, `invalidation_rule()`, `risk_reward_policy()` are not implemented on this strategy — `StrategyDefinition`'s Protocol in `app/domain/strategy/contracts.py` does not declare them yet (see that module's docstring). No `EvidenceCategory`/`InvalidationCondition`/`RiskRewardPolicy` types exist. Risk sizing, invalidation-level computation, and trade-gating are all future, separate layers — this strategy produces only a `Setup`.
