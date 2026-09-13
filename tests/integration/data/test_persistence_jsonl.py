from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.domain.audit.research_models import (
    RankingRationale,
    RejectionRecord,
    ResearchCoverage,
    ResearchRunRecord,
    ShortlistRecord,
)
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import (
    Candle,
    OptionChainSnapshot,
    OptionQuote,
    OptionRight,
    Quote,
    Timeframe,
)
from app.domain.options.models import IvObservation
from app.persistence.jsonl_file import (
    JsonlCandleRepository,
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
    JsonlResearchRunRepository,
)


def _freshness(ts: datetime) -> DataFreshness:
    return DataFreshness(data_timestamp=ts, received_timestamp=ts)


def _candle(ts: datetime, *, instrument_id: str = "INST1") -> Candle:
    return Candle(
        provider="test", freshness=_freshness(ts), instrument_id=instrument_id, timeframe=Timeframe.M15,
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=10,
    )


def _quote(ts: datetime, *, instrument_id: str = "INST1", price: str = "100.5") -> Quote:
    return Quote(provider="test", freshness=_freshness(ts), instrument_id=instrument_id, last_price=Decimal(price))


def _chain(ts: datetime, *, underlying: str = "NIFTY", expiry: date = date(2026, 9, 24)) -> OptionChainSnapshot:
    leg = OptionQuote(
        provider="test", freshness=_freshness(ts), underlying=underlying, expiry=expiry,
        strike=Decimal("24800"), right=OptionRight.CE, last_price=Decimal("120"),
    )
    return OptionChainSnapshot(
        provider="test", freshness=_freshness(ts), underlying=underlying, expiry=expiry,
        underlying_last_price=Decimal("24800"), legs=[leg],
    )


def _iv_obs(ts: datetime, *, underlying: str = "NIFTY") -> IvObservation:
    return IvObservation(
        provider="test", freshness=_freshness(ts), underlying=underlying, expiry=date(2026, 9, 24),
        atm_strike=Decimal("24800"), atm_ce_iv=Decimal("13.5"), atm_pe_iv=Decimal("13.1"), chain_iv=Decimal("13.3"),
    )


# -- JsonlCandleRepository ---------------------------------------------------


def test_candle_round_trips_and_survives_a_new_instance(tmp_path: Path) -> None:
    path = tmp_path / "candles.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    candle = _candle(t0)

    asyncio.run(JsonlCandleRepository(path).save(candle))

    # A brand-new repository instance reading the same file -- proves this
    # is real cross-process durability, not an in-memory cache.
    reloaded = asyncio.run(
        JsonlCandleRepository(path).query(
            instrument_id="INST1", timeframe=Timeframe.M15, start=t0 - timedelta(minutes=1),
            end=t0 + timedelta(minutes=1), as_of=t0 + timedelta(minutes=1),
        )
    )
    assert reloaded == [candle]


def test_candle_query_excludes_records_beyond_as_of(tmp_path: Path) -> None:
    path = tmp_path / "candles.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    repo = JsonlCandleRepository(path)

    async def run() -> list[Candle]:
        await repo.save(_candle(t0))
        await repo.save(_candle(t0 + timedelta(minutes=30)))
        return await repo.query(
            instrument_id="INST1", timeframe=Timeframe.M15, start=t0 - timedelta(minutes=5),
            end=t0 + timedelta(hours=1), as_of=t0 + timedelta(minutes=10),
        )

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0].freshness.data_timestamp == t0


# -- JsonlQuoteRepository -----------------------------------------------------


def test_quote_latest_returns_most_recent_at_or_before_as_of(tmp_path: Path) -> None:
    path = tmp_path / "quotes.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    repo = JsonlQuoteRepository(path)

    async def run() -> Quote | None:
        await repo.save(_quote(t0, price="100"))
        await repo.save(_quote(t0 + timedelta(minutes=5), price="105"))
        return await repo.latest(instrument_id="INST1", as_of=t0 + timedelta(minutes=2))

    result = asyncio.run(run())
    assert result is not None
    assert result.last_price == Decimal("100")


def test_quote_latest_none_when_no_matching_instrument(tmp_path: Path) -> None:
    path = tmp_path / "quotes.jsonl"
    repo = JsonlQuoteRepository(path)
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)

    asyncio.run(repo.save(_quote(t0, instrument_id="OTHER")))
    result = asyncio.run(repo.latest(instrument_id="INST1", as_of=t0 + timedelta(hours=1)))
    assert result is None


# -- JsonlOptionChainRepository ------------------------------------------------


def test_option_chain_round_trips_with_legs(tmp_path: Path) -> None:
    path = tmp_path / "chains.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    snapshot = _chain(t0)
    repo = JsonlOptionChainRepository(path)

    asyncio.run(repo.save(snapshot))
    result = asyncio.run(repo.latest(underlying="NIFTY", expiry=date(2026, 9, 24), as_of=t0 + timedelta(minutes=1)))

    assert result == snapshot
    assert result is not None and len(result.legs) == 1


def test_option_chain_latest_excludes_future_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "chains.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    repo = JsonlOptionChainRepository(path)

    async def run() -> OptionChainSnapshot | None:
        await repo.save(_chain(t0))
        await repo.save(_chain(t0 + timedelta(hours=1)))
        return await repo.latest(underlying="NIFTY", expiry=date(2026, 9, 24), as_of=t0 + timedelta(minutes=5))

    result = asyncio.run(run())
    assert result is not None
    assert result.freshness.data_timestamp == t0


def test_option_chain_query_range_returns_every_snapshot_in_window_ascending(tmp_path: Path) -> None:
    path = tmp_path / "chains.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    repo = JsonlOptionChainRepository(path)

    async def run() -> list[OptionChainSnapshot]:
        await repo.save(_chain(t0 - timedelta(minutes=20)))  # before window
        await repo.save(_chain(t0 + timedelta(minutes=5)))
        await repo.save(_chain(t0))
        await repo.save(_chain(t0 + timedelta(minutes=20)))  # after window
        return await repo.query_range(
            underlying="NIFTY", expiry=date(2026, 9, 24), start=t0, end=t0 + timedelta(minutes=10),
            as_of=t0 + timedelta(hours=1),
        )

    results = asyncio.run(run())
    assert [s.freshness.data_timestamp for s in results] == [t0, t0 + timedelta(minutes=5)]


def test_option_chain_query_range_respects_as_of_even_within_window(tmp_path: Path) -> None:
    path = tmp_path / "chains.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    repo = JsonlOptionChainRepository(path)

    async def run() -> list[OptionChainSnapshot]:
        await repo.save(_chain(t0))
        await repo.save(_chain(t0 + timedelta(minutes=5)))  # inside window but beyond as_of
        return await repo.query_range(
            underlying="NIFTY", expiry=date(2026, 9, 24), start=t0, end=t0 + timedelta(minutes=10),
            as_of=t0 + timedelta(minutes=1),
        )

    results = asyncio.run(run())
    assert [s.freshness.data_timestamp for s in results] == [t0]


def test_option_chain_latest_never_returns_a_different_expiry_of_the_same_underlying(tmp_path: Path) -> None:
    """Product-Level Analytical Audit, Phase 7 -- September-expiry
    isolation. The real-world trigger was evaluating a specific
    September contract; this proves the persistence layer keys strictly
    on (underlying, expiry) together, so a same-underlying October
    snapshot (saved first, more recent) can never be mistaken for the
    September one an analysis actually asked for."""
    path = tmp_path / "chains.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    repo = JsonlOptionChainRepository(path)
    september = _chain(t0, underlying="UNOMINDA", expiry=date(2026, 9, 24))
    october = _chain(t0 + timedelta(minutes=1), underlying="UNOMINDA", expiry=date(2026, 10, 29))

    async def run() -> tuple[OptionChainSnapshot | None, OptionChainSnapshot | None]:
        await repo.save(september)
        await repo.save(october)
        sep_result = await repo.latest(underlying="UNOMINDA", expiry=date(2026, 9, 24), as_of=t0 + timedelta(hours=1))
        oct_result = await repo.latest(underlying="UNOMINDA", expiry=date(2026, 10, 29), as_of=t0 + timedelta(hours=1))
        return sep_result, oct_result

    sep_result, oct_result = asyncio.run(run())
    assert sep_result is not None and sep_result.expiry == date(2026, 9, 24)
    assert oct_result is not None and oct_result.expiry == date(2026, 10, 29)
    assert sep_result != oct_result


def test_option_chain_query_range_never_mixes_expiries_of_the_same_underlying(tmp_path: Path) -> None:
    path = tmp_path / "chains.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    repo = JsonlOptionChainRepository(path)

    async def run() -> list[OptionChainSnapshot]:
        await repo.save(_chain(t0, underlying="UNOMINDA", expiry=date(2026, 9, 24)))
        await repo.save(_chain(t0, underlying="UNOMINDA", expiry=date(2026, 10, 29)))
        return await repo.query_range(
            underlying="UNOMINDA", expiry=date(2026, 9, 24), start=t0 - timedelta(minutes=1),
            end=t0 + timedelta(minutes=1), as_of=t0 + timedelta(hours=1),
        )

    results = asyncio.run(run())
    assert len(results) == 1
    assert results[0].expiry == date(2026, 9, 24)


# -- JsonlIvObservationRepository ----------------------------------------------


def test_iv_observation_query_history_orders_ascending_and_respects_lookback(tmp_path: Path) -> None:
    path = tmp_path / "iv.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    repo = JsonlIvObservationRepository(path)

    async def run() -> list[IvObservation]:
        await repo.save(_iv_obs(t0 - timedelta(days=10)))  # outside lookback
        await repo.save(_iv_obs(t0 - timedelta(days=1)))
        await repo.save(_iv_obs(t0))
        return await repo.query_history(underlying="NIFTY", as_of=t0, lookback=timedelta(days=5))

    results = asyncio.run(run())
    assert len(results) == 2
    assert results[0].freshness.data_timestamp < results[1].freshness.data_timestamp


def test_iv_observation_query_history_filters_by_underlying(tmp_path: Path) -> None:
    path = tmp_path / "iv.jsonl"
    t0 = datetime(2026, 8, 29, 9, 0, tzinfo=UTC)
    repo = JsonlIvObservationRepository(path)

    asyncio.run(repo.save(_iv_obs(t0, underlying="BANKNIFTY")))
    results = asyncio.run(repo.query_history(underlying="NIFTY", as_of=t0, lookback=timedelta(days=5)))
    assert results == []


# -- JsonlResearchRunRepository (Daily Market Researcher) ----------------------


def _research_run(generated_at: datetime, *, universe: list[str] | None = None) -> ResearchRunRecord:
    return ResearchRunRecord(
        generated_at=generated_at, market_state="LIVE_SNAPSHOT", universe=universe or ["GAIL", "RELIANCE"],
        screened_count=2, deep_analyzed_count=2,
        rejected=[RejectionRecord(symbol="GAIL", reason="no defensible direction (verdict: CONFLICTED)")],
        shortlist=[
            ShortlistRecord(
                rank=1, symbol="RELIANCE", audit_id="audit-1", selected_right="CE", selected_strike="1300",
                rationale=RankingRationale(
                    decision_tier="WATCH", directional_verdict="BULLISH_SIDE_BETTER_SUPPORTED", supporting_groups=2,
                    liquidity_grade="excellent", decay_verdict="DECAY_FAVORABLE",
                    required_underlying_move_pct="1.5", breakeven_distance_pct="2.0", headroom_pct="3.0",
                ),
            ),
        ],
        no_high_conviction=False,
    )


def test_research_run_round_trips(tmp_path: Path) -> None:
    directory = tmp_path / "research_journal"
    repo = JsonlResearchRunRepository(directory)
    t0 = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    record = _research_run(t0)

    asyncio.run(repo.save_run(record))
    results = asyncio.run(repo.query_by_date(t0.date()))

    assert len(results) == 1
    assert results[0] == record
    assert results[0].shortlist[0].rationale.liquidity_grade == "excellent"


def test_research_run_query_by_date_filters_to_the_real_ist_calendar_date(tmp_path: Path) -> None:
    repo = JsonlResearchRunRepository(tmp_path / "research_journal")
    # 2026-08-31 18:40 UTC is 2026-09-01 00:10 IST -- a genuinely different
    # real calendar date once converted, the same real distinction
    # `session_report.py`'s `to_ist()` usage already relies on.
    late_utc = datetime(2026, 8, 31, 18, 40, tzinfo=UTC)
    early_utc = datetime(2026, 8, 31, 3, 0, tzinfo=UTC)

    async def run() -> tuple[list[ResearchRunRecord], list[ResearchRunRecord]]:
        await repo.save_run(_research_run(late_utc))
        await repo.save_run(_research_run(early_utc))
        return (
            await repo.query_by_date(date(2026, 9, 1)),
            await repo.query_by_date(date(2026, 8, 31)),
        )

    sept_1_ist, aug_31_ist = asyncio.run(run())
    assert len(sept_1_ist) == 1 and sept_1_ist[0].generated_at == late_utc
    assert len(aug_31_ist) == 1 and aug_31_ist[0].generated_at == early_utc


def test_research_run_is_append_only_no_update_or_delete_method() -> None:
    """Same immutability discipline as every other repository in this
    module -- structurally enforced, not just documented."""
    assert not hasattr(JsonlResearchRunRepository, "update_run")
    assert not hasattr(JsonlResearchRunRepository, "delete_run")


def test_research_run_stage_one_fields_round_trip(tmp_path: Path) -> None:
    """Research-deepening phase -- Stage-1 survivor count and the
    rejection-reason tally persist and reload exactly."""
    repo = JsonlResearchRunRepository(tmp_path / "research_journal")
    t0 = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    record = ResearchRunRecord(
        generated_at=t0, market_state="LIVE_SNAPSHOT", universe=["RELIANCE", "TCS", "GAIL"],
        screened_count=210, deep_analyzed_count=30, stage_one_survivor_count=30,
        rejected=[RejectionRecord(symbol="GAIL", reason="screened out at stage 1 (not among the top 30...)")],
        rejection_summary={"STAGE_1_SCREENED_OUT": 179, "NO_DIRECTIONAL_CONVERGENCE": 28},
        shortlist=[
            ShortlistRecord(
                rank=1, symbol="RELIANCE", audit_id="audit-1", selected_right="CE", selected_strike="1300",
                research_confidence="STRONG",
                rationale=RankingRationale(
                    decision_tier="WATCH", directional_verdict="BULLISH_SIDE_BETTER_SUPPORTED", supporting_groups=2,
                    liquidity_grade="excellent", decay_verdict="DECAY_FAVORABLE",
                    required_underlying_move_pct="1.5", breakeven_distance_pct="2.0", headroom_pct="3.0",
                ),
            ),
        ],
        no_high_conviction=False,
    )

    asyncio.run(repo.save_run(record))
    results = asyncio.run(repo.query_by_date(t0.date()))

    assert results[0].stage_one_survivor_count == 30
    assert results[0].rejection_summary == {"STAGE_1_SCREENED_OUT": 179, "NO_DIRECTIONAL_CONVERGENCE": 28}
    assert results[0].shortlist[0].research_confidence == "STRONG"


def test_research_run_old_persisted_record_without_stage_one_fields_still_deserializes(tmp_path: Path) -> None:
    """A record written before this phase (no `stage_one_survivor_count`/
    `rejection_summary`/`research_confidence` fields) must still load --
    the new fields are additive and default, not a breaking migration."""
    path = tmp_path / "research_journal"
    path.mkdir(parents=True)
    old_line = (
        '{"run_id": "old1", "generated_at": "2026-08-30T09:00:00Z", "market_state": "LIVE_SNAPSHOT", '
        '"universe": ["GAIL"], "screened_count": 1, "deep_analyzed_count": 1, "rejected": [], '
        '"shortlist": [{"rank": 1, "symbol": "GAIL", "audit_id": "a1", "selected_right": "CE", '
        '"selected_strike": "170", "rationale": {"decision_tier": "WATCH", '
        '"directional_verdict": "BULLISH_SIDE_BETTER_SUPPORTED", "supporting_groups": 2, '
        '"liquidity_grade": "excellent", "decay_verdict": null, "required_underlying_move_pct": null, '
        '"breakeven_distance_pct": null, "headroom_pct": null}}], "no_high_conviction": false}'
    )
    (path / "research_runs.jsonl").write_text(old_line + "\n", encoding="utf-8")

    repo = JsonlResearchRunRepository(path)
    results = asyncio.run(repo.query_by_date(date(2026, 8, 30)))

    assert len(results) == 1
    assert results[0].stage_one_survivor_count is None
    assert results[0].rejection_summary == {}
    assert results[0].shortlist[0].research_confidence is None


def test_research_run_early_move_discovery_fields_round_trip(tmp_path: Path) -> None:
    """Early-move discovery phase -- `participation_note`/`market_context`
    persist and reload exactly, alongside the earlier `early_stage_state`
    field."""
    repo = JsonlResearchRunRepository(tmp_path / "research_journal")
    t0 = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    record = ResearchRunRecord(
        generated_at=t0, market_state="LIVE_SNAPSHOT", universe=["RELIANCE"],
        screened_count=1, deep_analyzed_count=1,
        rejected=[],
        shortlist=[
            ShortlistRecord(
                rank=1, symbol="RELIANCE", audit_id="audit-1", selected_right="CE", selected_strike="1300",
                research_confidence="STRONG", early_stage_state="EARLY_DIRECTIONAL_BUILD",
                participation_note="real Stage-1 buy/sell quantity ratio was 1.50:1 -- more buy-side than sell-side quantity was recorded today (participation build, not confirmed accumulation).",
                market_context="SUPPORTIVE",
                rationale=RankingRationale(
                    decision_tier="WATCH", directional_verdict="BULLISH_SIDE_BETTER_SUPPORTED", supporting_groups=2,
                    liquidity_grade="excellent", decay_verdict="DECAY_FAVORABLE",
                    required_underlying_move_pct="1.5", breakeven_distance_pct="2.0", headroom_pct="3.0",
                ),
            ),
        ],
        no_high_conviction=False,
    )

    asyncio.run(repo.save_run(record))
    results = asyncio.run(repo.query_by_date(t0.date()))

    assert results[0].shortlist[0].early_stage_state == "EARLY_DIRECTIONAL_BUILD"
    assert results[0].shortlist[0].market_context == "SUPPORTIVE"
    assert results[0].shortlist[0].participation_note is not None
    assert "1.50:1" in results[0].shortlist[0].participation_note


def test_research_run_old_persisted_record_without_early_move_discovery_fields_still_deserializes(tmp_path: Path) -> None:
    """A record written before THIS phase (has `research_confidence`/
    `early_stage_state` from the prior phase, but no `participation_note`/
    `market_context`) must still load -- additive, never a breaking
    migration."""
    path = tmp_path / "research_journal"
    path.mkdir(parents=True)
    old_line = (
        '{"run_id": "old2", "generated_at": "2026-08-30T09:00:00Z", "market_state": "LIVE_SNAPSHOT", '
        '"universe": ["GAIL"], "screened_count": 1, "deep_analyzed_count": 1, "rejected": [], '
        '"shortlist": [{"rank": 1, "symbol": "GAIL", "audit_id": "a1", "selected_right": "CE", '
        '"selected_strike": "170", "research_confidence": "STRONG", "early_stage_state": "DEVELOPING_MOMENTUM", '
        '"rationale": {"decision_tier": "WATCH", '
        '"directional_verdict": "BULLISH_SIDE_BETTER_SUPPORTED", "supporting_groups": 2, '
        '"liquidity_grade": "excellent", "decay_verdict": null, "required_underlying_move_pct": null, '
        '"breakeven_distance_pct": null, "headroom_pct": null}}], "no_high_conviction": false}'
    )
    (path / "research_runs.jsonl").write_text(old_line + "\n", encoding="utf-8")

    repo = JsonlResearchRunRepository(path)
    results = asyncio.run(repo.query_by_date(date(2026, 8, 30)))

    assert len(results) == 1
    assert results[0].shortlist[0].early_stage_state == "DEVELOPING_MOMENTUM"
    assert results[0].shortlist[0].participation_note is None
    assert results[0].shortlist[0].market_context is None


def test_research_run_coverage_round_trips(tmp_path: Path) -> None:
    """Sprint 1, Phase 7 -- `ResearchCoverage` persists and reloads
    exactly, same additive/backward-compatible pattern as every other
    field added to `ResearchRunRecord` so far."""
    repo = JsonlResearchRunRepository(tmp_path / "research_journal")
    t0 = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    coverage = ResearchCoverage(
        universe_count=210, stage1_attempted=210, stage1_successful=110, stage1_failed=100,
        stage2_attempted=30, stage2_successful=20, stage2_failed=10, timeout_failures=2,
        timestamp_quality_failures=8, classification="POOR",
    )
    record = ResearchRunRecord(
        generated_at=t0, market_state="LIVE_SNAPSHOT", universe=["RELIANCE"],
        screened_count=1, deep_analyzed_count=1, rejected=[], shortlist=[], no_high_conviction=True,
        coverage=coverage,
    )

    asyncio.run(repo.save_run(record))
    results = asyncio.run(repo.query_by_date(t0.date()))

    assert results[0].coverage is not None
    assert results[0].coverage.classification == "POOR"
    assert results[0].coverage.universe_count == 210
    assert results[0].coverage.timestamp_quality_failures == 8


def test_research_run_old_persisted_record_without_coverage_still_deserializes(tmp_path: Path) -> None:
    """A record written before Sprint 1 (no `coverage` field at all) must
    still load -- additive, never a breaking migration."""
    path = tmp_path / "research_journal"
    path.mkdir(parents=True)
    old_line = (
        '{"run_id": "old3", "generated_at": "2026-08-30T09:00:00Z", "market_state": "LIVE_SNAPSHOT", '
        '"universe": ["GAIL"], "screened_count": 1, "deep_analyzed_count": 1, "rejected": [], '
        '"shortlist": [], "no_high_conviction": true}'
    )
    (path / "research_runs.jsonl").write_text(old_line + "\n", encoding="utf-8")

    repo = JsonlResearchRunRepository(path)
    results = asyncio.run(repo.query_by_date(date(2026, 8, 30)))

    assert len(results) == 1
    assert results[0].coverage is None
