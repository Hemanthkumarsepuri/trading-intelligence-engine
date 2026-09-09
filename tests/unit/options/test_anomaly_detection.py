from __future__ import annotations

from decimal import Decimal

from app.domain.options.anomaly_detection import AnomalyVerdict, compute_baseline, detect_anomaly

_MIN_SAMPLE = 5
_ELEVATED = Decimal("2.0")
_UNUSUAL = Decimal("4.0")


def test_compute_baseline_empty_list() -> None:
    baseline = compute_baseline([])
    assert baseline.sample_count == 0
    assert baseline.mean is None


def test_compute_baseline_real_values() -> None:
    baseline = compute_baseline([Decimal("10"), Decimal("20"), Decimal("30")])
    assert baseline.sample_count == 3
    assert baseline.mean == Decimal("20")
    assert baseline.minimum == Decimal("10")
    assert baseline.maximum == Decimal("30")


def test_insufficient_history_when_current_value_missing() -> None:
    result = detect_anomaly(
        metric_name="volume", current_value=None, historical_values=[Decimal(x) for x in range(1, 10)],
        min_sample_size=_MIN_SAMPLE, elevated_ratio=_ELEVATED, unusual_ratio=_UNUSUAL,
    )
    assert result.verdict == AnomalyVerdict.INSUFFICIENT_HISTORY


def test_insufficient_history_when_sample_too_small() -> None:
    result = detect_anomaly(
        metric_name="volume", current_value=Decimal("1000"), historical_values=[Decimal("100"), Decimal("200")],
        min_sample_size=_MIN_SAMPLE, elevated_ratio=_ELEVATED, unusual_ratio=_UNUSUAL,
    )
    assert result.verdict == AnomalyVerdict.INSUFFICIENT_HISTORY
    assert "need >=" in result.detail


def test_insufficient_history_never_fabricates_a_baseline() -> None:
    # Exactly one below the floor -- must not silently proceed.
    history = [Decimal("100")] * (_MIN_SAMPLE - 1)
    result = detect_anomaly(
        metric_name="volume", current_value=Decimal("100000"), historical_values=history,
        min_sample_size=_MIN_SAMPLE, elevated_ratio=_ELEVATED, unusual_ratio=_UNUSUAL,
    )
    assert result.verdict == AnomalyVerdict.INSUFFICIENT_HISTORY


def test_normal_when_close_to_baseline_mean() -> None:
    history = [Decimal("100")] * _MIN_SAMPLE
    result = detect_anomaly(
        metric_name="volume", current_value=Decimal("110"), historical_values=history,
        min_sample_size=_MIN_SAMPLE, elevated_ratio=_ELEVATED, unusual_ratio=_UNUSUAL,
    )
    assert result.verdict == AnomalyVerdict.NORMAL
    assert result.deviation_ratio == Decimal("1.1")


def test_elevated_when_between_thresholds() -> None:
    history = [Decimal("100")] * _MIN_SAMPLE
    result = detect_anomaly(
        metric_name="volume", current_value=Decimal("250"), historical_values=history,
        min_sample_size=_MIN_SAMPLE, elevated_ratio=_ELEVATED, unusual_ratio=_UNUSUAL,
    )
    assert result.verdict == AnomalyVerdict.ELEVATED


def test_unusual_when_far_above_baseline() -> None:
    history = [Decimal("100")] * _MIN_SAMPLE
    result = detect_anomaly(
        metric_name="volume", current_value=Decimal("500"), historical_values=history,
        min_sample_size=_MIN_SAMPLE, elevated_ratio=_ELEVATED, unusual_ratio=_UNUSUAL,
    )
    assert result.verdict == AnomalyVerdict.UNUSUAL
    assert result.deviation_ratio == Decimal("5.0")


def test_zero_mean_baseline_returns_insufficient_history_not_a_crash() -> None:
    history = [Decimal("0")] * _MIN_SAMPLE
    result = detect_anomaly(
        metric_name="oi_change", current_value=Decimal("50"), historical_values=history,
        min_sample_size=_MIN_SAMPLE, elevated_ratio=_ELEVATED, unusual_ratio=_UNUSUAL,
    )
    assert result.verdict == AnomalyVerdict.INSUFFICIENT_HISTORY
    assert "ratio undefined" in result.detail
