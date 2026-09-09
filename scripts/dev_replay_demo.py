"""Development/synthetic replay demo for `EMAVWAPAlignmentStrategy`.

STATUS: DEVELOPMENT / SYNTHETIC REPLAY — NOT REAL MARKET PERFORMANCE.

Generates a deterministic, formula-based synthetic M15 candle series (no
randomness, no seed needed — the same code always produces the same
candles), then replays the *unmodified* `EMAVWAPAlignmentStrategy` across
every eligible `as_of` point in that series, using exactly the same
`bounded_series()`-backed, `as_of`-parameterized path the strategy already
uses in production and in its existing tests
(`app/domain/strategy/ema_vwap_alignment.py` is imported and called
directly, not reimplemented). This proves the strategy code runs
end-to-end, deterministically, with no future-candle influence, against a
real-shaped (but synthetic) OHLCV feed. It proves NOTHING about real market
behavior, performance, or profitability, and must never be cited as such —
see `docs/strategies/DEV_REPLAY_DEMO.md`.

Run: `python scripts/dev_replay_demo.py` (from the repo root, with the
project's `.venv` active).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from scripts.replay_core import ReplayPoint, ReplaySummary
from scripts.replay_core import run_replay as _run_replay_core

__all__ = ["ReplayPoint", "ReplaySummary", "build_synthetic_candles", "main", "render_report", "run_replay"]

INSTRUMENT_ID = "DEMO"
TIMEFRAME = Timeframe.M15
# Arbitrary fixed anchor instant — not a real trading date, only exists to
# give every synthetic candle a timezone-aware timestamp.
START = datetime(2026, 1, 5, 9, 15, tzinfo=UTC)
STEP = timedelta(minutes=15)
DEFAULT_CANDLE_COUNT = 320  # exact length of the engineered `_synthetic_closes()` sequence below


def _build_candle(index: int, close: float, *, previous_close: float) -> Candle:
    open_ = previous_close
    high = max(open_, close) + 0.5
    low = min(open_, close) - 0.5
    timestamp = START + index * STEP
    return Candle(
        provider="synthetic-dev",
        freshness=DataFreshness(data_timestamp=timestamp, received_timestamp=timestamp),
        instrument_id=INSTRUMENT_ID,
        timeframe=TIMEFRAME,
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=1000,
    )


def _decline_then_spike(base: float, spike: float, *, slope: float = 3.0, seg_len: int = 59) -> list[float]:
    segment = [base - slope * i for i in range(seg_len)]
    segment.append(segment[-1] + spike)  # sharp single-candle upward move
    return segment


def _rise_then_drop(base: float, drop: float, *, slope: float = 3.0, seg_len: int = 59) -> list[float]:
    segment = [base + slope * i for i in range(seg_len)]
    segment.append(segment[-1] - drop)  # sharp single-candle drop
    return segment


def _synthetic_closes() -> list[float]:
    """Deterministic, formula-based close-price path — no randomness of any
    kind, same output every run. A smooth trend alone does not move
    `EMAAlignmentState`/`VWAPPositionState` into the combinations this
    strategy requires (ASCENDING+ABOVE / DESCENDING+BELOW); a "slow drift,
    then a sharp single-candle reversal" shape does. The exact magnitudes
    below were found by running this construction and the real strategy
    together and observing the actual output — not hand-derived — because
    once candles accumulate, `compute_vwap_position()`'s unbounded
    (session-unaware) VWAP is influenced by the *entire* preceding series,
    not just the local segment, so a magnitude that works in isolation does
    not necessarily still work once earlier segments have piled up history
    behind it. See `docs/strategies/DEV_REPLAY_DEMO.md`.
    """
    closes: list[float] = []
    closes += _rise_then_drop(1000.0, 94.0)  # -> BEARISH near the end of this segment (final close 1080)
    closes += _decline_then_spike(closes[-1], 150.0)  # -> BULLISH near the end of this segment
    closes += _rise_then_drop(closes[-1], 300.0)
    closes += _decline_then_spike(closes[-1], 150.0)  # -> a sustained BEARISH regime follows
    closes += [closes[-1] + 0.5 * i for i in range(80)]  # gentle drift tail -> mostly NO_SETUP
    return closes


def build_synthetic_candles(count: int = DEFAULT_CANDLE_COUNT) -> list[Candle]:
    closes = _synthetic_closes()
    if count != len(closes):
        raise ValueError(f"build_synthetic_candles() only supports count={len(closes)} (the engineered default)")

    candles: list[Candle] = []
    previous_close = closes[0]
    for i, close in enumerate(closes):
        candles.append(_build_candle(i, close, previous_close=previous_close))
        previous_close = close
    return candles


def run_replay(candles: list[Candle], *, strategy: EMAVWAPAlignmentStrategy) -> ReplaySummary:
    """Thin, fixed-`instrument_id` wrapper around `scripts.replay_core.run_replay`
    — kept so this module's existing call sites/tests don't need to pass
    `instrument_id` explicitly (it's always `INSTRUMENT_ID` here). The actual
    no-look-ahead replay loop lives in `replay_core`, shared with
    `scripts/real_data_replay.py`.
    """
    return _run_replay_core(candles, strategy=strategy, instrument_id=INSTRUMENT_ID)


def _direction_label(point: ReplayPoint) -> str:
    if point.insufficient_history:
        return "INSUFFICIENT_HISTORY"
    if point.setup is None:
        return "NO_SETUP"
    return point.setup.direction


def _format_table(summary: ReplaySummary, *, sample_every: int) -> str:
    lines = ["timestamp                 direction", "-" * 50]
    for i, point in enumerate(summary.points):
        if i % sample_every != 0:
            continue
        lines.append(f"{point.as_of.isoformat():<26} {_direction_label(point)}")
    return "\n".join(lines)


def render_report(summary: ReplaySummary) -> str:
    first_bull = summary.bullish[0] if summary.bullish else None
    first_bear = summary.bearish[0] if summary.bearish else None
    sample_every = max(1, len(summary.points) // 30)

    lines = [
        "=" * 50,
        "EMA/VWAP ALIGNMENT - DEVELOPMENT REPLAY",
        "=" * 50,
        "",
        f"Instrument: {summary.instrument_id}",
        f"Timeframe: {summary.timeframe.value}",
        f"Strategy: {summary.strategy_name}",
        f"Version: {summary.strategy_version}",
        "Data: SYNTHETIC / DEVELOPMENT ONLY",
        "",
        f"Candles: {summary.candle_count}",
        f"Evaluation points: {len(summary.points)}",
        "",
        f"BULLISH SETUPS: {len(summary.bullish)}",
        f"BEARISH SETUPS: {len(summary.bearish)}",
        f"NO SETUP: {len(summary.no_setup)}",
        f"INSUFFICIENT HISTORY: {len(summary.insufficient)}",
        "",
        f"First bullish setup: {first_bull.as_of.isoformat() if first_bull else 'none'}",
        f"First bearish setup: {first_bear.as_of.isoformat() if first_bear else 'none'}",
        "",
        _format_table(summary, sample_every=sample_every),
        "",
        "=" * 50,
        "END DEVELOPMENT REPLAY",
        "REAL MARKET PERFORMANCE: NOT MEASURED",
        "=" * 50,
    ]
    return "\n".join(lines)


def main() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    candles = build_synthetic_candles()
    summary = run_replay(candles, strategy=strategy)
    print(render_report(summary))


if __name__ == "__main__":
    main()
