from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import (
    Candle,
    OptionChainSnapshot,
    OptionQuote,
    OptionRight,
    Quote,
    Timeframe,
)


def _freshness() -> DataFreshness:
    return DataFreshness(
        data_timestamp=datetime(2026, 8, 27, 10, 0, 0, tzinfo=UTC),
        received_timestamp=datetime(2026, 8, 27, 10, 0, 1, tzinfo=UTC),
    )


def test_candle_accepts_consistent_ohlc() -> None:
    candle = Candle(
        provider="mock",
        freshness=_freshness(),
        instrument_id="INST1",
        timeframe=Timeframe.M5,
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("102"),
        volume=1000,
    )
    assert candle.close == Decimal("102")


def test_candle_rejects_low_above_high() -> None:
    with pytest.raises(ValidationError):
        Candle(
            provider="mock",
            freshness=_freshness(),
            instrument_id="INST1",
            timeframe=Timeframe.M5,
            open=Decimal("100"),
            high=Decimal("99"),
            low=Decimal("101"),
            close=Decimal("100"),
            volume=1000,
        )


def test_candle_rejects_close_outside_low_high() -> None:
    with pytest.raises(ValidationError):
        Candle(
            provider="mock",
            freshness=_freshness(),
            instrument_id="INST1",
            timeframe=Timeframe.M5,
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("99"),
            close=Decimal("110"),
            volume=1000,
        )


def test_candle_rejects_negative_volume() -> None:
    with pytest.raises(ValidationError):
        Candle(
            provider="mock",
            freshness=_freshness(),
            instrument_id="INST1",
            timeframe=Timeframe.M5,
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("99"),
            close=Decimal("102"),
            volume=-1,
        )


def test_quote_rejects_non_positive_last_price() -> None:
    with pytest.raises(ValidationError):
        Quote(provider="mock", freshness=_freshness(), instrument_id="INST1", last_price=Decimal("0"))


def test_option_chain_snapshot_rejects_mismatched_leg() -> None:
    leg = OptionQuote(
        provider="mock",
        freshness=_freshness(),
        underlying="OTHER",
        expiry=date(2026, 9, 25),
        strike=Decimal("100"),
        right=OptionRight.CE,
    )
    with pytest.raises(ValidationError):
        OptionChainSnapshot(
            provider="mock",
            freshness=_freshness(),
            underlying="NIFTY",
            expiry=date(2026, 9, 25),
            legs=[leg],
        )


def test_option_chain_snapshot_accepts_matching_legs() -> None:
    ce = OptionQuote(
        provider="mock",
        freshness=_freshness(),
        underlying="NIFTY",
        expiry=date(2026, 9, 25),
        strike=Decimal("25000"),
        right=OptionRight.CE,
    )
    snapshot = OptionChainSnapshot(
        provider="mock",
        freshness=_freshness(),
        underlying="NIFTY",
        expiry=date(2026, 9, 25),
        legs=[ce],
    )
    assert len(snapshot.legs) == 1
