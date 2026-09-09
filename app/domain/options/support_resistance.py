"""Evidence-based support/resistance candidate levels derived from an option
chain. Explicitly does NOT claim "this OI level equals support/resistance"
— OI concentration is one input a real trader would look at, not a
guarantee. Every returned `Level` carries the raw evidence and a
qualitative strength rating so a consumer can judge for itself, and the
levels are candidates to weigh against everything else in the evidence
matrix, not a standalone verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.market.models import Candle, OptionChainSnapshot, OptionRight
from app.domain.options.models import StrikeOI


class LevelKind(str, Enum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


class LevelStrength(str, Enum):
    """Qualitative only — derived from this strike's OI rank and how far
    ahead it is of the next-highest strike on the same side (a lone,
    dominant strike is a stronger candidate than one of several
    similarly-sized strikes). Not a probability, not a guarantee.
    """

    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


@dataclass(frozen=True)
class Level:
    kind: LevelKind
    strike: Decimal
    open_interest: int
    strength: LevelStrength
    distance_from_spot: Decimal | None
    distance_from_spot_pct: Decimal | None
    evidence: str


def _strength_for_rank(rank: int, *, oi: int, next_oi: int | None) -> LevelStrength:
    """`rank` is 0 for the single highest-OI strike on this side. A rank-0
    strike whose OI is at least double the next-highest is `STRONG`
    (a clearly dominant concentration); rank 0/1 otherwise is `MODERATE`;
    everything else is `WEAK`. This 2x-dominance threshold is an
    engineering default for readability, not a statistically validated
    cutoff — documented here so it is visible and adjustable, not hidden.
    """
    if rank == 0 and (next_oi is None or oi >= next_oi * 2):
        return LevelStrength.STRONG
    if rank <= 1:
        return LevelStrength.MODERATE
    return LevelStrength.WEAK


def _top_oi_strikes_on_one_side(
    snapshot: OptionChainSnapshot, *, right: OptionRight, limit: int, spot: Decimal, above_spot: bool
) -> list[StrikeOI]:
    """Same OI-ranking methodology as `chain_analysis.top_oi_strikes()`
    (unknown-OI legs excluded, never treated as 0; ranked by OI
    descending), but with the geometric constraint applied BEFORE ranking
    and truncation — a strike on the wrong side of spot must never
    displace a genuinely valid candidate by being ranked first and then
    discarded. `strike == spot` is excluded from both sides (Part: a
    level cannot be both, or neither, a real geometric boundary).
    """
    entries = [
        StrikeOI(strike=leg.strike, open_interest=leg.open_interest)
        for leg in snapshot.legs
        if leg.right == right and leg.open_interest is not None and ((leg.strike > spot) if above_spot else (leg.strike < spot))
    ]
    entries.sort(key=lambda e: e.open_interest, reverse=True)
    return entries[:limit]


def support_resistance_levels(snapshot: OptionChainSnapshot, *, limit: int = 3) -> list[Level]:
    """Up to `limit` resistance candidates (from call OI strictly ABOVE
    spot) and `limit` support candidates (from put OI strictly BELOW
    spot), ranked by OI descending within each side. A strike can never
    appear as both — support and resistance are geometrically disjoint by
    construction, not merely by convention (Part: the geometric
    correctness fix — a heavy-OI strike on the wrong side of spot is a
    real, common chain shape, e.g. old/hedging OI far from a spot that
    has since moved, and must never be reported as a level on the wrong
    side). Returns `[]` entirely when spot is unknown — a support/
    resistance reading has no meaning without a real geometric reference
    to measure against, never a guessed one.
    """
    spot = snapshot.underlying_last_price
    if spot is None:
        return []

    levels: list[Level] = []
    for right, kind, above_spot in (
        (OptionRight.CE, LevelKind.RESISTANCE, True),
        (OptionRight.PE, LevelKind.SUPPORT, False),
    ):
        ranked = _top_oi_strikes_on_one_side(snapshot, right=right, limit=limit, spot=spot, above_spot=above_spot)
        for rank, entry in enumerate(ranked):
            next_oi = ranked[rank + 1].open_interest if rank + 1 < len(ranked) else None
            distance = entry.strike - spot
            distance_pct = distance / spot * Decimal(100)
            levels.append(
                Level(
                    kind=kind,
                    strike=entry.strike,
                    open_interest=entry.open_interest,
                    strength=_strength_for_rank(rank, oi=entry.open_interest, next_oi=next_oi),
                    distance_from_spot=distance,
                    distance_from_spot_pct=distance_pct,
                    evidence=(
                        f"{right.value} OI at strike {entry.strike} is {entry.open_interest:,} "
                        f"(rank {rank + 1} of {len(ranked)} by OI on this side, {'above' if above_spot else 'below'} spot {spot}) "
                        f"— a concentration candidate, not a confirmed level."
                    ),
                )
            )
    return levels


class StabilityState(str, Enum):
    """Whether a level's OI concentration grew, shrank, or held versus the
    SAME strike in one prior real persisted snapshot — a real, if modest,
    two-point comparison. This is explicitly NOT the full "held
    repeatedly across many sessions" multi-day pattern the product vision
    describes (§5) — that needs real accumulated history this system does
    not have yet; `UNCONFIRMED` is the honest answer until it does, not a
    fabricated multi-session read from two snapshots.
    """

    STABLE = "STABLE"
    STRENGTHENING = "STRENGTHENING"
    WEAKENING = "WEAKENING"
    UNCONFIRMED = "UNCONFIRMED"


@dataclass(frozen=True)
class LevelStability:
    level: Level
    state: StabilityState
    detail: str


def assess_level_stability(
    current_levels: list[Level], *, previous_snapshot: OptionChainSnapshot | None, meaningful_oi_change_fraction: Decimal
) -> list[LevelStability]:
    """`meaningful_oi_change_fraction` (THRESHOLD, required, undefaulted):
    the fractional OI change (e.g. `Decimal("0.10")` for 10%) at or beyond
    which a level is called STRENGTHENING/WEAKENING rather than STABLE —
    no single correct value exists independent of the underlying's own
    typical OI volatility, so the caller must supply and document its own.
    """
    results: list[LevelStability] = []
    for level in current_levels:
        if previous_snapshot is None:
            results.append(LevelStability(level=level, state=StabilityState.UNCONFIRMED, detail="no prior persisted snapshot to compare against"))
            continue

        right = OptionRight.CE if level.kind == LevelKind.RESISTANCE else OptionRight.PE
        previous_leg = next(
            (leg for leg in previous_snapshot.legs if leg.strike == level.strike and leg.right == right), None
        )
        if previous_leg is None or previous_leg.open_interest is None:
            results.append(
                LevelStability(
                    level=level, state=StabilityState.UNCONFIRMED,
                    detail=f"strike {level.strike} {right.value} was not present with known OI in the prior snapshot",
                )
            )
            continue

        previous_oi = previous_leg.open_interest
        if previous_oi == 0:
            results.append(LevelStability(level=level, state=StabilityState.UNCONFIRMED, detail="prior OI was zero -- change fraction undefined"))
            continue

        change_fraction = (Decimal(level.open_interest) - Decimal(previous_oi)) / Decimal(previous_oi)
        if change_fraction >= meaningful_oi_change_fraction:
            state = StabilityState.STRENGTHENING
        elif change_fraction <= -meaningful_oi_change_fraction:
            state = StabilityState.WEAKENING
        else:
            state = StabilityState.STABLE
        results.append(
            LevelStability(
                level=level, state=state,
                detail=f"OI changed {change_fraction:+.1%} vs the prior real snapshot ({previous_oi:,} -> {level.open_interest:,})",
            )
        )
    return results


# ============================================================
# Sprint 6 -- OI STRUCTURAL vs TECHNICAL PRICE vs CONFLUENCE levels.
#
# Every `Level` above is already, correctly, an OI-STRUCTURAL candidate
# only (see its own `evidence` string: "a concentration candidate, not a
# confirmed level" -- that principle is unchanged here). What was missing
# is a genuinely independent second lens: a real TECHNICAL PRICE level
# read directly off the same already-fetched M15 candle series every
# other technical result on this report already uses (never a new
# fetch, never an invented historical level). A CONFLUENCE level is
# reported ONLY when an OI-structural level and a real technical level
# independently land within the SAME `near_level_pct_threshold` this
# report already uses for "near a level" (never a second, silently
# different cutoff) -- that is genuine independent corroboration, not a
# relabeling of the same evidence.
# ============================================================


class TechnicalLevelKind(str, Enum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


@dataclass(frozen=True)
class TechnicalLevel:
    kind: TechnicalLevelKind
    price: Decimal
    evidence: str
    # Sprint 7A, Objective 11 -- which real technical source this level
    # came from ("SWING" / "VWAP" / "EMA") -- defaulted for backward
    # compatibility with the Sprint 6 caller, which only ever produced
    # swing levels. Distinct sources are what let `classify_level_
    # confluence()` tell a 2-way CONFLUENCE from a genuinely stronger
    # 3-way STRONGER_CONFLUENCE, never a numeric score.
    source_label: str = "SWING"


def technical_price_levels(candles: list[Candle], *, lookback: int) -> list[TechnicalLevel]:
    """The real highest high / lowest low over the most recent `lookback`
    real M15 candles -- deliberately the simplest defensible definition
    (a real swing extreme, not a fitted pivot-detection algorithm);
    HEURISTIC, documented as such, never a fabricated historical level.
    Returns `[]` honestly when fewer than `lookback` real candles exist,
    rather than computing a level from less history than was asked for.
    """
    if len(candles) < lookback:
        return []
    window = candles[-lookback:]
    highest = max(window, key=lambda c: c.high)
    lowest = min(window, key=lambda c: c.low)
    return [
        TechnicalLevel(
            kind=TechnicalLevelKind.RESISTANCE, price=highest.high,
            evidence=f"highest real M15 high over the last {lookback} candles (at {highest.freshness.data_timestamp.isoformat()})",
        ),
        TechnicalLevel(
            kind=TechnicalLevelKind.SUPPORT, price=lowest.low,
            evidence=f"lowest real M15 low over the last {lookback} candles (at {lowest.freshness.data_timestamp.isoformat()})",
        ),
    ]


def vwap_ema_levels(*, spot: Decimal | None, vwap_value: Decimal | None, ema_value: Decimal | None, ema_label: str = "EMA50") -> list[TechnicalLevel]:
    """Sprint 7A, Objective 11 -- VWAP/a major EMA as additional real,
    already-computed technical sources (zero new fetch/calculation --
    both are read straight off `report.analysis`, the SAME real values
    the M15 trend/VWAP evidence rows already use). A level acts as
    real-time SUPPORT when spot currently sits above it, RESISTANCE when
    below -- the standard, real geometric convention (never a fabricated
    historical level). `[]` honestly whenever spot or the source value
    itself is unavailable.
    """
    levels: list[TechnicalLevel] = []
    if spot is not None and vwap_value is not None:
        kind = TechnicalLevelKind.SUPPORT if spot >= vwap_value else TechnicalLevelKind.RESISTANCE
        levels.append(TechnicalLevel(kind=kind, price=vwap_value, evidence=f"rolling M15 VWAP ({vwap_value}), not session VWAP; spot currently {'above' if kind == TechnicalLevelKind.SUPPORT else 'below'} it", source_label="VWAP"))
    if spot is not None and ema_value is not None:
        kind = TechnicalLevelKind.SUPPORT if spot >= ema_value else TechnicalLevelKind.RESISTANCE
        levels.append(TechnicalLevel(kind=kind, price=ema_value, evidence=f"{ema_label} ({ema_value}), spot currently {'above' if kind == TechnicalLevelKind.SUPPORT else 'below'} it", source_label="EMA"))
    return levels


class LevelSource(str, Enum):
    OI_STRUCTURAL = "OI_STRUCTURAL"
    TECHNICAL_PRICE = "TECHNICAL_PRICE"
    CONFLUENCE = "CONFLUENCE"
    # Sprint 7A, Objective 11 -- 3+ independent real sources (OI plus 2
    # or more DISTINCT-labeled technical sources, e.g. swing AND VWAP)
    # agreeing at the same real zone. Still never a numeric score --
    # just "more independent evidence agreed."
    STRONGER_CONFLUENCE = "STRONGER_CONFLUENCE"


@dataclass(frozen=True)
class LevelClassification:
    kind: LevelKind
    price: Decimal
    source: LevelSource
    detail: str


def classify_level_confluence(
    oi_levels: list[Level], technical_levels: list[TechnicalLevel], *, near_pct_threshold: Decimal,
) -> list[LevelClassification]:
    """Never labels an OI concentration alone as confirmed support/
    resistance -- an OI level only becomes CONFLUENCE when a real
    technical level on the SAME side (support vs resistance) lands
    within `near_pct_threshold`% of it, and STRONGER_CONFLUENCE when 2+
    real technical levels from DISTINCT sources (e.g. a real swing level
    AND VWAP, not two labeled the same) all agree. A technical level
    with no corroborating OI concentration nearby is reported as its own
    TECHNICAL_PRICE entry, never silently dropped.
    """
    results: list[LevelClassification] = []
    matched_technical_prices: set[Decimal] = set()
    for oi in oi_levels:
        if oi.strike == 0:
            results.append(LevelClassification(kind=oi.kind, price=oi.strike, source=LevelSource.OI_STRUCTURAL, detail=oi.evidence))
            continue
        matches = [
            t for t in technical_levels
            if t.kind.value == oi.kind.value and abs(t.price - oi.strike) / oi.strike * Decimal(100) <= near_pct_threshold
        ]
        if matches:
            for m in matches:
                matched_technical_prices.add(m.price)
            distinct_sources = {m.source_label for m in matches}
            source = LevelSource.STRONGER_CONFLUENCE if len(distinct_sources) >= 2 else LevelSource.CONFLUENCE
            corroboration = " AND ".join(f"{m.source_label} at {m.price} ({m.evidence})" for m in matches)
            results.append(
                LevelClassification(
                    kind=oi.kind, price=oi.strike, source=source,
                    detail=(
                        f"OI concentration at {oi.strike} is within {near_pct_threshold}% of {len(distinct_sources)} "
                        f"independent real technical source(s) -- {oi.evidence} AND {corroboration}"
                    ),
                )
            )
        else:
            results.append(LevelClassification(kind=oi.kind, price=oi.strike, source=LevelSource.OI_STRUCTURAL, detail=oi.evidence))
    for t in technical_levels:
        if t.price in matched_technical_prices:
            continue
        results.append(LevelClassification(kind=LevelKind(t.kind.value), price=t.price, source=LevelSource.TECHNICAL_PRICE, detail=t.evidence))
    return results
