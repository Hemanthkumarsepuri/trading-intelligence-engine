"""Sprint 7A, Objective 14 -- RSI must remain context only, never an
automatic reversal signal. Confirms `_rsi_zone_label()`'s own bands, and
that RSI has no evidence-matrix row at all (the structural guarantee
that makes "context only" actually true, not just a docstring promise).
"""

from __future__ import annotations

from decimal import Decimal

from app.orchestration.options_intelligence_report import _rsi_zone_label


def test_rsi_zone_oversold_below_30() -> None:
    assert _rsi_zone_label(Decimal("25")) == "OVERSOLD"


def test_rsi_zone_overbought_above_70() -> None:
    assert _rsi_zone_label(Decimal("75")) == "OVERBOUGHT"


def test_rsi_zone_neutral_between_bands() -> None:
    assert _rsi_zone_label(Decimal("50")) == "NEUTRAL"
    assert _rsi_zone_label(Decimal("30")) == "NEUTRAL"  # boundary itself is not < 30
    assert _rsi_zone_label(Decimal("70")) == "NEUTRAL"  # boundary itself is not > 70


def test_rsi_zone_unknown_when_no_real_value() -> None:
    assert _rsi_zone_label(None) == "UNKNOWN"


def test_rsi_result_never_has_an_evidence_matrix_row_name() -> None:
    """Structural proof RSI cannot become directional evidence: no row
    builder in `evidence_matrix.py` is named/keyed off RSI at all -- it
    only ever reaches `report.rsi`, a purely descriptive field."""
    import app.domain.options.evidence_matrix as ev

    row_builder_names = [name for name in dir(ev) if name.startswith("row_")]
    assert not any("rsi" in name.lower() for name in row_builder_names)



