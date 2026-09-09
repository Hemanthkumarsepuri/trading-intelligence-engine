"""Price + open-interest combination classification.

Deliberately does NOT label a combination "long buildup" / "short
buildup" / "short covering" / "long unwinding" — those textbook labels
assert a *cause* (new positions vs. closing positions) that price+OI alone
cannot distinguish without also knowing whether the move was driven by
fresh money or unwinding, information this system does not have. What this
module returns is the observable fact only: which quadrant the price
change and OI change actually fell into, plus the textbook reading as a
clearly-labeled, unconfirmed convention — never asserted as what
happened.

Evidence required for a non-`INSUFFICIENT_DATA` classification: a real
previous observation of the SAME instrument (price and OI both), separated
in time from the current one. The first time this system ever sees an
instrument, this is unconditionally `INSUFFICIENT_DATA` — there is nothing
dishonest to report yet, and nothing here may report a synthetic prior.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class PriceOIQuadrant(str, Enum):
    PRICE_UP_OI_UP = "price_up_oi_up"
    PRICE_UP_OI_DOWN = "price_up_oi_down"
    PRICE_DOWN_OI_UP = "price_down_oi_up"
    PRICE_DOWN_OI_DOWN = "price_down_oi_down"
    PRICE_FLAT = "price_flat"  # OI may still have moved; price alone determined this quadrant is flat
    INSUFFICIENT_DATA = "insufficient_data"


# Textbook convention labels — NEVER returned as fact. Always presented
# alongside the disclaimer that this reading requires corroboration (e.g.
# real volume, or agreement from an independent evidence category) before
# being treated as anything more than "the conventional first guess."
_CONVENTIONAL_READING: dict[PriceOIQuadrant, str] = {
    PriceOIQuadrant.PRICE_UP_OI_UP: "conventionally read as 'long buildup' (fresh long positions) — unconfirmed",
    PriceOIQuadrant.PRICE_UP_OI_DOWN: "conventionally read as 'short covering' (shorts closing) — unconfirmed",
    PriceOIQuadrant.PRICE_DOWN_OI_UP: "conventionally read as 'short buildup' (fresh short positions) — unconfirmed",
    PriceOIQuadrant.PRICE_DOWN_OI_DOWN: "conventionally read as 'long unwinding' (longs closing) — unconfirmed",
    PriceOIQuadrant.PRICE_FLAT: "no meaningful price move to classify against the OI change",
    PriceOIQuadrant.INSUFFICIENT_DATA: "no prior observation of this instrument is available yet",
}


@dataclass(frozen=True)
class PriceOIObservation:
    quadrant: PriceOIQuadrant
    price_change: Decimal | None
    oi_change: int | None
    conventional_reading: str
    """Human-readable, explicitly-hedged textbook label — see module
    docstring. Never treat this string as a confirmed directional signal on
    its own."""


def classify_price_oi(
    *,
    current_price: Decimal | None,
    previous_price: Decimal | None,
    current_oi: int | None,
    previous_oi: int | None,
    price_flat_threshold: Decimal,
) -> PriceOIObservation:
    """`price_flat_threshold` is a required, undefaulted absolute price
    move below which a change is treated as noise rather than a real
    direction — there is no universal correct value (it depends on the
    instrument's tick size and typical range), so the caller must supply
    one appropriate to what it's evaluating rather than this module
    guessing a threshold it has no evidence for.
    """
    if current_price is None or previous_price is None or current_oi is None or previous_oi is None:
        return PriceOIObservation(
            quadrant=PriceOIQuadrant.INSUFFICIENT_DATA,
            price_change=None,
            oi_change=None,
            conventional_reading=_CONVENTIONAL_READING[PriceOIQuadrant.INSUFFICIENT_DATA],
        )

    price_change = current_price - previous_price
    oi_change = current_oi - previous_oi

    if abs(price_change) < price_flat_threshold:
        quadrant = PriceOIQuadrant.PRICE_FLAT
    elif price_change > 0:
        quadrant = PriceOIQuadrant.PRICE_UP_OI_UP if oi_change >= 0 else PriceOIQuadrant.PRICE_UP_OI_DOWN
    else:
        quadrant = PriceOIQuadrant.PRICE_DOWN_OI_UP if oi_change >= 0 else PriceOIQuadrant.PRICE_DOWN_OI_DOWN

    return PriceOIObservation(
        quadrant=quadrant,
        price_change=price_change,
        oi_change=oi_change,
        conventional_reading=_CONVENTIONAL_READING[quadrant],
    )


# ============================================================
# Final Hardening Pass, Phase 6 -- futures basis CHANGE (point-in-time
# basis already existed in evidence_matrix.row_spot_futures_basis(); this
# is the real, previously-missing time dimension, feasible with zero new
# fetch: both the underlying's and the futures leg's own quotes are
# already persisted to the SAME `repositories.quotes` store every run,
# keyed by instrument, so a genuine prior basis is directly computable --
# never a guessed/interpolated one. Deliberately kept OUT of the
# directional evidence matrix (same discipline as OI migration/realized
# volatility): informational only, reported alongside the point-in-time
# basis, never a new vote layered on evidence that would double-count the
# SAME futures quote the FUTURES group already reads.
# ============================================================


class BasisChangeDirection(str, Enum):
    WIDENING_PREMIUM = "widening_premium"
    NARROWING_PREMIUM = "narrowing_premium"
    WIDENING_DISCOUNT = "widening_discount"
    NARROWING_DISCOUNT = "narrowing_discount"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


_BASIS_CHANGE_READING: dict[BasisChangeDirection, str] = {
    BasisChangeDirection.WIDENING_PREMIUM: "futures premium to spot is widening -- conventionally read as strengthening participation, unconfirmed",
    BasisChangeDirection.NARROWING_PREMIUM: "futures premium to spot is narrowing -- conventionally read as cooling participation, unconfirmed",
    BasisChangeDirection.WIDENING_DISCOUNT: "futures discount to spot is widening (deeper backwardation) -- unusual, unconfirmed",
    BasisChangeDirection.NARROWING_DISCOUNT: "futures discount to spot is narrowing -- unconfirmed",
    BasisChangeDirection.STABLE: "no meaningful real change in basis since the prior observation",
    BasisChangeDirection.INSUFFICIENT_DATA: "no prior real basis observation is available yet",
}


@dataclass(frozen=True)
class BasisChangeObservation:
    direction: BasisChangeDirection
    current_basis_pct: Decimal | None
    previous_basis_pct: Decimal | None
    change_pct_points: Decimal | None
    interpretation: str


def classify_basis_change(
    *, current_basis_pct: Decimal | None, previous_basis_pct: Decimal | None, meaningful_change_pct: Decimal,
) -> BasisChangeObservation:
    """`meaningful_change_pct` is a required, undefaulted absolute
    percentage-point threshold below which a real basis change is treated
    as noise -- same discipline as `price_flat_threshold` above, no
    universally-correct value exists independent of the instrument."""
    if current_basis_pct is None or previous_basis_pct is None:
        return BasisChangeObservation(
            direction=BasisChangeDirection.INSUFFICIENT_DATA, current_basis_pct=current_basis_pct,
            previous_basis_pct=previous_basis_pct, change_pct_points=None,
            interpretation=_BASIS_CHANGE_READING[BasisChangeDirection.INSUFFICIENT_DATA],
        )

    change = current_basis_pct - previous_basis_pct
    if abs(change) < meaningful_change_pct:
        direction = BasisChangeDirection.STABLE
    elif current_basis_pct >= 0:
        direction = BasisChangeDirection.WIDENING_PREMIUM if change > 0 else BasisChangeDirection.NARROWING_PREMIUM
    else:
        direction = BasisChangeDirection.WIDENING_DISCOUNT if change < 0 else BasisChangeDirection.NARROWING_DISCOUNT

    return BasisChangeObservation(
        direction=direction, current_basis_pct=current_basis_pct, previous_basis_pct=previous_basis_pct,
        change_pct_points=change, interpretation=_BASIS_CHANGE_READING[direction],
    )
