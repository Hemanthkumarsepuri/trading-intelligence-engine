from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.data.providers.health import CircuitState, HealthStatus, ProviderHealthTracker


def test_starts_closed_and_up() -> None:
    tracker = ProviderHealthTracker(provider="x")
    assert tracker.circuit_state == CircuitState.CLOSED
    assert tracker.should_attempt(at=datetime(2026, 8, 27, 10, 0, tzinfo=UTC)) is True


def test_opens_after_failure_threshold() -> None:
    tracker = ProviderHealthTracker(provider="x", failure_threshold=3, half_open_after=timedelta(seconds=30))
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    for i in range(3):
        tracker.record_failure(at=t0 + timedelta(seconds=i), error="boom")
    assert tracker.circuit_state == CircuitState.OPEN
    assert tracker.should_attempt(at=t0 + timedelta(seconds=3)) is False
    snapshot = tracker.snapshot()
    assert snapshot.status == HealthStatus.DOWN
    assert snapshot.consecutive_failures == 3
    assert snapshot.circuit_state == CircuitState.OPEN


def test_half_opens_after_cooldown_and_closes_on_success() -> None:
    tracker = ProviderHealthTracker(provider="x", failure_threshold=1, half_open_after=timedelta(seconds=10))
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    tracker.record_failure(at=t0, error="boom")
    state_after_open: CircuitState = tracker.circuit_state
    assert state_after_open == CircuitState.OPEN
    assert tracker.should_attempt(at=t0 + timedelta(seconds=5)) is False
    assert tracker.should_attempt(at=t0 + timedelta(seconds=11)) is True
    state_after_probe: CircuitState = tracker.circuit_state
    assert state_after_probe == CircuitState.HALF_OPEN
    tracker.record_success(at=t0 + timedelta(seconds=11), latency_ms=5.0)
    assert tracker.circuit_state == CircuitState.CLOSED


def test_half_open_probe_failure_reopens() -> None:
    tracker = ProviderHealthTracker(provider="x", failure_threshold=1, half_open_after=timedelta(seconds=10))
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    tracker.record_failure(at=t0, error="boom")
    tracker.should_attempt(at=t0 + timedelta(seconds=11))
    state_after_probe: CircuitState = tracker.circuit_state
    assert state_after_probe == CircuitState.HALF_OPEN
    tracker.record_failure(at=t0 + timedelta(seconds=11), error="still down")
    state_after_reopen: CircuitState = tracker.circuit_state
    assert state_after_reopen == CircuitState.OPEN


def test_success_resets_consecutive_failures() -> None:
    tracker = ProviderHealthTracker(provider="x", failure_threshold=3)
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    tracker.record_failure(at=t0, error="boom")
    tracker.record_success(at=t0 + timedelta(seconds=1), latency_ms=1.0)
    snapshot = tracker.snapshot()
    assert snapshot.consecutive_failures == 0
    assert snapshot.status == HealthStatus.UP
