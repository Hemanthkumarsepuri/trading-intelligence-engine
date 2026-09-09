from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.market.models import Timeframe
from app.domain.options.market_regime import (
    MarketRegime,
    MarketRegimeResult,
    classify_market_regime,
)
from app.domain.technical.ema_alignment import EMAAlignmentState
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.volatility import ATRResult
from app.domain.technical.vwap_position import VWAPPositionState

AS_OF = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
_HIGH = Decimal("1.5")
_LOW = Decimal("0.5")


def _atr(value: str | None, *, status: IndicatorStatus = IndicatorStatus.OK) -> ATRResult:
    return ATRResult(
        status=status, instrument_id="X", timeframe=Timeframe.M15, period=14, as_of=AS_OF, candles_used=15,
        latest_candle_timestamp=AS_OF, value=Decimal(value) if value is not None else None, detail="",
    )


def _classify(
    *, ema: EMAAlignmentState | None, vwap: VWAPPositionState | None, atr_value: str, price: str = "100"
) -> MarketRegimeResult:
    return classify_market_regime(
        ema_alignment=ema, ema_status=IndicatorStatus.OK, vwap_state=vwap, vwap_status=IndicatorStatus.OK,
        atr=_atr(atr_value), current_price=Decimal(price), high_vol_atr_pct=_HIGH, low_vol_atr_pct=_LOW,
    )


def test_trending_bullish() -> None:
    result = _classify(ema=EMAAlignmentState.ASCENDING, vwap=VWAPPositionState.ABOVE, atr_value="1.0")
    assert result.regime == MarketRegime.TRENDING_BULLISH


def test_trending_bearish() -> None:
    result = _classify(ema=EMAAlignmentState.DESCENDING, vwap=VWAPPositionState.BELOW, atr_value="1.0")
    assert result.regime == MarketRegime.TRENDING_BEARISH


def test_mixed_when_ema_and_vwap_conflict() -> None:
    result = _classify(ema=EMAAlignmentState.ASCENDING, vwap=VWAPPositionState.BELOW, atr_value="1.0")
    assert result.regime == MarketRegime.MIXED


def test_high_volatility_when_no_clear_structure_and_atr_high() -> None:
    result = _classify(ema=EMAAlignmentState.MIXED, vwap=VWAPPositionState.AT, atr_value="2.0")
    assert result.regime == MarketRegime.HIGH_VOLATILITY


def test_low_volatility_when_no_clear_structure_and_atr_low() -> None:
    result = _classify(ema=EMAAlignmentState.MIXED, vwap=VWAPPositionState.AT, atr_value="0.1")
    assert result.regime == MarketRegime.LOW_VOLATILITY


def test_range_when_no_clear_structure_and_atr_normal() -> None:
    result = _classify(ema=EMAAlignmentState.MIXED, vwap=VWAPPositionState.AT, atr_value="1.0")
    assert result.regime == MarketRegime.RANGE


def test_trending_label_kept_even_if_also_volatile() -> None:
    result = _classify(ema=EMAAlignmentState.ASCENDING, vwap=VWAPPositionState.ABOVE, atr_value="5.0")
    assert result.regime == MarketRegime.TRENDING_BULLISH


def test_data_insufficient_when_atr_not_ok() -> None:
    result = classify_market_regime(
        ema_alignment=EMAAlignmentState.ASCENDING, ema_status=IndicatorStatus.OK,
        vwap_state=VWAPPositionState.ABOVE, vwap_status=IndicatorStatus.OK,
        atr=_atr(None, status=IndicatorStatus.INSUFFICIENT_HISTORY), current_price=Decimal("100"),
        high_vol_atr_pct=_HIGH, low_vol_atr_pct=_LOW,
    )
    assert result.regime == MarketRegime.DATA_INSUFFICIENT
