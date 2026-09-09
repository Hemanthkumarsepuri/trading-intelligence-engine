# Real Historical Data — CSV Import Contract

Status: **Infrastructure ready. No real dataset has been supplied yet.** This formalizes the data-acquisition contract from prior milestones as a discoverable doc instead of chat-only prose, and is what `scripts/real_data_replay.py` (`load_candles_from_csv`) validates against.

## Where to put the file

```
data/historical_replay/<INSTRUMENT_ID>_M15.csv
```
(new folder at the repo root, outside `app/` — not source code.)

## Required columns (header row, exact names)

```
timestamp,open,high,low,close,volume
```
`open_interest` optional (equities won't have it).

## Field rules

- **timestamp**: ISO-8601 **with an explicit UTC offset** — e.g. `2025-01-02T09:15:00+05:30` (IST) or `...Z` (UTC). A naive timestamp (no offset) is rejected outright.
- **open/high/low/close**: parseable numbers, and must satisfy `low <= open <= high` and `low <= close <= high` (enforced by `Candle`'s own existing validator — not re-implemented by the loader).
- **volume**: non-negative integer.
- **ordering**: strictly ascending by timestamp, one row per M15 bar.
- **duplicates**: none — a repeated timestamp is rejected.

Ordering and duplicate checks are enforced by reusing the existing `bounded_series()` contract directly (the same mechanism every other module in this codebase relies on) — the loader does not reimplement that logic, it only adds file/row-number context to the error when something fails.

## Minimum history

At least 50 M15 candles before the first meaningful evaluation point (`ema_vwap_alignment`'s own `minimum_candles`). A multi-week file satisfies this many times over — prefer weeks/months, not the bare minimum.

## Run it once the file exists

```
python -m scripts.real_data_replay data/historical_replay/<INSTRUMENT_ID>_M15.csv <INSTRUMENT_ID> NSE_EQ
```

This loads the CSV through the **existing** normalization path (`DefaultNormalizer`), validates it via the **existing** `Candle`/`bounded_series()` contracts, and replays the **unmodified** `ema_vwap_alignment` strategy across the full window using the same no-look-ahead mechanism (`scripts/replay_core.run_replay`) the synthetic development demo already proves safe — see `docs/strategies/DEV_REPLAY_DEMO.md`.

Output is factual counts and sample timestamps only — never profitability, win rate, or P&L. A `Setup` is not a trade.
