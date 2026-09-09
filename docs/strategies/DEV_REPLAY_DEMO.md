# Development Replay Demo — `scripts/dev_replay_demo.py`

Status: **DEVELOPMENT / SYNTHETIC REPLAY — NOT REAL MARKET PERFORMANCE.** This is not the historical-replay validation `STRATEGY_FRAMEWORK.md`'s "Replay Requirements" section requires (that still needs real NSE data — see `docs/architecture/DEPENDENCY_DIRECTION_FINDING.md`'s sibling milestone reports). This script exists only to prove the existing `ema_vwap_alignment` strategy runs correctly, deterministically, and without look-ahead end-to-end against an `as_of`-bounded candle feed shaped like the real one — nothing about its output should ever be read as evidence of trading performance.

## What it does

Generates one deterministic, formula-based synthetic M15 candle series (no randomness — same output every run), then evaluates the **unmodified** `EMAVWAPAlignmentStrategy` (`app/domain/strategy/ema_vwap_alignment.py`, imported directly, not reimplemented) at every candle's own timestamp as `as_of`, exposing only candles at or before that instant each time — the same shape every no-look-ahead test elsewhere in this repository already proves safe.

## Run it

```
python -m scripts.dev_replay_demo
```
(from the repo root, with the project's `.venv` active).

## Synthetic data construction

`_synthetic_closes()` builds a "slow drift, then a sharp single-candle reversal" price path in named segments — chosen because a smooth trend alone does not move `EMAAlignmentState`/`VWAPPositionState` through the ASCENDING+ABOVE / DESCENDING+BELOW combinations this strategy requires. The exact magnitudes were found empirically (running the construction against the real strategy and observing actual output), not hand-derived — `compute_vwap_position()`'s VWAP is unbounded/session-unaware, so it's influenced by the *entire* preceding series once candles accumulate, meaning a magnitude that works in an isolated segment doesn't necessarily still work once earlier segments have piled up history behind it.

## Actual output (captured this run)

```
Candles: 320
Evaluation points: 320

BULLISH SETUPS: 1
BEARISH SETUPS: 71
NO SETUP: 199
INSUFFICIENT HISTORY: 49

First bullish setup: 2026-01-06T15:00:00+00:00
First bearish setup: 2026-01-06T00:00:00+00:00
```
`49` matches the strategy's own `minimum_candles=50` requirement exactly (candles 1–49 are insufficient, candle 50 onward is evaluated). All four counts partition the full 320 evaluation points, proven by `test_replay_summary_totals_partition_all_points`.

## Validation

`tests/scripts/test_dev_replay_demo.py` (7 tests) proves: deterministic output across repeated runs, future-candle non-influence on an earlier `as_of` (truncating the series to an earlier cutoff reproduces every prior point's result exactly), at least one BULLISH and one BEARISH setup actually occur, at least one NO_SETUP point occurs, `INSUFFICIENT_HISTORY` holds for exactly the first 49 points and never pairs with a non-`None` setup, and the four categories partition every evaluation point.

## Limitations — read before citing this anywhere

- **Synthetic data, not real market data.** Proves the code path works; proves nothing about real NSE behavior.
- **No entry/exit/risk model exists.** `BULLISH`/`BEARISH` are `Setup`s, not trades — there is no P&L, win rate, or profitability claim here or anywhere yet.
- **Does not satisfy `STRATEGY_FRAMEWORK.md`'s Replay Requirements.** That gate needs a real historical window; this demo is a development aid, not that validation.

## Next step

**Done — see `scripts/real_data_replay.py` and `docs/data-sources/CSV_IMPORT.md`.** The no-look-ahead replay loop this demo used was extracted to `scripts/replay_core.py` and is now shared by `scripts/real_data_replay.py`, which loads a real CSV through the existing normalization/validation path and runs the identical, unmodified strategy. Once a real dataset lands at `data/historical_replay/<INSTRUMENT_ID>_M15.csv`, running it is a one-command action — no further code changes needed.
