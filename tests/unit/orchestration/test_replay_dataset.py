"""Release gate (Sections 9-12) -- the historical research dataset:
episodes (not bars) are the counting unit, market context is as-of bounded,
and KNEW_THEN never contains anything that happened afterwards."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.audit.research_models import ResearchObservation
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.orchestration.replay_dataset import (
    MARKET_DOWN,
    MARKET_FLAT,
    MARKET_UNKNOWN,
    MARKET_UP,
    CapturedObservation,
    Episode,
    MarketContextSeries,
    build_dataset_row,
    group_into_episodes,
)

# 2026-09-14 is a Monday; 03:45 UTC = 09:15 IST.
MON_0915 = datetime(2026, 9, 14, 3, 45, tzinfo=UTC)


def _obs(at: datetime, *, symbol: str = "SBIN", pattern: str = "PRE_BREAKOUT_COMPRESSION", direction: str = "BULLISH") -> ResearchObservation:
    return ResearchObservation(
        run_id="r", audit_id=None, generated_at=at, symbol=symbol, direction=direction, early_stage_state="EARLY_SETUP",
        research_confidence="WEAK", actionability="WATCH", spot_at_observation="100", contractual_expiry_breakeven=None,
        nearest_level_kind="resistance", nearest_level_value="102", market_context=None, participation_note=None,
        coverage_classification="REPLAY", thesis="t", source="REPLAY", pattern=pattern, derivatives_evidence_available=False,
        invalidation_level_kind="support", invalidation_level_value="98",
    )


def _cap(obs: ResearchObservation) -> CapturedObservation:
    return CapturedObservation(observation=obs, knew_then={"symbol": obs.symbol, "timestamp": obs.generated_at.isoformat()})


def _candle(at: datetime, close: str, *, high: str | None = None, low: str | None = None) -> Candle:
    return Candle(
        provider="test", freshness=DataFreshness(data_timestamp=at, received_timestamp=at), instrument_id="X",
        timeframe=Timeframe.M15, open=Decimal(close), high=Decimal(high or close), low=Decimal(low or close),
        close=Decimal(close), volume=1,
    )


def test_contiguous_bars_of_one_setup_are_one_episode_represented_by_its_first_bar() -> None:
    bars = [_cap(_obs(MON_0915 + timedelta(minutes=15 * i))) for i in range(4)]
    [episode] = group_into_episodes(list(reversed(bars)))
    assert episode.bars == 4
    assert episode.first.observation.generated_at == MON_0915
    assert episode.last_bar_at == MON_0915 + timedelta(minutes=45)


def test_a_gap_a_new_pattern_a_new_direction_a_new_session_or_a_new_symbol_starts_a_new_episode() -> None:
    items = [
        _cap(_obs(MON_0915)),
        _cap(_obs(MON_0915 + timedelta(minutes=45))),  # gap of two bars
        _cap(_obs(MON_0915 + timedelta(minutes=60), pattern="EARLY_REVERSAL")),
        _cap(_obs(MON_0915 + timedelta(minutes=75), pattern="EARLY_REVERSAL", direction="BEARISH")),
        _cap(_obs(MON_0915 + timedelta(days=1), pattern="EARLY_REVERSAL", direction="BEARISH")),
        _cap(_obs(MON_0915 + timedelta(days=1), symbol="TCS", pattern="EARLY_REVERSAL", direction="BEARISH")),
    ]
    assert len(group_into_episodes(items)) == 6


def test_market_context_uses_only_index_bars_at_or_before_the_observation() -> None:
    prev_close = _candle(MON_0915 - timedelta(days=3) + timedelta(hours=6), "100")  # previous Friday
    series = MarketContextSeries([
        prev_close,
        _candle(MON_0915, "100.1"),
        _candle(MON_0915 + timedelta(minutes=15), "101"),
        _candle(MON_0915 + timedelta(minutes=30), "98"),  # the future relative to the checks below
    ])
    assert series.classify(MON_0915 + timedelta(minutes=5)) == MARKET_FLAT
    assert series.classify(MON_0915 + timedelta(minutes=20)) == MARKET_UP
    assert series.classify(MON_0915 + timedelta(minutes=31)) == MARKET_DOWN


def test_market_context_is_unknown_without_a_previous_session_or_with_stale_index_data() -> None:
    only_today = MarketContextSeries([_candle(MON_0915, "100")])
    assert only_today.classify(MON_0915 + timedelta(minutes=1)) == MARKET_UNKNOWN
    stale = MarketContextSeries([_candle(MON_0915 - timedelta(days=4), "100"), _candle(MON_0915 - timedelta(days=3), "101")])
    assert stale.classify(MON_0915 + timedelta(minutes=1)) == MARKET_UNKNOWN
    assert MarketContextSeries([]).classify(MON_0915) == MARKET_UNKNOWN


def test_dataset_row_keeps_knew_then_free_of_outcomes_and_reports_every_horizon() -> None:
    obs = _obs(MON_0915)
    episode = Episode(first=_cap(obs), bars=2, last_bar_at=MON_0915 + timedelta(minutes=15))
    candles = [_candle(MON_0915 + timedelta(minutes=15 * i), "100", high="103", low="99.5") for i in range(30)]
    observation, _status, row = build_dataset_row(
        episode, candles=candles, dataset_as_of=MON_0915 + timedelta(hours=7), market=None, sector="Financial Services",
        provenance={"candle_source": "test"},
    )
    assert observation.market_context == MARKET_UNKNOWN
    assert set(row) == {"dataset_version", "observation_id", "knew_then", "happened_after", "provenance"}
    assert not any("outcome" in key for key in row["knew_then"])
    assert row["knew_then"]["sector_context"] == "Financial Services"
    after = row["happened_after"]
    assert set(after) == {"outcome_30m", "outcome_1h", "outcome_1d", "outcome_3d", "outcome_5d", "reference_status_plus_5d"}
    assert after["outcome_30m"]["confirmation"] == "CONFIRMED"  # high 103 broke the recorded 102 resistance
    assert after["outcome_5d"]["data_sufficient"] is False  # the dataset ends before +5 sessions
    assert after["reference_status_plus_5d"] == "PENDING"
    assert row["provenance"]["episode_bars"] == 2


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(symbol: str, direction: str, status: str) -> dict[str, object]:
    return {
        "observation_id": f"{symbol}-{direction}-{status}",
        "knew_then": {"symbol": symbol, "direction": direction},
        "happened_after": {"reference_status_plus_5d": status},
    }


def test_read_replay_dataset_rows_filters_without_reordering_or_recomputing(tmp_path: Path) -> None:
    from app.orchestration.pattern_views import read_replay_dataset_rows

    path = tmp_path / "replay_dataset.jsonl"
    rows = [
        _row("SBIN", "BULLISH", "FAILED_SETUP"),
        _row("INFY", "BEARISH", "FOLLOW_THROUGH_OBSERVED"),
        _row("SBIN", "BEARISH", "FOLLOW_THROUGH_OBSERVED"),
        _row("SBIN", "BULLISH", "FOLLOW_THROUGH_OBSERVED"),
    ]
    _write_rows(path, rows)

    view = read_replay_dataset_rows(path, symbol="sbin", status="follow_through_observed")
    assert (view.total_rows, view.matched_rows, view.returned_rows) == (4, 2, 2)
    assert view.rows == [rows[2], rows[3]]  # dataset order, byte-for-byte rows

    limited = read_replay_dataset_rows(path, direction="BULLISH", limit=1)
    assert (limited.matched_rows, limited.returned_rows) == (2, 1)
    assert limited.rows == [rows[0]]


def test_read_replay_dataset_rows_reports_a_missing_dataset(tmp_path: Path) -> None:
    from app.orchestration.pattern_views import ReplayDatasetUnavailable, read_replay_dataset_rows

    with pytest.raises(ReplayDatasetUnavailable):
        read_replay_dataset_rows(tmp_path / "absent.jsonl")
