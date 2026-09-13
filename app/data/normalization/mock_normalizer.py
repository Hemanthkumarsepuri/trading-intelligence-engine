"""Normalizer for `MockMarketDataProvider`/`MockOptionChainProvider` output."""

from __future__ import annotations

from app.data.normalization.base import DefaultNormalizer


class MockNormalizer(DefaultNormalizer):
    def __init__(self) -> None:
        super().__init__(provider_name="mock")
