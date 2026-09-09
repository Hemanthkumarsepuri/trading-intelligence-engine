"""Sprint 7B, Objective 1 -- pure unit tests for `classify_macro_regime()`."""

from __future__ import annotations

from decimal import Decimal

from app.domain.options.market_regime_context import MacroRegime, classify_macro_regime

_MEANINGFUL = Decimal("0.3")


def test_insufficient_data_when_everything_unavailable() -> None:
    result = classify_macro_regime(
        nifty_day_change_pct=None, bank_nifty_day_change_pct=None, india_vix_day_change_pct=None,
        crude_oil_day_change_pct=None, usdinr_day_change_pct=None, meaningful_move_pct=_MEANINGFUL,
    )
    assert result.regime == MacroRegime.INSUFFICIENT_DATA


def test_transition_when_all_available_but_none_meaningful() -> None:
    result = classify_macro_regime(
        nifty_day_change_pct=Decimal("0.05"), bank_nifty_day_change_pct=Decimal("0.02"), india_vix_day_change_pct=Decimal("0.1"),
        crude_oil_day_change_pct=Decimal("0.1"), usdinr_day_change_pct=Decimal("0.05"), meaningful_move_pct=_MEANINGFUL,
    )
    assert result.regime == MacroRegime.TRANSITION


def test_risk_off_reproduces_the_named_example() -> None:
    """NIFTY down, BANKNIFTY down, VIX up, crude up, USDINR up -> RISK_OFF."""
    result = classify_macro_regime(
        nifty_day_change_pct=Decimal("-1.2"), bank_nifty_day_change_pct=Decimal("-1.5"), india_vix_day_change_pct=Decimal("8.0"),
        crude_oil_day_change_pct=Decimal("2.0"), usdinr_day_change_pct=Decimal("0.6"), meaningful_move_pct=_MEANINGFUL,
    )
    assert result.regime == MacroRegime.RISK_OFF


def test_risk_on_when_all_contributing_inputs_agree() -> None:
    result = classify_macro_regime(
        nifty_day_change_pct=Decimal("1.2"), bank_nifty_day_change_pct=Decimal("1.5"), india_vix_day_change_pct=Decimal("-6.0"),
        crude_oil_day_change_pct=Decimal("-1.0"), usdinr_day_change_pct=Decimal("-0.4"), meaningful_move_pct=_MEANINGFUL,
    )
    assert result.regime == MacroRegime.RISK_ON


def test_mixed_when_inputs_disagree() -> None:
    result = classify_macro_regime(
        nifty_day_change_pct=Decimal("1.0"), bank_nifty_day_change_pct=Decimal("-1.0"), india_vix_day_change_pct=None,
        crude_oil_day_change_pct=None, usdinr_day_change_pct=None, meaningful_move_pct=_MEANINGFUL,
    )
    assert result.regime == MacroRegime.MIXED


def test_usdinr_genuinely_votes_here_unlike_global_context() -> None:
    """The real, deliberate difference from `global_context.py`: a
    meaningful, isolated USD/INR move alone is enough to produce a real
    regime read here."""
    result = classify_macro_regime(
        nifty_day_change_pct=None, bank_nifty_day_change_pct=None, india_vix_day_change_pct=None,
        crude_oil_day_change_pct=None, usdinr_day_change_pct=Decimal("1.0"), meaningful_move_pct=_MEANINGFUL,
    )
    assert result.regime == MacroRegime.RISK_OFF
    usdinr_input = next(i for i in result.inputs if i.label == "USD/INR")
    assert usdinr_input.contributes is True


def test_single_input_never_decisive_when_others_disagree() -> None:
    """A single strong risk-off vote does not overwhelm two risk-on votes
    -- MIXED, not a forced regime."""
    result = classify_macro_regime(
        nifty_day_change_pct=Decimal("2.0"), bank_nifty_day_change_pct=Decimal("2.0"), india_vix_day_change_pct=Decimal("20.0"),
        crude_oil_day_change_pct=None, usdinr_day_change_pct=None, meaningful_move_pct=_MEANINGFUL,
    )
    assert result.regime == MacroRegime.MIXED


def test_every_input_reported_even_when_not_contributing() -> None:
    result = classify_macro_regime(
        nifty_day_change_pct=Decimal("1.0"), bank_nifty_day_change_pct=None, india_vix_day_change_pct=Decimal("0.05"),
        crude_oil_day_change_pct=None, usdinr_day_change_pct=None, meaningful_move_pct=_MEANINGFUL,
    )
    assert len(result.inputs) == 5
    labels = {i.label for i in result.inputs}
    assert labels == {"NIFTY 50", "Nifty Bank", "India VIX", "MCX Crude Oil", "USD/INR"}
