"""IPO Intelligence -- query intent routing. Critically must never route
an options query (e.g. "KAYNES 4000 CE") into the IPO parser, and vice
versa -- see Part 28's explicit non-coupling requirement.
"""

from __future__ import annotations

from app.domain.ipo.query_parser import IPOIntentKind, is_ipo_query, parse_ipo_query
from app.domain.options.query_parser import parse_instrument_query


def test_options_query_is_never_treated_as_an_ipo_query() -> None:
    assert is_ipo_query("KAYNES 4000 CE") is False
    assert is_ipo_query("RELIANCE") is False
    assert is_ipo_query("NIFTY 25000 PE") is False


def test_options_parser_is_unaffected_by_this_module_existing() -> None:
    parsed = parse_instrument_query("KAYNES 4000 CE")
    assert parsed.symbol == "KAYNES"
    assert parsed.has_specific_contract is True


def test_ipo_today_is_detected() -> None:
    assert is_ipo_query("IPO today") is True
    intent = parse_ipo_query("IPO today")
    assert intent.intent == IPOIntentKind.TODAY_OPEN


def test_todays_ipos_is_detected() -> None:
    assert is_ipo_query("Today's IPOs") is True


def test_which_ipo_should_i_apply_is_general_or_today() -> None:
    intent = parse_ipo_query("Which IPO should I apply?")
    assert intent.intent in (IPOIntentKind.GENERAL, IPOIntentKind.TODAY_OPEN)


def test_best_ipo_for_listing() -> None:
    intent = parse_ipo_query("Best IPO for listing")
    assert intent.intent == IPOIntentKind.BEST_LISTING


def test_highest_allotment_chance() -> None:
    intent = parse_ipo_query("Which IPO has highest allotment chance?")
    assert intent.intent == IPOIntentKind.BEST_ALLOTMENT


def test_hidden_potential() -> None:
    intent = parse_ipo_query("Which IPO has hidden potential?")
    assert intent.intent == IPOIntentKind.HIDDEN_OPPORTUNITY


def test_ipo_gmp_today() -> None:
    intent = parse_ipo_query("IPO GMP today")
    assert intent.intent == IPOIntentKind.GMP_LOOKUP


def test_jio_ipo_extracts_company_hint() -> None:
    intent = parse_ipo_query("Jio IPO")
    assert intent.intent == IPOIntentKind.COMPANY_LOOKUP
    assert intent.company_hint == "JIO"


def test_can_i_get_jio_ipo() -> None:
    intent = parse_ipo_query("Can I get Jio IPO?")
    assert is_ipo_query("Can I get Jio IPO?") is True
    assert intent.company_hint == "JIO"


def test_shareholder_quota_query() -> None:
    intent = parse_ipo_query("Do Reliance shareholders get Jio IPO quota?")
    assert intent.intent == IPOIntentKind.SHAREHOLDER_QUOTA_LOOKUP
    assert "RELIANCE" in intent.mentions
    assert "JIO" in intent.mentions


def test_kaynes_ipo_extracts_company_hint_and_does_not_collide_with_options() -> None:
    intent = parse_ipo_query("KAYNES IPO")
    assert intent.company_hint == "KAYNES"
    # And the bare options query for the SAME symbol is completely unaffected:
    assert is_ipo_query("KAYNES 4000 CE") is False


def test_bare_followup_with_no_ipo_token_is_honestly_not_detected() -> None:
    """Documented limitation (see module docstring): a stateless
    follow-up with no "IPO" token and no company name cannot be
    identified in isolation -- this test locks in that honest behavior
    rather than silently asserting a guess."""
    assert is_ipo_query("How much chance do I have?") is False
