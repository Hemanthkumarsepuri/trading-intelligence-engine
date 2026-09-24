"""Sprint 3.5 -- the scheduled outcome sweep: trigger, restart, missed-tick,
overlap, failure, observability and certification of the whole
T0 -> +1 -> +3 -> +5 loop.

No test depends on the wall clock: the scheduler is driven by an injected clock
and `run_once()` (the single function the production loop also calls). The
sweep itself is the REAL canonical sweep on the repository's live-shape mock
fixture -- the scheduler under test is never stubbed out of the loop except
where a test is specifically about failure or overlap.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.main as main_module
import app.config.settings as settings_module
import app.orchestration.outcome_scheduler as scheduler_module
from app.domain.audit.research_models import (
    HorizonState,
    ResearchCheckpointLabel,
    ResearchObservation,
    ResearchOutcomeCheckpoint,
)
from app.domain.market.trading_calendar import configure_nse_holidays
from app.domain.options.evidence_availability import AvailabilityState, EvidenceClass
from app.domain.options.evidence_matrix import VOTING_GROUPS, EvidenceGroup
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.outcome_scheduler import OutcomeSweepScheduler, SweepHealth
from app.orchestration.research_outcome import (
    OutcomeSweepResult,
    build_horizon_progress,
    sweep_all_due_research_outcomes_detailed,
)
from app.persistence.jsonl_file import JsonlResearchOutcomeRepository
from tests.integration.api.test_dashboard_api import _configured_app
from tests.integration.orchestration import test_research_outcome as ro
from tests.integration.orchestration.test_outcome_progression import (
    _QUIET_CONFIG,
    PLUS1_CLOSE,
    PLUS3_CLOSE,
    PLUS5_CLOSE,
    T0,
    _captured,
    _provider,
    _rows,
)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


RowsFor = Callable[[datetime], list[list[object]]]


def _result(as_of: datetime, *, total: int = 0) -> OutcomeSweepResult:
    return OutcomeSweepResult(
        as_of=as_of, observations_total=total, observations_examined=0, observations_still_awaiting=0,
        observations_failed=0, horizons_created=0, horizons_created_insufficient=0, horizons_already_complete=0,
        horizons_not_yet_due=0, horizons_due_unresolved=0, insufficient_horizons_total=0,
    )


def _runner(
    repo: JsonlResearchOutcomeRepository, tmp_path: Path, *, rows_for: RowsFor = _rows, quotes_status: int = 200,
) -> Callable[[datetime], Awaitable[OutcomeSweepResult]]:
    """The production runner's body, without the worker thread: the canonical
    detailed sweep with the mock-transport provider."""
    calls = {"n": 0}

    async def run(as_of: datetime) -> OutcomeSweepResult:
        calls["n"] += 1
        return await sweep_all_due_research_outcomes_detailed(
            as_of=as_of, provider=_provider(rows_for(as_of), quotes_status=quotes_status), instrument_master=ro._MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=ro._repos(tmp_path / f"repos-{calls['n']}"),
            config=_QUIET_CONFIG, outcome_repository=repo,
        )

    return run


def _scheduler(runner: Callable[[datetime], Awaitable[OutcomeSweepResult]], clock: Clock) -> OutcomeSweepScheduler:
    return OutcomeSweepScheduler(runner, clock=clock)


async def _await(awaitable: Awaitable[OutcomeSweepResult]) -> OutcomeSweepResult:
    return await awaitable


def _tick(scheduler: OutcomeSweepScheduler) -> SweepHealth:
    return asyncio.run(scheduler.run_once())


def _checkpoints(repo: JsonlResearchOutcomeRepository) -> list[ResearchOutcomeCheckpoint]:
    return asyncio.run(repo.query_all_checkpoints())


# -- 1-2: it triggers the canonical sweep and contains no research logic ------


def test_a_tick_invokes_the_canonical_sweep_with_the_real_clock_only(tmp_path: Path) -> None:
    seen: list[datetime] = []

    async def spy(as_of: datetime) -> OutcomeSweepResult:
        seen.append(as_of)
        return _result(as_of)

    clock = Clock(PLUS1_CLOSE)
    scheduler = _scheduler(spy, clock)
    assert _tick(scheduler) == SweepHealth.OPERATIONAL
    clock.now = PLUS3_CLOSE
    _tick(scheduler)
    assert seen == [PLUS1_CLOSE, PLUS3_CLOSE]  # exactly what the clock said; no caller-chosen or future instant


def test_production_wiring_runs_the_canonical_sweep_on_a_worker_thread(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_canonical(**kwargs: object) -> OutcomeSweepResult:
        captured.update(kwargs)
        return _result(kwargs["as_of"])  # type: ignore[arg-type]

    monkeypatch.setattr(main_module, "sweep_all_due_research_outcomes_detailed", fake_canonical)
    app = _configured_app(tmp_path, provider=_provider([]), instrument_master=ro._MASTER)
    app.state.outcome_repository = JsonlResearchOutcomeRepository(tmp_path / "journal")
    runner = main_module._worker_thread_sweep_runner(app)
    assert runner is not None
    try:
        result: OutcomeSweepResult = asyncio.run(_await(runner(PLUS1_CLOSE)))
    finally:
        app.state.outcome_sweep_executor.shutdown(wait=True)
    assert result.as_of == PLUS1_CLOSE
    assert captured["as_of"] == PLUS1_CLOSE and captured["outcome_repository"] is app.state.outcome_repository


def test_the_scheduler_module_contains_no_research_logic() -> None:
    tree = ast.parse(inspect.getsource(scheduler_module))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert imported & {"due_research_checkpoints", "compute_price_path_outcome", "ResearchObservation", "horizon_close_utc"} == set()
    for forbidden in (
        "due_research_checkpoints", "compute_price_path_outcome_from_bars", "save_checkpoint_once", "save_observation",
        "place_order", "modify_order", "cancel_order", "run_analysis", "qwen", "win_rate", "probability",
    ):
        assert not any(forbidden in str(n).lower() for n in names), forbidden


# -- 3-9, 21-24: due semantics, missed tick, restart, idempotency ------------


def test_a_tick_with_nothing_persisted_is_a_clean_no_op(tmp_path: Path) -> None:
    repo = JsonlResearchOutcomeRepository(tmp_path / "journal")
    scheduler = _scheduler(_runner(repo, tmp_path), Clock(PLUS1_CLOSE))
    assert _tick(scheduler) == SweepHealth.OPERATIONAL
    status = scheduler.status_payload()
    assert status["last_result"]["horizons_created"] == 0 and status["outcomes_complete"] is None  # nothing to complete


def test_plus_one_is_created_at_the_target_close_and_never_before(tmp_path: Path) -> None:
    obs, repo, _ = _captured(tmp_path)
    clock = Clock(PLUS1_CLOSE - timedelta(minutes=1))  # 15:29 IST
    scheduler = _scheduler(_runner(repo, tmp_path), clock)
    for early in (T0 + timedelta(hours=1), datetime(2026, 8, 31, 4, 0, tzinfo=T0.tzinfo), PLUS1_CLOSE - timedelta(minutes=1)):
        clock.now = early
        _tick(scheduler)
        assert _checkpoints(repo) == []  # 09:30 IST, 15:29 IST, ...: the +1 session has not closed
    clock.now = PLUS1_CLOSE
    _tick(scheduler)
    assert [c.checkpoint_label for c in _checkpoints(repo)] == [ResearchCheckpointLabel.PLUS_1_SESSION]
    assert scheduler.status_payload()["last_result"]["horizons_not_yet_due"] == 2  # +3 and +5 wait, honestly
    assert obs.observation_id == _checkpoints(repo)[0].observation_id


def test_weekend_and_holiday_ticks_create_nothing_early(tmp_path: Path) -> None:
    obs, repo, _ = _captured(tmp_path)
    clock = Clock(datetime(2026, 8, 29, 10, 0, tzinfo=T0.tzinfo))  # Saturday 15:30 IST
    scheduler = _scheduler(_runner(repo, tmp_path), clock)
    _tick(scheduler)
    clock.now = datetime(2026, 8, 30, 10, 0, tzinfo=T0.tzinfo)  # Sunday
    _tick(scheduler)
    assert _checkpoints(repo) == []  # weekends never count as the +1 session
    try:
        configure_nse_holidays(frozenset({date(2026, 9, 1)}))  # Tuesday holiday -> +3 moves from Wed to Thu
        clock.now = PLUS3_CLOSE  # Wednesday 15:30 IST: +3 would have been due without the holiday
        _tick(scheduler)
        assert [c.checkpoint_label for c in _checkpoints(repo)] == [ResearchCheckpointLabel.PLUS_1_SESSION]
        clock.now = datetime(2026, 9, 3, 10, 0, tzinfo=T0.tzinfo)  # Thursday close
        _tick(scheduler)
        assert {c.checkpoint_label for c in _checkpoints(repo)} == {
            ResearchCheckpointLabel.PLUS_1_SESSION, ResearchCheckpointLabel.PLUS_3_SESSIONS,
        }
    finally:
        configure_nse_holidays(None)
    assert obs.generated_at == T0


def test_a_missed_tick_is_recovered_by_the_next_one(tmp_path: Path) -> None:
    _obs, repo, _ = _captured(tmp_path)
    clock = Clock(T0 + timedelta(hours=1))
    scheduler = _scheduler(_runner(repo, tmp_path), clock)
    _tick(scheduler)  # ordinary tick before the horizon
    # 15:30 IST passes with no tick at all (the trigger was down/failed) ...
    clock.now = PLUS1_CLOSE + timedelta(minutes=15)  # ... the next tick is 15:45 IST
    _tick(scheduler)
    (checkpoint,) = _checkpoints(repo)
    assert checkpoint.checkpoint_label == ResearchCheckpointLabel.PLUS_1_SESSION
    assert checkpoint.evaluated_through == PLUS1_CLOSE  # evaluated at the horizon, not at the tick time
    assert checkpoint.captured_late is False  # still before the next session opened: state fields valid


def test_a_horizon_that_came_due_while_the_application_was_down_is_found_on_startup(tmp_path: Path) -> None:
    _obs, _repo, obs_path = _captured(tmp_path)
    # The application is "down" through +1 and +3. A fresh process starts after +3 closed.
    restarted_repo = JsonlResearchOutcomeRepository(obs_path.parent)
    clock = Clock(PLUS3_CLOSE + timedelta(minutes=30))
    scheduler = _scheduler(_runner(restarted_repo, tmp_path), clock)
    _tick(scheduler)
    got = {c.checkpoint_label: c for c in _checkpoints(restarted_repo)}
    assert set(got) == {ResearchCheckpointLabel.PLUS_1_SESSION, ResearchCheckpointLabel.PLUS_3_SESSIONS}
    assert got[ResearchCheckpointLabel.PLUS_1_SESSION].captured_late is True  # honest: a later session had opened
    assert got[ResearchCheckpointLabel.PLUS_1_SESSION].next_observed_early_stage_state is None
    assert got[ResearchCheckpointLabel.PLUS_3_SESSIONS].captured_late is False


def test_repeated_ticks_and_restarts_never_duplicate_a_checkpoint(tmp_path: Path) -> None:
    _obs, repo, obs_path = _captured(tmp_path)
    clock = Clock(PLUS1_CLOSE)
    scheduler = _scheduler(_runner(repo, tmp_path), clock)
    _tick(scheduler)
    _tick(scheduler)
    for _ in range(3):  # a process restart each time: no in-memory state carries over
        fresh_repo = JsonlResearchOutcomeRepository(obs_path.parent)
        _tick(_scheduler(_runner(fresh_repo, tmp_path), clock))
    cp_path = obs_path.parent / "research_outcome_checkpoints.jsonl"
    assert len(cp_path.read_text(encoding="utf-8").splitlines()) == 1
    assert scheduler.status_payload()["last_result"]["horizons_created"] == 0  # the second tick found +1 already complete
    assert scheduler.status_payload()["last_result"]["horizons_already_complete"] == 1


def test_the_full_t0_to_plus_five_loop_progresses_automatically(tmp_path: Path) -> None:
    obs, repo, _obs_path = _captured(tmp_path)
    clock = Clock(PLUS1_CLOSE)
    scheduler = _scheduler(_runner(repo, tmp_path), clock)
    seen_states = []
    for now in (PLUS1_CLOSE, PLUS3_CLOSE, PLUS5_CLOSE):
        clock.now = now
        _tick(scheduler)
        seen_states.append([h.state for h in build_horizon_progress(obs, _checkpoints(repo), as_of=now)])
    assert seen_states == [
        [HorizonState.AVAILABLE, HorizonState.PENDING, HorizonState.PENDING],
        [HorizonState.AVAILABLE, HorizonState.AVAILABLE, HorizonState.PENDING],
        [HorizonState.AVAILABLE, HorizonState.AVAILABLE, HorizonState.AVAILABLE],
    ]
    assert scheduler.status_payload()["last_result"]["observations_awaiting_horizons"] == 0
    assert scheduler.status_payload()["outcomes_complete"] is True  # everything captured AND nothing lacked price data


# -- 10: overlap ---------------------------------------------------------------


def test_a_tick_arriving_during_a_running_sweep_is_skipped_not_run_twice() -> None:
    async def scenario() -> tuple[int, dict[str, object]]:
        gate = asyncio.Event()
        calls = 0

        async def slow(as_of: datetime) -> OutcomeSweepResult:
            nonlocal calls
            calls += 1
            await gate.wait()
            return _result(as_of)

        scheduler = _scheduler(slow, Clock(PLUS1_CLOSE))
        first = asyncio.create_task(scheduler.run_once())
        await asyncio.sleep(0)  # the first sweep is now in flight
        skipped = await scheduler.run_once()
        gate.set()
        await first
        return calls, {**scheduler.status_payload(), "skipped_result": skipped.value}

    # bounded: without an overlap guard the second tick would wait on the running sweep forever
    calls, status = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert calls == 1 and status["overlaps_skipped"] == 1 and status["attempts"] == 1


def test_simultaneous_ticks_on_the_real_sweep_yield_one_checkpoint(tmp_path: Path) -> None:
    _obs, repo, _obs_path = _captured(tmp_path)
    scheduler = _scheduler(_runner(repo, tmp_path), Clock(PLUS1_CLOSE))

    async def scenario() -> None:
        await asyncio.gather(scheduler.run_once(), scheduler.run_once(), scheduler.run_once())

    asyncio.run(scenario())
    assert len(_checkpoints(repo)) == 1
    status = scheduler.status_payload()
    # Whether ticks interleave depends on where the mock transport suspends; either way each of the
    # three ticks was either run or skipped, and the horizon exists exactly once. (The overlap guard
    # itself is pinned deterministically by the gated-runner test above.)
    assert status["attempts"] + status["overlaps_skipped"] == 3


def test_two_processes_sweeping_the_same_horizon_still_persist_it_once(tmp_path: Path) -> None:
    """No distributed lock exists or is pretended: duplicate execution is made
    safe by the idempotent persistence instead."""
    _obs, repo, obs_path = _captured(tmp_path)
    other = JsonlResearchOutcomeRepository(obs_path.parent)  # a second "process" on the same files
    a = _scheduler(_runner(repo, tmp_path / "a"), Clock(PLUS1_CLOSE))
    b = _scheduler(_runner(other, tmp_path / "b"), Clock(PLUS1_CLOSE))
    _tick(a)
    _tick(b)
    assert len(_checkpoints(repo)) == 1


# -- 12-15, 28-30: failure, retry, insufficient data, honest status ----------


def test_a_failed_evaluation_persists_nothing_reports_degraded_and_is_retried(tmp_path: Path) -> None:
    _obs, repo, _ = _captured(tmp_path)
    clock = Clock(PLUS1_CLOSE)
    bad = _scheduler(_runner(repo, tmp_path / "bad", quotes_status=503), clock)
    assert _tick(bad) == SweepHealth.DEGRADED
    assert _checkpoints(repo) == []  # a failed evaluation is not a completed checkpoint
    assert bad.status_payload()["last_result"]["horizons_due_unresolved"] == 1
    assert bad.status_payload()["outcomes_complete"] is False
    good = _scheduler(_runner(repo, tmp_path / "good"), clock)
    assert _tick(good) == SweepHealth.OPERATIONAL
    assert len(_checkpoints(repo)) == 1


def test_a_persisted_checkpoint_is_never_recomputed_or_rewritten(tmp_path: Path) -> None:
    _obs, repo, obs_path = _captured(tmp_path)
    clock = Clock(PLUS1_CLOSE)
    scheduler = _scheduler(_runner(repo, tmp_path), clock)
    _tick(scheduler)
    cp_path = obs_path.parent / "research_outcome_checkpoints.jsonl"
    first_line = cp_path.read_text(encoding="utf-8").splitlines()[0]
    clock.now = PLUS5_CLOSE
    scheduler2 = _scheduler(_runner(repo, tmp_path, rows_for=lambda t: _rows(t, wild_after=PLUS1_CLOSE)), clock)
    _tick(scheduler2)  # later, with violent post-+1 data present
    lines = cp_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == first_line  # +1 byte-identical: later data cannot alter an earlier checkpoint
    assert len(lines) == 3


def test_insufficient_data_stays_insufficient_and_is_never_upgraded_or_called_complete(tmp_path: Path) -> None:
    _obs, repo, _obs_path = _captured(tmp_path)
    no_future = lambda t: _rows(t, stop_after=ro.OBSERVATION_AS_OF)
    clock = Clock(PLUS1_CLOSE)
    scheduler = _scheduler(_runner(repo, tmp_path, rows_for=no_future), clock)
    assert _tick(scheduler) == SweepHealth.OPERATIONAL  # the trigger worked ...
    (cp,) = _checkpoints(repo)
    assert cp.spot_at_checkpoint is None and cp.max_favorable_move_pct is None  # ... and the data was insufficient
    assert cp.confirmation_outcome is None and cp.invalidation_outcome is None and cp.progression.value == "UNKNOWN"
    status = scheduler.status_payload()
    assert status["last_result"]["horizons_created_insufficient"] == 1
    assert status["last_result"]["horizons_data_insufficient_total"] == 1

    # Data appears later. The persisted insufficient checkpoint is final -- never silently upgraded.
    clock.now = PLUS5_CLOSE
    later = _scheduler(_runner(repo, tmp_path / "later"), clock)
    _tick(later)
    still = {c.checkpoint_label: c for c in _checkpoints(repo)}[ResearchCheckpointLabel.PLUS_1_SESSION]
    assert still == cp
    final = later.status_payload()
    assert final["last_result"]["observations_awaiting_horizons"] == 0  # every horizon has a checkpoint ...
    assert final["outcomes_complete"] is False  # ... but a scheduler that ran does not make insufficient data complete
    assert final["last_result"]["horizons_data_insufficient_total"] == 1


def test_a_sweep_exception_never_escapes_and_status_reports_it_honestly_without_details() -> None:
    async def scenario() -> tuple[SweepHealth, SweepHealth, dict[str, object]]:
        outcomes = iter([RuntimeError("secret token=abc /etc/passwd"), None])

        async def flaky(as_of: datetime) -> OutcomeSweepResult:
            failure = next(outcomes)
            if failure is not None:
                raise failure
            return _result(as_of, total=3)

        scheduler = _scheduler(flaky, Clock(PLUS1_CLOSE))
        first = await scheduler.run_once()
        failed_status = scheduler.status_payload()
        second = await scheduler.run_once()
        return first, second, {"failed": failed_status, "recovered": scheduler.status_payload()}

    first, second, status = asyncio.run(scenario())
    assert (first, second) == (SweepHealth.FAILED, SweepHealth.OPERATIONAL)
    failed = status["failed"]
    assert isinstance(failed, dict)
    assert failed["health"] == "FAILED" and failed["last_error_type"] == "RuntimeError" and failed["consecutive_failures"] == 1
    assert "secret" not in str(failed) and "passwd" not in str(failed)  # type only: no message, no stack
    recovered = status["recovered"]
    assert isinstance(recovered, dict)
    assert recovered["consecutive_failures"] == 0 and recovered["last_error_type"] is None


def test_one_observations_failure_does_not_stop_the_others(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.orchestration.research_outcome as outcome_module

    obs_a, repo, _obs_path = _captured(tmp_path)
    # A second, distinct forward observation (different T0) in the same journal.
    from tests.integration.orchestration import test_forward_capture as fc

    candidate, _observation = fc._candidate(tmp_path / "second")
    moved = candidate.response.model_copy(update={"generated_at": T0 - timedelta(minutes=10)})
    import dataclasses

    from app.orchestration.daily_research import build_research_observation, build_research_thesis
    from app.orchestration.forward_capture import CaptureStatus, capture_forward_observation

    second_candidate = dataclasses.replace(candidate, response=moved)
    second_obs = build_research_observation(
        second_candidate, build_research_thesis(second_candidate), run_id="r2", coverage_classification="HIGH",
    )
    assert asyncio.run(capture_forward_observation(repo, second_candidate, second_obs, persisted_at=T0)).status == CaptureStatus.CAPTURED
    real = outcome_module.sweep_due_research_outcomes

    async def explode_for_first(observation: ResearchObservation, **kwargs: object) -> list[ResearchOutcomeCheckpoint]:
        if observation.observation_id == obs_a.observation_id:
            raise ValueError("boom")
        return await real(observation, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(outcome_module, "sweep_due_research_outcomes", explode_for_first)
    scheduler = _scheduler(_runner(repo, tmp_path), Clock(PLUS1_CLOSE))
    assert _tick(scheduler) == SweepHealth.DEGRADED
    created = _checkpoints(repo)
    assert len(created) == 1 and created[0].observation_id != obs_a.observation_id  # the other observation was processed
    assert scheduler.status_payload()["last_result"]["observations_failed"] == 1


# -- 16-17, 26-27, 33-35: T0 immutability, persistence, determinism ----------


def test_the_observation_file_is_byte_identical_after_every_tick(tmp_path: Path) -> None:
    obs, repo, obs_path = _captured(tmp_path)
    before = obs_path.read_bytes()
    availability = obs.evidence_availability
    clock = Clock(PLUS1_CLOSE)
    scheduler = _scheduler(_runner(repo, tmp_path, rows_for=lambda t: _rows(t, wild_after=PLUS1_CLOSE)), clock)
    for now in (PLUS1_CLOSE, PLUS3_CLOSE, PLUS5_CLOSE, PLUS5_CLOSE + timedelta(days=3)):
        clock.now = now
        _tick(scheduler)
    assert obs_path.read_bytes() == before  # T0 file untouched
    assert (obs_path.parent / "research_outcome_checkpoints.jsonl").exists()  # outcomes live in a separate file
    reloaded = asyncio.run(JsonlResearchOutcomeRepository(obs_path.parent).get_observation(obs.observation_id))
    assert reloaded == obs and reloaded is not None and reloaded.evidence_availability == availability
    assert reloaded.evidence_availability.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.AVAILABLE  # type: ignore[union-attr]
    assert (reloaded.selected_strike, reloaded.selected_right, reloaded.direction) == ("1000.0", "CE", "BULLISH")
    assert reloaded.forward_capture is not None and reloaded.forward_capture.observed_expiry == ro.EXPIRY


def test_the_persisted_state_after_a_full_run_is_deterministic(tmp_path: Path) -> None:
    def full_run(name: str) -> tuple[bytes, bytes]:
        _obs, repo, obs_path = _captured(tmp_path / name)
        clock = Clock(PLUS1_CLOSE)
        scheduler = _scheduler(_runner(repo, tmp_path / name), clock)
        for now in (PLUS1_CLOSE, PLUS3_CLOSE, PLUS5_CLOSE):
            clock.now = now
            _tick(scheduler)
        return obs_path.read_bytes(), (obs_path.parent / "research_outcome_checkpoints.jsonl").read_bytes()

    assert full_run("one") == full_run("two")


def test_later_future_data_cannot_alter_an_earlier_checkpoint_via_the_scheduler(tmp_path: Path) -> None:
    _on_time_obs, on_time_repo, _ = _captured(tmp_path / "ontime")
    _tick(_scheduler(_runner(on_time_repo, tmp_path / "ontime"), Clock(PLUS1_CLOSE)))
    (on_time,) = _checkpoints(on_time_repo)

    _late_obs, late_repo, _ = _captured(tmp_path / "late")
    _tick(_scheduler(_runner(late_repo, tmp_path / "late", rows_for=lambda t: _rows(t, wild_after=PLUS1_CLOSE)), Clock(PLUS5_CLOSE)))
    late = {c.checkpoint_label: c for c in _checkpoints(late_repo)}[ResearchCheckpointLabel.PLUS_1_SESSION]
    for field in ("spot_at_checkpoint", "max_favorable_move_pct", "max_adverse_move_pct", "confirmation_outcome", "evaluated_through"):
        assert getattr(late, field) == getattr(on_time, field), field


# -- lifecycle, wiring, health -------------------------------------------------


def test_the_loop_waits_the_initial_delay_then_ticks_every_interval_and_stops_cleanly() -> None:
    async def scenario() -> tuple[list[float], int, bool, bool]:
        sleeps: list[float] = []
        ticks = 0
        stop_after = asyncio.Event()

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) >= 4:
                stop_after.set()
                await asyncio.sleep(3600)  # parked; stop() must cancel it
            await asyncio.sleep(0)

        async def runner(as_of: datetime) -> OutcomeSweepResult:
            nonlocal ticks
            ticks += 1
            return _result(as_of)

        scheduler = OutcomeSweepScheduler(
            runner, clock=Clock(PLUS1_CLOSE), sleep=fake_sleep, interval_seconds=900, initial_delay_seconds=30,
        )
        scheduler.start()
        scheduler.start()  # idempotent: still one task
        await stop_after.wait()
        running_before = scheduler.running
        await scheduler.stop()
        return sleeps, ticks, running_before, scheduler.running

    sleeps, ticks, running_before, running_after = asyncio.run(scenario())
    assert sleeps[:3] == [30, 900, 900] and ticks == 3
    assert running_before is True and running_after is False  # no orphan task


def test_lifespan_wiring_reports_health_starts_and_stops_the_trigger(tmp_path: Path) -> None:
    async def runner(as_of: datetime) -> OutcomeSweepResult:
        return _result(as_of)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.provider = None
        app.state.instrument_master = None
        main_module.start_outcome_sweep(app, runner=runner)
        try:
            yield
        finally:
            await main_module.stop_outcome_sweep(app)

    app = main_module.create_app(lifespan=lifespan)
    with TestClient(app) as client:
        body = client.get("/api/health").json()
        assert body["outcome_sweep"]["health"] == "NEVER_RAN"
        assert body["outcome_sweep"]["running"] is True
        assert body["outcome_sweep"]["outcomes_complete"] is None  # nothing has run: no green is claimed
        scheduler = app.state.outcome_sweep
    assert scheduler.running is False  # shutdown stopped the loop


def test_health_reports_not_configured_and_disabled_and_not_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    app = _configured_app(tmp_path, provider=None, instrument_master=None)
    assert TestClient(app).get("/api/health").json()["outcome_sweep"]["health"] == "NOT_STARTED"

    no_provider = _configured_app(tmp_path / "np", provider=None, instrument_master=None)
    scheduler = main_module.start_outcome_sweep(no_provider)  # no provider, no injected runner
    assert scheduler.health == SweepHealth.NOT_CONFIGURED and scheduler.running is False
    assert TestClient(no_provider).get("/api/health").json()["outcome_sweep"]["health"] == "NOT_CONFIGURED"

    monkeypatch.setattr(settings_module.settings, "outcome_sweep_enabled", False)
    disabled = _configured_app(tmp_path / "d", provider=_provider([]), instrument_master=ro._MASTER)
    assert main_module.start_outcome_sweep(disabled).health == SweepHealth.DISABLED


def test_there_is_no_unauthenticated_mutation_endpoint_for_the_sweep() -> None:
    app = main_module.create_app(lifespan=_noop)
    sweep_routes = [r for r in app.routes if "sweep" in getattr(r, "path", "")]
    assert sweep_routes == []  # status is on GET /api/health only; a manual trigger would open the known auth gap


@asynccontextmanager
async def _noop(app: FastAPI) -> AsyncIterator[None]:
    yield


# -- neighbours -----------------------------------------------------------------


def test_sprint_3_1_and_3_2_invariants_and_safety_are_unchanged() -> None:
    assert VOTING_GROUPS == {
        EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceGroup.FUTURES, EvidenceGroup.OPTIONS_OI,
        EvidenceGroup.GLOBAL, EvidenceGroup.RELATIVE_STRENGTH,
    }
    assert EvidenceGroup.OPTIONS_IV not in VOTING_GROUPS  # OPTIONS_IV still non-voting
    assert all(EvidenceGroup(g.value).value != "availability" for g in EvidenceGroup)  # availability is not a voting group
    assert [s.value for s in AvailabilityState] == ["AVAILABLE", "UNAVAILABLE", "INSUFFICIENT", "STALE", "CONFLICTING"]
    from app.config.settings import Settings

    with pytest.raises(Exception):  # noqa: B017 -- the broker lock refuses execution being enabled
        Settings(broker_order_execution_enabled=True).assert_broker_execution_disabled()
