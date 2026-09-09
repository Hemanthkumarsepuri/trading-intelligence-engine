"""Multi-strike OI migration (Sprint 7A, Objective 3) — extends the
existing single-contract/single-comparison OI interpretation
(`price_oi_interpretation.py`, preserved unchanged) with a view ACROSS
the top real OI-concentration strikes on one side, using the SAME already-
fetched current + previous `OptionChainSnapshot`s the rest of this
pipeline already relies on (never a new fetch).

This is deliberately kept OUT of the directional evidence matrix
(`evidence_matrix.py`)/decision engine — it is a descriptive, informational
read on WHERE real OI concentration is shifting, not a new directional
vote layered on top of the OI evidence the matrix already has (which
would double-count the same underlying OI data this module also reads).
Every interpretation is hedged, reports its own quality/confidence state
(reusing `EvidenceQuality` from `temporal_evidence.py` — the SAME
real HIGH/MODERATE/LOW/INSUFFICIENT taxonomy, not a second one), and
never asserts trader intent as fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.chain_analysis import top_oi_strikes
from app.domain.options.temporal_evidence import EvidenceQuality


class OIMigrationDirection(str, Enum):
    MIGRATING_LOWER = "MIGRATING_LOWER"
    MIGRATING_HIGHER = "MIGRATING_HIGHER"
    STABLE = "STABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class StrikeOIMigration:
    """One real top-OI strike's own current OI, and (when a real prior
    snapshot carried the SAME strike) its previous OI and real percent
    change -- `None`, honestly, when this strike wasn't in the prior
    snapshot (e.g. the chain window shifted) rather than treated as 0.
    """

    strike: Decimal
    current_oi: int
    previous_oi: int | None
    change_pct: Decimal | None


@dataclass(frozen=True)
class OIMigrationResult:
    right: OptionRight
    entries: list[StrikeOIMigration]
    direction: OIMigrationDirection
    quality: EvidenceQuality
    interpretation: str


def analyze_oi_migration(
    *, current_snapshot: OptionChainSnapshot, previous_snapshot: OptionChainSnapshot | None,
    right: OptionRight, top_n: int, meaningful_shift_pct: Decimal,
) -> OIMigrationResult:
    """Compares the real OI-weighted-average strike (METRIC: sum(strike *
    OI) / sum(OI), over only the strikes present with known OI in BOTH
    the current and prior snapshot's top-`top_n` set -- a genuine
    apples-to-apples real comparison, never padded with a guessed prior
    value) between `current_snapshot` and `previous_snapshot`. A real
    shift beyond `meaningful_shift_pct` (THRESHOLD, required, undefaulted
    -- no single correct value independent of the underlying's own
    strike spacing) is read as real migration; a smaller one, or missing
    prior data, is honestly `STABLE`/`INSUFFICIENT_DATA` -- never forced.

    `interpretation` explicitly never claims certainty ("conventionally
    can indicate... not confirmation of...", matching this module's
    sibling `price_oi_interpretation.py`'s own documented discipline).
    """
    current_top = top_oi_strikes(current_snapshot, right=right, limit=top_n)
    if not current_top:
        return OIMigrationResult(
            right=right, entries=[], direction=OIMigrationDirection.INSUFFICIENT_DATA, quality=EvidenceQuality.INSUFFICIENT,
            interpretation="no real OI data available for this side this run",
        )

    if previous_snapshot is None:
        entries = [StrikeOIMigration(strike=e.strike, current_oi=e.open_interest, previous_oi=None, change_pct=None) for e in current_top]
        return OIMigrationResult(
            right=right, entries=entries, direction=OIMigrationDirection.INSUFFICIENT_DATA, quality=EvidenceQuality.INSUFFICIENT,
            interpretation="no real prior option-chain snapshot is available yet to assess OI migration",
        )

    previous_by_strike = {
        leg.strike: leg.open_interest for leg in previous_snapshot.legs if leg.right == right and leg.open_interest is not None
    }
    entries = []
    for e in current_top:
        previous_oi = previous_by_strike.get(e.strike)
        change_pct = ((Decimal(e.open_interest) - Decimal(previous_oi)) / Decimal(previous_oi) * Decimal(100)) if previous_oi else None
        entries.append(StrikeOIMigration(strike=e.strike, current_oi=e.open_interest, previous_oi=previous_oi, change_pct=change_pct))

    comparable = [(en.strike, en.current_oi, en.previous_oi) for en in entries if en.previous_oi is not None]
    if len(comparable) < 2:
        return OIMigrationResult(
            right=right, entries=entries, direction=OIMigrationDirection.INSUFFICIENT_DATA, quality=EvidenceQuality.LOW,
            interpretation="not enough real overlapping strikes with known prior OI to assess a migration direction",
        )

    current_total = sum(oi for _, oi, _ in comparable)
    previous_total = sum(prev for _, _, prev in comparable if prev is not None)
    current_wavg = sum(Decimal(strike) * Decimal(oi) for strike, oi, _ in comparable) / Decimal(current_total)
    previous_wavg = sum(Decimal(strike) * Decimal(prev) for strike, _, prev in comparable if prev is not None) / Decimal(previous_total)
    shift_pct = (current_wavg - previous_wavg) / previous_wavg * Decimal(100) if previous_wavg != 0 else None

    side_label = "Call" if right == OptionRight.CE else "Put"
    quality = EvidenceQuality.HIGH if len(comparable) >= top_n else EvidenceQuality.MODERATE

    if shift_pct is None or abs(shift_pct) < meaningful_shift_pct:
        direction = OIMigrationDirection.STABLE
        interpretation = f"{side_label} OI concentration among the top {len(current_top)} strikes has not meaningfully shifted"
    elif shift_pct < 0:
        direction = OIMigrationDirection.MIGRATING_LOWER
        note = (
            "nearby support positioning being reduced while lower strikes attract positioning"
            if right == OptionRight.PE
            else "call-side positioning rolling down toward spot"
        )
        interpretation = (
            f"{side_label} OI is migrating to lower strikes (real OI-weighted average strike moved {shift_pct:.2f}%). "
            f"Conventionally this can indicate {note} -- this is not confirmation of a breakdown."
        )
    else:
        direction = OIMigrationDirection.MIGRATING_HIGHER
        note = (
            "nearby resistance positioning being reduced while higher strikes attract positioning"
            if right == OptionRight.CE
            else "put-side positioning rolling up toward spot"
        )
        interpretation = (
            f"{side_label} OI is migrating to higher strikes (real OI-weighted average strike moved {shift_pct:+.2f}%). "
            f"Conventionally this can indicate {note} -- this is not confirmation of a breakout."
        )

    return OIMigrationResult(right=right, entries=entries, direction=direction, quality=quality, interpretation=interpretation)
