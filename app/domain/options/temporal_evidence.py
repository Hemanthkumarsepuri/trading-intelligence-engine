"""The temporal evidence engine — the real, multi-snapshot extension of
`price_oi_interpretation.py`'s single-comparison logic (Phase 6 of the
Options Intelligence Agent milestone).

Given TWO real, already-persisted `OptionChainSnapshot`s of the same
underlying+expiry at two different real instants, computes the
mathematical transition for one strike+right, then keeps four things
explicitly separate rather than blending them into one opaque verdict:

    OBSERVATION    — the raw, mathematically-derived transition (a
                     `TemporalObservation`'s price/OI/volume/IV fields —
                     FACT and METRIC only, nothing interpreted).
    INTERPRETATION — `price_oi_interpretation.classify_price_oi()`'s
                     already-hedged, already-disclaimed textbook reading.
                     Reused, not reimplemented — this module adds a time
                     dimension to that existing logic, it does not
                     duplicate its interpretive judgement.
    QUALITY        — how much weight this specific observation deserves,
                     given the real magnitude of the move and the real
                     freshness/interval of both snapshots.
    CONTRADICTING_EVIDENCE — deliberately NOT populated here. A single
                     strike's own transition cannot contradict itself;
                     only a caller with visibility across strikes/rows
                     (the evidence-matrix/report layer) can identify a
                     genuine contradiction, so that field is that layer's
                     responsibility, not this pure function's.

Every threshold below is tagged with its class, per this project's
explicit threshold-classification discipline:

    FACT      — an exchange/provider-reported value, not computed here.
    METRIC    — a mathematically derived value with no free parameter
                (e.g. a percentage change).
    HEURISTIC — an engineering judgement call with no historical
                validation behind it yet — explicitly labeled, never
                hidden as if it were a fact.
    THRESHOLD — a specific cutoff number awaiting empirical validation
                (this session provides none; every THRESHOLD-class value
                is a REQUIRED, undefaulted parameter the caller must
                supply and document, exactly like every other threshold
                in this package).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from app.domain.market.models import OptionChainSnapshot, OptionQuote, OptionRight
from app.domain.options.price_oi_interpretation import PriceOIObservation, classify_price_oi


class EvidenceQuality(str, Enum):
    """HEURISTIC classification — see module docstring. Not a statistical
    confidence interval; a qualitative "how much should this particular
    reading be trusted" signal only.
    """

    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class TemporalObservation:
    strike: Decimal
    right: OptionRight
    t0: datetime  # FACT — the earlier snapshot's own data_timestamp
    t1: datetime  # FACT — the later snapshot's own data_timestamp
    interval: timedelta  # METRIC — t1 - t0, the real elapsed time actually observed

    price_t0: Decimal | None  # FACT
    price_t1: Decimal | None  # FACT
    price_change_pct: Decimal | None  # METRIC

    oi_t0: int | None  # FACT
    oi_t1: int | None  # FACT
    oi_change_pct: Decimal | None  # METRIC

    volume_t0: int | None  # FACT — Upstox's own cumulative-for-session volume counter
    volume_t1: int | None  # FACT
    volume_change: int | None  # METRIC — t1-t0; only meaningful within one continuous session (see caveat below)

    iv_t0: Decimal | None  # FACT
    iv_t1: Decimal | None  # FACT
    iv_change: Decimal | None  # METRIC

    price_oi: PriceOIObservation  # INTERPRETATION — reused, hedged, from price_oi_interpretation.py

    quality: EvidenceQuality  # HEURISTIC
    quality_reasons: list[str]


def _leg(snapshot: OptionChainSnapshot, *, strike: Decimal, right: OptionRight) -> OptionQuote | None:
    for leg in snapshot.legs:
        if leg.strike == strike and leg.right == right:
            return leg
    return None


def _pct_change(t0: Decimal | int | None, t1: Decimal | int | None) -> Decimal | None:
    if t0 is None or t1 is None or t0 == 0:
        return None
    return (Decimal(t1) - Decimal(t0)) / Decimal(t0) * Decimal(100)


def compute_temporal_observation(
    *,
    earlier: OptionChainSnapshot,
    later: OptionChainSnapshot,
    strike: Decimal,
    right: OptionRight,
    price_flat_threshold: Decimal,
    min_oi_for_high_quality: int,
    min_volume_for_high_quality: int,
    max_reasonable_interval: timedelta,
) -> TemporalObservation:
    """`price_flat_threshold` (THRESHOLD, forwarded to
    `classify_price_oi`), `min_oi_for_high_quality`/
    `min_volume_for_high_quality` (THRESHOLD), and
    `max_reasonable_interval` (HEURISTIC — beyond this gap, a two-point
    "transition" risks conflating two different market regimes rather
    than describing continuous movement) are all required, undefaulted —
    there is no single correct value for any of them independent of what
    the caller is using this evidence for; see the pipeline's call site
    for the documented values actually used in production.

    Raises `ValueError` if `later` is not strictly after `earlier` — this
    is a caller ordering bug, not a data-availability gap, and must never
    be silently tolerated or auto-corrected (silently swapping would risk
    masking a real defect upstream).
    """
    t0 = earlier.freshness.data_timestamp
    t1 = later.freshness.data_timestamp
    if t1 <= t0:
        raise ValueError(f"`later` ({t1.isoformat()}) must be strictly after `earlier` ({t0.isoformat()})")
    interval = t1 - t0

    leg0 = _leg(earlier, strike=strike, right=right)
    leg1 = _leg(later, strike=strike, right=right)

    price_t0 = leg0.last_price if leg0 else None
    price_t1 = leg1.last_price if leg1 else None
    oi_t0 = leg0.open_interest if leg0 else None
    oi_t1 = leg1.open_interest if leg1 else None
    volume_t0 = leg0.volume if leg0 else None
    volume_t1 = leg1.volume if leg1 else None
    iv_t0 = leg0.implied_volatility if leg0 else None
    iv_t1 = leg1.implied_volatility if leg1 else None

    price_oi = classify_price_oi(
        current_price=price_t1, previous_price=price_t0, current_oi=oi_t1, previous_oi=oi_t0,
        price_flat_threshold=price_flat_threshold,
    )

    volume_change = (volume_t1 - volume_t0) if volume_t0 is not None and volume_t1 is not None else None

    quality, reasons = _assess_quality(
        leg0_present=leg0 is not None, leg1_present=leg1 is not None, oi_t1=oi_t1, volume_change=volume_change,
        interval=interval, min_oi_for_high_quality=min_oi_for_high_quality,
        min_volume_for_high_quality=min_volume_for_high_quality, max_reasonable_interval=max_reasonable_interval,
    )

    return TemporalObservation(
        strike=strike, right=right, t0=t0, t1=t1, interval=interval,
        price_t0=price_t0, price_t1=price_t1, price_change_pct=_pct_change(price_t0, price_t1),
        oi_t0=oi_t0, oi_t1=oi_t1, oi_change_pct=_pct_change(oi_t0, oi_t1),
        volume_t0=volume_t0, volume_t1=volume_t1, volume_change=volume_change,
        iv_t0=iv_t0, iv_t1=iv_t1, iv_change=(iv_t1 - iv_t0) if iv_t0 is not None and iv_t1 is not None else None,
        price_oi=price_oi, quality=quality, quality_reasons=reasons,
    )


def _assess_quality(
    *,
    leg0_present: bool,
    leg1_present: bool,
    oi_t1: int | None,
    volume_change: int | None,
    interval: timedelta,
    min_oi_for_high_quality: int,
    min_volume_for_high_quality: int,
    max_reasonable_interval: timedelta,
) -> tuple[EvidenceQuality, list[str]]:
    if not leg0_present or not leg1_present:
        return EvidenceQuality.INSUFFICIENT, ["strike/right leg not present in one or both snapshots"]
    if oi_t1 is None or volume_change is None:
        return EvidenceQuality.INSUFFICIENT, ["OI or volume unavailable in one or both snapshots"]

    reasons: list[str] = []
    if interval > max_reasonable_interval:
        reasons.append(f"interval {interval} exceeds the {max_reasonable_interval} 'reasonable' window -- may span more than one regime")
        return EvidenceQuality.LOW, reasons

    if oi_t1 >= min_oi_for_high_quality and abs(volume_change) >= min_volume_for_high_quality:
        return EvidenceQuality.HIGH, ["OI and volume-change both clear the high-quality floor"]

    if oi_t1 >= min_oi_for_high_quality or abs(volume_change) >= min_volume_for_high_quality:
        return EvidenceQuality.MODERATE, ["only one of OI / volume-change clears the high-quality floor"]

    reasons.append(f"OI ({oi_t1}) and volume-change ({volume_change}) are both below the high-quality floor -- likely noise")
    return EvidenceQuality.LOW, reasons


def strikes_from_intersection(earlier: OptionChainSnapshot, later: OptionChainSnapshot) -> list[tuple[Decimal, OptionRight]]:
    """The (strike, right) pairs present in BOTH snapshots — the only
    ones `compute_temporal_observation` can produce a non-INSUFFICIENT
    reading for. A strike newly added or dropped from the chain between
    the two snapshots is real information (a strike migrated in/out of
    the traded window) but is out of scope for a same-strike transition
    comparison.
    """
    keys0 = {(leg.strike, leg.right) for leg in earlier.legs}
    keys1 = {(leg.strike, leg.right) for leg in later.legs}
    return sorted(keys0 & keys1, key=lambda k: (k[0], k[1].value))
