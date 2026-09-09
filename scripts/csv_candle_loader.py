"""CSV historical-candle loader — the "drop a real dataset in and go" entry
point for `scripts/real_data_replay.py`. Schema: `docs/data-sources/CSV_IMPORT.md`.

Reads a CSV (`timestamp,open,high,low,close,volume[,open_interest]`) and
returns fully normalized `Candle` objects via the EXISTING `DefaultNormalizer`
(`app/data/normalization/base.py`) — this module does not reimplement OHLC
sanity, timezone-awareness, or ordering/duplicate checks; those are enforced
by `Candle`'s own pydantic validators, `DataFreshness`'s own validators, and
`bounded_series()` respectively (`bounded_series()` is called directly here,
reused rather than duplicated). This module's only job is reading the file
and parsing each row's own fields with clear row-level error context — it
never repairs, sorts, or deduplicates malformed input.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from app.data.normalization.base import DefaultNormalizer
from app.data.providers.base import RawCandle
from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import bounded_series

REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")


class CsvCandleFileError(ValueError):
    """Raised for any problem with the CSV file itself — missing columns,
    an unparseable field, or a row that fails `Candle`'s own OHLC-sanity
    validator. Never raised for a problem this module silently repaired —
    nothing here repairs anything; every failure is reported and stops the
    load immediately.
    """


def _parse_raw_candle(row: dict[str, str], *, line_number: int) -> RawCandle:
    raw_timestamp = row.get("timestamp", "")
    try:
        timestamp = datetime.fromisoformat(raw_timestamp)
    except ValueError as exc:
        raise CsvCandleFileError(f"line {line_number}: unparseable timestamp {raw_timestamp!r}") from exc
    if timestamp.tzinfo is None:
        raise CsvCandleFileError(
            f"line {line_number}: timestamp {raw_timestamp!r} has no UTC offset — naive timestamps "
            "are rejected (see docs/data-sources/CSV_IMPORT.md)"
        )

    try:
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        volume = int(row["volume"])
        oi_raw = (row.get("open_interest") or "").strip()
        open_interest = int(oi_raw) if oi_raw else None
    except (KeyError, ValueError) as exc:
        raise CsvCandleFileError(f"line {line_number}: could not parse OHLCV fields: {exc}") from exc

    return RawCandle(
        timestamp=timestamp, open=open_, high=high, low=low, close=close, volume=volume, open_interest=open_interest
    )


def load_candles_from_csv(
    path: Path,
    *,
    instrument_id: str,
    timeframe: Timeframe = Timeframe.M15,
    provider_name: str = "csv-import",
) -> list[Candle]:
    """Reads, parses, normalizes, and structurally validates a real (or any
    other legitimate) historical OHLCV CSV.

    Raises `CsvCandleFileError` for a file/row-level problem (missing
    column, unparseable field, a row that fails `Candle`'s OHLC-sanity
    validator), or `InvalidCandleSeriesError` (propagated unmodified from
    `bounded_series()`) for a series-level problem — out-of-order
    timestamps, duplicate timestamps. Never repairs, sorts, or deduplicates.
    """
    if not path.exists():
        raise CsvCandleFileError(f"CSV file not found: {path}")

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise CsvCandleFileError(f"{path}: file has no header row")
        missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise CsvCandleFileError(f"{path}: missing required column(s): {missing}")

        rows = [(i + 2, row) for i, row in enumerate(reader)]  # +2: header row + 1-indexed data rows

    if not rows:
        raise CsvCandleFileError(f"{path}: no data rows found")

    normalizer = DefaultNormalizer(provider_name=provider_name)
    candles: list[Candle] = []
    for line_number, row in rows:
        raw_candle = _parse_raw_candle(row, line_number=line_number)
        try:
            candle = normalizer.normalize_candle(
                raw_candle, instrument_id=instrument_id, timeframe=timeframe, received_at=raw_candle.timestamp
            )
        except ValidationError as exc:
            raise CsvCandleFileError(f"line {line_number}: candle failed validation: {exc}") from exc
        candles.append(candle)

    # Reuse the EXISTING ordering/duplicate/instrument-timeframe contract
    # instead of reimplementing it: bounding to the last candle's own
    # timestamp includes every row while still exercising every structural
    # check bounded_series() already enforces (ascending order, no
    # duplicates, instrument_id/timeframe consistency).
    bounded_series(
        candles, instrument_id=instrument_id, timeframe=timeframe, as_of=candles[-1].freshness.data_timestamp
    )

    return candles
