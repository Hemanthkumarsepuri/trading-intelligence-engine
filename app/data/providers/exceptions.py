"""Provider-adapter error hierarchy.

Every concrete provider (Dhan, Mock, Historical, and any future one) must
raise one of these instead of letting a provider-native exception (an httpx
error, a KeyError from a malformed payload, ...) escape into `data/ingestion`
and beyond. This is what lets `data/validation` and the failure-mode matrix
(ARCHITECTURE.md §25) treat every provider uniformly.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for all provider-adapter errors."""


class ProviderTimeout(ProviderError):
    """The provider did not respond within the allotted time."""


class ProviderRateLimited(ProviderError):
    """The provider rejected the request for exceeding its rate limit."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProviderMalformedResponse(ProviderError):
    """The provider responded, but the payload could not be parsed into the
    expected shape (missing fields, wrong type, unparsable JSON, ...).
    """


class ProviderUnavailable(ProviderError):
    """The provider could not be reached at all, or its circuit breaker is
    currently OPEN and refusing to attempt a call.
    """
