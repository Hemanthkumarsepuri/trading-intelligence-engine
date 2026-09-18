from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.market.freshness import DataFreshness
from app.domain.market.market_state import MarketState, assemble_market_state
from app.domain.market.models import Candle, Quote, Timeframe
from app.domain.market.timeframe_requirement import TimeframeRequirement
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from tests.unit.strategy.fixtures import (
    BOUNCE_IN_FALLING_STRUCTURE_CLOSES,
    BOUNDARY_MINIMUM_CLOSES,
    DIP_IN_RISING_STRUCTURE_CLOSES,
    FALLING_CLOSES,
    INSUFFICIENT_CLOSES,
    RISING_CLOSES,
)
from tests.unit.technical.factories import INSTRUMENT_ID, make_series_from_closes

T0 = datetime(2026, 8, 27, 9, 15, tzinfo=UTC)


def _market_state(as_of: datetime) -> MarketState:
    quote = Quote(
        provider="mock",
        freshness=DataFreshness(data_timestamp=as_of, received_timestamp=as_of),
        instrument_id=INSTRUMENT_ID,
        last_price=Decimal("100"),
    )
    return assemble_market_state(quote, as_of=as_of)


def _candles_for(closes: list[float]) -> tuple[list[Candle], datetime]:
    series = make_series_from_closes(closes, start=T0, step=timedelta(minutes=15), timeframe=Timeframe.M15)
    as_of = series[-1].freshness.data_timestamp
    return series, as_of


def test_required_timeframes_declares_exactly_one_m15_requirement() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    assert strategy.required_timeframes() == [TimeframeRequirement(timeframe=Timeframe.M15, minimum_candles=50)]


def test_bullish_setup_detected() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series, as_of = _candles_for(RISING_CLOSES)
    market_state = _market_state(as_of)

    setup = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: series}, as_of=as_of)

    assert setup is not None
    assert setup.direction == "BULLISH"
    assert setup.strategy_name == "ema_vwap_alignment"
    assert setup.strategy_version == "0.1.0"
    assert setup.origin_timestamp == as_of
    assert setup.trigger_level == series[-1].close


def test_bearish_setup_detected() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series, as_of = _candles_for(FALLING_CLOSES)
    market_state = _market_state(as_of)

    setup = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: series}, as_of=as_of)

    assert setup is not None
    assert setup.direction == "BEARISH"
    assert setup.trigger_level == series[-1].close


@pytest.mark.parametrize("closes", [BOUNCE_IN_FALLING_STRUCTURE_CLOSES, DIP_IN_RISING_STRUCTURE_CLOSES])
def test_conflicting_facts_produce_no_setup(closes: list[float]) -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series, as_of = _candles_for(closes)
    market_state = _market_state(as_of)

    setup = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: series}, as_of=as_of)

    assert setup is None


def test_insufficient_history_produces_no_setup() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series, as_of = _candles_for(INSUFFICIENT_CLOSES)
    assert len(series) == 49
    market_state = _market_state(as_of)

    setup = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: series}, as_of=as_of)

    assert setup is None


def test_exactly_minimum_candles_boundary_is_evaluated_not_treated_as_insufficient() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series, as_of = _candles_for(BOUNDARY_MINIMUM_CLOSES)
    assert len(series) == 50
    market_state = _market_state(as_of)

    # Must not raise InvalidCandleSeriesError / InsufficientHistory-as-error;
    # a None or a Setup are both acceptable outcomes here, the point is that
    # exactly 50 candles is evaluated (not silently rejected as too few).
    strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: series}, as_of=as_of)


def test_missing_declared_timeframe_key_is_treated_as_no_candles_not_an_error() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    as_of = T0
    market_state = _market_state(as_of)

    setup = strategy.detect_setup(market_state=market_state, candles={}, as_of=as_of)

    assert setup is None


def test_market_state_as_of_mismatch_raises() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series, as_of = _candles_for(RISING_CLOSES)
    mismatched_market_state = _market_state(as_of - timedelta(minutes=15))

    with pytest.raises(ValueError, match="as_of"):
        strategy.detect_setup(market_state=mismatched_market_state, candles={Timeframe.M15: series}, as_of=as_of)


def test_deterministic_repeated_execution() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series, as_of = _candles_for(RISING_CLOSES)
    market_state = _market_state(as_of)

    first = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: series}, as_of=as_of)
    second = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: series}, as_of=as_of)

    assert first == second
