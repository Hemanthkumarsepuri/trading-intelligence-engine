"""Normalizer for `DhanProvider` output.

Currently a thin named alias over `DefaultNormalizer` (see base.py's module
docstring for why) — kept as its own module/class rather than inlined so
Dhan-specific normalization behavior has an obvious, dedicated home the
moment it's actually needed, without a call-site change elsewhere.
"""

from __future__ import annotations

from app.data.normalization.base import DefaultNormalizer


class DhanNormalizer(DefaultNormalizer):
    def __init__(self) -> None:
        super().__init__(provider_name="dhan")
