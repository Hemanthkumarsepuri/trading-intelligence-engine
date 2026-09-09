"""Market regime classification from already-computed, already-tested M15
technical facts (`ema_alignment`, `vwap_position`, `ATR`) — no new
indicator math, no re-fetching, no new data source. Composes three
existing facts into one label.

`ema_alignment.py`'s and `vwap_position.py`'s own docstrings are explicit
that ASCENDING/DESCENDING and ABOVE/BELOW describe numeric facts only, not
inherent directional claims (ARCHITECTURE.md's "Directional Vote Final
Gate", 2026-08-27). This module makes its OWN interpretation of those facts
— exactly the same kind of deliberate, documented interpretive step
`app.domain.strategy.ema_vwap_alignment.EMAVWAPAlignmentStrategy` already
takes for its own `Setup.direction` — not a restatement of a fact the
technical layer itself asserts.

`high_vol_atr_pct` / `low_vol_atr_pct` are required, undefaulted
parameters: ATR-as-percent-of-price thresholds for "unusually volatile" /
"unusually quiet" are engineering judgement, not a statistically derived
cutoff for this instrument's own volatility history — the caller must
supply and document its own choice (see the pipeline's call site) rather
than this module silently asserting one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.technical.ema_alignment import EMAAlignmentState
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.volatility import ATRResult
from app.domain.technical.vwap_position import VWAPPositionState


class MarketRegime(str, Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    MIXED = "MIXED"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


@dataclass(frozen=True)
class MarketRegimeResult:
    regime: MarketRegime
    detail: str
    atr_pct_of_price: Decimal | None


def classify_market_regime(
    *,
    ema_alignment: EMAAlignmentState | None,
    ema_status: IndicatorStatus,
    vwap_state: VWAPPositionState | None,
    vwap_status: IndicatorStatus,
    atr: ATRResult,
    current_price: Decimal | None,
    high_vol_atr_pct: Decimal,
    low_vol_atr_pct: Decimal,
) -> MarketRegimeResult:
    if (
        ema_status != IndicatorStatus.OK
        or vwap_status != IndicatorStatus.OK
        or atr.status != IndicatorStatus.OK
        or ema_alignment is None
        or vwap_state is None
        or atr.value is None
        or current_price is None
        or current_price <= 0
    ):
        return MarketRegimeResult(
            regime=MarketRegime.DATA_INSUFFICIENT,
            detail="one or more of EMA alignment / VWAP position / ATR is not yet computable (insufficient history)",
            atr_pct_of_price=None,
        )

    atr_pct = atr.value / current_price * Decimal(100)

    if ema_alignment == EMAAlignmentState.ASCENDING and vwap_state == VWAPPositionState.ABOVE:
        return MarketRegimeResult(
            regime=MarketRegime.TRENDING_BULLISH,
            detail="EMA(9,21,50) ascending AND price above VWAP -- this module's own reading, per its docstring",
            atr_pct_of_price=atr_pct,
        )
    if ema_alignment == EMAAlignmentState.DESCENDING and vwap_state == VWAPPositionState.BELOW:
        return MarketRegimeResult(
            regime=MarketRegime.TRENDING_BEARISH,
            detail="EMA(9,21,50) descending AND price below VWAP -- this module's own reading, per its docstring",
            atr_pct_of_price=atr_pct,
        )
    if (ema_alignment == EMAAlignmentState.ASCENDING and vwap_state == VWAPPositionState.BELOW) or (
        ema_alignment == EMAAlignmentState.DESCENDING and vwap_state == VWAPPositionState.ABOVE
    ):
        return MarketRegimeResult(
            regime=MarketRegime.MIXED,
            detail=f"EMA alignment ({ema_alignment.value}) and VWAP position ({vwap_state.value}) disagree",
            atr_pct_of_price=atr_pct,
        )

    # EMA alignment is MIXED, or price is exactly AT VWAP -- no clear
    # directional structure from either fact. Subdivide by volatility only
    # in this inconclusive case; a clearly trending regime keeps its
    # TRENDING label above regardless of how volatile it also is.
    if atr_pct >= high_vol_atr_pct:
        return MarketRegimeResult(
            regime=MarketRegime.HIGH_VOLATILITY,
            detail=f"no clear EMA/VWAP structure; ATR is {atr_pct:.2f}% of price (>= {high_vol_atr_pct}% threshold)",
            atr_pct_of_price=atr_pct,
        )
    if atr_pct <= low_vol_atr_pct:
        return MarketRegimeResult(
            regime=MarketRegime.LOW_VOLATILITY,
            detail=f"no clear EMA/VWAP structure; ATR is {atr_pct:.2f}% of price (<= {low_vol_atr_pct}% threshold)",
            atr_pct_of_price=atr_pct,
        )
    return MarketRegimeResult(
        regime=MarketRegime.RANGE,
        detail=f"no clear EMA/VWAP structure; ATR {atr_pct:.2f}% of price is within the normal band",
        atr_pct_of_price=atr_pct,
    )
