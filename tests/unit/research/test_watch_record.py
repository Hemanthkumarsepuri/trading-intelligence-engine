from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.research.watch_record import (
    ObservationSnapshot,
    WatchChangeCategory,
    compare_snapshots,
    snapshot_from_analyze_payload,
)
from app.orchestration.research_watch import (
    DuplicateWatchError,
    ResearchWatchService,
    WatchUnavailableError,
)
from app.persistence.jsonl_file import JsonlWatchRecordRepository

T0 = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)


def _snap(
    *,
    state: str = "EARLY_SETUP",
    pattern: str = "FAILED_BREAKDOWN_RECLAIM",
    stamp: datetime | None = None,
    observation_id: str = "obs1",
    contract: str = "AVAILABLE",
    freshness: str = "LIVE",
    groups: list[str] | None = None,
) -> ObservationSnapshot:
    return ObservationSnapshot(
        observation_id=observation_id,
        observation_timestamp=stamp or T0,
        research_state=state,
        timing_stage="EARLY",
        pattern=pattern,
        direction="CONVERGENCE_BULLISH",
        contract_state=contract,
        freshness=freshness,
        evidence_groups=groups or ["PRICE", "OI"],
        missing_confirmation="volume confirmation",
        confirmation_condition="hold above reclaim",
        invalidation_condition="lose reclaim",
        underlying="RELIANCE",
        option_type="PE",
        strike="1270",
    )


def test_no_material_change() -> None:
    a = _snap()
    changes = compare_snapshots(a, a)
    assert len(changes) == 1
    assert changes[0].category == WatchChangeCategory.NO_MATERIAL_CHANGE


def test_state_and_conflict_change() -> None:
    t0 = _snap()
    later = _snap(state="CONFLICT", observation_id="obs2")
    cats = {c.category for c in compare_snapshots(t0, later)}
    assert WatchChangeCategory.STATE_CHANGED in cats
    assert WatchChangeCategory.CONFLICT_APPEARED in cats
    assert WatchChangeCategory.NO_MATERIAL_CHANGE not in cats


def test_contract_degraded_and_freshness_degraded() -> None:
    t0 = _snap()
    later = _snap(contract="UNTRADEABLE", freshness="STALE", observation_id="obs2")
    cats = {c.category for c in compare_snapshots(t0, later)}
    assert WatchChangeCategory.CONTRACT_DEGRADED in cats
    assert WatchChangeCategory.FRESHNESS_DEGRADED in cats


def test_pattern_change() -> None:
    t0 = _snap()
    later = _snap(pattern="PRE_BREAKOUT_COMPRESSION", observation_id="obs2")
    cats = {c.category for c in compare_snapshots(t0, later)}
    assert WatchChangeCategory.PATTERN_CHANGED in cats


def test_snapshot_from_analyze_payload_uses_audit_and_market_time() -> None:
    snap = snapshot_from_analyze_payload(
        {
            "audit_id": "abc",
            "symbol": "RELIANCE",
            "query": "RELIANCE 1270 PE",
            "research_state": "EARLY_SETUP",
            "timing_stage": "EARLY",
            "market_observed_at": "2026-09-19T06:00:00+00:00",
            "generated_at": "2026-09-19T06:05:00+00:00",
            "visual": {
                "research_state": "EARLY_SETUP",
                "convergence": "CONVERGENCE_BULLISH",
                "development": {
                    "pattern": "FAILED_BREAKDOWN_RECLAIM",
                    "what_is_missing": "hold",
                    "confirm_if": "confirm",
                    "invalidate_if": "invalidate",
                },
                "quality": {"data_quality": "LIVE"},
                "evidence": [{"name": "EMA", "group": "PRICE", "direction": "BULLISH", "detail": "x"}],
                "requested_contract": {
                    "found": True,
                    "assessment": {
                        "strike": "1270",
                        "right": "PE",
                        "ltp": "12.5",
                        "bid": "12",
                        "ask": "13",
                        "volume": 100,
                        "open_interest": 2000,
                        "change_in_open_interest": 50,
                        "implied_volatility": "18.2",
                        "liquidity_grade": "ACCEPTABLE",
                    },
                },
            },
        }
    )
    assert snap.observation_id == "abc"
    assert snap.pattern == "FAILED_BREAKDOWN_RECLAIM"
    assert snap.option_type == "PE"
    assert snap.strike == "1270"
    assert snap.oi == 2000
    assert snap.observation_timestamp is not None
    assert snap.observation_timestamp < snap.generated_at  # type: ignore[operator]


def test_create_duplicate_remove_and_t0_immutable(tmp_path: Path) -> None:
    repo = JsonlWatchRecordRepository(tmp_path)
    service = ResearchWatchService(repo)

    async def _run() -> None:
        created = await service.create(
            symbol="reliance",
            query="RELIANCE 1270 PE",
            observation=_snap(),
            t0_unavailable=False,
        )
        assert created.t0 is not None
        assert created.t0.research_state == "EARLY_SETUP"
        try:
            await service.create(
                symbol="RELIANCE",
                query="RELIANCE 1270 PE",
                observation=_snap(observation_id="other"),
                t0_unavailable=False,
            )
            raise AssertionError("duplicate allowed")
        except DuplicateWatchError:
            pass
        later = _snap(
            state="CONFLICT",
            observation_id="obs2",
            stamp=T0 + timedelta(minutes=30),
        )
        updated = await service.update_latest(created.watch_id, later)
        assert updated.t0 is not None
        assert updated.t0.research_state == "EARLY_SETUP"
        assert updated.latest is not None
        assert updated.latest.research_state == "CONFLICT"
        cats = {c.category.value for c in updated.changes}
        assert "CONFLICT_APPEARED" in cats
        listed = await service.list_watches()
        assert len(listed) == 1
        assert await service.remove(created.watch_id) is True
        assert await service.list_watches() == []
        assert await service.remove(created.watch_id) is False

    asyncio.run(_run())


def test_missing_observation_rejected_unless_legacy(tmp_path: Path) -> None:
    service = ResearchWatchService(JsonlWatchRecordRepository(tmp_path))

    async def _run() -> None:
        try:
            await service.create(symbol="KAYNES", query="KAYNES", observation=None, t0_unavailable=False)
            raise AssertionError("should require T0")
        except WatchUnavailableError:
            pass
        legacy = await service.create(symbol="KAYNES", query="KAYNES", observation=None, t0_unavailable=True)
        assert legacy.t0_unavailable is True
        assert legacy.t0 is None

    asyncio.run(_run())


def test_lookahead_latest_does_not_replace_current(tmp_path: Path) -> None:
    service = ResearchWatchService(JsonlWatchRecordRepository(tmp_path))

    async def _run() -> None:
        created = await service.create(
            symbol="BEL", query="BEL", observation=_snap(stamp=T0), t0_unavailable=False,
        )
        earlier = _snap(state="CONFLICT", observation_id="past", stamp=T0 - timedelta(hours=1))
        updated = await service.update_latest(created.watch_id, earlier)
        assert updated.latest is not None
        assert updated.latest.research_state == "EARLY_SETUP"
        assert updated.t0 is not None
        assert updated.t0.observation_id == "obs1"

    asyncio.run(_run())


def test_migration_no_duplicates_and_restart(tmp_path: Path) -> None:
    async def _run() -> None:
        repo = JsonlWatchRecordRepository(tmp_path)
        service = ResearchWatchService(repo)
        await service.migrate_symbols(
            ["BEL", "BEL", "KAYNES"],
            {"BEL": _snap()},
        )
        listed = await service.list_watches()
        assert sorted(v.symbol for v in listed) == ["BEL", "KAYNES"]
        kaynes = next(v for v in listed if v.symbol == "KAYNES")
        assert kaynes.t0_unavailable is True
        restarted = ResearchWatchService(JsonlWatchRecordRepository(tmp_path))
        again = await restarted.list_watches()
        assert sorted(v.symbol for v in again) == ["BEL", "KAYNES"]

    asyncio.run(_run())


def test_jsonl_skips_corrupt_lines(tmp_path: Path) -> None:
    repo = JsonlWatchRecordRepository(tmp_path)
    service = ResearchWatchService(repo)

    async def _run() -> None:
        await service.create(symbol="RELIANCE", query="RELIANCE", observation=_snap(), t0_unavailable=False)
        path = tmp_path / "research_watches.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
            handle.write(json.dumps({"kind": "CREATED"}) + "\n")
        listed = await ResearchWatchService(JsonlWatchRecordRepository(tmp_path)).list_watches()
        assert len(listed) == 1
        assert listed[0].symbol == "RELIANCE"

    asyncio.run(_run())
