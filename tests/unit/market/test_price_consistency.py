"""Sprint 6 -- pure unit tests for `classify_price_consistency()`. No I/O,
no pipeline, real Decimal/datetime values only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.market.price_consistency import (
    PRICE_SOURCE_USER_LABEL,
    DataConsistency,
    PriceSource,
    classify_price_consistency,
    price_source_user_label,
)

T = datetime(2026, 8, 28, 15, 15, tzinfo=UTC)
MAX_CONSISTENT = Decimal("0.5")
MAX_PARTIAL = Decimal("2.0")


def _classify(*, quote: str | None, candle: str | None, chain: str | None, futures: str | None = None) -> tuple[DataConsistency, Decimal | None]:
    result = classify_price_consistency(
        quote_price=Decimal(quote) if quote else None, quote_timestamp=T,
        candle_price=Decimal(candle) if candle else None, candle_timestamp=T,
        option_chain_price=Decimal(chain) if chain else None, option_chain_timestamp=T,
        futures_price=Decimal(futures) if futures else None, futures_timestamp=T,
        max_consistent_diff_pct=MAX_CONSISTENT, max_partially_aligned_diff_pct=MAX_PARTIAL,
    )
    return result.classification, result.max_pairwise_diff_pct


def test_consistent_when_all_three_agree_closely() -> None:
    classification, diff = _classify(quote="3685.0", candle="3685.5", chain="3684.8")
    assert classification == DataConsistency.CONSISTENT
    assert diff is not None and diff < MAX_CONSISTENT


def test_partially_aligned_within_the_wider_band() -> None:
    # 3685 vs 3720 is ~0.95% -- above CONSISTENT (0.5%), below PARTIAL ceiling (2.0%).
    classification, diff = _classify(quote="3685.0", candle="3720.0", chain="3685.0")
    assert classification == DataConsistency.PARTIALLY_ALIGNED
    assert diff is not None and MAX_CONSISTENT < diff <= MAX_PARTIAL


def test_inconsistent_reproduces_the_real_kaynes_discrepancy() -> None:
    """The real, live-observed KAYNES numbers: candle close 3943.10 vs
    option-chain reference 3685.0 -- a genuine, material (~7%) gap."""
    classification, diff = _classify(quote="3685.0", candle="3943.10", chain="3685.0")
    assert classification == DataConsistency.INCONSISTENT
    assert diff is not None and diff > MAX_PARTIAL


def test_unknown_when_fewer_than_two_real_prices_available() -> None:
    classification, diff = _classify(quote="3685.0", candle=None, chain=None)
    assert classification == DataConsistency.UNKNOWN
    assert diff is None


def test_unknown_when_no_real_prices_available() -> None:
    classification, diff = _classify(quote=None, candle=None, chain=None)
    assert classification == DataConsistency.UNKNOWN
    assert diff is None


def test_futures_never_counted_toward_consistency_even_when_it_diverges_from_spot() -> None:
    """A real futures premium/discount must never itself trigger
    INCONSISTENT -- only the underlying-equity sources are compared."""
    classification, diff = _classify(quote="3685.0", candle="3685.5", chain="3684.8", futures="3660.0")
    assert classification == DataConsistency.CONSISTENT
    assert diff is not None and diff < MAX_CONSISTENT


def test_every_source_is_labeled_even_when_missing() -> None:
    result = classify_price_consistency(
        quote_price=Decimal("3685.0"), quote_timestamp=T,
        candle_price=None, candle_timestamp=None,
        option_chain_price=Decimal("3684.5"), option_chain_timestamp=T,
        futures_price=None, futures_timestamp=None,
        max_consistent_diff_pct=MAX_CONSISTENT, max_partially_aligned_diff_pct=MAX_PARTIAL,
    )
    sources = {p.source: p.price for p in result.prices}
    assert sources[PriceSource.LATEST_QUOTE] == Decimal("3685.0")
    assert sources[PriceSource.LATEST_CANDLE] is None
    assert sources[PriceSource.OPTION_CHAIN_REFERENCE] == Decimal("3684.5")
    assert sources[PriceSource.FUTURES] is None
    assert len(result.prices) == 4  # every source present, whether or not it had a real value


def test_never_fabricates_a_unified_spot() -> None:
    """Structural proof: `PriceConsistencyResult` has no single "spot"
    field at all -- only the labeled list and a classification/diff."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(classify_price_consistency(
        quote_price=Decimal("100"), quote_timestamp=T, candle_price=Decimal("100"), candle_timestamp=T,
        option_chain_price=None, option_chain_timestamp=None, futures_price=None, futures_timestamp=None,
        max_consistent_diff_pct=MAX_CONSISTENT, max_partially_aligned_diff_pct=MAX_PARTIAL,
    ))}
    assert "spot" not in field_names
    assert field_names == {"classification", "detail", "prices", "max_pairwise_diff_pct", "candle_excluded_as_stale"}


def test_boundary_at_consistent_threshold_is_still_consistent() -> None:
    # 3685 * 1.005 = 3703.425 -> exactly 0.5% away.
    classification, diff = _classify(quote="3685.0", candle="3703.425", chain="3685.0")
    assert diff == MAX_CONSISTENT
    assert classification == DataConsistency.CONSISTENT


def test_stale_candle_is_not_treated_as_live_inconsistency() -> None:
    """Live quote + live chain agree; previous-session M15 close diverges.
    That is a freshness fact, not INCONSISTENT live-vs-live data."""
    result = classify_price_consistency(
        quote_price=Decimal("365"), quote_timestamp=T,
        candle_price=Decimal("350"), candle_timestamp=T,
        option_chain_price=Decimal("365"), option_chain_timestamp=T,
        futures_price=Decimal("366"), futures_timestamp=T,
        max_consistent_diff_pct=MAX_CONSISTENT, max_partially_aligned_diff_pct=MAX_PARTIAL,
        candle_is_current=False,
    )
    assert result.candle_excluded_as_stale is True
    assert result.classification == DataConsistency.CONSISTENT
    labeled = {p.source: p.price for p in result.prices}
    assert labeled[PriceSource.LATEST_CANDLE] == Decimal("350")


def test_stale_quote_is_excluded_from_live_vs_live_comparison() -> None:
    result = classify_price_consistency(
        quote_price=Decimal("365"), quote_timestamp=T,
        candle_price=Decimal("350"), candle_timestamp=T,
        option_chain_price=Decimal("350"), option_chain_timestamp=T,
        futures_price=None, futures_timestamp=None,
        max_consistent_diff_pct=MAX_CONSISTENT, max_partially_aligned_diff_pct=MAX_PARTIAL,
        candle_is_current=True, quote_is_current=False, chain_is_current=True,
    )
    assert result.classification == DataConsistency.CONSISTENT
    labeled = {p.source: p.price for p in result.prices}
    assert labeled[PriceSource.LATEST_QUOTE] == Decimal("365")


def test_stale_chain_is_excluded_from_live_vs_live_comparison() -> None:
    result = classify_price_consistency(
        quote_price=Decimal("365"), quote_timestamp=T,
        candle_price=Decimal("365"), candle_timestamp=T,
        option_chain_price=Decimal("350"), option_chain_timestamp=T,
        futures_price=None, futures_timestamp=None,
        max_consistent_diff_pct=MAX_CONSISTENT, max_partially_aligned_diff_pct=MAX_PARTIAL,
        candle_is_current=True, quote_is_current=True, chain_is_current=False,
    )
    assert result.classification == DataConsistency.CONSISTENT
    labeled = {p.source: p.price for p in result.prices}
    assert labeled[PriceSource.OPTION_CHAIN_REFERENCE] == Decimal("350")


def test_live_quote_vs_live_chain_disagreement_is_inconsistent() -> None:
    result = classify_price_consistency(
        quote_price=Decimal("365"), quote_timestamp=T,
        candle_price=Decimal("365"), candle_timestamp=T,
        option_chain_price=Decimal("350"), option_chain_timestamp=T,
        futures_price=None, futures_timestamp=None,
        max_consistent_diff_pct=MAX_CONSISTENT, max_partially_aligned_diff_pct=MAX_PARTIAL,
        candle_is_current=True,
    )
    assert result.classification == DataConsistency.INCONSISTENT
    assert result.candle_excluded_as_stale is False


def test_boundary_at_partial_threshold_is_still_partially_aligned() -> None:
    # 3685 * 1.02 = 3758.7 -> exactly 2.0% away.
    classification, diff = _classify(quote="3685.0", candle="3758.7", chain="3685.0")
    assert diff == MAX_PARTIAL
    assert classification == DataConsistency.PARTIALLY_ALIGNED


def test_user_labels_distinguish_live_quote_from_last_completed_candle() -> None:
    assert price_source_user_label(PriceSource.LATEST_QUOTE) == "LIVE QUOTE"
    assert price_source_user_label(PriceSource.LATEST_CANDLE) == "LAST COMPLETED CANDLE"
    assert PRICE_SOURCE_USER_LABEL[PriceSource.LATEST_QUOTE] != PRICE_SOURCE_USER_LABEL[PriceSource.LATEST_CANDLE]
