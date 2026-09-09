"""Option Decay Attribution Engine (Section 6) — decomposes an OBSERVED
option-premium change between two real, persisted snapshots into its
plausible component drivers, using the provider's own reported Greeks at
the earlier snapshot as the decomposition basis:

    observed premium change ~= delta effect + gamma effect + vega effect
                                + theta effect + residual

This is a first/second-order Taylor-expansion approximation of option
P&L — a standard, textbook options-pricing convention (not a proprietary
invention), applied here across a real, possibly-not-infinitesimal
interval rather than an instantaneous one. It is NOT exact, and this
module never claims it is: `residual` is always reported alongside the
four named effects, and `confidence` is explicitly downgraded when the
residual dominates rather than silently absorbed into "theta."

Threshold classification (per this project's explicit fact/metric/
heuristic/threshold discipline):
    FACT      — the provider's own reported Greeks/prices at t0, taken as
                given (not independently verified against a pricing
                model).
    METRIC    — the four component-effect formulas themselves
                (delta*ΔS, 0.5*gamma*ΔS², vega*ΔIV, theta*Δt) — standard
                options-pricing math with no free parameter.
    HEURISTIC — theta is assumed to be reported "per day" (Upstox's own
                documentation did not confirm this explicitly this
                session — every dedicated Greeks/theta doc page 404'd;
                this assumption is consistent with the real magnitudes
                observed live, typically single-digit-to-low-double-digit
                values, which fits a per-day convention far better than
                a per-year one, but it is NOT independently confirmed and
                is flagged here rather than silently assumed elsewhere).
    THRESHOLD — the residual-fraction cutoffs for confidence tiers
                (required, undefaulted parameters — no universally
                correct cutoff exists).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum

from app.domain.market.models import OptionRight
from app.domain.options.temporal_evidence import TemporalObservation


class AttributionConfidence(str, Enum):
    ATTRIBUTED = "ATTRIBUTED"  # residual is a small fraction of the total observed move
    PARTIAL = "PARTIAL"  # residual is meaningful but the decomposition is still informative
    UNRELIABLE = "UNRELIABLE"  # residual dominates -- do not trust this decomposition
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class DecayAttribution:
    strike: Decimal
    right: OptionRight
    interval: timedelta
    time_elapsed_days: Decimal | None

    observed_price_change: Decimal | None
    underlying_price_change: Decimal | None
    iv_change: Decimal | None

    delta_effect: Decimal | None
    gamma_effect: Decimal | None
    vega_effect: Decimal | None
    theta_effect: Decimal | None
    residual: Decimal | None

    confidence: AttributionConfidence
    detail: str


def attribute_decay(
    observation: TemporalObservation,
    *,
    underlying_price_t0: Decimal | None,
    underlying_price_t1: Decimal | None,
    delta_t0: Decimal | None,
    gamma_t0: Decimal | None,
    vega_t0: Decimal | None,
    theta_t0: Decimal | None,
    attributed_max_residual_fraction: Decimal,
    unreliable_min_residual_fraction: Decimal,
) -> DecayAttribution:
    """`attributed_max_residual_fraction` / `unreliable_min_residual_fraction`
    (THRESHOLD-class, required, undefaulted — see module docstring) are
    fractions of `abs(observed_price_change)`: at/below the first,
    confidence is `ATTRIBUTED`; at/above the second, `UNRELIABLE`;
    between them, `PARTIAL`.
    """
    strike, right, interval = observation.strike, observation.right, observation.interval
    time_elapsed_days = Decimal(observation.interval.total_seconds()) / Decimal(86400)

    observed_price_change = (
        observation.price_t1 - observation.price_t0
        if observation.price_t0 is not None and observation.price_t1 is not None
        else None
    )
    underlying_price_change = (
        underlying_price_t1 - underlying_price_t0
        if underlying_price_t0 is not None and underlying_price_t1 is not None
        else None
    )
    iv_change = observation.iv_change

    if observed_price_change is None:
        return DecayAttribution(
            strike=strike, right=right, interval=interval, time_elapsed_days=time_elapsed_days,
            observed_price_change=None, underlying_price_change=underlying_price_change, iv_change=iv_change,
            delta_effect=None, gamma_effect=None, vega_effect=None, theta_effect=None, residual=None,
            confidence=AttributionConfidence.INSUFFICIENT_DATA,
            detail="observed premium change unavailable (missing option price in one or both snapshots)",
        )

    delta_effect = delta_t0 * underlying_price_change if delta_t0 is not None and underlying_price_change is not None else None
    gamma_effect = (
        (gamma_t0 * underlying_price_change * underlying_price_change) / 2
        if gamma_t0 is not None and underlying_price_change is not None
        else None
    )
    vega_effect = vega_t0 * iv_change if vega_t0 is not None and iv_change is not None else None
    theta_effect = theta_t0 * time_elapsed_days if theta_t0 is not None else None

    if None in (delta_effect, gamma_effect, vega_effect, theta_effect):
        missing = [
            name for name, value in (
                ("delta", delta_effect), ("gamma", gamma_effect), ("vega", vega_effect), ("theta", theta_effect),
            ) if value is None
        ]
        return DecayAttribution(
            strike=strike, right=right, interval=interval, time_elapsed_days=time_elapsed_days,
            observed_price_change=observed_price_change, underlying_price_change=underlying_price_change,
            iv_change=iv_change, delta_effect=delta_effect, gamma_effect=gamma_effect, vega_effect=vega_effect,
            theta_effect=theta_effect, residual=None, confidence=AttributionConfidence.INSUFFICIENT_DATA,
            detail=f"cannot decompose -- missing input(s) for: {', '.join(missing)}",
        )

    assert delta_effect is not None and gamma_effect is not None and vega_effect is not None and theta_effect is not None
    known_sum = delta_effect + gamma_effect + vega_effect + theta_effect
    residual = observed_price_change - known_sum

    if observed_price_change == 0:
        confidence = AttributionConfidence.ATTRIBUTED
        detail = "no observed premium change to attribute"
    else:
        residual_fraction = abs(residual) / abs(observed_price_change)
        if residual_fraction <= attributed_max_residual_fraction:
            confidence = AttributionConfidence.ATTRIBUTED
        elif residual_fraction >= unreliable_min_residual_fraction:
            confidence = AttributionConfidence.UNRELIABLE
        else:
            confidence = AttributionConfidence.PARTIAL
        detail = (
            f"observed premium change {observed_price_change:+.2f}: delta {delta_effect:+.2f}, "
            f"gamma {gamma_effect:+.2f}, vega {vega_effect:+.2f}, theta {theta_effect:+.2f}, "
            f"residual {residual:+.2f} ({residual_fraction:.0%} of the move)"
        )

    return DecayAttribution(
        strike=strike, right=right, interval=interval, time_elapsed_days=time_elapsed_days,
        observed_price_change=observed_price_change, underlying_price_change=underlying_price_change,
        iv_change=iv_change, delta_effect=delta_effect, gamma_effect=gamma_effect, vega_effect=vega_effect,
        theta_effect=theta_effect, residual=residual, confidence=confidence, detail=detail,
    )
