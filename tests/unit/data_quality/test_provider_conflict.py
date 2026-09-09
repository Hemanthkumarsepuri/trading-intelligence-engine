from __future__ import annotations

from decimal import Decimal

from app.domain.data_quality.provider_conflict import (
    PROVIDER_CONFLICT,
    PROVIDER_DISAGREEMENT,
    classify_provider_conflict,
)


def test_provider_conflict_and_disagreement_are_the_same_event() -> None:
    assert PROVIDER_DISAGREEMENT == PROVIDER_CONFLICT == "PROVIDER_CONFLICT"


def test_agreement_within_tolerance_is_not_a_conflict() -> None:
    event = classify_provider_conflict(
        field="ltp",
        provider_a="upstox",
        value_a=Decimal("100.0"),
        provider_b="fivepaisa",
        value_b=Decimal("100.4"),
        tolerance=Decimal("0.005"),
    )
    assert event is None


def test_disagreement_beyond_tolerance_is_provider_conflict_not_an_average() -> None:
    event = classify_provider_conflict(
        field="ltp",
        provider_a="upstox",
        value_a=Decimal("100.0"),
        provider_b="fivepaisa",
        value_b=Decimal("102.0"),
        tolerance=Decimal("0.005"),
    )
    assert event is not None
    assert event.event == PROVIDER_CONFLICT
    assert "101" not in event.value_a  # never averaged
