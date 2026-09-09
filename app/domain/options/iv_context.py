"""Implied-volatility context: ATM IV, CE/PE skew at ATM, a nearby-strike
IV smile snapshot, and IV rank computed ONLY from real persisted history
(never a fabricated percentage).

IV rank/percentile is a genuinely historical concept — "where does today's
IV sit relative to its own real recent history" — and cannot be honestly
computed from a single chain snapshot. `compute_iv_rank()` requires a real
`IvObservation` history (see `app.domain.options.models.IvObservation`,
`app.persistence.interfaces.IvObservationRepository`) and returns
`IvRankStatus.UNAVAILABLE` rather than any numeric value whenever that
history doesn't yet contain enough real observations — this is the
system's history accumulating over time, not a placeholder to work around.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.chain_analysis import atm_strike, strike_window
from app.domain.options.models import IvObservation


@dataclass(frozen=True)
class AtmIvSummary:
    atm_strike: Decimal | None
    atm_ce_iv: Decimal | None
    atm_pe_iv: Decimal | None
    chain_iv: Decimal | None  # average of the two when both present, else whichever is present
    ce_pe_skew: Decimal | None  # atm_ce_iv - atm_pe_iv; positive = calls pricier than puts at ATM


@dataclass(frozen=True)
class StrikeIv:
    strike: Decimal
    ce_iv: Decimal | None
    pe_iv: Decimal | None


class IvRankStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class IvRankResult:
    status: IvRankStatus
    value: Decimal | None
    observations_used: int
    detail: str


def _usable_iv(value: Decimal | None) -> Decimal | None:
    """ENGINEERING HEURISTIC, not an Upstox-documented fact: an IV of
    exactly 0 (or negative) is treated as "no usable reading," the same
    as `None` — an option with any real time value cannot have zero
    implied volatility, so a literal `0` observed live (confirmed
    2026-08-29, a real far-dated RELIANCE strike) is read as the
    provider's placeholder for "no tradeable quote to back an IV out of,"
    not a genuine data point. Feeding a literal 0 into `chain_iv`/skew/
    term-structure math would otherwise silently manufacture a misleading
    "IV crashed to zero" or an inflated term-structure slope from what is
    actually a missing-data condition.
    """
    if value is None or value <= 0:
        return None
    return value


def atm_iv_summary(snapshot: OptionChainSnapshot) -> AtmIvSummary:
    atm = atm_strike(snapshot)
    if atm is None:
        return AtmIvSummary(atm_strike=None, atm_ce_iv=None, atm_pe_iv=None, chain_iv=None, ce_pe_skew=None)

    ce_iv = pe_iv = None
    for leg in snapshot.legs:
        if leg.strike != atm:
            continue
        if leg.right == OptionRight.CE:
            ce_iv = _usable_iv(leg.implied_volatility)
        else:
            pe_iv = _usable_iv(leg.implied_volatility)

    if ce_iv is not None and pe_iv is not None:
        chain_iv: Decimal | None = (ce_iv + pe_iv) / 2
        skew: Decimal | None = ce_iv - pe_iv
    else:
        chain_iv = ce_iv if ce_iv is not None else pe_iv
        skew = None  # skew needs both sides; one missing side must not silently read as zero skew

    return AtmIvSummary(atm_strike=atm, atm_ce_iv=ce_iv, atm_pe_iv=pe_iv, chain_iv=chain_iv, ce_pe_skew=skew)


def iv_smile(snapshot: OptionChainSnapshot, *, strikes_each_side: int) -> list[StrikeIv]:
    """CE/PE IV at each strike within `strikes_each_side` of ATM, ascending
    by strike — the raw shape a real skew/smile reading would draw on. Not
    a fitted curve; just the observed points — EXCEPT a literal `0`/negative
    IV is still substituted with `None`, per `_usable_iv()`'s documented
    heuristic (a strike with no tradeable quote, not a genuine zero-vol
    reading); showing a raw `0.0` next to real IV values would be exactly
    as misleading here as it would be in `atm_iv_summary()`.
    """
    window = strike_window(snapshot, strikes_each_side=strikes_each_side)
    if not window:
        return []
    by_strike: dict[Decimal, dict[OptionRight, Decimal | None]] = {s: {} for s in window}
    for leg in snapshot.legs:
        if leg.strike in by_strike:
            by_strike[leg.strike][leg.right] = _usable_iv(leg.implied_volatility)
    return [
        StrikeIv(strike=s, ce_iv=by_strike[s].get(OptionRight.CE), pe_iv=by_strike[s].get(OptionRight.PE))
        for s in window
    ]


def compute_iv_rank(history: list[IvObservation], *, current_chain_iv: Decimal | None, min_observations: int) -> IvRankResult:
    """`min_observations` is required, not defaulted — there is no single
    correct minimum sample size for a meaningful rank; the caller must
    decide and document its own choice (see the pipeline's call site).

    Rank is a simple min-max normalization
    `(current - min) / (max - min) * 100` over `history` with
    `current_chain_iv` included in that range — the standard convention
    for "today's IV relative to its own recent real history." Returns
    `UNAVAILABLE` (never a fabricated number) if there's no current IV, no
    history at all, fewer than `min_observations` real observations, or
    the historical range is degenerate (max == min, so a rank is
    undefined).
    """
    if current_chain_iv is None:
        return IvRankResult(status=IvRankStatus.UNAVAILABLE, value=None, observations_used=0, detail="no current ATM IV")

    known_history = [obs.chain_iv for obs in history if obs.chain_iv is not None]
    if len(known_history) < min_observations:
        return IvRankResult(
            status=IvRankStatus.UNAVAILABLE,
            value=None,
            observations_used=len(known_history),
            detail=f"need at least {min_observations} persisted IV observations, have {len(known_history)}",
        )

    all_values = [*known_history, current_chain_iv]
    lo, hi = min(all_values), max(all_values)
    if hi == lo:
        return IvRankResult(
            status=IvRankStatus.UNAVAILABLE,
            value=None,
            observations_used=len(known_history),
            detail="historical IV range is degenerate (no variation to rank against)",
        )

    rank = (current_chain_iv - lo) / (hi - lo) * Decimal(100)
    return IvRankResult(
        status=IvRankStatus.OK,
        value=rank,
        observations_used=len(known_history),
        detail=f"computed from {len(known_history)} persisted observation(s)",
    )


class IvTrend(str, Enum):
    RISING = "RISING"
    FALLING = "FALLING"
    STABLE = "STABLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


def classify_iv_trend(
    *, current_chain_iv: Decimal | None, history: list[IvObservation], meaningful_change_points: Decimal
) -> IvTrend:
    """Compares `current_chain_iv` against the single most recent real
    persisted observation in `history` (already-sorted-ascending, per
    `IvObservationRepository.query_history()`'s own contract) — NOT a
    multi-point trendline (that would need more real history than a
    two-point comparison honestly supports). `meaningful_change_points`
    (THRESHOLD, required): the absolute IV-point change below which a
    move is treated as noise (`STABLE`) rather than a real trend — no
    universally correct value exists independent of the underlying's own
    typical IV volatility.
    """
    if current_chain_iv is None or not history:
        return IvTrend.INSUFFICIENT_HISTORY
    most_recent = history[-1].chain_iv
    if most_recent is None:
        return IvTrend.INSUFFICIENT_HISTORY
    change = current_chain_iv - most_recent
    if change >= meaningful_change_points:
        return IvTrend.RISING
    if change <= -meaningful_change_points:
        return IvTrend.FALLING
    return IvTrend.STABLE
