"""Sprint 3.4 -- outcome & research progression.

Two layers:

* pure tests of the shared outcome computation (`outcome_sessions`,
  `outcome_horizons`) with explicit price bars -- MFE/MAE arithmetic, first
  confirmation / invalidation, same-bar ambiguity, missing data, and the
  adversarial no-lookahead property (a horizon's result must be identical
  whether or not later data exists);
* integration tests of the forward sweep on the repository's live-shape
  fixture: T0 immutability, idempotency, restart safety, partial progression,
  late capture, and equivalence with the replay computation.

Nothing here fabricates market history as if it were real: bars are explicit
test fixtures, and forward tests reuse the existing mock-transport fixture.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.audit.research_models import (
    HorizonState,
    ResearchCheckpointLabel,
    ResearchObservation,
    ResearchOutcomeCheckpoint,
    ResearchOutcomeStatus,
)
from app.domain.market.trading_calendar import configure_nse_holidays
from app.domain.options.evidence_availability import AvailabilityState, EvidenceClass
from app.domain.options.evidence_matrix import VOTING_GROUPS, EvidenceGroup
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.forward_capture import CaptureStatus, capture_forward_observation
from app.orchestration.options_intelligence_pipeline import PipelineConfig
from app.orchestration.outcome_horizons import (
    ConfirmationOutcome,
    InvalidationOutcome,
    OutcomeHorizonLabel,
    PriceBar,
    PricePathOutcome,
    compute_price_path_outcome,
    compute_price_path_outcome_from_bars,
    resolve_first_event,
)
from app.orchestration.outcome_sessions import (
    horizon_close_utc,
    horizon_session_date,
    next_session_open_after,
    target_trading_session_date,
)
from app.orchestration.research_outcome import (
    build_horizon_progress,
    build_research_outcome_detail,
    capture_research_outcome_checkpoint,
    due_research_checkpoints,
    summarize_research_outcome,
    sweep_all_due_research_outcomes,
    sweep_due_research_outcomes,
)
from app.persistence.jsonl_file import JsonlResearchOutcomeRepository
from tests.integration.orchestration import test_forward_capture as fc
from tests.integration.orchestration import test_research_outcome as ro

FRIDAY = date(2026, 8, 28)
T0 = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)  # Friday 15:30 IST
PLUS1_CLOSE = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)  # Monday
PLUS3_CLOSE = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)  # Wednesday
PLUS5_CLOSE = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)  # Friday


# ----------------------------------------------------------------------------
# pure layer: sessions
# ----------------------------------------------------------------------------


def _obs(*, direction: str = "BULLISH", spot: str | None = "1000", right: str = "CE") -> ResearchObservation:
    return ResearchObservation(
        run_id="r", audit_id=None, generated_at=T0, symbol="X", direction=direction, selected_right=right, selected_strike="1000",
        early_stage_state="EARLY_DIRECTIONAL_BUILD", research_confidence="MODERATE", actionability="WATCH",
        spot_at_observation=spot, contractual_expiry_breakeven="1200" if direction == "BULLISH" else "800",
        nearest_level_kind="resistance" if direction == "BULLISH" else "support",
        nearest_level_value="1050" if direction == "BULLISH" else "950",
        invalidation_level_kind="support" if direction == "BULLISH" else "resistance",
        invalidation_level_value="950" if direction == "BULLISH" else "1050",
        market_context=None, participation_note=None, coverage_classification="HIGH", thesis="t",
    )


def test_plus_one_three_five_resolve_to_the_next_valid_trading_sessions() -> None:
    obs = _obs()
    assert horizon_session_date(obs, 1) == date(2026, 8, 31)  # Fri -> Mon: the weekend is skipped
    assert horizon_session_date(obs, 3) == date(2026, 9, 2)
    assert horizon_session_date(obs, 5) == date(2026, 9, 4)
    assert horizon_close_utc(obs, 1) == PLUS1_CLOSE
    assert horizon_close_utc(obs, 3) == PLUS3_CLOSE
    assert horizon_close_utc(obs, 5) == PLUS5_CLOSE


def test_weekends_never_count_as_sessions() -> None:
    for sessions in (1, 2, 3, 4, 5):
        assert target_trading_session_date(FRIDAY, sessions).weekday() < 5


def test_exchange_holidays_are_excluded_via_the_canonical_calendar() -> None:
    """Regression: the old default was the empty `NSE_HOLIDAYS` constant, which
    silently bypassed a loaded official holiday list (the app loads one at
    startup)."""
    try:
        configure_nse_holidays(frozenset({date(2026, 9, 1)}))  # Tuesday
        assert target_trading_session_date(FRIDAY, 1) == date(2026, 8, 31)
        assert target_trading_session_date(FRIDAY, 2) == date(2026, 9, 2)  # Tue skipped
        assert target_trading_session_date(FRIDAY, 3) == date(2026, 9, 3)
        assert horizon_session_date(_obs(), 3) == date(2026, 9, 3)
        # ...and an observation the day before a holiday: +1 jumps over it.
        assert target_trading_session_date(date(2026, 8, 31), 1) == date(2026, 9, 2)
    finally:
        configure_nse_holidays(None)
    assert target_trading_session_date(date(2026, 8, 31), 1) == date(2026, 9, 1)


def test_session_is_the_ist_date_of_t0_not_the_utc_date() -> None:
    late_ist = _obs().model_copy(update={"generated_at": datetime(2026, 8, 27, 19, 0, tzinfo=UTC)})  # Fri 00:30 IST
    assert horizon_session_date(late_ist, 1) == date(2026, 8, 31)  # Friday's session, so +1 is Monday
    assert next_session_open_after(date(2026, 8, 31)) == datetime(2026, 9, 1, 3, 45, tzinfo=UTC)


def test_a_horizon_is_only_due_once_its_session_has_closed() -> None:
    obs = _obs()
    assert due_research_checkpoints(obs, set(), as_of=PLUS1_CLOSE - timedelta(minutes=1)) == []
    assert due_research_checkpoints(obs, set(), as_of=PLUS1_CLOSE) == [ResearchCheckpointLabel.PLUS_1_SESSION]
    assert due_research_checkpoints(obs, set(), as_of=PLUS3_CLOSE - timedelta(minutes=1)) == [ResearchCheckpointLabel.PLUS_1_SESSION]
    assert due_research_checkpoints(obs, {ResearchCheckpointLabel.PLUS_1_SESSION}, as_of=PLUS5_CLOSE) == [
        ResearchCheckpointLabel.PLUS_3_SESSIONS, ResearchCheckpointLabel.PLUS_5_SESSIONS,
    ]


# ----------------------------------------------------------------------------
# pure layer: price-path outcomes
# ----------------------------------------------------------------------------


def _quiet(ts: datetime) -> PriceBar:
    return PriceBar(timestamp=ts, high=Decimal("1001"), low=Decimal("999"), close=Decimal("1000"))


def _bars(end: datetime, overrides: dict[datetime, PriceBar] | None = None) -> list[PriceBar]:
    """15-minute bars from T0 through `end`, quiet unless overridden."""
    bars: list[PriceBar] = []
    ts = T0
    while ts <= end:
        bars.append((overrides or {}).get(ts, _quiet(ts)))
        ts += timedelta(minutes=15)
    return bars


def _bar(ts: datetime, *, high: str, low: str, close: str = "1000") -> PriceBar:
    return PriceBar(timestamp=ts, high=Decimal(high), low=Decimal(low), close=Decimal(close))


EARLY = datetime(2026, 8, 31, 5, 0, tzinfo=UTC)  # Monday, inside +1
MID = datetime(2026, 9, 1, 5, 0, tzinfo=UTC)  # Tuesday, after +1 and before +3
LATE = datetime(2026, 9, 3, 5, 0, tzinfo=UTC)  # Thursday, after +3 and before +5


def _outcome(
    obs: ResearchObservation, bars: list[PriceBar], horizon: OutcomeHorizonLabel, as_of: datetime,
) -> PricePathOutcome:
    return compute_price_path_outcome_from_bars(obs, horizon, bars, as_of=as_of)


def test_mfe_and_mae_bullish_use_the_existing_direction_convention() -> None:
    bars = _bars(PLUS1_CLOSE, {EARLY: _bar(EARLY, high="1040", low="980", close="1010")})
    out = _outcome(_obs(direction="BULLISH"), bars, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    assert out.data_sufficient
    assert out.max_favorable_move_pct == Decimal("4")  # (1040 - 1000) / 1000 * 100
    assert out.max_adverse_move_pct == Decimal("2")  # (1000 - 980) / 1000 * 100
    assert (out.subsequent_high, out.subsequent_low) == (Decimal("1040"), Decimal("980"))


def test_mfe_and_mae_bearish_mirror_the_convention() -> None:
    bars = _bars(PLUS1_CLOSE, {EARLY: _bar(EARLY, high="1040", low="980", close="1010")})
    out = _outcome(_obs(direction="BEARISH"), bars, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    assert out.max_favorable_move_pct == Decimal("2")  # (1000 - 980) / 1000 * 100
    assert out.max_adverse_move_pct == Decimal("4")  # (1040 - 1000) / 1000 * 100


def test_horizon_close_is_the_close_of_the_last_bar_at_the_horizon() -> None:
    bars = _bars(PLUS1_CLOSE, {PLUS1_CLOSE: _bar(PLUS1_CLOSE, high="1002", low="998", close="1001.5")})
    assert _outcome(_obs(), bars, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE).subsequent_close == Decimal("1001.5")


def test_first_valid_confirmation_event_is_recorded_at_its_bar() -> None:
    bars = _bars(PLUS1_CLOSE, {EARLY: _bar(EARLY, high="1051", low="999")})  # crosses the 1050 opposing level
    out = _outcome(_obs(), bars, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    assert out.confirmation_outcome == ConfirmationOutcome.CONFIRMED
    assert out.first_confirmation_at == EARLY
    assert out.invalidation_outcome == InvalidationOutcome.NOT_INVALIDATED and out.first_invalidation_at is None
    assert resolve_first_event(out) == ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED


def test_first_valid_invalidation_event_is_recorded_at_its_bar() -> None:
    bars = _bars(PLUS1_CLOSE, {EARLY: _bar(EARLY, high="1001", low="949")})  # loses the 950 supporting level
    out = _outcome(_obs(), bars, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    assert out.invalidation_outcome == InvalidationOutcome.INVALIDATED and out.first_invalidation_at == EARLY
    assert resolve_first_event(out) == ResearchOutcomeStatus.FAILED_SETUP


def test_the_earliest_event_wins_and_a_later_reversal_never_rewrites_it() -> None:
    confirm_first = _bars(PLUS1_CLOSE, {
        EARLY: _bar(EARLY, high="1051", low="999"), EARLY + timedelta(hours=2): _bar(EARLY + timedelta(hours=2), high="1001", low="949"),
    })
    out = _outcome(_obs(), confirm_first, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    assert resolve_first_event(out) == ResearchOutcomeStatus.FOLLOW_THROUGH_OBSERVED  # later invalidation does not undo it
    invalidate_first = _bars(PLUS1_CLOSE, {
        EARLY: _bar(EARLY, high="1001", low="949"), EARLY + timedelta(hours=2): _bar(EARLY + timedelta(hours=2), high="1051", low="999"),
    })
    out2 = _outcome(_obs(), invalidate_first, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    assert resolve_first_event(out2) == ResearchOutcomeStatus.FAILED_SETUP


def test_same_bar_ambiguity_is_insufficient_outcome_data_never_a_guess() -> None:
    bars = _bars(PLUS1_CLOSE, {EARLY: _bar(EARLY, high="1051", low="949")})  # both levels inside one M15 bar
    out = _outcome(_obs(), bars, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    assert out.first_confirmation_at == out.first_invalidation_at == EARLY
    assert resolve_first_event(out) == ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA


def test_no_event_by_the_horizon_is_no_follow_through() -> None:
    out = _outcome(_obs(), _bars(PLUS1_CLOSE), OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    assert out.confirmation_outcome == ConfirmationOutcome.NOT_CONFIRMED
    assert out.invalidation_outcome == InvalidationOutcome.NOT_INVALIDATED
    assert resolve_first_event(out) == ResearchOutcomeStatus.NO_FOLLOW_THROUGH


def test_missing_future_data_is_insufficient_and_never_neutral_confirmed_or_invalidated() -> None:
    truncated = _bars(EARLY)  # the series stops mid-session; the +1 close is never covered
    out = _outcome(_obs(), truncated, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    assert out.data_sufficient is False
    assert out.confirmation_outcome == ConfirmationOutcome.UNKNOWN
    assert out.invalidation_outcome == InvalidationOutcome.UNKNOWN
    assert out.max_favorable_move_pct is None and out.max_adverse_move_pct is None and out.subsequent_close is None
    assert resolve_first_event(out) == ResearchOutcomeStatus.INSUFFICIENT_OUTCOME_DATA
    # no series at all / no T0 spot: also insufficient, not zero movement
    assert _outcome(_obs(), [], OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE).data_sufficient is False
    assert _outcome(_obs(spot=None), _bars(PLUS1_CLOSE), OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE).data_sufficient is False


def test_a_horizon_not_yet_reached_is_reported_as_not_reached() -> None:
    out = _outcome(_obs(), _bars(PLUS1_CLOSE), OutcomeHorizonLabel.PLUS_3D, PLUS1_CLOSE)
    assert out.data_sufficient is False and out.note is not None and "not been reached" in out.note


def _spiky(from_ts: datetime, to_ts: datetime) -> dict[datetime, PriceBar]:
    """Wild post-horizon bars: a huge spike up and a crash, every 4 hours."""
    overrides: dict[datetime, PriceBar] = {}
    ts = from_ts
    flip = True
    while ts <= to_ts:
        overrides[ts] = _bar(ts, high="5000", low="1000") if flip else _bar(ts, high="1000", low="10")
        flip = not flip
        ts += timedelta(hours=4)
    return overrides


def test_plus_one_is_unaffected_by_any_later_data() -> None:
    obs = _obs()
    only_through_plus1 = _bars(PLUS1_CLOSE)
    with_everything = _bars(PLUS5_CLOSE, _spiky(PLUS1_CLOSE + timedelta(minutes=15), PLUS5_CLOSE))
    a = _outcome(obs, only_through_plus1, OutcomeHorizonLabel.PLUS_1D, PLUS1_CLOSE)
    b = _outcome(obs, with_everything, OutcomeHorizonLabel.PLUS_1D, PLUS5_CLOSE)  # evaluated much later, full future present
    assert a == b
    assert a.data_sufficient and a.confirmation_outcome == ConfirmationOutcome.NOT_CONFIRMED


def test_plus_three_is_unaffected_by_plus_four_and_plus_five_data() -> None:
    obs = _obs()
    after_plus3 = PLUS3_CLOSE + timedelta(minutes=15)
    a = _outcome(obs, _bars(PLUS3_CLOSE, _spiky(MID, LATE)), OutcomeHorizonLabel.PLUS_3D, PLUS3_CLOSE)
    b = _outcome(obs, _bars(PLUS5_CLOSE, {**_spiky(MID, LATE), **_spiky(after_plus3, PLUS5_CLOSE)}), OutcomeHorizonLabel.PLUS_3D, PLUS5_CLOSE)
    assert a == b


def test_an_earlier_horizon_is_computed_independently_of_a_later_one() -> None:
    obs = _obs()
    bars = _bars(PLUS5_CLOSE, {MID: _bar(MID, high="1051", low="999")})  # breakout during +2
    plus1 = _outcome(obs, bars, OutcomeHorizonLabel.PLUS_1D, PLUS5_CLOSE)
    plus3 = _outcome(obs, bars, OutcomeHorizonLabel.PLUS_3D, PLUS5_CLOSE)
    assert plus1.confirmation_outcome == ConfirmationOutcome.NOT_CONFIRMED  # +1 does not see Tuesday
    assert plus3.confirmation_outcome == ConfirmationOutcome.CONFIRMED and plus3.first_confirmation_at == MID


def test_the_outcome_computation_is_deterministic_and_never_mutates_t0() -> None:
    obs = _obs()
    snapshot = obs.model_dump_json()
    bars = _bars(PLUS5_CLOSE, {EARLY: _bar(EARLY, high="1051", low="949")})
    runs = [_outcome(obs, bars, h, PLUS5_CLOSE) for h in (OutcomeHorizonLabel.PLUS_1D, OutcomeHorizonLabel.PLUS_3D, OutcomeHorizonLabel.PLUS_5D)]
    assert runs == [_outcome(obs, bars, h, PLUS5_CLOSE) for h in (OutcomeHorizonLabel.PLUS_1D, OutcomeHorizonLabel.PLUS_3D, OutcomeHorizonLabel.PLUS_5D)]
    assert obs.model_dump_json() == snapshot
    assert obs.direction == "BULLISH"  # T0 direction is never derived from the future path


def test_replay_and_forward_share_one_computation() -> None:
    """`compute_price_path_outcome(candles)` is a thin adapter over the bar
    computation the forward sweep uses, so the two cannot drift apart."""
    from app.domain.market.freshness import DataFreshness
    from app.domain.market.models import Candle, Timeframe

    obs = _obs()
    bars = _bars(PLUS3_CLOSE, {EARLY: _bar(EARLY, high="1051", low="949")})
    candles = [
        Candle(
            provider="test", freshness=DataFreshness(data_timestamp=b.timestamp, received_timestamp=b.timestamp),
            instrument_id="X", timeframe=Timeframe.M15, open=b.close, high=b.high, low=b.low, close=b.close, volume=1,
        )
        for b in bars
    ]
    for horizon in (OutcomeHorizonLabel.PLUS_1D, OutcomeHorizonLabel.PLUS_3D):
        assert compute_price_path_outcome(obs, horizon, candles, as_of=PLUS3_CLOSE) == _outcome(obs, bars, horizon, PLUS3_CLOSE)


# ----------------------------------------------------------------------------
# forward sweep on the live-shape fixture
# ----------------------------------------------------------------------------

_QUIET_CONFIG = PipelineConfig(chain_fetch_attempts=1, underlying_quote_fetch_attempts=1, chain_fetch_backoff_seconds=0.01)


def _rows(through: datetime, *, wild_after: datetime | None = None, stop_after: datetime | None = None) -> list[list[object]]:
    """The fixture's own candle series (through Monday 15:30 IST) extended, when
    asked, with later sessions. `wild_after` makes every bar after that instant
    a violent spike/crash so any contamination of an earlier horizon shows;
    `stop_after` cuts the series (missing future data)."""
    rows = [r for r in ro._candle_rows() if datetime.fromisoformat(str(r[0])) <= through]
    limit = stop_after or through
    rows = [r for r in rows if datetime.fromisoformat(str(r[0])) <= limit]
    ts = ro.CHECKPOINT_AS_OF + timedelta(minutes=15)
    while ts <= through and (stop_after is None or ts <= stop_after):
        if wild_after is not None and ts > wild_after:
            high, low = (3000.0, 1080.0) if int(ts.timestamp() // 900) % 2 else (1090.0, 20.0)
        else:
            high, low = 1085.0, 1083.0
        rows.append([ts.isoformat(), 1084.0, high, low, 1084.0, 1000, 0])
        ts += timedelta(minutes=15)
    return rows


def _provider(rows: list[list[object]], *, quotes_status: int = 200) -> UpstoxProvider:
    inner = ro._router({}, {"value": "checkpoint"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": rows}})
        if quotes_status != 200 and request.url.path == "/v2/market-quote/quotes":
            return httpx.Response(quotes_status, text="unavailable")
        return inner(request)

    return UpstoxProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token="tok")


def _captured(tmp_path: Path) -> tuple[ResearchObservation, JsonlResearchOutcomeRepository, Path]:
    candidate, observation = fc._candidate(tmp_path / "analysis")
    directory = tmp_path / "journal"
    repo = JsonlResearchOutcomeRepository(directory)
    outcome = asyncio.run(capture_forward_observation(repo, candidate, observation, persisted_at=T0 + timedelta(seconds=2)))
    assert outcome.status == CaptureStatus.CAPTURED
    (stored,) = asyncio.run(repo.query_all_observations())
    return stored, repo, directory / "research_observations.jsonl"


def _sweep(
    obs: ResearchObservation, repo: JsonlResearchOutcomeRepository, as_of: datetime, rows: list[list[object]], tmp_path: Path,
    *, quotes_status: int = 200,
) -> list[ResearchOutcomeCheckpoint]:
    return asyncio.run(sweep_due_research_outcomes(
        obs, as_of=as_of, provider=_provider(rows, quotes_status=quotes_status), instrument_master=ro._MASTER,
        strategy=EMAVWAPAlignmentStrategy(), repositories=ro._repos(tmp_path / f"repos-{as_of.isoformat().replace(':', '')}"),
        config=_QUIET_CONFIG, outcome_repository=repo,
    ))


def _checkpoints(repo: JsonlResearchOutcomeRepository, obs: ResearchObservation) -> list[ResearchOutcomeCheckpoint]:
    return asyncio.run(repo.query_checkpoints_for_observation(obs.observation_id))


def test_no_checkpoint_appears_before_its_horizon_session_has_closed(tmp_path: Path) -> None:
    obs, repo, _ = _captured(tmp_path)
    assert _sweep(obs, repo, PLUS1_CLOSE - timedelta(minutes=15), _rows(PLUS1_CLOSE), tmp_path) == []
    assert _checkpoints(repo, obs) == []
    with pytest.raises(ValueError, match="not due yet"):
        asyncio.run(capture_research_outcome_checkpoint(
            ResearchCheckpointLabel.PLUS_3_SESSIONS, obs, as_of=PLUS1_CLOSE, provider=_provider(_rows(PLUS1_CLOSE)),
            instrument_master=ro._MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=ro._repos(tmp_path / "x"),
            config=_QUIET_CONFIG,
        ))


def test_partial_progression_is_valid_and_creates_no_placeholders(tmp_path: Path) -> None:
    obs, repo, _ = _captured(tmp_path)
    captured = _sweep(obs, repo, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path)
    assert [c.checkpoint_label for c in captured] == [ResearchCheckpointLabel.PLUS_1_SESSION]
    checkpoints = _checkpoints(repo, obs)
    assert len(checkpoints) == 1  # +3 and +5 do not exist -- not even as pending placeholders

    horizons = build_horizon_progress(obs, checkpoints, as_of=PLUS1_CLOSE)
    assert [h.state for h in horizons] == [HorizonState.AVAILABLE, HorizonState.PENDING, HorizonState.PENDING]
    assert [h.reached for h in horizons] == [True, False, False]
    assert horizons[0].captured_at == PLUS1_CLOSE and horizons[1].captured_at is None
    summary = summarize_research_outcome(obs, checkpoints, as_of=PLUS1_CLOSE)
    assert summary.outcome_status != ResearchOutcomeStatus.FAILED_SETUP  # a pending horizon is never a failure
    detail = asyncio.run(build_research_outcome_detail(repo, obs.observation_id, as_of=PLUS1_CLOSE))
    assert detail is not None and [h.state for h in detail.horizons] == [h.state for h in horizons]


def test_a_reached_but_unswept_horizon_is_pending_not_completed(tmp_path: Path) -> None:
    obs, _, _ = _captured(tmp_path)
    horizons = build_horizon_progress(obs, [], as_of=PLUS3_CLOSE)
    assert [h.state for h in horizons] == [HorizonState.PENDING] * 3
    assert [h.reached for h in horizons] == [True, True, False]
    assert "awaiting" in horizons[0].detail and "not closed" in horizons[2].detail


def test_repeat_sweep_is_idempotent_and_restart_safe_and_never_touches_t0(tmp_path: Path) -> None:
    obs, repo, obs_path = _captured(tmp_path)
    t0_bytes = obs_path.read_bytes()
    cp_path = obs_path.parent / "research_outcome_checkpoints.jsonl"

    assert len(_sweep(obs, repo, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path)) == 1
    assert _sweep(obs, repo, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path) == []  # repeat: nothing new

    restarted = JsonlResearchOutcomeRepository(obs_path.parent)  # a fresh process: no in-memory state
    reloaded = asyncio.run(restarted.get_observation(obs.observation_id))
    assert reloaded == obs
    assert _sweep(reloaded, restarted, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path) == []
    assert len(_sweep(reloaded, restarted, PLUS3_CLOSE, _rows(PLUS3_CLOSE), tmp_path)) == 1  # only +3 is new
    restarted_again = JsonlResearchOutcomeRepository(obs_path.parent)
    assert _sweep(reloaded, restarted_again, PLUS3_CLOSE, _rows(PLUS3_CLOSE), tmp_path) == []
    assert len(_sweep(reloaded, restarted_again, PLUS5_CLOSE, _rows(PLUS5_CLOSE), tmp_path)) == 1

    labels = [c.checkpoint_label for c in _checkpoints(restarted_again, obs)]
    assert sorted(labels, key=lambda label: label.value) == sorted(ResearchCheckpointLabel, key=lambda label: label.value)  # exactly one each
    assert len(cp_path.read_text(encoding="utf-8").splitlines()) == 3
    assert obs_path.read_bytes() == t0_bytes  # T0 file byte-for-byte unchanged


def test_sweep_all_identifies_due_observations_and_is_repeatable(tmp_path: Path) -> None:
    obs, repo, _ = _captured(tmp_path)

    def sweep_all(as_of: datetime) -> list[ResearchOutcomeCheckpoint]:
        return asyncio.run(sweep_all_due_research_outcomes(
            as_of=as_of, provider=_provider(_rows(as_of)), instrument_master=ro._MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=ro._repos(tmp_path / f"all-{as_of.isoformat().replace(':', '')}"), config=_QUIET_CONFIG,
            outcome_repository=repo,
        ))

    assert sweep_all(T0 + timedelta(hours=1)) == []  # T0 exists; no horizon has closed
    assert [c.checkpoint_label for c in sweep_all(PLUS1_CLOSE)] == [ResearchCheckpointLabel.PLUS_1_SESSION]
    assert sweep_all(PLUS1_CLOSE) == []
    assert len(_checkpoints(repo, obs)) == 1


def test_persistence_refuses_a_second_checkpoint_for_the_same_horizon(tmp_path: Path) -> None:
    obs, repo, obs_path = _captured(tmp_path)
    (first,) = _sweep(obs, repo, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path)
    forged = first.model_copy(update={"spot_at_checkpoint": "1.0", "max_favorable_move_pct": "999"})
    assert asyncio.run(repo.save_checkpoint_once(forged)) is False  # e.g. an overlapping sweep
    assert _checkpoints(repo, obs) == [first]  # the first persisted checkpoint is final
    restarted = JsonlResearchOutcomeRepository(obs_path.parent)
    assert asyncio.run(restarted.save_checkpoint_once(forged)) is False


def test_checkpoint_round_trips_through_persistence(tmp_path: Path) -> None:
    obs, repo, obs_path = _captured(tmp_path)
    (cp,) = _sweep(obs, repo, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path)
    assert ResearchOutcomeCheckpoint.model_validate_json(cp.model_dump_json()) == cp
    assert _checkpoints(JsonlResearchOutcomeRepository(obs_path.parent), obs) == [cp]
    assert cp.evaluated_through == PLUS1_CLOSE and cp.captured_late is False
    assert cp.confirmation_outcome in {"CONFIRMED", "NOT_CONFIRMED"} and cp.invalidation_outcome == "UNKNOWN"


def test_legacy_checkpoints_without_the_new_fields_still_load() -> None:
    legacy = {
        "observation_id": "abc", "checkpoint_label": "PLUS_1_SESSION", "target_trading_session_date": "2026-08-31",
        "captured_at": "2026-08-31T10:00:00Z", "data_state": None, "spot_at_checkpoint": None, "move_pct_from_observation": None,
        "max_favorable_move_pct": None, "max_adverse_move_pct": None, "swing_level_broken": None, "breakeven_reached": None,
        "became_extended": None, "next_observed_early_stage_state": None, "progression": "UNKNOWN", "note": None,
    }
    cp = ResearchOutcomeCheckpoint.model_validate(legacy)
    assert (cp.evaluated_through, cp.confirmation_outcome, cp.captured_late) == (None, None, None)  # "not recorded"


def test_late_sweep_price_facts_equal_the_on_time_facts_and_never_see_later_sessions(tmp_path: Path) -> None:
    """The lookahead regression for the forward path. Sweep A runs at the +1
    close with data through Monday only. Sweep B runs on Friday with violent
    Tuesday-Friday data present. Every price fact for the +1 horizon must be
    identical; only the state-derived fields (which describe "now") differ."""
    obs_a, repo_a, _ = _captured(tmp_path / "a")
    (on_time,) = _sweep(obs_a, repo_a, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path / "a")

    obs_b, repo_b, _ = _captured(tmp_path / "b")
    late_all = _sweep(obs_b, repo_b, PLUS5_CLOSE, _rows(PLUS5_CLOSE, wild_after=PLUS1_CLOSE), tmp_path / "b")
    late = next(c for c in late_all if c.checkpoint_label == ResearchCheckpointLabel.PLUS_1_SESSION)

    for field in (
        "spot_at_checkpoint", "move_pct_from_observation", "max_favorable_move_pct", "max_adverse_move_pct",
        "swing_level_broken", "breakeven_reached", "confirmation_outcome", "invalidation_outcome",
        "first_confirmation_at", "first_invalidation_at", "evaluated_through",
    ):
        assert getattr(late, field) == getattr(on_time, field), field
    assert on_time.captured_late is False and late.captured_late is True
    # The withheld state-derived fields are honest, not guessed.
    assert late.next_observed_early_stage_state is None and late.became_extended is None
    assert late.progression.value == "UNKNOWN" and late.note is not None and "next session opened" in late.note
    assert float(late.max_favorable_move_pct or 0) < 20  # the Tuesday-Friday spike to 3000 never leaked in


def test_plus_three_checkpoint_ignores_plus_four_and_plus_five_data(tmp_path: Path) -> None:
    obs_a, repo_a, _ = _captured(tmp_path / "a")
    _sweep(obs_a, repo_a, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path / "a")
    (a3,) = _sweep(obs_a, repo_a, PLUS3_CLOSE, _rows(PLUS3_CLOSE, wild_after=PLUS1_CLOSE), tmp_path / "a")

    obs_b, repo_b, _ = _captured(tmp_path / "b")
    both = _sweep(obs_b, repo_b, PLUS5_CLOSE, _rows(PLUS5_CLOSE, wild_after=PLUS1_CLOSE), tmp_path / "b")
    b3 = next(c for c in both if c.checkpoint_label == ResearchCheckpointLabel.PLUS_3_SESSIONS)
    assert (a3.max_favorable_move_pct, a3.max_adverse_move_pct, a3.spot_at_checkpoint) == (
        b3.max_favorable_move_pct, b3.max_adverse_move_pct, b3.spot_at_checkpoint,
    )


def test_forward_sweep_is_deterministic(tmp_path: Path) -> None:
    def run(name: str) -> list[ResearchOutcomeCheckpoint]:
        obs, repo, _ = _captured(tmp_path / name)
        _sweep(obs, repo, PLUS3_CLOSE, _rows(PLUS3_CLOSE), tmp_path / name)
        return _checkpoints(repo, obs)

    assert run("one") == run("two")


def test_missing_future_data_records_an_insufficient_horizon_never_a_neutral_one(tmp_path: Path) -> None:
    obs, repo, _ = _captured(tmp_path)
    rows = _rows(PLUS1_CLOSE, stop_after=ro.OBSERVATION_AS_OF)  # nothing after T0
    (cp,) = _sweep(obs, repo, PLUS1_CLOSE, rows, tmp_path)
    assert cp.spot_at_checkpoint is None and cp.max_favorable_move_pct is None and cp.max_adverse_move_pct is None
    assert cp.confirmation_outcome is None and cp.invalidation_outcome is None
    assert cp.swing_level_broken is None and cp.breakeven_reached is None  # not False -- unknown
    assert cp.progression.value == "UNKNOWN" and cp.next_observed_early_stage_state is None and cp.became_extended is None
    assert cp.note is not None and "not determinable" in cp.note
    (horizon, *_) = build_horizon_progress(obs, _checkpoints(repo, obs), as_of=PLUS1_CLOSE)
    assert horizon.state == HorizonState.INSUFFICIENT


def test_a_failed_reanalysis_persists_nothing_and_is_retried(tmp_path: Path) -> None:
    obs, repo, _ = _captured(tmp_path)
    assert _sweep(obs, repo, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path / "bad", quotes_status=503) == []
    assert _checkpoints(repo, obs) == []  # an infrastructure hole is not an outcome
    assert len(_sweep(obs, repo, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path / "good")) == 1


# ----------------------------------------------------------------------------
# T0 immutability -- adversarial
# ----------------------------------------------------------------------------


def test_t0_is_byte_and_field_identical_after_every_outcome(tmp_path: Path) -> None:
    obs, repo, obs_path = _captured(tmp_path)
    before_bytes = obs_path.read_bytes()
    before = obs.model_dump()
    _sweep(obs, repo, PLUS5_CLOSE, _rows(PLUS5_CLOSE, wild_after=PLUS1_CLOSE), tmp_path)
    assert len(_checkpoints(repo, obs)) == 3

    assert obs_path.read_bytes() == before_bytes
    after = asyncio.run(JsonlResearchOutcomeRepository(obs_path.parent).get_observation(obs.observation_id))
    assert after is not None and after.model_dump() == before
    capture = after.forward_capture
    assert capture is not None and obs.forward_capture is not None
    # the fields the spec names, one by one
    assert capture.observed_expiry == obs.forward_capture.observed_expiry == ro.EXPIRY
    assert after.selected_strike == obs.selected_strike == "1000.0"
    assert after.selected_right == obs.selected_right == "CE"
    assert capture.contract_instrument_key == obs.forward_capture.contract_instrument_key == ro.Q_CE
    assert capture.underlying_instrument_key == ro.Q_KEY and capture.futures_instrument_key == ro.Q_FUT
    assert after.spot_at_observation == obs.spot_at_observation and after.contractual_expiry_breakeven == "1030.0"
    assert (after.nearest_level_kind, after.nearest_level_value) == (obs.nearest_level_kind, obs.nearest_level_value)
    assert after.early_stage_state == obs.early_stage_state and after.pattern == obs.pattern
    assert after.direction == obs.direction == "BULLISH"  # never re-derived from the future path
    assert after.generated_at == obs.generated_at == ro.OBSERVATION_AS_OF
    assert after.evidence_availability == obs.evidence_availability
    assert after.forward_capture == obs.forward_capture  # provenance frozen
    with pytest.raises(Exception):  # noqa: B017 -- frozen pydantic model
        after.direction = "BEARISH"  # type: ignore[misc]


def test_evidence_availability_is_never_reinterpreted_by_a_future_chain(tmp_path: Path) -> None:
    """The sweep's fresh analyses have a live option chain; T0's availability
    must stay exactly what T0 recorded."""
    obs, repo, _ = _captured(tmp_path)
    before = obs.evidence_availability
    assert before is not None
    _sweep(obs, repo, PLUS3_CLOSE, _rows(PLUS3_CLOSE), tmp_path)
    after = asyncio.run(repo.get_observation(obs.observation_id))
    assert after is not None and after.evidence_availability == before
    assert before.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.AVAILABLE
    assert before.state_of(EvidenceClass.MARKET_CONTEXT) == AvailabilityState.UNAVAILABLE  # unchanged by later data


# ----------------------------------------------------------------------------
# neighbours
# ----------------------------------------------------------------------------


def test_forward_and_replay_observations_stay_distinguishable_through_outcomes(tmp_path: Path) -> None:
    from app.orchestration.forward_capture import (
        FORWARD_LIVE_CAPTURE,
        HISTORICAL_REPLAY,
        observation_capture_kind,
    )

    obs, repo, _ = _captured(tmp_path)
    _sweep(obs, repo, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path)
    (stored,) = asyncio.run(repo.query_all_observations())
    assert observation_capture_kind(stored) == FORWARD_LIVE_CAPTURE and stored.source == "LIVE"
    assert observation_capture_kind(obs.model_copy(update={"source": "REPLAY", "forward_capture": None})) == HISTORICAL_REPLAY


def test_sprint_3_1_voting_policy_and_sprint_3_2_availability_are_unchanged() -> None:
    assert VOTING_GROUPS == {
        EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceGroup.FUTURES, EvidenceGroup.OPTIONS_OI,
        EvidenceGroup.GLOBAL, EvidenceGroup.RELATIVE_STRENGTH,
    }
    assert EvidenceGroup.OPTIONS_IV not in VOTING_GROUPS
    assert [s.value for s in AvailabilityState] == ["AVAILABLE", "UNAVAILABLE", "INSUFFICIENT", "STALE", "CONFLICTING"]


def test_outcome_modules_contain_no_prediction_profit_or_broker_terms() -> None:
    import ast
    import inspect

    import app.orchestration.outcome_horizons as horizons
    import app.orchestration.outcome_sessions as sessions
    import app.orchestration.research_outcome as outcome

    for module in (horizons, sessions, outcome):
        tree = ast.parse(inspect.getsource(module))
        names = {n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr.lower() for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        names |= {n.name.lower() for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for forbidden in ("place_order", "modify_order", "cancel_order", "win_rate", "hit_rate", "sharpe", "expectancy", "profit", "roi", "alpha"):
            assert not any(forbidden in name for name in names), (module.__name__, forbidden)


def test_checkpoint_json_is_stable_for_the_same_inputs(tmp_path: Path) -> None:
    obs, repo, _ = _captured(tmp_path)
    (cp,) = _sweep(obs, repo, PLUS1_CLOSE, _rows(PLUS1_CLOSE), tmp_path)
    assert json.loads(cp.model_dump_json()) == json.loads(ResearchOutcomeCheckpoint.model_validate_json(cp.model_dump_json()).model_dump_json())
