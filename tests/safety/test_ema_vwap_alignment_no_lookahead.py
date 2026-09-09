"""Anti-look-ahead and source-hygiene safety tests for the first concrete
strategy, `EMAVWAPAlignmentStrategy`, mirroring the established pattern in
`test_technical_no_lookahead.py` / `test_strategy_input_no_lookahead.py`.
"""

from __future__ import annotations

import inspect
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.freshness import DataFreshness
from app.domain.market.market_state import MarketState, assemble_market_state
from app.domain.market.models import Quote, Timeframe
from app.domain.strategy import ema_vwap_alignment as ema_vwap_alignment_module
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from tests.unit.strategy.fixtures import BULLISH_CLOSES
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


def test_future_candles_do_not_alter_setup() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    through_t = make_series_from_closes(BULLISH_CLOSES, start=T0, step=timedelta(minutes=15), timeframe=Timeframe.M15)
    as_of = through_t[-1].freshness.data_timestamp
    future = make_series_from_closes(
        [1, 999, 1, 999], start=as_of + timedelta(minutes=15), step=timedelta(minutes=15), timeframe=Timeframe.M15
    )
    market_state = _market_state(as_of)

    before = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: through_t}, as_of=as_of)
    after = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: through_t + future}, as_of=as_of)

    assert before == after
    assert before is not None  # confirm this is a meaningful (non-trivial) comparison


def test_results_identical_across_real_wall_clock_time() -> None:
    strategy = EMAVWAPAlignmentStrategy()
    series = make_series_from_closes(BULLISH_CLOSES, start=T0, step=timedelta(minutes=15), timeframe=Timeframe.M15)
    as_of = series[-1].freshness.data_timestamp
    market_state = _market_state(as_of)

    first = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: series}, as_of=as_of)
    time.sleep(0.05)
    second = strategy.detect_setup(market_state=market_state, candles={Timeframe.M15: series}, as_of=as_of)

    assert first == second


def test_no_forbidden_clock_or_execution_calls_in_strategy_module() -> None:
    forbidden = (
        "datetime.now(",
        ".utcnow(",
        "time.time(",
        "utc_now(",
        "place_order",
        "modify_order",
        "cancel_order",
        "recovery",
        "revenge",
        "target_recovery",
    )
    source = inspect.getsource(ema_vwap_alignment_module)
    for pattern in forbidden:
        assert pattern not in source, f"strategy module contains forbidden pattern: {pattern!r}"


def test_no_generic_directional_vote_vocabulary_in_strategy_module() -> None:
    forbidden = ("MTFAlignment", "FULL_ALIGNMENT", "PARTIAL_ALIGNMENT", "REGIME_UNCLEAR")
    source = inspect.getsource(ema_vwap_alignment_module)
    for pattern in forbidden:
        assert pattern not in source, f"strategy module contains forbidden pattern: {pattern!r}"
