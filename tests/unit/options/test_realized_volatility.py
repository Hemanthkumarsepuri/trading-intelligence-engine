"""Sprint 7A, Objective 7 -- pure unit tests for realized volatility vs
implied volatility. No I/O, real Decimal/datetime math only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.domain.options.realized_volatility import (
    VolatilityState,
    classify_iv_vs_realized,
    compute_realized_volatility,
)

START = datetime(2026, 8, 1, 9, 15, tzinfo=UTC)  # IST 14:45 -- irrelevant here, just a fixed real instant


def _candle(ts: datetime, *, close: float) -> Candle:
    return Candle(
        provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts), instrument_id="TEST",
        timeframe=Timeframe.M15, open=Decimal(str(close)), high=Decimal(str(close)) + 1, low=Decimal(str(close)) - 1,
        close=Decimal(str(close)), volume=1000,
    )


def _daily_candles(closes: list[float]) -> list[Candle]:
    """One real M15 candle per real day (a fixed 09:15 UTC time each
    day -- distinct calendar dates is all `_daily_closes_from_candles`
    needs)."""
    return [_candle(START + timedelta(days=i), close=c) for i, c in enumerate(closes)]


def test_insufficient_data_when_too_few_real_trading_sessions() -> None:
    candles = _daily_candles([100.0, 101.0, 102.0])  # only 3 real days
    result = compute_realized_volatility(candles, window_trading_days=5, window_label="5D")
    assert result.status == "INSUFFICIENT_DATA"
    assert result.annualized_vol_pct is None


def test_ok_with_enough_real_trading_sessions() -> None:
    closes = [100.0, 101.0, 99.5, 100.8, 102.0, 101.2]  # 6 real days -> 5 real returns
    candles = _daily_candles(closes)
    result = compute_realized_volatility(candles, window_trading_days=5, window_label="5D")
    assert result.status == "OK"
    assert result.trading_days_used == 5
    assert result.annualized_vol_pct is not None and result.annualized_vol_pct > 0


def test_multiple_intraday_candles_per_day_use_only_the_last_real_close() -> None:
    """Several real M15 candles on the SAME real day must collapse to
    ONE real daily close (the session's own last one) -- never treated
    as multiple independent trading days."""
    day0 = [_candle(START, close=100.0), _candle(START + timedelta(minutes=15), close=105.0)]  # same day, last close 105
    later_days = [_candle(START + timedelta(days=i), close=c) for i, c in enumerate([106.0, 104.0, 107.0, 108.0, 109.0], start=1)]
    candles = day0 + later_days
    result = compute_realized_volatility(candles, window_trading_days=5, window_label="5D")
    assert result.status == "OK"
    assert result.trading_days_used == 5  # 6 real distinct days -> 5 real returns, not 6


def test_flat_series_produces_near_zero_realized_vol() -> None:
    candles = _daily_candles([100.0] * 6)
    result = compute_realized_volatility(candles, window_trading_days=5, window_label="5D")
    assert result.status == "OK"
    assert result.annualized_vol_pct == Decimal(0)


# -- IV vs realized -------------------------------------------------------


def test_iv_vs_realized_insufficient_when_either_missing() -> None:
    state, _detail = classify_iv_vs_realized(None, Decimal("20"), low_ratio=Decimal("0.8"), high_ratio=Decimal("1.3"))
    assert state == VolatilityState.INSUFFICIENT_DATA
    state2, _ = classify_iv_vs_realized(Decimal("20"), None, low_ratio=Decimal("0.8"), high_ratio=Decimal("1.3"))
    assert state2 == VolatilityState.INSUFFICIENT_DATA


def test_iv_relatively_high_when_iv_well_above_realized() -> None:
    """The detail may legitimately mention 'cheap'/'expensive' only as
    part of its own explicit disclaimer that this ratio never establishes
    either -- it must never assert cheapness/expensiveness as fact."""
    state, detail = classify_iv_vs_realized(Decimal("40"), Decimal("20"), low_ratio=Decimal("0.8"), high_ratio=Decimal("1.3"))
    assert state == VolatilityState.IV_RELATIVELY_HIGH
    assert "never means an option is 'cheap' or 'expensive'" in detail


def test_iv_relatively_low_when_iv_well_below_realized() -> None:
    state, _ = classify_iv_vs_realized(Decimal("10"), Decimal("20"), low_ratio=Decimal("0.8"), high_ratio=Decimal("1.3"))
    assert state == VolatilityState.IV_RELATIVELY_LOW


def test_iv_reasonable_when_close_to_realized() -> None:
    state, _ = classify_iv_vs_realized(Decimal("21"), Decimal("20"), low_ratio=Decimal("0.8"), high_ratio=Decimal("1.3"))
    assert state == VolatilityState.IV_REASONABLE


def test_iv_vs_realized_explains_the_expectation_vs_history_distinction() -> None:
    _, detail = classify_iv_vs_realized(Decimal("40"), Decimal("20"), low_ratio=Decimal("0.8"), high_ratio=Decimal("1.3"))
    assert "expectation" in detail.lower()
    assert "historical" in detail.lower() or "already happened" in detail.lower()
