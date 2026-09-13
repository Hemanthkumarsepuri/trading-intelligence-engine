"""RHP fetch is fail-closed: https allowlist, no redirect follow."""

from __future__ import annotations

import pytest

from app.data.providers.exceptions import ProviderMalformedResponse
from app.data.providers.rhp_fetcher import _assert_safe_rhp_url


def test_rhp_url_refuses_non_https_and_unknown_host() -> None:
    with pytest.raises(ProviderMalformedResponse, match="allowlist"):
        _assert_safe_rhp_url("http://www.sebi.gov.in/foo.pdf")
    with pytest.raises(ProviderMalformedResponse, match="allowlist"):
        _assert_safe_rhp_url("https://evil.example/rhp.pdf")


def test_rhp_url_allows_sebi_https() -> None:
    _assert_safe_rhp_url("https://www.sebi.gov.in/filings/example.pdf")
