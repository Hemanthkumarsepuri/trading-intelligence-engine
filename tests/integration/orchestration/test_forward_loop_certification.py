"""Sprint 3.6 -- forward research loop certification & dataset integrity.

SINGLE-PROCESS certification. Cross-process atomicity of the JSONL files is NOT
certified (see docs/sprints/phase-3/SPRINT-3.6-*). Every test here protects a
real invariant of the lifecycle

    canonical analysis -> capture gate -> immutable T0 -> persisted -> scheduled
    sweep -> +1 -> +3 -> +5 -> progression

and the asserts are about what was KNOWN, WHEN, what was RECORDED, what HAPPENED
afterwards, and whether the record REMAINED UNCHANGED. Nothing fabricates market
history: the repository's mock-transport live-shape fixture is used throughout.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import app.orchestration.forward_capture as forward_capture_module
from app.domain.audit.research_models import (
    ForwardCaptureProvenance,
    HorizonState,
    ResearchCheckpointLabel,
    ResearchObservation,
    StreamProvenance,
)
from app.domain.market.trading_calendar import (
    configure_nse_holidays,
    configure_nse_special_sessions,
)
from app.domain.options.evidence_availability import AvailabilityState, EvidenceClass
from app.domain.options.evidence_matrix import VOTING_GROUPS, EvidenceGroup
from app.orchestration.forward_capture import (
    FORWARD_LIVE_CAPTURE,
    HISTORICAL_REPLAY,
    LEGACY_LIVE_UNVERIFIED,
    audit_research_dataset,
    observation_capture_kind,
    verify_forward_observation,
)
from app.orchestration.outcome_horizons import OutcomeHorizonLabel, horizon_target_timestamp
from app.orchestration.outcome_sessions import horizon_close_utc, session_close_utc
from app.orchestration.research_outcome import build_horizon_progress, due_research_checkpoints
from app.persistence.jsonl_file import JsonlResearchOutcomeRepository, JsonlWatchRecordRepository
from tests.integration.orchestration import test_forward_capture as fc
from tests.integration.orchestration import test_research_outcome as ro
from tests.integration.orchestration.test_outcome_progression import (
    PLUS1_CLOSE,
    PLUS3_CLOSE,
    PLUS5_CLOSE,
    T0,
    _captured,
    _rows,
)
from tests.integration.orchestration.test_outcome_scheduler import (
    Clock,
    _checkpoints,
    _runner,
    _scheduler,
    _tick,
)

_APP_ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _reset_calendar_overlays() -> Iterator[None]:
    yield
    configure_nse_holidays(None)
    configure_nse_special_sessions(None)


def _run_loop(tmp_path: Path, name: str, *, rows_for: Any = None, ticks: tuple[datetime, ...] = (PLUS1_CLOSE, PLUS3_CLOSE, PLUS5_CLOSE)) -> tuple[ResearchObservation, JsonlResearchOutcomeRepository, Path]:
    """capture T0 -> scheduled ticks (restarting the repository between ticks)."""
    obs, _repo, obs_path = _captured(tmp_path / name)
    clock = Clock(ticks[0])
    for now in ticks:
        clock.now = now
        fresh = JsonlResearchOutcomeRepository(obs_path.parent)  # every tick is a fresh process
        kwargs = {} if rows_for is None else {"rows_for": rows_for}
        _tick(_scheduler(_runner(fresh, tmp_path / name, **kwargs), clock))
    return obs, JsonlResearchOutcomeRepository(obs_path.parent), obs_path


_VIOLENT = lambda t: _rows(t, wild_after=PLUS1_CLOSE)


# ----------------------------------------------------------------------------
# INVARIANTS 1-3: a valid T0 that contains only T0, with an identity that binds
# ----------------------------------------------------------------------------

OBSERVED = "OBSERVED_AT_T0"
DERIVED = "DERIVED_FROM_T0"
PROVENANCE = "PROVENANCE"
PERSISTENCE = "PERSISTENCE_METADATA"

OBSERVATION_FIELD_CLASS = {
    "observation_id": DERIVED,  # deterministic identity of (contract, T0)
    "run_id": PERSISTENCE, "audit_id": PROVENANCE, "generated_at": OBSERVED, "symbol": OBSERVED,
    "direction": DERIVED, "selected_right": OBSERVED, "selected_strike": OBSERVED,
    "early_stage_state": DERIVED, "research_confidence": DERIVED, "actionability": DERIVED,
    "spot_at_observation": OBSERVED, "contractual_expiry_breakeven": DERIVED,
    "nearest_level_kind": DERIVED, "nearest_level_value": DERIVED, "market_context": DERIVED,
    "participation_note": DERIVED, "coverage_classification": PROVENANCE, "thesis": DERIVED,
    "structural_context": DERIVED, "participation_depth": DERIVED, "relative_strength": DERIVED,
    "pre_breakout_signal": DERIVED, "source": PROVENANCE, "derivatives_evidence_available": DERIVED,
    "missing_evidence": DERIVED, "pattern": DERIVED, "invalidation_level_kind": DERIVED,
    "invalidation_level_value": DERIVED, "evidence_availability": OBSERVED, "forward_capture": PROVENANCE,
}
CAPTURE_FIELD_CLASS = {
    "capture_policy_version": PROVENANCE, "session_window": DERIVED, "research_session_mode": DERIVED,
    "market_state": OBSERVED, "persisted_at": PERSISTENCE, "underlying_instrument_key": OBSERVED,
    "contract_instrument_key": OBSERVED, "futures_instrument_key": OBSERVED, "observed_expiry": OBSERVED,
    "streams": PROVENANCE,
}
STREAM_FIELD_CLASS = {"stream": PROVENANCE, "label": PROVENANCE, "data_timestamp": PROVENANCE, "retrieved_at": PROVENANCE, "source": PROVENANCE}


def test_every_t0_field_is_classified_so_a_new_field_cannot_arrive_unaudited() -> None:
    assert set(ResearchObservation.model_fields) == set(OBSERVATION_FIELD_CLASS)
    assert set(ForwardCaptureProvenance.model_fields) == set(CAPTURE_FIELD_CLASS)
    assert set(StreamProvenance.model_fields) == set(STREAM_FIELD_CLASS)
    assert {v for m in (OBSERVATION_FIELD_CLASS, CAPTURE_FIELD_CLASS, STREAM_FIELD_CLASS) for v in m.values()} <= {
        OBSERVED, DERIVED, PROVENANCE, PERSISTENCE,
    }
    # persistence metadata is exactly the write-time facts -- nothing analytical hides in that class
    assert {k for m in (OBSERVATION_FIELD_CLASS, CAPTURE_FIELD_CLASS) for k, v in m.items() if v == PERSISTENCE} == {"run_id", "persisted_at"}


def _datetimes(value: Any, path: str = "") -> Iterator[tuple[str, datetime]]:
    if isinstance(value, datetime):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _datetimes(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _datetimes(v, f"{path}[{i}]")


def test_t0_holds_no_timestamp_later_than_t0_other_than_the_persistence_metadata(tmp_path: Path) -> None:
    obs, _, _ = _captured(tmp_path)
    stamps = dict(_datetimes(obs.model_dump()))
    assert stamps[".generated_at"] == T0
    later = {path for path, ts in stamps.items() if ts > obs.generated_at}
    assert later == {".forward_capture.persisted_at"}  # the only later instant is the write time -- and it is labelled as such
    assert obs.forward_capture is not None and obs.forward_capture.observed_expiry >= T0.date()
    # identity fields all present -- what was observed is fully determined by the record itself
    assert obs.forward_capture.underlying_instrument_key and obs.forward_capture.contract_instrument_key
    assert (obs.symbol, obs.selected_right, obs.selected_strike) == ("SYMBOLQ", "CE", "1000.0")


def test_capture_consumes_the_canonical_analysis_and_fetches_nothing() -> None:
    """Forward capture takes an already-built candidate; it has no provider, HTTP client or
    market-data import, so it cannot fetch anything to enrich a record."""
    params = set(inspect.signature(forward_capture_module.capture_forward_observation).parameters)
    assert not params & {"provider", "client", "http_client", "instrument_master", "repositories"}
    tree = ast.parse(inspect.getsource(forward_capture_module))
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m.startswith(("app.data", "httpx", "app.orchestration.dashboard_service")) for m in imported if m)


def test_the_observation_identity_is_recomputable_from_the_record_and_survives_the_whole_loop(tmp_path: Path) -> None:
    obs, repo, _obs_path = _run_loop(tmp_path, "id", rows_for=_VIOLENT)
    reloaded = asyncio.run(repo.get_observation(obs.observation_id))
    assert reloaded is not None and reloaded == obs
    assert verify_forward_observation(reloaded) == ()  # includes IDENTITY_MISMATCH: id still recomputes from the fields
    assert reloaded.forward_capture is not None and obs.forward_capture is not None
    assert reloaded.forward_capture.observed_expiry == obs.forward_capture.observed_expiry == ro.EXPIRY


# ----------------------------------------------------------------------------
# INVARIANTS 4-7, 12-15: the full lifecycle, adversarial future data, restarts
# ----------------------------------------------------------------------------


def test_complete_lifecycle_certification(tmp_path: Path) -> None:
    obs, repo, obs_path = _run_loop(
        tmp_path, "life", rows_for=_VIOLENT,
        ticks=(T0 + timedelta(hours=1), PLUS1_CLOSE, PLUS1_CLOSE, PLUS3_CLOSE + timedelta(minutes=15), PLUS5_CLOSE, PLUS5_CLOSE + timedelta(days=2)),
    )
    cp_path = obs_path.parent / "research_outcome_checkpoints.jsonl"

    # one T0, byte-for-byte what was captured (compare against a fresh capture of the same analysis)
    baseline_obs, _, baseline_path = _captured(tmp_path / "baseline")
    assert obs_path.read_bytes() == baseline_path.read_bytes()

    # exactly one checkpoint per horizon, each evaluated through ITS OWN close and no later
    checkpoints = {c.checkpoint_label: c for c in asyncio.run(repo.query_all_checkpoints())}
    assert len(cp_path.read_text(encoding="utf-8").splitlines()) == 3 and len(checkpoints) == 3
    assert {k: v.evaluated_through for k, v in checkpoints.items()} == {
        ResearchCheckpointLabel.PLUS_1_SESSION: PLUS1_CLOSE,
        ResearchCheckpointLabel.PLUS_3_SESSIONS: PLUS3_CLOSE,
        ResearchCheckpointLabel.PLUS_5_SESSIONS: PLUS5_CLOSE,
    }
    # the violent post-+1 data (3000 / 20) never reached +1's excursions
    assert float(checkpoints[ResearchCheckpointLabel.PLUS_1_SESSION].max_favorable_move_pct or 0) < 20
    assert float(checkpoints[ResearchCheckpointLabel.PLUS_3_SESSIONS].max_favorable_move_pct or 0) > 100  # ... but +3 legitimately saw it

    # T0 fields the spec names
    after = asyncio.run(repo.get_observation(obs.observation_id))
    assert after is not None and after.evidence_availability == obs.evidence_availability
    assert after.evidence_availability is not None
    assert after.evidence_availability.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.AVAILABLE
    assert after.early_stage_state == baseline_obs.early_stage_state and after.direction == "BULLISH"

    horizons = build_horizon_progress(after, list(checkpoints.values()), as_of=PLUS5_CLOSE + timedelta(days=2))
    assert [h.state for h in horizons] == [HorizonState.AVAILABLE] * 3

    audit = asyncio.run(audit_research_dataset(repo))
    assert audit.clean and (audit.forward_valid, audit.checkpoints, audit.observations) == (1, 3, 1)


def test_checkpoints_never_carry_or_recompute_evidence_availability_or_contract_identity() -> None:
    from app.domain.audit.research_models import ResearchOutcomeCheckpoint

    assert not {"evidence_availability", "selected_strike", "selected_right", "observed_expiry", "contract_instrument_key"} & set(
        ResearchOutcomeCheckpoint.model_fields
    )


def test_a_future_chain_and_changed_contract_fixtures_cannot_rewrite_t0(tmp_path: Path) -> None:
    """The sweep's analyses see a live chain (T0's `MARKET_CONTEXT` was UNAVAILABLE; the outcome
    fixture has its own chain/expiry). None of it can change what T0 recorded."""
    obs, repo, obs_path = _captured(tmp_path)
    before = obs_path.read_bytes()
    availability = obs.evidence_availability
    assert availability is not None and availability.state_of(EvidenceClass.MARKET_CONTEXT) == AvailabilityState.UNAVAILABLE
    _tick(_scheduler(_runner(repo, tmp_path, rows_for=_VIOLENT), Clock(PLUS5_CLOSE)))
    assert obs_path.read_bytes() == before
    reloaded = asyncio.run(repo.get_observation(obs.observation_id))
    assert reloaded is not None and reloaded.evidence_availability == availability


def test_missing_future_data_is_never_confirmation_or_invalidation_anywhere_in_the_loop(tmp_path: Path) -> None:
    no_future = lambda t: _rows(t, stop_after=ro.OBSERVATION_AS_OF)
    obs, repo, _ = _run_loop(tmp_path, "gap", rows_for=no_future, ticks=(PLUS1_CLOSE,))
    (cp,) = _checkpoints(repo)
    assert cp.confirmation_outcome is None and cp.invalidation_outcome is None  # not CONFIRMED / INVALIDATED / NOT_*
    assert cp.spot_at_checkpoint is None and cp.max_favorable_move_pct is None and cp.swing_level_broken is None
    assert cp.progression.value == "UNKNOWN"
    (h, *_) = build_horizon_progress(obs, [cp], as_of=PLUS1_CLOSE)
    assert h.state == HorizonState.INSUFFICIENT
    assert asyncio.run(audit_research_dataset(repo)).clean  # insufficient data is honest data, not corruption


# ----------------------------------------------------------------------------
# INVARIANTS 8-11: horizon isolation (datasets A/B, C/D, E/F)
# ----------------------------------------------------------------------------

_PRICE_FIELDS = (
    "spot_at_checkpoint", "move_pct_from_observation", "max_favorable_move_pct", "max_adverse_move_pct", "swing_level_broken",
    "breakeven_reached", "confirmation_outcome", "invalidation_outcome", "first_confirmation_at", "first_invalidation_at",
    "evaluated_through",
)


def _horizon_checkpoint(
    tmp_path: Path, name: str, label: ResearchCheckpointLabel, sweep_at: datetime, rows_through: datetime,
    spike_after: datetime,
) -> Any:
    _obs, repo, _ = _captured(tmp_path / name)
    clock = Clock(sweep_at)
    # Quiet through the horizon under test, violent (3000 / 20) only AFTER it: any leak shows.
    rows_for = lambda t: _rows(rows_through, wild_after=spike_after)
    _tick(_scheduler(_runner(repo, tmp_path / name, rows_for=rows_for), clock))
    return {c.checkpoint_label: c for c in asyncio.run(repo.query_all_checkpoints())}[label]


@pytest.mark.parametrize(
    ("label", "horizon_close", "isolated_end", "contaminated_end"),
    [
        # A vs B: data through +1 only, versus everything through +5
        (ResearchCheckpointLabel.PLUS_1_SESSION, PLUS1_CLOSE, PLUS1_CLOSE, PLUS5_CLOSE),
        # C vs D: data through +3 only, versus +4 and +5 as well
        (ResearchCheckpointLabel.PLUS_3_SESSIONS, PLUS3_CLOSE, PLUS3_CLOSE, PLUS5_CLOSE),
        # E vs F: data through +5, versus days beyond +5
        (ResearchCheckpointLabel.PLUS_5_SESSIONS, PLUS5_CLOSE, PLUS5_CLOSE, PLUS5_CLOSE + timedelta(days=3)),
    ],
)
def test_a_horizon_is_identical_with_and_without_any_later_data(
    tmp_path: Path, label: ResearchCheckpointLabel, horizon_close: datetime, isolated_end: datetime, contaminated_end: datetime,
) -> None:
    isolated = _horizon_checkpoint(tmp_path, "isolated", label, horizon_close, isolated_end, horizon_close)
    contaminated = _horizon_checkpoint(tmp_path, "contaminated", label, contaminated_end, contaminated_end, horizon_close)
    for field in _PRICE_FIELDS:
        assert getattr(isolated, field) == getattr(contaminated, field), (label.value, field)
    assert isolated.evaluated_through == horizon_close  # bounded to ITS close, not to the data that happened to exist


# ----------------------------------------------------------------------------
# calendar: forward and replay share one definition
# ----------------------------------------------------------------------------


def _at(t0: datetime, base: ResearchObservation) -> ResearchObservation:
    return base.model_copy(update={"generated_at": t0})


@pytest.mark.parametrize(
    ("scenario", "t0", "holidays", "specials"),
    [
        ("normal in-session", datetime(2026, 8, 28, 6, 0, tzinfo=UTC), None, None),
        ("after the close", datetime(2026, 8, 28, 11, 30, tzinfo=UTC), None, None),  # Fri 17:00 IST
        ("on a weekend", datetime(2026, 8, 29, 6, 0, tzinfo=UTC), None, None),
        ("across a holiday", datetime(2026, 8, 28, 6, 0, tzinfo=UTC), frozenset({date(2026, 9, 1)}), None),
        ("across a special session", datetime(2026, 11, 6, 6, 0, tzinfo=UTC), None, frozenset({date(2026, 11, 8)})),
    ],
)
def test_forward_and_replay_use_the_same_session_and_close_definition(
    tmp_path: Path, scenario: str, t0: datetime, holidays: frozenset[date] | None, specials: frozenset[date] | None,
) -> None:
    obs, _, _ = _captured(tmp_path)
    moved = _at(t0, obs)
    configure_nse_holidays(holidays)
    configure_nse_special_sessions(specials)
    replay_horizons = {1: OutcomeHorizonLabel.PLUS_1D, 3: OutcomeHorizonLabel.PLUS_3D, 5: OutcomeHorizonLabel.PLUS_5D}
    forward_labels = {1: ResearchCheckpointLabel.PLUS_1_SESSION, 3: ResearchCheckpointLabel.PLUS_3_SESSIONS, 5: ResearchCheckpointLabel.PLUS_5_SESSIONS}
    for sessions, horizon in replay_horizons.items():
        target = horizon_target_timestamp(moved, horizon)  # replay's horizon instant
        assert target == horizon_close_utc(moved, sessions), scenario
        # the forward sweep's due boundary is that same instant, to the second
        assert forward_labels[sessions] not in due_research_checkpoints(moved, set(), as_of=target - timedelta(seconds=1)), scenario
        assert forward_labels[sessions] in due_research_checkpoints(moved, set(), as_of=target), scenario


def test_a_special_session_close_is_never_earlier_than_the_session_trades(tmp_path: Path) -> None:
    """Muhurat-style special sessions trade at hours the calendar file does not record (only the
    date). Treating them as closing at 15:30 IST would mark the horizon due -- and freeze it as
    insufficient -- before the session traded. The close is therefore the end of that IST day."""
    configure_nse_special_sessions(frozenset({date(2026, 8, 29)}))  # a Saturday
    assert session_close_utc(date(2026, 8, 29)) == datetime(2026, 8, 29, 18, 29, 59, tzinfo=UTC)  # 23:59:59 IST
    assert session_close_utc(date(2026, 8, 31)) == datetime(2026, 8, 31, 10, 0, tzinfo=UTC)  # ordinary day unchanged

    _obs, repo, _ = _captured(tmp_path)  # T0 Friday 28 Aug 15:30 IST; +1 is the special Saturday
    clock = Clock(datetime(2026, 8, 29, 10, 0, tzinfo=UTC))  # Saturday 15:30 IST: session may not have traded yet
    scheduler = _scheduler(_runner(repo, tmp_path), clock)
    _tick(scheduler)
    assert asyncio.run(repo.query_all_checkpoints()) == []
    clock.now = datetime(2026, 8, 29, 18, 30, tzinfo=UTC)  # just past 23:59:59 IST
    _tick(scheduler)
    (cp,) = asyncio.run(repo.query_all_checkpoints())
    assert cp.checkpoint_label == ResearchCheckpointLabel.PLUS_1_SESSION and cp.target_trading_session_date == date(2026, 8, 29)


def test_a_special_session_is_not_a_live_capture_session() -> None:
    """Fail-closed by construction: `classify_session_window` only treats 09:15-15:30 IST as live, so
    a special evening session cannot be captured as a forward observation (documented limitation)."""
    from app.domain.market.trading_calendar import classify_session_window

    configure_nse_special_sessions(frozenset({date(2026, 11, 8)}))
    assert classify_session_window(datetime(2026, 11, 8, 12, 45, tzinfo=UTC)).research_session_mode != "LIVE"


# ----------------------------------------------------------------------------
# persistence: torn writes, races, dataset audit
# ----------------------------------------------------------------------------


def _tear(path: Path) -> None:
    """Simulate a process killed mid-append: half of the last record, no newline."""
    data = path.read_bytes()
    path.write_bytes(data + data[: len(data) // 2])


def test_a_torn_trailing_line_neither_kills_reads_nor_swallows_the_next_record(tmp_path: Path) -> None:
    import dataclasses

    from app.orchestration.daily_research import build_research_observation, build_research_thesis

    candidate, observation = fc._candidate(tmp_path / "a")
    repo = JsonlResearchOutcomeRepository(tmp_path / "journal")
    first_id = fc._capture(repo, candidate, observation).observation_id
    path = tmp_path / "journal" / "research_observations.jsonl"
    _tear(path)

    assert len(asyncio.run(repo.query_all_observations())) == 1  # the fragment is skipped, not fatal
    assert asyncio.run(repo.count_malformed_lines()) == 1  # ... and visible

    earlier = dataclasses.replace(candidate, response=candidate.response.model_copy(update={"generated_at": T0 - timedelta(minutes=10)}))
    second = build_research_observation(earlier, build_research_thesis(earlier), run_id="r2", coverage_classification="HIGH")
    outcome = fc._capture(repo, earlier, second, persisted_at=T0 + timedelta(seconds=5))
    assert outcome.status.value == "CAPTURED"
    # CAPTURED must mean readable: the new record is on its own line, not glued to the fragment
    assert {o.observation_id for o in asyncio.run(repo.query_all_observations())} == {first_id, outcome.observation_id}
    assert len(asyncio.run(repo.query_all_observations())) == 2


def test_a_torn_checkpoint_line_leaves_the_horizon_uncaptured_and_the_next_sweep_recreates_it(tmp_path: Path) -> None:
    _obs, repo, obs_path = _captured(tmp_path)
    clock = Clock(PLUS1_CLOSE)
    scheduler = _scheduler(_runner(repo, tmp_path), clock)
    _tick(scheduler)
    cp_path = obs_path.parent / "research_outcome_checkpoints.jsonl"
    complete = cp_path.read_bytes()
    cp_path.write_bytes(complete[: len(complete) // 2])  # the write was cut in half; no trailing newline
    assert asyncio.run(repo.query_all_checkpoints()) == []
    assert _tick(scheduler).value == "DEGRADED"  # the malformed line is reported, not hidden
    assert scheduler.status_payload()["last_result"]["malformed_lines"] == 1
    assert len(asyncio.run(repo.query_all_checkpoints())) == 1  # +1 was recreated (deterministically)
    assert asyncio.run(repo.query_all_checkpoints())[0].evaluated_through == PLUS1_CLOSE


def test_concurrent_writers_in_one_process_persist_a_single_logical_record(tmp_path: Path) -> None:
    candidate, observation = fc._candidate(tmp_path / "a")
    repo = JsonlResearchOutcomeRepository(tmp_path / "journal")
    from app.orchestration.forward_capture import assess_forward_capture

    forward, reasons = assess_forward_capture(candidate, observation, persisted_at=T0 + timedelta(seconds=2))
    assert forward is not None and reasons == ()
    with ThreadPoolExecutor(max_workers=8) as pool:
        wrote = list(pool.map(lambda _: asyncio.run(repo.save_observation_once(forward)), range(16)))
    assert wrote.count(True) == 1  # SINGLE-PROCESS guarantee (a lock); cross-process atomicity is NOT certified
    assert len(asyncio.run(repo.query_all_observations())) == 1


def test_the_dataset_audit_detects_duplicates_orphans_causality_and_malformed_lines(tmp_path: Path) -> None:
    _obs, repo, obs_path = _captured(tmp_path)
    _tick(_scheduler(_runner(repo, tmp_path), Clock(PLUS1_CLOSE)))
    assert asyncio.run(audit_research_dataset(repo)).clean

    cp_path = obs_path.parent / "research_outcome_checkpoints.jsonl"
    cp_line = cp_path.read_text(encoding="utf-8").splitlines()[0]
    import json

    orphan = json.dumps({**json.loads(cp_line), "observation_id": "0" * 32})
    early = json.dumps({**json.loads(cp_line), "checkpoint_label": "PLUS_3_SESSIONS", "captured_at": "2026-08-31T10:00:00Z", "evaluated_through": PLUS3_CLOSE.isoformat()})
    with cp_path.open("a", encoding="utf-8") as f:
        f.write(f"{cp_line}\n{orphan}\n{early}\n")  # duplicate key, orphan, checkpoint dated before its horizon
    with obs_path.open("a", encoding="utf-8") as f:
        f.write(obs_path.read_text(encoding="utf-8").splitlines()[0] + "\n{not json\n")  # duplicate observation id + a malformed line
    audit = asyncio.run(audit_research_dataset(repo))
    assert not audit.clean
    assert (audit.duplicate_checkpoint_keys, audit.orphan_checkpoints, audit.checkpoint_causality_violations) == (1, 1, 1)
    assert audit.duplicate_observation_ids == 1 and audit.malformed_lines == 1


def test_the_audit_flags_a_forward_observation_that_broke_the_quality_contract(tmp_path: Path) -> None:
    obs, repo, obs_path = _captured(tmp_path)
    bad = obs.model_copy(update={"selected_strike": "1100.0"})  # identity no longer recomputes
    with obs_path.open("a", encoding="utf-8") as f:
        f.write(bad.model_copy(update={"observation_id": "f" * 32}).model_dump_json() + "\n")
    audit = asyncio.run(audit_research_dataset(repo))
    assert audit.forward_invalid == 1 and audit.invalid_reasons.get("IDENTITY_MISMATCH") == 1 and not audit.clean
    # the fixture runs without an audit journal, so its T0 has no snapshot reference: reported, not hidden
    assert audit.forward_without_audit_id == 2


# ----------------------------------------------------------------------------
# INVARIANTS 18-19: historical replay vs forward capture
# ----------------------------------------------------------------------------


def test_source_and_capture_provenance_can_only_combine_in_three_ways(tmp_path: Path) -> None:
    obs, _, _ = _captured(tmp_path)
    forward = obs
    legacy = obs.model_copy(update={"forward_capture": None})
    replay = obs.model_copy(update={"forward_capture": None, "source": "REPLAY"})
    assert [observation_capture_kind(o) for o in (forward, legacy, replay)] == [FORWARD_LIVE_CAPTURE, LEGACY_LIVE_UNVERIFIED, HISTORICAL_REPLAY]
    with pytest.raises(ValueError, match="source='LIVE'"):  # a replay observation can never carry a live-capture record ...
        ResearchObservation.model_validate({**forward.model_dump(), "source": "REPLAY"})
    # ... and a forward capture can never be presented as replay or as unverified legacy by verification
    assert verify_forward_observation(replay) == ("NOT_A_FORWARD_CAPTURE",) == verify_forward_observation(legacy)
    assert verify_forward_observation(forward) == ()


def test_the_replay_path_never_produces_forward_provenance(tmp_path: Path) -> None:
    from tests.integration.orchestration.test_historical_replay import (
        _SESSION_START,
        _reclaim_candles,
    )
    from tests.integration.orchestration.test_historical_validation_contract import _replay

    result = _replay(tmp_path, _reclaim_candles(_SESSION_START))
    assert result.observations
    assert all(o.forward_capture is None and o.source == "REPLAY" for o in result.observations)
    assert all(observation_capture_kind(o) == HISTORICAL_REPLAY for o in result.observations)


# ----------------------------------------------------------------------------
# INVARIANT 20: Watch is a separate store
# ----------------------------------------------------------------------------


def test_watch_and_research_history_cannot_mutate_each_other(tmp_path: Path) -> None:
    from app.orchestration.research_watch import ResearchWatchService, observation_from_client

    _obs, repo, obs_path = _captured(tmp_path / "research")
    payload = fc._analyze(tmp_path / "watch").model_dump(mode="json")
    snapshot = observation_from_client(payload, kind="analyze")
    assert snapshot is not None
    watch_dir = tmp_path / "watches"
    service = ResearchWatchService(JsonlWatchRecordRepository(watch_dir))
    view = asyncio.run(service.create(symbol="SYMBOLQ", query="SYMBOLQ", observation=snapshot, t0_unavailable=False))
    watch_path = watch_dir / "research_watches.jsonl"
    watch_bytes, research_bytes = watch_path.read_bytes(), obs_path.read_bytes()

    # research outcomes never touch the Watch store
    _tick(_scheduler(_runner(repo, tmp_path / "r"), Clock(PLUS3_CLOSE)))
    assert watch_path.read_bytes() == watch_bytes
    # a Watch update never touches the research T0 or its outcomes
    outcomes_bytes = (obs_path.parent / "research_outcome_checkpoints.jsonl").read_bytes()
    asyncio.run(service.update_latest(view.watch_id, snapshot.model_copy(update={"research_state": "CHANGED"})))
    assert obs_path.read_bytes() == research_bytes
    assert (obs_path.parent / "research_outcome_checkpoints.jsonl").read_bytes() == outcomes_bytes
    assert watch_path.read_bytes() != watch_bytes  # the watch did record its own update


def test_watch_modules_reference_no_research_observation_or_outcome_write_api() -> None:
    forbidden = {"ResearchObservation", "JsonlResearchOutcomeRepository", "save_observation", "save_observation_once", "save_checkpoint", "save_checkpoint_once", "research_models"}
    for rel in ("app/orchestration/research_watch.py", "app/domain/research/watch_record.py"):
        tree = ast.parse((_APP_ROOT / rel).read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        names |= {n.module.split(".")[-1] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not names & forbidden, (rel, names & forbidden)


# ----------------------------------------------------------------------------
# INVARIANTS 23-24: LLM and broker cannot reach the research record
# ----------------------------------------------------------------------------

_LIFECYCLE_ROOTS = (
    "app.orchestration.forward_capture", "app.orchestration.outcome_scheduler", "app.orchestration.research_outcome",
    "app.orchestration.outcome_horizons", "app.orchestration.outcome_sessions", "app.domain.audit.research_models",
    "app.persistence.jsonl_file",
)


def _import_closure(roots: tuple[str, ...]) -> dict[str, str]:
    seen: dict[str, str] = {}
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen or not name.startswith("app"):
            continue
        base = _APP_ROOT / name.replace(".", "/")
        path = base.with_suffix(".py") if base.with_suffix(".py").exists() else base / "__init__.py"
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        seen[name] = source
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                stack.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                stack.append(node.module)
                stack.extend(f"{node.module}.{a.name}" for a in node.names)
    return seen


def test_the_llm_is_outside_the_research_record_import_closure() -> None:
    closure = _import_closure(_LIFECYCLE_ROOTS)
    assert len(closure) > 20  # the walk really traversed the lifecycle
    assert not [m for m in closure if m.startswith("app.llm") or "qwen" in m.lower()]
    # ... and the narrative adapter itself imports nothing that can write research records
    llm_tree = ast.parse((_APP_ROOT / "app/llm/qwen_narrative.py").read_text(encoding="utf-8"))
    llm_imports = {n.module or "" for n in ast.walk(llm_tree) if isinstance(n, ast.ImportFrom)}
    assert not [m for m in llm_imports if m.startswith(("app.persistence", "app.orchestration", "app.domain.audit"))]


def test_no_llm_call_happens_anywhere_in_the_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm.qwen_narrative import QwenNarrativeAdapter

    def _boom(*_: object, **__: object) -> None:
        raise AssertionError("the LLM adapter was called inside the research lifecycle")

    for attr in dir(QwenNarrativeAdapter):
        if not attr.startswith("_") and callable(getattr(QwenNarrativeAdapter, attr)):
            monkeypatch.setattr(QwenNarrativeAdapter, attr, _boom)
    obs, repo, _ = _run_loop(tmp_path, "nollm")
    assert len(asyncio.run(repo.query_all_checkpoints())) == 3 and obs.forward_capture is not None


def test_broker_execution_is_unreachable_from_the_research_lifecycle() -> None:
    closure = _import_closure(_LIFECYCLE_ROOTS)
    for module, source in closure.items():
        tree = ast.parse(source)
        defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert not (defined | called) & {"place_order", "modify_order", "cancel_order", "submit_order"}, module
    import app.api.main as main_module

    app = main_module.create_app(lifespan=_noop_lifespan)
    assert not [getattr(r, "path", "") for r in app.routes if "order" in getattr(r, "path", "").lower()]
    from app.config.settings import Settings

    with pytest.raises(Exception):  # noqa: B017 -- the broker lock refuses execution being enabled
        Settings(broker_order_execution_enabled=True).assert_broker_execution_disabled()


@asynccontextmanager
async def _noop_lifespan(app: Any) -> AsyncIterator[None]:
    yield


# ----------------------------------------------------------------------------
# INVARIANTS 21-22 and the neutral/unknown ambiguity
# ----------------------------------------------------------------------------


def test_voting_policy_and_availability_are_unchanged_and_availability_cannot_vote() -> None:
    assert VOTING_GROUPS == {
        EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceGroup.FUTURES, EvidenceGroup.OPTIONS_OI,
        EvidenceGroup.GLOBAL, EvidenceGroup.RELATIVE_STRENGTH,
    }
    assert EvidenceGroup.OPTIONS_IV not in VOTING_GROUPS
    assert {c.value for c in EvidenceClass}.isdisjoint({g.value for g in EvidenceGroup})  # an availability class is not a voting group


def test_the_legacy_neutral_or_unknown_list_cannot_reach_the_forward_dataset() -> None:
    """`neutral_or_unknown_evidence` lumps NEUTRAL and UNKNOWN rows; it exists only in the replay
    dataset's "knew then" block. The forward record carries Sprint 3.2's per-class availability
    instead, and the forward lifecycle never imports the replay dataset builder."""
    assert "neutral_or_unknown_evidence" not in ResearchObservation.model_fields
    closure = _import_closure(_LIFECYCLE_ROOTS)
    assert "app.orchestration.replay_dataset" not in closure
    assert all("neutral_or_unknown_evidence" not in source for source in closure.values())
