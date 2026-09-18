"""The M15 trend row must describe the tape it was computed from.

This exists because it did not. Until 18 Sep 2026 every caller of
`compute_ema_alignment()` read its sequence label as a direction and read
it backwards: `ASCENDING` (EMA9 < EMA21 < EMA50 -- the FASTER averages
BELOW the slower ones, which is what a FALLING series produces) was
mapped to BULLISH evidence, a BULLISH strategy setup and a
`TRENDING_BULLISH` regime. The module's own docstring had warned against
exactly that reading; three call sites did it anyway, so no test caught
it -- every test asserted the enum, and the enum was self-consistent.

These tests therefore assert against REAL PRICE MOVEMENT rather than
against the enum: they take the repository's own real RELIANCE M15
series, find the windows where price genuinely rose and genuinely fell
the most, and require the evidence row to say so. A future inversion
cannot satisfy them by renaming a label.

Nothing here claims the row PREDICTS anything. The assertion is only
that a row describing the last 50 real bars agrees with what those 50
real bars did -- the minimum bar for calling it observable evidence
(master directive Section 4, "Direction must be observable").
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.market.models import Candle, Timeframe
from app.domain.options.evidence_matrix import EvidenceDirection, row_m15_trend
from app.domain.technical.ema_alignment import EMAAlignmentState, compute_ema_alignment
from app.domain.technical.series import IndicatorStatus
from scripts.csv_candle_loader import load_candles_from_csv

_KEY = "NSE_EQ|INE002A01018"
_CSV = Path(__file__).resolve().parents[2] / "data" / "historical_replay" / "RELIANCE_M15.csv"
_LOOKBACK = 50


@pytest.fixture(scope="module")
def real_candles() -> list[Candle]:
    candles = load_candles_from_csv(_CSV, instrument_id=_KEY)
    assert len(candles) > 500, "the real sample series is expected to be substantial"
    return candles


def _trailing_change_pct(candles: list[Candle], end: int) -> Decimal:
    start_close = candles[end - _LOOKBACK].close
    return (candles[end].close - start_close) / start_close * Decimal(100)


def _row_direction_at(candles: list[Candle], end: int) -> EvidenceDirection:
    window = candles[: end + 1]
    alignment = compute_ema_alignment(
        window, instrument_id=_KEY, timeframe=Timeframe.M15, periods=(9, 21, 50),
        as_of=window[-1].freshness.data_timestamp,
    )
    assert alignment.status == IndicatorStatus.OK
    return row_m15_trend(ema_alignment=alignment.state, ema_status=alignment.status).direction


def test_the_strongest_real_advance_is_not_called_bearish(real_candles: list[Candle]) -> None:
    end = max(range(_LOOKBACK, len(real_candles)), key=lambda i: _trailing_change_pct(real_candles, i))
    change = _trailing_change_pct(real_candles, end)
    assert change > 0, "expected the best window in the real series to be a genuine advance"
    assert _row_direction_at(real_candles, end) == EvidenceDirection.BULLISH, (
        f"price rose {change:.2f}% over the trailing {_LOOKBACK} real bars, "
        "so the M15 trend row must not read BEARISH"
    )


def test_the_deepest_real_decline_is_not_called_bullish(real_candles: list[Candle]) -> None:
    end = min(range(_LOOKBACK, len(real_candles)), key=lambda i: _trailing_change_pct(real_candles, i))
    change = _trailing_change_pct(real_candles, end)
    assert change < 0, "expected the worst window in the real series to be a genuine decline"
    assert _row_direction_at(real_candles, end) == EvidenceDirection.BEARISH, (
        f"price fell {change:.2f}% over the trailing {_LOOKBACK} real bars, "
        "so the M15 trend row must not read BULLISH"
    )


def test_the_row_agrees_with_the_real_tape_across_the_whole_sample(real_candles: list[Candle]) -> None:
    """The aggregate version of the two cases above, over every 13th bar
    of the real series. A directional row that disagrees with the
    trailing move more often than it agrees is not evidence, whatever it
    is labelled. The bar is deliberately loose (a 2:1 majority) -- this
    pins the SIGN of the relationship, which is what was wrong, not a
    performance claim about the indicator."""
    agree = disagree = 0
    for end in range(_LOOKBACK + 10, len(real_candles), 13):
        direction = _row_direction_at(real_candles, end)
        if direction not in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH):
            continue
        change = _trailing_change_pct(real_candles, end)
        rose = change > 0
        if (direction == EvidenceDirection.BULLISH) == rose:
            agree += 1
        else:
            disagree += 1
    assert agree + disagree > 50, "expected a meaningful number of directional rows in the real sample"
    assert agree > 2 * disagree, (
        f"the M15 trend row agreed with the real trailing move {agree} times and "
        f"disagreed {disagree} times -- the directional mapping is inverted"
    )


def test_the_sequence_labels_still_mean_what_the_module_says(real_candles: list[Candle]) -> None:
    """Guards the other half: the fix must stay a mapping correction, not
    a change to what `compute_ema_alignment()` itself reports. A falling
    series must still produce the `ASCENDING` value sequence."""
    falling = [c for c in real_candles[:200]]
    end = min(range(_LOOKBACK, len(falling)), key=lambda i: _trailing_change_pct(falling, i))
    window = falling[: end + 1]
    alignment = compute_ema_alignment(
        window, instrument_id=_KEY, timeframe=Timeframe.M15, periods=(9, 21, 50),
        as_of=window[-1].freshness.data_timestamp,
    )
    assert alignment.state == EMAAlignmentState.ASCENDING
    assert alignment.values is not None
    assert alignment.values[0] < alignment.values[1] < alignment.values[2]
