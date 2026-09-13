"""Provider health tracking and circuit breaker.

Feeds the `provider_health` table (ARCHITECTURE.md §7) and the registry's
failover decision (§8). This module holds the decision logic only — writing
`ProviderHealth` rows to persistence is a separate concern (a future
`persistence.interfaces.ProviderHealthRepository`), so this class has no I/O
and is trivially unit-testable with fake clocks.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel

from app.utils.time import utc_now


class HealthStatus(str, Enum):
    UP = "UP"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ProviderHealth(BaseModel):
    provider: str
    status: HealthStatus
    checked_at: datetime
    latency_ms: float | None = None
    consecutive_failures: int = 0
    circuit_state: CircuitState = CircuitState.CLOSED
    last_error: str | None = None


class ProviderHealthTracker:
    """In-memory circuit breaker + health record for one provider.

    CLOSED: calls proceed normally.
    OPEN: calls are refused (`should_attempt` returns False) until
        `half_open_after` has elapsed since the circuit opened.
    HALF_OPEN: exactly one probe call is allowed through; its outcome decides
        whether the circuit closes again or reopens.
    """

    def __init__(
        self,
        *,
        provider: str,
        failure_threshold: int = 3,
        half_open_after: timedelta = timedelta(seconds=30),
    ) -> None:
        self._provider = provider
        self._failure_threshold = failure_threshold
        self._half_open_after = half_open_after
        self._consecutive_failures = 0
        self._circuit_state = CircuitState.CLOSED
        self._opened_at: datetime | None = None
        self._last_status = HealthStatus.UP
        self._last_error: str | None = None
        self._last_checked_at: datetime | None = None
        self._last_latency_ms: float | None = None

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def circuit_state(self) -> CircuitState:
        return self._circuit_state

    def record_success(self, *, at: datetime, latency_ms: float) -> None:
        self._consecutive_failures = 0
        self._circuit_state = CircuitState.CLOSED
        self._opened_at = None
        self._last_status = HealthStatus.UP
        self._last_error = None
        self._last_checked_at = at
        self._last_latency_ms = latency_ms

    def record_failure(self, *, at: datetime, error: str) -> None:
        self._consecutive_failures += 1
        self._last_status = (
            HealthStatus.DOWN if self._consecutive_failures >= self._failure_threshold else HealthStatus.DEGRADED
        )
        self._last_error = error
        self._last_checked_at = at
        if self._consecutive_failures >= self._failure_threshold and self._circuit_state != CircuitState.OPEN:
            self._circuit_state = CircuitState.OPEN
            self._opened_at = at

    def should_attempt(self, *, at: datetime | None = None) -> bool:
        """Whether a call should even be attempted right now. Advances the
        circuit from OPEN to HALF_OPEN as a side effect once the cooldown has
        elapsed, matching standard circuit-breaker behavior.
        """
        now = at or utc_now()
        if self._circuit_state == CircuitState.CLOSED:
            return True
        if self._circuit_state == CircuitState.OPEN:
            if self._opened_at is not None and now - self._opened_at >= self._half_open_after:
                self._circuit_state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN: allow the probe through; record_success/record_failure
        # (called by the probe's caller) will move it back to CLOSED or OPEN.
        return True

    def snapshot(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self._provider,
            status=self._last_status,
            checked_at=self._last_checked_at or utc_now(),
            latency_ms=self._last_latency_ms,
            consecutive_failures=self._consecutive_failures,
            circuit_state=self._circuit_state,
            last_error=self._last_error,
        )
