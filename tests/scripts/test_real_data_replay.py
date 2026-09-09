from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from scripts.dev_replay_demo import build_synthetic_candles
from scripts.real_data_replay import main, render_real_data_report
from scripts.replay_core import run_replay

INSTRUMENT_ID = "DEMO"


def _write_synthetic_csv(path: Path) -> None:
    candles = build_synthetic_candles()
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for c in candles:
            writer.writerow(
                {
                    "timestamp": c.freshness.data_timestamp.isoformat(),
                    "open": str(c.open),
                    "high": str(c.high),
                    "low": str(c.low),
                    "close": str(c.close),
                    "volume": str(c.volume),
                }
            )


def test_full_pipeline_deterministic_across_repeated_runs(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    _write_synthetic_csv(path)

    from scripts.csv_candle_loader import load_candles_from_csv

    candles = load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)
    strategy = EMAVWAPAlignmentStrategy()

    first = run_replay(candles, strategy=strategy, instrument_id=INSTRUMENT_ID)
    second = run_replay(candles, strategy=strategy, instrument_id=INSTRUMENT_ID)

    assert [p.setup for p in first.points] == [p.setup for p in second.points]


def test_full_pipeline_finds_bullish_and_bearish_via_real_csv_path(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    _write_synthetic_csv(path)

    from scripts.csv_candle_loader import load_candles_from_csv

    candles = load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)
    strategy = EMAVWAPAlignmentStrategy()

    summary = run_replay(candles, strategy=strategy, instrument_id=INSTRUMENT_ID)

    assert len(summary.bullish) >= 1
    assert len(summary.bearish) >= 1


def test_future_candles_in_csv_do_not_alter_earlier_as_of_result(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    _write_synthetic_csv(path)

    from scripts.csv_candle_loader import load_candles_from_csv

    candles = load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)
    strategy = EMAVWAPAlignmentStrategy()

    cutoff = 150
    full_summary = run_replay(candles, strategy=strategy, instrument_id=INSTRUMENT_ID)
    truncated_summary = run_replay(candles[: cutoff + 1], strategy=strategy, instrument_id=INSTRUMENT_ID)

    for i in range(cutoff + 1):
        assert full_summary.points[i].setup == truncated_summary.points[i].setup
        assert full_summary.points[i].insufficient_history == truncated_summary.points[i].insufficient_history


def test_render_real_data_report_never_claims_profitability(tmp_path: Path) -> None:
    from app.domain.market.models import ExchangeSegment
    from scripts.csv_candle_loader import load_candles_from_csv

    path = tmp_path / "data.csv"
    _write_synthetic_csv(path)
    candles = load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)
    strategy = EMAVWAPAlignmentStrategy()
    summary = run_replay(candles, strategy=strategy, instrument_id=INSTRUMENT_ID)

    report = render_real_data_report(summary, source=str(path), exchange_segment=ExchangeSegment.NSE_EQ)

    assert "A SETUP IS NOT A TRADE" in report
    # The report's own disclaimer legitimately contains words like "profitability"
    # while explicitly saying none is computed -- forbidden terms must not appear
    # anywhere OUTSIDE that one disclaimer line (i.e. no actual claim/number).
    body_without_disclaimer = "\n".join(
        line for line in report.splitlines() if "IS COMPUTED" not in line and "NOT A TRADE" not in line
    ).lower()
    forbidden = ("win rate", "profit", "p&l", "sharpe", "expectancy", "drawdown")
    for term in forbidden:
        assert term not in body_without_disclaimer


def test_main_reports_usage_error_on_wrong_arg_count(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])
    assert exit_code == 2
    assert "usage" in capsys.readouterr().out


def test_main_reports_error_on_unknown_exchange_segment(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "data.csv"
    _write_synthetic_csv(path)

    exit_code = main([str(path), INSTRUMENT_ID, "NOT_A_REAL_SEGMENT"])

    assert exit_code == 2
    assert "unknown exchange_segment" in capsys.readouterr().out


def test_main_reports_error_on_missing_csv(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["does_not_exist.csv", INSTRUMENT_ID, "NSE_EQ"])

    assert exit_code == 1
    assert "not found" in capsys.readouterr().out


def test_main_succeeds_and_prints_report_for_a_valid_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "data.csv"
    _write_synthetic_csv(path)

    exit_code = main([str(path), INSTRUMENT_ID, "NSE_EQ"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "REAL DATA REPLAY" in out
    assert "BULLISH SETUPS" in out
