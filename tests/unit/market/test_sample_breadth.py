from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.market.sample_breadth import BreadthCoverage, classify_sample_breadth

AS_OF = datetime(2026, 9, 8, 5, 12, tzinfo=UTC)


def test_partial_48_of_50_is_partial_not_unchanged() -> None:
    symbols = tuple(f"S{i:02d}" for i in range(50))
    changes = {s: Decimal("1.0") if i < 42 else (Decimal("-1.0") if i < 47 else (Decimal("0") if i == 47 else None)) for i, s in enumerate(symbols)}
    result = classify_sample_breadth(
        universe="Nifty 50", source="test", expected_symbols=symbols,
        day_change_pct_by_symbol=changes, as_of=AS_OF, retrieved_at=AS_OF, session="LIVE_SESSION",
    )
    assert result.observed_count == 48
    assert result.expected_count == 50
    assert result.missing_count == 2
    assert result.coverage == BreadthCoverage.PARTIAL
    assert result.unchanged == 1
    assert result.advances == 42
    assert result.declines == 5
    assert "SAMPLE-UNIVERSE BREADTH" in result.detail
    assert "not NSE official" in result.detail


def test_insufficient_coverage_is_unknown_not_fabricated() -> None:
    symbols = tuple(f"S{i:02d}" for i in range(50))
    changes = {s: (Decimal("1.0") if i < 10 else None) for i, s in enumerate(symbols)}
    result = classify_sample_breadth(
        universe="Nifty 50", source="test", expected_symbols=symbols,
        day_change_pct_by_symbol=changes, as_of=AS_OF, retrieved_at=AS_OF, session="LIVE_SESSION",
    )
    assert result.coverage in (BreadthCoverage.INSUFFICIENT, BreadthCoverage.UNKNOWN)
    assert result.advances is None
    assert result.declines is None
    assert "coverage insufficient" in result.detail
