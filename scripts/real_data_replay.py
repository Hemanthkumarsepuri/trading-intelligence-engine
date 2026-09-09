"""Real-data replay pipeline for `EMAVWAPAlignmentStrategy`.

STATUS: infrastructure. Runs the *unmodified* `ema_vwap_alignment` strategy
against whatever real (or otherwise legitimate) historical CSV is supplied
via `scripts/csv_candle_loader.py` — see `docs/data-sources/CSV_IMPORT.md`
for the exact schema and where to place the file. Produces factual setup
counts only. **A `Setup` is not a trade** — nothing here computes or claims
profitability, win rate, P&L, or any other outcome metric; no entry/exit/
risk model exists yet.

Run: `python -m scripts.real_data_replay <csv_path> <instrument_id> <exchange_segment>`
e.g. `python -m scripts.real_data_replay data/historical_replay/RELIANCE_M15.csv RELIANCE NSE_EQ`
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.domain.market.models import ExchangeSegment, Timeframe
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from scripts.csv_candle_loader import CsvCandleFileError, load_candles_from_csv
from scripts.replay_core import ReplayPoint, ReplaySummary, run_replay


def _percentage(count: int, total: int) -> str:
    return f"{(100.0 * count / total):.1f}%" if total else "0.0%"


def _sample_timestamps(points: list[ReplayPoint], *, limit: int = 5) -> list[str]:
    return [p.as_of.isoformat() for p in points[:limit]]


def render_real_data_report(summary: ReplaySummary, *, source: str, exchange_segment: ExchangeSegment) -> str:
    total = len(summary.points)
    first_candle = summary.points[0].as_of.isoformat() if summary.points else "n/a"
    last_candle = summary.points[-1].as_of.isoformat() if summary.points else "n/a"

    lines = [
        "=" * 60,
        "EMA/VWAP ALIGNMENT - REAL DATA REPLAY",
        "=" * 60,
        "",
        f"Instrument: {summary.instrument_id}",
        f"Exchange: {exchange_segment.value}",
        f"Data source: {source}",
        f"Timeframe: {summary.timeframe.value}",
        f"Strategy: {summary.strategy_name}",
        f"Version: {summary.strategy_version}",
        "",
        f"Historical start: {first_candle}",
        f"Historical end: {last_candle}",
        f"Candles: {summary.candle_count}",
        f"Evaluation points: {total}",
        "",
        f"INSUFFICIENT HISTORY: {len(summary.insufficient)} ({_percentage(len(summary.insufficient), total)})",
        f"BULLISH SETUPS: {len(summary.bullish)} ({_percentage(len(summary.bullish), total)})",
        f"BEARISH SETUPS: {len(summary.bearish)} ({_percentage(len(summary.bearish), total)})",
        f"NO SETUP: {len(summary.no_setup)} ({_percentage(len(summary.no_setup), total)})",
        "",
        f"First BULLISH timestamps: {_sample_timestamps(summary.bullish)}",
        f"First BEARISH timestamps: {_sample_timestamps(summary.bearish)}",
        f"Representative NO_SETUP timestamps: {_sample_timestamps(summary.no_setup)}",
        "",
        "Future-candle invariance: enforced structurally (visible = candles[:i+1] in replay_core.run_replay)",
        "  and covered by tests/scripts/test_real_data_replay.py.",
        "Deterministic replay: yes -- no randomness, no wall-clock read, same input -> same output.",
        "Malformed-data issues: none -- csv_candle_loader validates the file before replay can start;",
        "  a malformed file stops here with an error, it is never silently repaired.",
        "",
        "=" * 60,
        "A SETUP IS NOT A TRADE. NO PROFITABILITY / WIN-RATE / P&L IS COMPUTED.",
        "=" * 60,
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 3:
        print("usage: python -m scripts.real_data_replay <csv_path> <instrument_id> <exchange_segment>")
        return 2

    csv_path, instrument_id, exchange_segment_raw = args
    try:
        exchange_segment = ExchangeSegment(exchange_segment_raw)
    except ValueError:
        valid = ", ".join(e.value for e in ExchangeSegment)
        print(f"error: unknown exchange_segment {exchange_segment_raw!r} (expected one of: {valid})")
        return 2

    try:
        candles = load_candles_from_csv(Path(csv_path), instrument_id=instrument_id, timeframe=Timeframe.M15)
    except CsvCandleFileError as exc:
        print(f"error: {exc}")
        return 1

    strategy = EMAVWAPAlignmentStrategy()
    summary = run_replay(candles, strategy=strategy, instrument_id=instrument_id)
    print(render_real_data_report(summary, source=csv_path, exchange_segment=exchange_segment))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
