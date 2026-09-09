from __future__ import annotations

from decimal import Decimal

from app.domain.market.models import OptionRight
from app.domain.options.query_parser import parse_instrument_query


def test_symbol_only() -> None:
    result = parse_instrument_query("KAYNES")
    assert result.symbol == "KAYNES"
    assert result.strike is None and result.right is None and result.expiry_hint is None
    assert result.errors == []
    assert result.has_specific_contract is False


def test_symbol_strike_and_right() -> None:
    result = parse_instrument_query("KAYNES 4000 CE")
    assert result.symbol == "KAYNES"
    assert result.strike == Decimal("4000")
    assert result.right == OptionRight.CE
    assert result.errors == []
    assert result.has_specific_contract is True


def test_symbol_strike_right_and_expiry_hint() -> None:
    result = parse_instrument_query("KAYNES 4000 CE SEP")
    assert result.expiry_hint == "SEP"
    assert result.strike == Decimal("4000")
    assert result.right == OptionRight.CE


def test_case_insensitive() -> None:
    result = parse_instrument_query("kaynes 4000 ce")
    assert result.symbol == "KAYNES"
    assert result.right == OptionRight.CE


def test_put_option() -> None:
    result = parse_instrument_query("BANKNIFTY 58000 PE")
    assert result.symbol == "BANKNIFTY"
    assert result.strike == Decimal("58000")
    assert result.right == OptionRight.PE


def test_decimal_strike() -> None:
    result = parse_instrument_query("RELIANCE 1400.5 CE")
    assert result.strike == Decimal("1400.5")


def test_empty_query_is_an_error() -> None:
    result = parse_instrument_query("   ")
    assert result.symbol is None
    assert result.errors == ["empty query"]


def test_strike_without_right_is_flagged() -> None:
    result = parse_instrument_query("KAYNES 4000")
    assert result.strike == Decimal("4000")
    assert result.right is None
    assert any("without CE/PE" in e for e in result.errors)
    assert result.has_specific_contract is False


def test_right_without_strike_is_flagged() -> None:
    result = parse_instrument_query("KAYNES CE")
    assert result.right == OptionRight.CE
    assert result.strike is None
    assert any("without a strike" in e for e in result.errors)


def test_duplicate_right_tokens_flagged() -> None:
    result = parse_instrument_query("KAYNES 4000 CE PE")
    assert any("multiple option-right" in e for e in result.errors)


def test_duplicate_strike_tokens_flagged() -> None:
    result = parse_instrument_query("KAYNES 4000 4100 CE")
    assert any("multiple strike-like" in e for e in result.errors)


def test_duplicate_expiry_hint_flagged() -> None:
    result = parse_instrument_query("KAYNES 4000 CE SEP OCT")
    assert any("multiple expiry hints" in e for e in result.errors)


def test_unrecognized_token_flagged() -> None:
    result = parse_instrument_query("KAYNES XYZ")
    assert any("unrecognized token" in e for e in result.errors)


def test_non_positive_strike_flagged() -> None:
    result = parse_instrument_query("KAYNES -100 CE")
    assert any("must be positive" in e for e in result.errors)


def test_never_fabricates_a_symbol_when_none_given() -> None:
    # A pure-numeric-only query has no symbol token to use -- the FIRST
    # token becomes the symbol unconditionally (matching every real
    # example in the spec), even if it looks like it could be a strike;
    # this module never second-guesses word order.
    result = parse_instrument_query("4000 CE")
    assert result.symbol == "4000"
    assert result.strike is None
    assert result.right == OptionRight.CE


def test_order_independent_beyond_the_first_token() -> None:
    result = parse_instrument_query("NIFTY CE 25000")
    assert result.symbol == "NIFTY"
    assert result.right == OptionRight.CE
    assert result.strike == Decimal("25000")
