"""Transparent, explainable liquidity-quality assessment for one option
leg, from real observable data only (spread, volume, OI, quote presence,
staleness). A theoretically attractive candidate must never be promoted
purely on price/OI/IV evidence while its liquidity is poor — this module
is the gate that enforces that.

The rubric below is an ENGINEERING DEFAULT, not a value sourced from an
authoritative NSE liquidity study — documented explicitly, in one place,
so it is visible and easy for the product owner to retune (per this
project's "no hidden invented thresholds" discipline). Treat it as a
first-pass rubric pending owner review, not a statistically validated
cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from app.domain.market.models import OptionQuote


class LiquidityGrade(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    POOR = "poor"
    UNTRADEABLE = "untradeable"


@dataclass(frozen=True)
class LiquidityAssessment:
    grade: LiquidityGrade
    spread_fraction: Decimal | None
    volume: int | None
    open_interest: int | None
    is_stale: bool
    reasons: list[str]


# Documented rubric (engineering defaults, owner-tunable):
#   UNTRADEABLE — no usable bid/ask, OR stale quote, OR zero volume AND zero OI
#   POOR        — anything that clears UNTRADEABLE but misses the GOOD bar
#   GOOD        — spread <= 10%  AND volume >= 500    AND OI >= 5,000
#   EXCELLENT   — spread <= 3%   AND volume >= 5,000   AND OI >= 50,000
_GOOD_MAX_SPREAD = Decimal("0.10")
_GOOD_MIN_VOLUME = 500
_GOOD_MIN_OI = 5_000
_EXCELLENT_MAX_SPREAD = Decimal("0.03")
_EXCELLENT_MIN_VOLUME = 5_000
_EXCELLENT_MIN_OI = 50_000


def assess_liquidity(leg: OptionQuote, *, as_of: datetime, max_quote_age: timedelta) -> LiquidityAssessment:
    """`max_quote_age` is required, not defaulted — there is no single
    correct staleness threshold for every use of this assessment; the
    caller must supply one appropriate to what it's evaluating for.
    """
    reasons: list[str] = []
    age = as_of - leg.freshness.data_timestamp
    is_stale = age > max_quote_age
    if is_stale:
        reasons.append(f"quote is {age} old (max allowed {max_quote_age})")

    if leg.bid_price is None or leg.ask_price is None or leg.ask_price <= 0:
        reasons.append("no usable bid/ask quote")
        return LiquidityAssessment(
            grade=LiquidityGrade.UNTRADEABLE, spread_fraction=None, volume=leg.volume,
            open_interest=leg.open_interest, is_stale=is_stale, reasons=reasons,
        )

    spread_fraction = (leg.ask_price - leg.bid_price) / leg.ask_price
    volume = leg.volume or 0
    oi = leg.open_interest or 0

    if is_stale or (volume == 0 and oi == 0):
        if volume == 0 and oi == 0:
            reasons.append("zero volume and zero open interest")
        return LiquidityAssessment(
            grade=LiquidityGrade.UNTRADEABLE, spread_fraction=spread_fraction, volume=leg.volume,
            open_interest=leg.open_interest, is_stale=is_stale, reasons=reasons,
        )

    # Sprint 6 -- transparency fix: the POOR branch below already explains
    # exactly which real dimension(s) it checked, with the real observed
    # numbers; EXCELLENT/GOOD previously only said "all clear the rubric"
    # without showing what was actually measured. Same real numbers,
    # never a threshold change -- a reader should never have to guess
    # whether a GOOD grade was comfortably clear of the bar or barely so.
    if spread_fraction <= _EXCELLENT_MAX_SPREAD and volume >= _EXCELLENT_MIN_VOLUME and oi >= _EXCELLENT_MIN_OI:
        return LiquidityAssessment(
            grade=LiquidityGrade.EXCELLENT, spread_fraction=spread_fraction, volume=leg.volume,
            open_interest=leg.open_interest, is_stale=is_stale,
            reasons=[
                (
                    f"spread {spread_fraction:.2%} <= {_EXCELLENT_MAX_SPREAD:.0%}, volume {volume:,} >= {_EXCELLENT_MIN_VOLUME:,}, "
                    f"OI {oi:,} >= {_EXCELLENT_MIN_OI:,} -- clears the 'excellent' rubric on all three dimensions"
                ),
            ],
        )

    if spread_fraction <= _GOOD_MAX_SPREAD and volume >= _GOOD_MIN_VOLUME and oi >= _GOOD_MIN_OI:
        return LiquidityAssessment(
            grade=LiquidityGrade.GOOD, spread_fraction=spread_fraction, volume=leg.volume,
            open_interest=leg.open_interest, is_stale=is_stale,
            reasons=[
                (
                    f"spread {spread_fraction:.2%} <= {_GOOD_MAX_SPREAD:.0%}, volume {volume:,} >= {_GOOD_MIN_VOLUME:,}, "
                    f"OI {oi:,} >= {_GOOD_MIN_OI:,} -- clears the 'good' rubric on all three dimensions "
                    f"(did not additionally clear the stricter 'excellent' rubric: spread <= {_EXCELLENT_MAX_SPREAD:.0%}, "
                    f"volume >= {_EXCELLENT_MIN_VOLUME:,}, OI >= {_EXCELLENT_MIN_OI:,})"
                ),
            ],
        )

    if spread_fraction > _GOOD_MAX_SPREAD:
        reasons.append(f"spread {spread_fraction:.1%} exceeds the 'good' threshold ({_GOOD_MAX_SPREAD:.0%})")
    if volume < _GOOD_MIN_VOLUME:
        reasons.append(f"volume {volume} below the 'good' threshold ({_GOOD_MIN_VOLUME})")
    if oi < _GOOD_MIN_OI:
        reasons.append(f"OI {oi} below the 'good' threshold ({_GOOD_MIN_OI})")

    return LiquidityAssessment(
        grade=LiquidityGrade.POOR, spread_fraction=spread_fraction, volume=leg.volume,
        open_interest=leg.open_interest, is_stale=is_stale, reasons=reasons,
    )
