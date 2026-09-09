from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.freshness import DataFreshness
from app.domain.market.live_candle_aggregator import PartialCandle
from app.domain.market.market_state import MarketState, assemble_market_state
from app.domain.market.models import Quote, Timeframe
from app.domain.strategy.current_analysis import assemble_current_analysis
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.domain.technical.series import IndicatorStatus
from tests.unit.strategy.fixtures import BULLISH_CLOSES, INSUFFICIENT_CLOSES
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


def test_bullish_case_produces_full_auditable_result() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series = make_series_from_closes(BULLISH_CLOSES, start=T0, step=timedelta(minutes=15), timeframe=Timeframe.M15)
    as_of = series[-1].freshness.data_timestamp
    market_state = _market_state(as_of)

    result = assemble_current_analysis(
        market_state=market_state,
        completed_candles={Timeframe.M15: series},
        current_partial_candle=None,
        strategy=strategy,
        as_of=as_of,
    )

    assert result.instrument_id == INSTRUMENT_ID
    assert result.timeframe == Timeframe.M15
    assert result.as_of == as_of
    assert result.strategy_name == "ema_vwap_alignment"
    assert result.strategy_version == "0.1.0"
    assert result.ema_status == IndicatorStatus.OK
    assert result.vwap_status == IndicatorStatus.OK
    assert result.ema9 is not None and result.ema21 is not None and result.ema50 is not None
    assert result.setup is not None
    assert result.setup.direction == "BULLISH"
    assert result.price == series[-1].close
    assert result.last_completed_candle_timestamp == as_of
    assert result.data_freshness_seconds == 0.0


def test_insufficient_history_reports_status_without_fabricating_values() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series = make_series_from_closes(INSUFFICIENT_CLOSES, start=T0, step=timedelta(minutes=15), timeframe=Timeframe.M15)
    as_of = series[-1].freshness.data_timestamp
    market_state = _market_state(as_of)

    result = assemble_current_analysis(
        market_state=market_state,
        completed_candles={Timeframe.M15: series},
        current_partial_candle=None,
        strategy=strategy,
        as_of=as_of,
    )

    assert result.ema_status == IndicatorStatus.INSUFFICIENT_HISTORY
    assert result.ema9 is None and result.ema21 is None and result.ema50 is None
    assert result.setup is None


def test_current_partial_candle_is_surfaced_but_never_fed_to_the_strategy() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series = make_series_from_closes(BULLISH_CLOSES, start=T0, step=timedelta(minutes=15), timeframe=Timeframe.M15)
    as_of = series[-1].freshness.data_timestamp
    market_state = _market_state(as_of)

    partial = PartialCandle(
        instrument_id=INSTRUMENT_ID,
        timeframe=Timeframe.M15,
        period_start=as_of + timedelta(minutes=15),
        period_end=as_of + timedelta(minutes=30),
        open=Decimal("999"),
        high=Decimal("999"),
        low=Decimal("999"),
        close=Decimal("999"),
        volume=1,
        last_tick_timestamp=as_of + timedelta(minutes=15, seconds=1),
    )

    without_partial = assemble_current_analysis(
        market_state=market_state,
        completed_candles={Timeframe.M15: series},
        current_partial_candle=None,
        strategy=strategy,
        as_of=as_of,
    )
    with_partial = assemble_current_analysis(
        market_state=market_state,
        completed_candles={Timeframe.M15: series},
        current_partial_candle=partial,
        strategy=strategy,
        as_of=as_of,
    )

    # The partial candle's own fields are surfaced for display...
    assert with_partial.current_partial_candle_close == Decimal("999")
    assert with_partial.current_partial_candle_period_start == partial.period_start
    # ...but never influences the strategy's own computed facts/setup.
    assert with_partial.setup == without_partial.setup
    assert with_partial.ema9 == without_partial.ema9
    assert with_partial.vwap_value == without_partial.vwap_value
    assert without_partial.current_partial_candle_close is None


def test_deterministic_across_repeated_calls() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series = make_series_from_closes(BULLISH_CLOSES, start=T0, step=timedelta(minutes=15), timeframe=Timeframe.M15)
    as_of = series[-1].freshness.data_timestamp
    market_state = _market_state(as_of)

    first = assemble_current_analysis(
        market_state=market_state,
        completed_candles={Timeframe.M15: series},
        current_partial_candle=None,
        strategy=strategy,
        as_of=as_of,
    )
    second = assemble_current_analysis(
        market_state=market_state,
        completed_candles={Timeframe.M15: series},
        current_partial_candle=None,
        strategy=strategy,
        as_of=as_of,
    )

    assert first == second
