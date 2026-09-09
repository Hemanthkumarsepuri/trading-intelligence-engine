"""Tests for the development/synthetic replay demo
(`scripts/dev_replay_demo.py`). This is NOT historical market validation —
see the module's own docstring — these tests only prove the demo's replay
mechanism itself is correct: deterministic, no-look-ahead, and that its
engineered synthetic data actually exercises all three strategy outcomes.
"""

from __future__ import annotations

from app.domain.market.models import Timeframe
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from scripts.dev_replay_demo import build_synthetic_candles, run_replay


def test_deterministic_output_across_repeated_runs() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    candles = build_synthetic_candles()

    first = run_replay(candles, strategy=strategy)
    second = run_replay(candles, strategy=strategy)

    assert [p.setup for p in first.points] == [p.setup for p in second.points]
    assert [p.insufficient_history for p in first.points] == [p.insufficient_history for p in second.points]


def test_future_candles_do_not_alter_an_earlier_as_of_result() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    candles = build_synthetic_candles()

    # Evaluate strategy directly at an earlier as_of using only candles up to
    # that point, then again with the full (later) series truncated the same
    # way inside run_replay's own loop — the two must agree at every shared
    # index, proving later candles never leak backward into an earlier point.
    cutoff = 150
    full_summary = run_replay(candles, strategy=strategy)
    truncated_summary = run_replay(candles[: cutoff + 1], strategy=strategy)

    for i in range(cutoff + 1):
        assert full_summary.points[i].setup == truncated_summary.points[i].setup
        assert full_summary.points[i].insufficient_history == truncated_summary.points[i].insufficient_history


def test_synthetic_data_produces_at_least_one_bullish_setup() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    candles = build_synthetic_candles()

    summary = run_replay(candles, strategy=strategy)

    assert len(summary.bullish) >= 1
    assert all(p.setup is not None and p.setup.direction == "BULLISH" for p in summary.bullish)


def test_synthetic_data_produces_at_least_one_bearish_setup() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    candles = build_synthetic_candles()

    summary = run_replay(candles, strategy=strategy)

    assert len(summary.bearish) >= 1
    assert all(p.setup is not None and p.setup.direction == "BEARISH" for p in summary.bearish)


def test_synthetic_data_produces_neutral_no_setup_points() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    candles = build_synthetic_candles()

    summary = run_replay(candles, strategy=strategy)

    assert len(summary.no_setup) >= 1


def test_insufficient_history_before_fifty_candles() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    candles = build_synthetic_candles()
    requirement = strategy.required_timeframes()[0]
    assert requirement.timeframe == Timeframe.M15
    assert requirement.minimum_candles == 50

    summary = run_replay(candles, strategy=strategy)

    for i, point in enumerate(summary.points):
        assert point.insufficient_history == (i + 1 < 50)
        if point.insufficient_history:
            assert point.setup is None


def test_replay_summary_totals_partition_all_points() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    candles = build_synthetic_candles()

    summary = run_replay(candles, strategy=strategy)

    total = len(summary.bullish) + len(summary.bearish) + len(summary.no_setup) + len(summary.insufficient)
    assert total == len(summary.points) == len(candles)
