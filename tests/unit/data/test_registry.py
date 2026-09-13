from __future__ import annotations

from datetime import UTC, datetime

from app.data.providers.base import ProviderRegistry
from app.data.providers.health import ProviderHealthTracker


class _FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name


def test_registry_orders_by_priority() -> None:
    registry: ProviderRegistry[_FakeProvider] = ProviderRegistry()
    primary = _FakeProvider("primary")
    secondary = _FakeProvider("secondary")
    registry.register(secondary, priority=10)
    registry.register(primary, priority=0)

    assert registry.all_providers() == [primary, secondary]
    assert registry.available() == [primary, secondary]


def test_registry_excludes_provider_with_open_circuit() -> None:
    registry: ProviderRegistry[_FakeProvider] = ProviderRegistry()
    failing = _FakeProvider("failing")
    healthy = _FakeProvider("healthy")
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    failing_tracker = ProviderHealthTracker(provider="failing", failure_threshold=1)
    failing_tracker.record_failure(at=t0, error="down")
    registry.register(failing, priority=0, health_tracker=failing_tracker)
    registry.register(healthy, priority=1)

    assert registry.available(at=t0) == [healthy]


def test_registry_trackers_keyed_by_provider_name() -> None:
    registry: ProviderRegistry[_FakeProvider] = ProviderRegistry()
    provider = _FakeProvider("dhan")
    registry.register(provider, priority=0)

    trackers = registry.trackers()
    assert "dhan" in trackers
