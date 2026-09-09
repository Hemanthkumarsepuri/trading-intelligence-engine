from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain.market.freshness import DataFreshness
from app.domain.market.models import OptionQuote, OptionRight
from app.domain.options.liquidity import LiquidityGrade, assess_liquidity

EXPIRY = date(2026, 9, 24)
AS_OF = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
_MAX_AGE = timedelta(seconds=30)


def _leg(*, bid: float | None, ask: float | None, volume: int | None, oi: int | None, age: timedelta = timedelta(0)) -> OptionQuote:
    ts = AS_OF - age
    return OptionQuote(
        provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts), underlying="NIFTY",
        expiry=EXPIRY, strike=Decimal("24800"), right=OptionRight.CE,
        bid_price=Decimal(str(bid)) if bid is not None else None, ask_price=Decimal(str(ask)) if ask is not None else None,
        volume=volume, open_interest=oi,
    )


def test_untradeable_when_no_bid_ask() -> None:
    leg = _leg(bid=None, ask=None, volume=1000, oi=10000)
    result = assess_liquidity(leg, as_of=AS_OF, max_quote_age=_MAX_AGE)
    assert result.grade == LiquidityGrade.UNTRADEABLE


def test_untradeable_when_stale() -> None:
    leg = _leg(bid=100, ask=101, volume=1000, oi=10000, age=timedelta(minutes=5))
    result = assess_liquidity(leg, as_of=AS_OF, max_quote_age=_MAX_AGE)
    assert result.grade == LiquidityGrade.UNTRADEABLE
    assert result.is_stale is True


def test_untradeable_when_zero_volume_and_zero_oi() -> None:
    leg = _leg(bid=100, ask=101, volume=0, oi=0)
    result = assess_liquidity(leg, as_of=AS_OF, max_quote_age=_MAX_AGE)
    assert result.grade == LiquidityGrade.UNTRADEABLE


def test_excellent_grade() -> None:
    leg = _leg(bid=100, ask=102, volume=10000, oi=100000)  # spread ~2%
    result = assess_liquidity(leg, as_of=AS_OF, max_quote_age=_MAX_AGE)
    assert result.grade == LiquidityGrade.EXCELLENT


def test_excellent_grade_reason_shows_the_real_observed_numbers() -> None:
    """Sprint 6 -- transparency fix: EXCELLENT/GOOD must explain exactly
    which real dimensions were checked, not just "clears the rubric"."""
    leg = _leg(bid=100, ask=102, volume=10000, oi=100000)  # spread ~1.96%
    result = assess_liquidity(leg, as_of=AS_OF, max_quote_age=_MAX_AGE)
    reason = result.reasons[0]
    assert "1.96%" in reason
    assert "10,000" in reason
    assert "100,000" in reason


def test_good_grade() -> None:
    leg = _leg(bid=100, ask=108, volume=1000, oi=10000)  # spread ~7.4%
    result = assess_liquidity(leg, as_of=AS_OF, max_quote_age=_MAX_AGE)
    assert result.grade == LiquidityGrade.GOOD


def test_good_grade_reason_shows_the_real_observed_numbers() -> None:
    leg = _leg(bid=100, ask=108, volume=1000, oi=10000)  # spread ~7.41%
    result = assess_liquidity(leg, as_of=AS_OF, max_quote_age=_MAX_AGE)
    reason = result.reasons[0]
    assert "7.41%" in reason
    assert "1,000" in reason
    assert "10,000" in reason


def test_poor_grade_wide_spread() -> None:
    leg = _leg(bid=100, ask=130, volume=1000, oi=10000)  # spread ~23%
    result = assess_liquidity(leg, as_of=AS_OF, max_quote_age=_MAX_AGE)
    assert result.grade == LiquidityGrade.POOR
    assert any("spread" in r for r in result.reasons)


def test_poor_grade_low_volume() -> None:
    leg = _leg(bid=100, ask=102, volume=10, oi=10000)
    result = assess_liquidity(leg, as_of=AS_OF, max_quote_age=_MAX_AGE)
    assert result.grade == LiquidityGrade.POOR
