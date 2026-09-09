from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.domain.market.models import Timeframe
from app.domain.technical.series import InvalidCandleSeriesError
from scripts.csv_candle_loader import CsvCandleFileError, load_candles_from_csv
from scripts.dev_replay_demo import build_synthetic_candles

INSTRUMENT_ID = "TESTCO"


def _write_csv(path: Path, rows: list[dict[str, str]], *, columns: list[str] | None = None) -> None:
    fieldnames = columns or ["timestamp", "open", "high", "low", "close", "volume"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _valid_rows(n: int = 3) -> list[dict[str, str]]:
    return [
        {
            "timestamp": f"2025-01-02T09:{15 + 15 * i:02d}:00+05:30",
            "open": str(100.0 + i),
            "high": str(101.0 + i),
            "low": str(99.0 + i),
            "close": str(100.5 + i),
            "volume": "1000",
        }
        for i in range(n)
    ]


def test_valid_csv_loads_correctly(tmp_path: Path) -> None:
    path = tmp_path / "valid.csv"
    _write_csv(path, _valid_rows(3))

    candles = load_candles_from_csv(path, instrument_id=INSTRUMENT_ID, timeframe=Timeframe.M15)

    assert len(candles) == 3
    assert all(c.instrument_id == INSTRUMENT_ID for c in candles)
    assert all(c.timeframe == Timeframe.M15 for c in candles)
    assert candles[0].close == candles[0].close  # sanity: constructed without error
    assert candles[0].freshness.data_timestamp < candles[1].freshness.data_timestamp < candles[2].freshness.data_timestamp


def test_missing_file_raises_csv_candle_file_error(tmp_path: Path) -> None:
    with pytest.raises(CsvCandleFileError, match="not found"):
        load_candles_from_csv(tmp_path / "does_not_exist.csv", instrument_id=INSTRUMENT_ID)


def test_missing_required_column_raises(tmp_path: Path) -> None:
    path = tmp_path / "missing_col.csv"
    _write_csv(path, [{"timestamp": "2025-01-02T09:15:00+05:30", "open": "100"}], columns=["timestamp", "open"])

    with pytest.raises(CsvCandleFileError, match="missing required column"):
        load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    _write_csv(path, [])

    with pytest.raises(CsvCandleFileError, match="no data rows"):
        load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)


def test_naive_timestamp_rejected(tmp_path: Path) -> None:
    path = tmp_path / "naive.csv"
    rows = _valid_rows(1)
    rows[0]["timestamp"] = "2025-01-02T09:15:00"  # no UTC offset
    _write_csv(path, rows)

    with pytest.raises(CsvCandleFileError, match="UTC offset"):
        load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)


def test_unparseable_numeric_field_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_number.csv"
    rows = _valid_rows(1)
    rows[0]["close"] = "not-a-number"
    _write_csv(path, rows)

    with pytest.raises(CsvCandleFileError, match="could not parse"):
        load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)


def test_ohlc_sanity_violation_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_ohlc.csv"
    rows = _valid_rows(1)
    rows[0]["low"] = "999"  # low > high -> Candle's own validator must reject this
    _write_csv(path, rows)

    with pytest.raises(CsvCandleFileError, match="failed validation"):
        load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)


def test_out_of_order_timestamps_rejected(tmp_path: Path) -> None:
    path = tmp_path / "out_of_order.csv"
    rows = _valid_rows(3)
    rows[0], rows[1] = rows[1], rows[0]
    _write_csv(path, rows)

    with pytest.raises(InvalidCandleSeriesError):
        load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)


def test_duplicate_timestamps_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    rows = _valid_rows(2)
    rows[1]["timestamp"] = rows[0]["timestamp"]
    _write_csv(path, rows)

    with pytest.raises(InvalidCandleSeriesError):
        load_candles_from_csv(path, instrument_id=INSTRUMENT_ID)


def test_round_trip_matches_directly_built_candles(tmp_path: Path) -> None:
    """Serializes the same engineered synthetic candles the dev demo uses to
    a real CSV file on disk, reloads them through `load_candles_from_csv`,
    and confirms every OHLCV/timestamp field survives the CSV round-trip
    exactly — proving the loader + existing normalization path reproduces
    the original data faithfully, not just "some" data.
    """
    original = build_synthetic_candles()
    path = tmp_path / "roundtrip.csv"
    rows = [
        {
            "timestamp": c.freshness.data_timestamp.isoformat(),
            "open": str(c.open),
            "high": str(c.high),
            "low": str(c.low),
            "close": str(c.close),
            "volume": str(c.volume),
        }
        for c in original
    ]
    _write_csv(path, rows)

    reloaded = load_candles_from_csv(path, instrument_id="DEMO", timeframe=Timeframe.M15)

    assert len(reloaded) == len(original)
    for orig, loaded in zip(original, reloaded, strict=True):
        assert loaded.freshness.data_timestamp == orig.freshness.data_timestamp
        assert loaded.open == orig.open
        assert loaded.high == orig.high
        assert loaded.low == orig.low
        assert loaded.close == orig.close
        assert loaded.volume == orig.volume
