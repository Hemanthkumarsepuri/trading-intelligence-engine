"""Sprint 7B, Objectives 2/3 -- pure unit tests for geopolitical/macro
transmission analysis. No hardcoded current event, no sentiment.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.news.event_classification import NewsEventCategory
from app.domain.news.geopolitical_transmission import (
    TransmissionChannel,
    build_transmission_note,
    classify_transmission_channels,
)

MEANINGFUL = Decimal("0.3")


def test_oil_channel_matched_from_real_keywords() -> None:
    channels = classify_transmission_channels("Middle East escalation raises crude supply risk", None)
    assert TransmissionChannel.OIL in channels
    assert TransmissionChannel.GLOBAL_RISK in channels


def test_unknown_channel_when_no_keywords_match() -> None:
    assert classify_transmission_channels("Company X unveils new logo", None) == [TransmissionChannel.UNKNOWN]


def test_none_for_a_non_macro_non_geopolitical_category() -> None:
    """This module never runs transmission analysis on an EARNINGS/ORDER/
    etc. headline -- no real macro-transmission story to tell."""
    note = build_transmission_note(
        title="Company X posts Q2 results", summary=None, category=NewsEventCategory.EARNINGS,
        crude_day_change_pct=Decimal("2.0"), usdinr_day_change_pct=None, vix_day_change_pct=None,
        meaningful_move_pct=MEANINGFUL,
    )
    assert note is None


def test_oil_transmission_reproduces_the_named_example() -> None:
    """The prompt's own example: Middle East escalation -> crude supply
    risk -> higher crude -- built from real, already-fetched crude
    day-change, never a hardcoded scenario."""
    note = build_transmission_note(
        title="Middle East tension escalates, crude supply risk rises", summary=None, category=NewsEventCategory.GEOPOLITICAL,
        crude_day_change_pct=Decimal("3.5"), usdinr_day_change_pct=Decimal("0.6"), vix_day_change_pct=Decimal("10.0"),
        meaningful_move_pct=MEANINGFUL,
    )
    assert note is not None
    assert note.direction in ("POSITIVE", "NEGATIVE")  # a real, non-UNKNOWN read given real, meaningful crude data
    assert note.sector_exposure == "UNKNOWN"
    assert note.company_exposure == "UNKNOWN"


def test_direction_positive_when_market_variable_rose() -> None:
    note = build_transmission_note(
        title="Crude oil price jumps on supply concerns", summary=None, category=NewsEventCategory.MACRO,
        crude_day_change_pct=Decimal("2.5"), usdinr_day_change_pct=None, vix_day_change_pct=None,
        meaningful_move_pct=MEANINGFUL,
    )
    assert note is not None
    assert note.direction == "POSITIVE"
    assert note.affected_market_variable == "MCX Crude Oil"


def test_direction_unknown_when_no_real_series_wired_for_matched_channel() -> None:
    """A real channel match (SHIPPING) with no corresponding real macro
    series fetched by this system -> honestly UNKNOWN, never fabricated."""
    note = build_transmission_note(
        title="Red Sea shipping disruption continues", summary=None, category=NewsEventCategory.GEOPOLITICAL,
        crude_day_change_pct=None, usdinr_day_change_pct=None, vix_day_change_pct=None,
        meaningful_move_pct=MEANINGFUL,
    )
    assert note is not None
    assert note.affected_market_variable is None
    assert note.direction == "UNKNOWN"


def test_direction_mixed_when_real_move_is_not_meaningful() -> None:
    note = build_transmission_note(
        title="Crude oil steady amid geopolitical tension", summary=None, category=NewsEventCategory.GEOPOLITICAL,
        crude_day_change_pct=Decimal("0.05"), usdinr_day_change_pct=None, vix_day_change_pct=None,
        meaningful_move_pct=MEANINGFUL,
    )
    assert note is not None
    assert note.direction == "MIXED"


def test_never_infers_company_exposure_from_company_name() -> None:
    """Sprint 7B, Objective 3 -- `build_transmission_note()` takes no
    company-name/symbol argument at all, so `company_exposure` cannot be
    inferred from a name (e.g. an oil-sounding company name) even if the
    caller tried to pass one -- structurally impossible, not just
    unexercised."""
    import inspect

    params = inspect.signature(build_transmission_note).parameters
    for forbidden in ("company_name", "symbol", "stock_name", "instrument"):
        assert forbidden not in params
    # Always UNKNOWN today -- no authorized company-exposure metadata
    # source exists, regardless of how oil/export/import-sensitive the
    # underlying's real business happens to be.
    note = build_transmission_note(
        title="Middle East tension escalates, crude supply risk rises", summary=None, category=NewsEventCategory.GEOPOLITICAL,
        crude_day_change_pct=Decimal("3.5"), usdinr_day_change_pct=None, vix_day_change_pct=None,
        meaningful_move_pct=MEANINGFUL,
    )
    assert note is not None
    assert note.company_exposure == "UNKNOWN"


def test_never_hardcodes_a_specific_geopolitical_event() -> None:
    """Structural proof: no source line in this module contains a
    specific real-world proper noun (e.g. a country/conflict name) --
    only generic keyword categories."""
    import inspect

    import app.domain.news.geopolitical_transmission as mod

    source = inspect.getsource(mod)
    for forbidden in ("Iran", "Israel", "Gaza", "Ukraine", "Ukraine war", "Hamas"):
        assert forbidden not in source
