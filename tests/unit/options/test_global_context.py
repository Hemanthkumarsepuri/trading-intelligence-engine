from __future__ import annotations

from decimal import Decimal

from app.domain.options.global_context import GlobalContextVerdict, assess_global_context

_MEANINGFUL = Decimal("0.3")


def test_insufficient_data_when_everything_unavailable() -> None:
    result = assess_global_context(
        nifty_day_change_pct=None, bank_nifty_day_change_pct=None, india_vix_day_change_pct=None,
        meaningful_move_pct=_MEANINGFUL,
    )
    assert result.verdict == GlobalContextVerdict.INSUFFICIENT_DATA


def test_low_relevance_when_all_moves_below_floor() -> None:
    result = assess_global_context(
        nifty_day_change_pct=Decimal("0.05"), bank_nifty_day_change_pct=Decimal("0.02"),
        india_vix_day_change_pct=Decimal("0.1"), meaningful_move_pct=_MEANINGFUL,
    )
    assert result.verdict == GlobalContextVerdict.LOW_RELEVANCE


def test_tailwind_when_nifty_up_and_vix_down() -> None:
    result = assess_global_context(
        nifty_day_change_pct=Decimal("1.0"), bank_nifty_day_change_pct=Decimal("1.2"),
        india_vix_day_change_pct=Decimal("-2.0"), meaningful_move_pct=_MEANINGFUL,
    )
    assert result.verdict == GlobalContextVerdict.TAILWIND


def test_headwind_when_nifty_down_and_vix_up() -> None:
    result = assess_global_context(
        nifty_day_change_pct=Decimal("-1.0"), bank_nifty_day_change_pct=Decimal("-1.2"),
        india_vix_day_change_pct=Decimal("3.0"), meaningful_move_pct=_MEANINGFUL,
    )
    assert result.verdict == GlobalContextVerdict.HEADWIND


def test_mixed_when_inputs_disagree() -> None:
    result = assess_global_context(
        nifty_day_change_pct=Decimal("1.0"), bank_nifty_day_change_pct=Decimal("-1.0"),
        india_vix_day_change_pct=None, meaningful_move_pct=_MEANINGFUL,
    )
    assert result.verdict == GlobalContextVerdict.MIXED


def test_crude_oil_rising_is_a_headwind_contribution() -> None:
    result = assess_global_context(
        nifty_day_change_pct=None, bank_nifty_day_change_pct=None, india_vix_day_change_pct=None,
        crude_oil_day_change_pct=Decimal("2.0"), meaningful_move_pct=_MEANINGFUL,
    )
    assert result.verdict == GlobalContextVerdict.HEADWIND


def test_usdinr_never_contributes_to_the_verdict() -> None:
    result = assess_global_context(
        nifty_day_change_pct=None, bank_nifty_day_change_pct=None, india_vix_day_change_pct=None,
        usdinr_day_change_pct=Decimal("5.0"),  # a huge move -- must still never vote
        meaningful_move_pct=_MEANINGFUL,
    )
    assert result.verdict == GlobalContextVerdict.INSUFFICIENT_DATA
    usdinr_input = next(i for i in result.inputs if i.label == "USD/INR")
    assert usdinr_input.contributes_to_verdict is False


def test_usdinr_reported_even_though_it_never_votes() -> None:
    result = assess_global_context(
        nifty_day_change_pct=Decimal("1.0"), bank_nifty_day_change_pct=Decimal("1.0"),
        india_vix_day_change_pct=Decimal("-1.0"), usdinr_day_change_pct=Decimal("0.4"),
        meaningful_move_pct=_MEANINGFUL,
    )
    usdinr_input = next(i for i in result.inputs if i.label == "USD/INR")
    assert usdinr_input.day_change_pct == Decimal("0.4")
    assert usdinr_input.contributes_to_verdict is False


# -- Sprint 7A, Objective 2: invalid provider value quarantine ------------


def test_implausible_nifty_move_is_quarantined_not_voted() -> None:
    """The real named example: an impossible magnitude (here modeling the
    same class of defect as "-100% USD/INR") must never become
    directional evidence, even for an instrument that would otherwise
    vote."""
    result = assess_global_context(
        nifty_day_change_pct=Decimal("-100.0"), bank_nifty_day_change_pct=None, india_vix_day_change_pct=None,
        meaningful_move_pct=_MEANINGFUL,
    )
    nifty_input = next(i for i in result.inputs if i.label == "NIFTY 50")
    assert nifty_input.contributes_to_verdict is False
    assert "INVALID PROVIDER VALUE" in nifty_input.detail
    assert nifty_input.day_change_pct == Decimal("-100.0")  # the real, impossible value is still shown, never replaced
    assert result.verdict == GlobalContextVerdict.INSUFFICIENT_DATA


def test_implausible_usdinr_move_is_labeled_invalid() -> None:
    """The prompt's own named scenario: -100% USD/INR."""
    result = assess_global_context(
        nifty_day_change_pct=None, bank_nifty_day_change_pct=None, india_vix_day_change_pct=None,
        usdinr_day_change_pct=Decimal("-100.0"), meaningful_move_pct=_MEANINGFUL,
    )
    usdinr_input = next(i for i in result.inputs if i.label == "USD/INR")
    assert "INVALID PROVIDER VALUE" in usdinr_input.detail
    assert usdinr_input.contributes_to_verdict is False


def test_plausible_large_move_still_votes_normally() -> None:
    """A real, large-but-plausible move (well under the implausibility
    ceiling) must still vote -- the quarantine must not become an
    over-broad filter on genuinely large real moves."""
    result = assess_global_context(
        nifty_day_change_pct=Decimal("-5.0"), bank_nifty_day_change_pct=Decimal("-5.5"), india_vix_day_change_pct=Decimal("15.0"),
        meaningful_move_pct=_MEANINGFUL,
    )
    assert result.verdict == GlobalContextVerdict.HEADWIND
    nifty_input = next(i for i in result.inputs if i.label == "NIFTY 50")
    assert nifty_input.contributes_to_verdict is True
