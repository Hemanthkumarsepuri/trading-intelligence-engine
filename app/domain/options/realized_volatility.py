"""Realized volatility vs implied volatility (Sprint 7A, Objective 7).

Realized volatility is computed deterministically from the SAME already-
fetched M15 candle series every other technical result in this pipeline
uses (never a new fetch) — one real daily close per real trading session
(the last M15 candle on that real IST calendar date), annualized log-
return standard deviation over a real trading-day window. Genuinely
INSUFFICIENT_DATA, honestly, whenever fewer real trading sessions exist
in the fetched history than the requested window needs — never padded
or estimated from a shorter window.

IV is a market-implied, forward-looking EXPECTATION; realized volatility
is a backward-looking, historical FACT about what already happened. This
module compares them as a ratio only — it never calls an option "cheap"
or "expensive" from this comparison alone (see `VolatilityState`'s own
docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from app.domain.market.models import Candle
from app.utils.time import to_ist


@dataclass(frozen=True)
class RealizedVolatilityResult:
    window_label: str  # "5D" / "10D" / "20D"
    trading_days_used: int  # real closes actually used (0 when INSUFFICIENT_DATA)
    annualized_vol_pct: Decimal | None
    status: str  # "OK" | "INSUFFICIENT_DATA"


def _daily_closes_from_candles(candles: list[Candle]) -> list[tuple[date, Decimal]]:
    """One real close per real IST trading session -- the LAST real M15
    candle's close on each real calendar date present in the series,
    ascending by date. Candles are assumed already sorted ascending by
    timestamp (the provider's own contract), so the last write for a
    given date is naturally that session's real closing candle.
    """
    by_date: dict[date, Decimal] = {}
    for c in candles:
        by_date[to_ist(c.freshness.data_timestamp).date()] = c.close
    return sorted(by_date.items())


def compute_realized_volatility(candles: list[Candle], *, window_trading_days: int, window_label: str) -> RealizedVolatilityResult:
    """`window_trading_days` (THRESHOLD-adjacent, required): a real
    trading-day count (5/10/20), not calendar days -- weekends/holidays
    are never counted as elapsed volatility-measurement time, since only
    real trading sessions with a real close are used at all.
    """
    closes = _daily_closes_from_candles(candles)
    if len(closes) < window_trading_days + 1:
        return RealizedVolatilityResult(window_label=window_label, trading_days_used=0, annualized_vol_pct=None, status="INSUFFICIENT_DATA")

    recent = closes[-(window_trading_days + 1):]
    log_returns = [
        (recent[i][1] / recent[i - 1][1]).ln() for i in range(1, len(recent)) if recent[i - 1][1] > 0 and recent[i][1] > 0
    ]
    if len(log_returns) < window_trading_days:
        return RealizedVolatilityResult(window_label=window_label, trading_days_used=len(log_returns), annualized_vol_pct=None, status="INSUFFICIENT_DATA")

    mean = sum(log_returns, Decimal(0)) / len(log_returns)
    variance = sum(((r - mean) ** 2 for r in log_returns), Decimal(0)) / (len(log_returns) - 1)
    annualized_pct = variance.sqrt() * Decimal(252).sqrt() * Decimal(100)
    return RealizedVolatilityResult(window_label=window_label, trading_days_used=len(log_returns), annualized_vol_pct=annualized_pct, status="OK")


class VolatilityState(str, Enum):
    """Never a claim that an option is "cheap"/"expensive" -- purely a
    factual ratio between a forward-looking market EXPECTATION (IV) and
    a backward-looking historical FACT (realized vol)."""

    IV_RELATIVELY_LOW = "IV_RELATIVELY_LOW"
    IV_REASONABLE = "IV_REASONABLE"
    IV_RELATIVELY_HIGH = "IV_RELATIVELY_HIGH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def classify_iv_vs_realized(
    atm_iv_pct: Decimal | None, realized_vol_pct: Decimal | None, *, low_ratio: Decimal, high_ratio: Decimal,
) -> tuple[VolatilityState, str]:
    """`low_ratio`/`high_ratio` (THRESHOLD, required, undefaulted): the
    IV/realized-vol ratio bands -- no single objectively-correct cutoff
    independent of the instrument's own typical IV-realized spread.
    """
    if atm_iv_pct is None or realized_vol_pct is None or realized_vol_pct == 0:
        return (
            VolatilityState.INSUFFICIENT_DATA,
            (
                "real ATM IV or a real realized-volatility window is unavailable this run -- IV is a market-implied "
                "expectation, realized volatility is historical fact, and this comparison needs both"
            ),
        )
    ratio = atm_iv_pct / realized_vol_pct
    if ratio < low_ratio:
        state = VolatilityState.IV_RELATIVELY_LOW
        read = "below"
    elif ratio > high_ratio:
        state = VolatilityState.IV_RELATIVELY_HIGH
        read = "above"
    else:
        state = VolatilityState.IV_REASONABLE
        read = "broadly in line with"
    return (
        state,
        (
            f"ATM IV ({atm_iv_pct:.2f}%) is {ratio:.2f}x real realized volatility ({realized_vol_pct:.2f}%) -- {read} its own "
            "recent realized range. IV is a market-implied expectation of future movement, not a measurement of what already "
            "happened; this ratio alone never means an option is 'cheap' or 'expensive'."
        ),
    )
