"""SAMPLE-UNIVERSE market breadth — informational context, never an
EvidenceGroup vote.

Built from an official constituent list (Nifty 50 CSV on nsearchives) plus
already-authorized Upstox quotes. This is NOT the NSE official
advance/decline product. Missing constituents are PARTIAL/UNKNOWN, never
silently treated as unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from statistics import median


class BreadthCoverage(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


# Engineering heuristic: below this observed/expected fraction the sample
# is too thin to report A/D totals as if they described the universe.
MIN_COVERAGE_FRACTION = Decimal("0.60")


@dataclass(frozen=True)
class SampleBreadthResult:
    universe: str
    source: str
    expected_count: int
    observed_count: int
    missing_count: int
    missing_symbols: tuple[str, ...]
    advances: int | None
    declines: int | None
    unchanged: int | None
    percent_up: Decimal | None
    median_day_change_pct: Decimal | None
    coverage: BreadthCoverage
    as_of: datetime
    retrieved_at: datetime
    session: str
    detail: str


def classify_sample_breadth(
    *,
    universe: str,
    source: str,
    expected_symbols: tuple[str, ...],
    day_change_pct_by_symbol: dict[str, Decimal | None],
    as_of: datetime,
    retrieved_at: datetime,
    session: str,
    min_coverage_fraction: Decimal = MIN_COVERAGE_FRACTION,
) -> SampleBreadthResult:
    """`day_change_pct_by_symbol` may omit symbols or map them to None --
    both count as missing. A 0.00% day-change is UNCHANGED, never missing.
    """
    expected = tuple(expected_symbols)
    if not expected:
        return SampleBreadthResult(
            universe=universe, source=source, expected_count=0, observed_count=0, missing_count=0,
            missing_symbols=(), advances=None, declines=None, unchanged=None, percent_up=None,
            median_day_change_pct=None, coverage=BreadthCoverage.UNKNOWN, as_of=as_of,
            retrieved_at=retrieved_at, session=session, detail="no constituent universe supplied",
        )

    observed: list[tuple[str, Decimal]] = []
    missing: list[str] = []
    for symbol in expected:
        pct = day_change_pct_by_symbol.get(symbol)
        if pct is None:
            missing.append(symbol)
        else:
            observed.append((symbol, pct))

    expected_count = len(expected)
    observed_count = len(observed)
    missing_count = len(missing)
    coverage_frac = Decimal(observed_count) / Decimal(expected_count)

    if observed_count == 0 or coverage_frac < min_coverage_fraction:
        return SampleBreadthResult(
            universe=universe, source=source, expected_count=expected_count, observed_count=observed_count,
            missing_count=missing_count, missing_symbols=tuple(missing), advances=None, declines=None,
            unchanged=None, percent_up=None, median_day_change_pct=None,
            coverage=BreadthCoverage.INSUFFICIENT if observed_count else BreadthCoverage.UNKNOWN,
            as_of=as_of, retrieved_at=retrieved_at, session=session,
            detail=(
                f"SAMPLE-UNIVERSE BREADTH coverage insufficient ({observed_count}/{expected_count} observed) -- "
                "not reporting A/D as if the sample were complete"
            ),
        )

    advances = sum(1 for _, pct in observed if pct > 0)
    declines = sum(1 for _, pct in observed if pct < 0)
    unchanged = sum(1 for _, pct in observed if pct == 0)
    percent_up = (Decimal(advances) / Decimal(observed_count)) * Decimal(100)
    med = Decimal(str(median([float(pct) for _, pct in observed])))
    coverage = BreadthCoverage.COMPLETE if missing_count == 0 else BreadthCoverage.PARTIAL
    detail = (
        f"SAMPLE-UNIVERSE BREADTH ({universe}): {advances} up / {declines} down / {unchanged} unchanged "
        f"({observed_count}/{expected_count} observed, {coverage.value}) -- not NSE official A/D"
    )
    return SampleBreadthResult(
        universe=universe, source=source, expected_count=expected_count, observed_count=observed_count,
        missing_count=missing_count, missing_symbols=tuple(missing), advances=advances, declines=declines,
        unchanged=unchanged, percent_up=percent_up, median_day_change_pct=med, coverage=coverage,
        as_of=as_of, retrieved_at=retrieved_at, session=session, detail=detail,
    )
