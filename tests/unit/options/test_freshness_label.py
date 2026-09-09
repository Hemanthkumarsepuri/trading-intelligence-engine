from __future__ import annotations

from app.domain.market.data_state import MarketDataState
from app.domain.options.freshness_label import FreshnessLabel, classify_freshness_label

_RECENT_MAX = 5.0


def _classify(*, data_state: MarketDataState, age: float | None, issues: bool = False) -> FreshnessLabel:
    return classify_freshness_label(
        data_state=data_state, data_age_seconds=age, has_quality_issues=issues, recent_max_age_seconds=_RECENT_MAX
    )


def test_insufficient_history_is_unavailable() -> None:
    assert _classify(data_state=MarketDataState.INSUFFICIENT_HISTORY, age=1.0) == FreshnessLabel.UNAVAILABLE


def test_provider_unavailable_is_unavailable() -> None:
    assert _classify(data_state=MarketDataState.PROVIDER_UNAVAILABLE, age=1.0) == FreshnessLabel.UNAVAILABLE


def test_error_is_unavailable() -> None:
    assert _classify(data_state=MarketDataState.ERROR, age=1.0) == FreshnessLabel.UNAVAILABLE


def test_market_closed_dominates_over_fresh_age() -> None:
    assert _classify(data_state=MarketDataState.MARKET_CLOSED_LATEST_DATA, age=0.5) == FreshnessLabel.MARKET_CLOSED


def test_stale_data_is_stale() -> None:
    assert _classify(data_state=MarketDataState.STALE_DATA, age=999.0) == FreshnessLabel.STALE


def test_quality_issues_yield_degraded_even_when_fresh() -> None:
    assert _classify(data_state=MarketDataState.LIVE_SNAPSHOT, age=1.0, issues=True) == FreshnessLabel.DEGRADED


def test_live_when_fresh_and_clean() -> None:
    assert _classify(data_state=MarketDataState.LIVE_SNAPSHOT, age=1.0) == FreshnessLabel.LIVE


def test_recent_when_older_than_the_recent_threshold_but_otherwise_clean() -> None:
    assert _classify(data_state=MarketDataState.LIVE_SNAPSHOT, age=10.0) == FreshnessLabel.RECENT


def test_live_streaming_also_supports_the_full_gradient() -> None:
    assert _classify(data_state=MarketDataState.LIVE_STREAMING, age=1.0) == FreshnessLabel.LIVE
    assert _classify(data_state=MarketDataState.LIVE_STREAMING, age=10.0) == FreshnessLabel.RECENT
