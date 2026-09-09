"""Sprint 8, Objective P1 -- pure unit tests for sector classification and
3-level relative strength."""

from __future__ import annotations

from decimal import Decimal

from app.domain.options.sector_strength import (
    RelativeStrengthTier,
    SectorClassification,
    classify_sector,
    classify_sector_relative_strength,
    sector_index_trading_symbol,
)

_GAP = Decimal("0.5")
_SECTOR_MAP = {"TCS": "Information Technology", "RELIANCE": "Oil Gas & Consumable Fuels"}


def test_classify_sector_known_symbol() -> None:
    info = classify_sector("TCS", _SECTOR_MAP)
    assert info.classification == SectorClassification.KNOWN
    assert info.industry == "Information Technology"


def test_classify_sector_unknown_symbol_never_guessed_from_name() -> None:
    """A symbol not in the real NSE constituent file is honestly UNKNOWN
    -- never inferred from its own name (e.g. a hypothetical 'POWERGRID'
    string must not trigger a name-based guess)."""
    info = classify_sector("SOMEUNLISTEDSYMBOL", _SECTOR_MAP)
    assert info.classification == SectorClassification.UNKNOWN
    assert info.industry is None


def test_sector_index_symbol_mapped_for_known_industries() -> None:
    assert sector_index_trading_symbol("Information Technology") == "NIFTY IT"
    assert sector_index_trading_symbol("Financial Services") == "FINNIFTY"


def test_sector_index_symbol_unmapped_for_unmapped_industries() -> None:
    """'Power' deliberately has no dedicated index in the real mapping --
    honestly None, never a loose match to a nearby index."""
    assert sector_index_trading_symbol("Power") is None
    assert sector_index_trading_symbol(None) is None


def test_relative_strength_unknown_when_sector_unknown() -> None:
    result = classify_sector_relative_strength(
        sector_info=classify_sector("X", {}), stock_day_change_pct=Decimal("1.0"),
        sector_day_change_pct=Decimal("1.0"), market_day_change_pct=Decimal("1.0"), meaningful_gap_pct=_GAP,
    )
    assert result.tier == RelativeStrengthTier.UNKNOWN
    assert result.sector_day_change_pct is None  # never used even if a real value was passed in


def test_relative_strength_unknown_when_any_real_value_missing() -> None:
    info = classify_sector("TCS", _SECTOR_MAP)
    result = classify_sector_relative_strength(
        sector_info=info, stock_day_change_pct=None, sector_day_change_pct=Decimal("1.0"),
        market_day_change_pct=Decimal("1.0"), meaningful_gap_pct=_GAP,
    )
    assert result.tier == RelativeStrengthTier.UNKNOWN


def test_relative_strength_stock_leading() -> None:
    info = classify_sector("TCS", _SECTOR_MAP)
    result = classify_sector_relative_strength(
        sector_info=info, stock_day_change_pct=Decimal("3.8"), sector_day_change_pct=Decimal("1.7"),
        market_day_change_pct=Decimal("-0.6"), meaningful_gap_pct=_GAP,
    )
    assert result.tier == RelativeStrengthTier.STOCK_LEADING


def test_relative_strength_stock_lagging() -> None:
    info = classify_sector("TCS", _SECTOR_MAP)
    result = classify_sector_relative_strength(
        sector_info=info, stock_day_change_pct=Decimal("-2.0"), sector_day_change_pct=Decimal("0.5"),
        market_day_change_pct=Decimal("1.0"), meaningful_gap_pct=_GAP,
    )
    assert result.tier == RelativeStrengthTier.STOCK_LAGGING


def test_relative_strength_sector_leading_stock_inline() -> None:
    """Sector moved meaningfully more than market, but the stock itself
    barely differs from its own sector -- the stock is just riding a
    sector-wide move, not showing independent strength."""
    info = classify_sector("TCS", _SECTOR_MAP)
    result = classify_sector_relative_strength(
        sector_info=info, stock_day_change_pct=Decimal("2.1"), sector_day_change_pct=Decimal("2.0"),
        market_day_change_pct=Decimal("0.1"), meaningful_gap_pct=_GAP,
    )
    assert result.tier == RelativeStrengthTier.SECTOR_LEADING_STOCK_INLINE


def test_relative_strength_mixed_when_no_clear_leader() -> None:
    info = classify_sector("TCS", _SECTOR_MAP)
    result = classify_sector_relative_strength(
        sector_info=info, stock_day_change_pct=Decimal("0.1"), sector_day_change_pct=Decimal("0.2"),
        market_day_change_pct=Decimal("0.15"), meaningful_gap_pct=_GAP,
    )
    assert result.tier == RelativeStrengthTier.MIXED


def test_relative_strength_never_a_numeric_score() -> None:
    """Structural guarantee: the tier is an enum member, never a float/int."""
    info = classify_sector("TCS", _SECTOR_MAP)
    result = classify_sector_relative_strength(
        sector_info=info, stock_day_change_pct=Decimal("3.8"), sector_day_change_pct=Decimal("1.7"),
        market_day_change_pct=Decimal("-0.6"), meaningful_gap_pct=_GAP,
    )
    assert isinstance(result.tier, RelativeStrengthTier)
    assert not isinstance(result.tier, (int, float))
