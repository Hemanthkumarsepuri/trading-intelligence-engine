"""Sprint 3.3 -- forward F&O observation capture.

Uses the repository's existing live-shape fixture (`test_research_outcome`: a
mock Upstox transport serving a real-shaped quote/candle/option-chain/futures
payload for one F&O underlying at a Friday 15:30 IST instant). Nothing here
fabricates market history: every record is produced by the real
`run_analysis()` path and only ever COPIED by the capture step.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.audit.research_models import ForwardCaptureProvenance, ResearchObservation
from app.domain.market.trading_calendar import classify_session_window, session_window_payload
from app.domain.options.evidence_availability import AvailabilityState, EvidenceClass
from app.domain.options.evidence_matrix import VOTING_GROUPS, EvidenceGroup
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.daily_research import (
    DailyResearchResult,
    RankedCandidate,
    build_research_observation,
    build_research_thesis,
    rank_candidates,
    run_daily_research,
)
from app.orchestration.dashboard_service import AnalyzeResponse, run_analysis
from app.orchestration.forward_capture import (
    FORWARD_LIVE_CAPTURE,
    HISTORICAL_REPLAY,
    LEGACY_LIVE_UNVERIFIED,
    CaptureOutcome,
    CaptureStatus,
    build_forward_capture_view,
    capture_forward_observation,
    forward_observation_identity,
    observation_capture_kind,
    verify_forward_observation,
)
from app.orchestration.research_outcome import sweep_due_research_outcomes
from app.orchestration.visual_data import VisualData
from app.persistence.jsonl_file import JsonlResearchOutcomeRepository
from tests.integration.orchestration import test_research_outcome as ro

T0 = ro.OBSERVATION_AS_OF  # Friday 15:30:00 IST -- the last instant of the live session
PERSISTED = T0 + timedelta(seconds=3)


def _provider(*, market_status: str = "NORMAL_OPEN", phase: str = "observation") -> UpstoxProvider:
    inner = ro._router({}, {"value": phase})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": market_status}})
        return inner(request)

    return UpstoxProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token="tok")


def _analyze(tmp_path: Path, *, as_of: datetime = T0, market_status: str = "NORMAL_OPEN") -> AnalyzeResponse:
    return asyncio.run(run_analysis(
        "SYMBOLQ", provider=_provider(market_status=market_status), instrument_master=ro._MASTER,
        strategy=EMAVWAPAlignmentStrategy(), repositories=ro._repos(tmp_path), config=ro._FAST_CONFIG, as_of=as_of,
    ))


def _candidate(tmp_path: Path, *, market_status: str = "NORMAL_OPEN") -> tuple[RankedCandidate, ResearchObservation]:
    response = _analyze(tmp_path, market_status=market_status)
    shortlist, _ = rank_candidates({"SYMBOLQ": response})
    assert shortlist, "the fixture must yield a real shortlisted candidate"
    candidate = shortlist[0]
    observation = build_research_observation(
        candidate, build_research_thesis(candidate), run_id="run-fwd", coverage_classification="HIGH",
    )
    return candidate, observation


def _capture(
    repo: JsonlResearchOutcomeRepository, candidate: RankedCandidate, observation: ResearchObservation,
    *, persisted_at: datetime = PERSISTED,
) -> CaptureOutcome:
    return asyncio.run(capture_forward_observation(repo, candidate, observation, persisted_at=persisted_at))


def _stored(repo: JsonlResearchOutcomeRepository) -> list[ResearchObservation]:
    return asyncio.run(repo.query_all_observations())


Live = tuple[RankedCandidate, ResearchObservation, JsonlResearchOutcomeRepository, Path]


@pytest.fixture()
def live(tmp_path: Path) -> Live:
    candidate, observation = _candidate(tmp_path / "analysis")
    directory = tmp_path / "journal"
    return candidate, observation, JsonlResearchOutcomeRepository(directory), directory / "research_observations.jsonl"


# -- 1-3, 29-30: a valid live capture, its T0 and provenance -----------------


def test_valid_live_fno_analysis_is_captured_with_identity_evidence_availability_and_provenance(live: Live) -> None:
    candidate, observation, repo, _ = live
    outcome = _capture(repo, candidate, observation)
    assert outcome.status == CaptureStatus.CAPTURED and outcome.reasons == ()

    (stored,) = _stored(repo)
    assert stored.observation_id == outcome.observation_id
    assert observation_capture_kind(stored) == FORWARD_LIVE_CAPTURE
    assert stored.source == "LIVE"
    assert verify_forward_observation(stored) == ()

    # Identity: the contract actually observed at T0 (strike/right from the
    # selected contract, expiry from the observed chain, keys from the analysis).
    capture = stored.forward_capture
    assert capture is not None
    assert (stored.symbol, stored.selected_right, stored.selected_strike) == ("SYMBOLQ", "CE", "1000.0")
    assert capture.observed_expiry == ro.EXPIRY
    assert capture.underlying_instrument_key == ro.Q_KEY
    assert capture.contract_instrument_key == ro.Q_CE
    assert capture.futures_instrument_key == ro.Q_FUT

    # Sprint 3.2 contract present, copied from the analysis, describing T0.
    assert stored.evidence_availability is not None
    assert stored.evidence_availability == candidate.response.visual.evidence_availability  # type: ignore[union-attr]
    assert stored.evidence_availability.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.AVAILABLE


def test_t0_is_the_analysis_time_and_is_distinct_from_persisted_at_and_source_timestamps(live: Live) -> None:
    candidate, observation, repo, _ = live
    _capture(repo, candidate, observation)
    (stored,) = _stored(repo)
    capture = stored.forward_capture
    assert capture is not None
    assert stored.generated_at == T0 == candidate.response.generated_at
    assert capture.persisted_at == PERSISTED != stored.generated_at
    streams = {s.stream: s for s in capture.streams}
    assert streams["underlying_quote"].data_timestamp == T0
    # Unknown stays unknown: no fabricated source timestamp.
    assert streams["macro"].data_timestamp is None
    # The option chain's timestamp is recorded as a stream fact with its source string.
    assert streams["option_chain"].source == "upstox /v2/option/chain"
    assert streams["option_chain"].label == "LIVE"


def test_provenance_is_copied_verbatim_from_the_analysis_streams(live: Live) -> None:
    candidate, observation, repo, _ = live
    _capture(repo, candidate, observation)
    (stored,) = _stored(repo)
    assert stored.forward_capture is not None
    by_name = {s.stream: s for s in candidate.response.visual.freshness.streams}  # type: ignore[union-attr]
    for s in stored.forward_capture.streams:
        src = by_name[s.stream]
        assert (s.label, s.data_timestamp, s.retrieved_at, s.source) == (src.label, src.data_timestamp, src.retrieved_at, src.source)


# -- 4-10, 27: immutability and no future information ------------------------


def test_captured_observation_is_immutable(live: Live) -> None:
    candidate, observation, repo, _ = live
    _capture(repo, candidate, observation)
    (stored,) = _stored(repo)
    assert stored.forward_capture is not None
    for target, attr, value in (
        (stored, "generated_at", T0 + timedelta(days=1)), (stored, "selected_strike", "2000.0"),
        (stored, "selected_right", "PE"), (stored, "early_stage_state", "CONFIRMED_SETUP"),
        (stored.forward_capture, "observed_expiry", date(2027, 1, 1)),
        (stored.evidence_availability, "classes", ()),
    ):
        with pytest.raises(Exception):  # noqa: B017 -- pydantic frozen-instance ValidationError
            setattr(target, attr, value)


def test_a_later_conflicting_version_of_the_same_t0_never_replaces_the_persisted_record(live: Live) -> None:
    """A hostile "future" rewrite of expiry / strike / option type / spot /
    premium / evidence availability under the SAME T0 identity is refused and
    the persisted bytes do not change."""
    candidate, observation, repo, path = live
    _capture(repo, candidate, observation)
    before = path.read_bytes()
    (original,) = _stored(repo)
    assert original.forward_capture is not None

    from app.domain.options.evidence_availability import EvidenceAvailability

    future_availability = EvidenceAvailability(classes=tuple(
        c.model_copy(update={"state": AvailabilityState.STALE}) for c in original.evidence_availability.classes  # type: ignore[union-attr]
    ))
    rewrites = [
        original.model_copy(update={"forward_capture": original.forward_capture.model_copy(update={"observed_expiry": date(2026, 10, 27)})}),
        original.model_copy(update={"selected_strike": "1100.0"}),
        original.model_copy(update={"selected_right": "PE"}),
        original.model_copy(update={"spot_at_observation": "1234.5", "contractual_expiry_breakeven": "9999"}),
        original.model_copy(update={"evidence_availability": future_availability}),
    ]
    for rewrite in rewrites:
        assert asyncio.run(repo.save_observation_once(rewrite)) is False
    assert path.read_bytes() == before
    assert _stored(repo) == [original]


@pytest.mark.parametrize(
    ("field", "value"),
    [("selected_strike", "1100.0"), ("selected_right", "PE")],
)
def test_identity_binds_strike_and_option_type(live: Live, field: str, value: str) -> None:
    candidate, observation, repo, _ = live
    _capture(repo, candidate, observation)
    (original,) = _stored(repo)
    assert "IDENTITY_MISMATCH" in verify_forward_observation(original.model_copy(update={field: value}))


def test_identity_binds_expiry_and_contract_key(live: Live) -> None:
    candidate, observation, repo, _ = live
    _capture(repo, candidate, observation)
    (original,) = _stored(repo)
    assert original.forward_capture is not None
    for update in ({"observed_expiry": date(2026, 10, 27)}, {"contract_instrument_key": "NSE_FO|OTHER"}):
        tampered = original.model_copy(update={"forward_capture": original.forward_capture.model_copy(update=update)})
        assert "IDENTITY_MISMATCH" in verify_forward_observation(tampered)


def test_evidence_availability_is_copied_never_recalculated_at_capture(
    live: Live, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, observation, repo, _ = live

    def _boom(**_: object) -> None:
        raise AssertionError("capture must not recalculate evidence availability")

    monkeypatch.setattr("app.domain.options.evidence_availability.assess_evidence_availability", _boom)
    monkeypatch.setattr("app.orchestration.options_intelligence_pipeline.assess_evidence_availability", _boom)
    assert _capture(repo, candidate, observation).status == CaptureStatus.CAPTURED


def test_t0_record_contains_no_information_later_than_t0_except_persisted_at(live: Live) -> None:
    candidate, observation, repo, _ = live
    _capture(repo, candidate, observation)
    (stored,) = _stored(repo)
    assert stored.forward_capture is not None
    for s in stored.forward_capture.streams:
        for stamp in (s.data_timestamp, s.retrieved_at):
            assert stamp is None or stamp <= stored.generated_at
    assert stored.forward_capture.observed_expiry >= T0.date()
    # The identity does not depend on when the record was written.
    other = forward_observation_identity(
        symbol=stored.symbol, underlying_instrument_key=stored.forward_capture.underlying_instrument_key,
        right="CE", strike="1000.0", expiry=stored.forward_capture.observed_expiry,
        contract_instrument_key=stored.forward_capture.contract_instrument_key, t0=stored.generated_at,
    )
    assert other == stored.observation_id


def test_t0_later_than_persistence_is_refused(live: Live) -> None:
    candidate, observation, repo, _ = live
    outcome = _capture(repo, candidate, observation, persisted_at=T0 - timedelta(seconds=1))
    assert outcome.status == CaptureStatus.INELIGIBLE
    assert _stored(repo) == []


# -- 11-13, 28: identity, deduplication, restart -----------------------------


def test_identical_capture_is_deduplicated(live: Live) -> None:
    candidate, observation, repo, path = live
    first = _capture(repo, candidate, observation)
    second = _capture(repo, candidate, observation, persisted_at=PERSISTED + timedelta(minutes=5))
    assert (first.status, second.status) == (CaptureStatus.CAPTURED, CaptureStatus.DUPLICATE)
    assert first.observation_id == second.observation_id
    assert len(_stored(repo)) == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    # First write is final: the retry's later persisted_at was NOT recorded.
    assert _stored(repo)[0].forward_capture.persisted_at == PERSISTED  # type: ignore[union-attr]


def test_same_contract_at_a_different_t0_is_a_separate_legitimate_observation(live: Live) -> None:
    candidate, observation, repo, _ = live
    later_t0 = T0 - timedelta(minutes=10)  # still inside the live session
    later_response = candidate.response.model_copy(update={"generated_at": later_t0})
    later_candidate = dataclasses.replace(candidate, response=later_response)
    later_observation = build_research_observation(
        later_candidate, build_research_thesis(later_candidate), run_id="run-fwd-2", coverage_classification="HIGH",
    )
    a = _capture(repo, candidate, observation)
    b = _capture(repo, later_candidate, later_observation)
    assert (a.status, b.status) == (CaptureStatus.CAPTURED, CaptureStatus.CAPTURED)
    assert a.observation_id != b.observation_id
    assert len(_stored(repo)) == 2


def test_process_restart_does_not_duplicate_an_existing_t0(live: Live, tmp_path: Path) -> None:
    candidate, observation, repo, path = live
    assert _capture(repo, candidate, observation).status == CaptureStatus.CAPTURED
    restarted = JsonlResearchOutcomeRepository(path.parent)  # fresh process: no in-memory state at all
    assert _capture(restarted, candidate, observation, persisted_at=PERSISTED + timedelta(hours=1)).status == CaptureStatus.DUPLICATE
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_same_t0_from_independent_analyses_has_the_same_observation_id(tmp_path: Path) -> None:
    (c1, o1) = _candidate(tmp_path / "a")
    (c2, o2) = _candidate(tmp_path / "b")
    r1 = _capture(JsonlResearchOutcomeRepository(tmp_path / "j1"), c1, o1)
    r2 = _capture(JsonlResearchOutcomeRepository(tmp_path / "j2"), c2, o2)
    assert r1.observation_id == r2.observation_id is not None
    assert o1.observation_id != o2.observation_id  # the un-captured builder ids are random; capture ids are not


# -- 16-19: fail-closed eligibility ------------------------------------------


def test_capture_outside_live_market_state_fails_closed_and_never_becomes_live(tmp_path: Path) -> None:
    """Real analysis whose provider reports the market CLOSED at an instant the
    clock calls in-session: MARKET_CLOSED_LATEST_DATA is never promoted to LIVE."""
    response = _analyze(tmp_path / "closed", market_status="NORMAL_CLOSE")
    assert response.market_state == "MARKET_CLOSED_LATEST_DATA"
    shortlist, _ = rank_candidates({"SYMBOLQ": response})
    assert shortlist, "the closed-state fixture still shortlists, so the gate (not the shortlist) must refuse it"
    candidate = shortlist[0]
    observation = build_research_observation(
        candidate, build_research_thesis(candidate), run_id="r", coverage_classification="HIGH",
    )
    repo = JsonlResearchOutcomeRepository(tmp_path / "journal")
    outcome = _capture(repo, candidate, observation)
    assert outcome.status == CaptureStatus.INELIGIBLE
    assert "MARKET_STATE_NOT_LIVE" in outcome.reasons
    assert _stored(repo) == []


def test_t0_outside_the_live_session_window_fails_closed(live: Live) -> None:
    candidate, _, repo, _ = live
    for closed_t0 in (
        datetime(2026, 8, 29, 6, 0, tzinfo=UTC),  # Saturday
        datetime(2026, 8, 28, 11, 0, tzinfo=UTC),  # Friday 16:30 IST, after the close
        datetime(2026, 8, 28, 3, 0, tzinfo=UTC),  # Friday 08:30 IST, before pre-open
        datetime(2026, 8, 28, 3, 35, tzinfo=UTC),  # Friday 09:05 IST, pre-open call auction
    ):
        window = classify_session_window(closed_t0)
        assert window.research_session_mode != "LIVE"
        assert session_window_payload(window)["observation_kind"] == "LAST_OBSERVED"
        moved = dataclasses.replace(candidate, response=candidate.response.model_copy(update={"generated_at": closed_t0}))
        obs = build_research_observation(moved, build_research_thesis(moved), run_id="r", coverage_classification="HIGH")
        outcome = _capture(repo, moved, obs, persisted_at=closed_t0 + timedelta(seconds=2))
        assert outcome.status == CaptureStatus.INELIGIBLE, closed_t0
        assert "T0_NOT_IN_LIVE_SESSION" in outcome.reasons
    assert _stored(repo) == []  # nothing, in particular no record mislabelled LIVE


def test_missing_identity_fails_closed(live: Live) -> None:
    candidate, observation, repo, _ = live
    visual = candidate.response.visual
    assert visual is not None

    def variant(new_visual: VisualData | None, **response_update: object) -> RankedCandidate:
        response = candidate.response.model_copy(update={"visual": new_visual, **response_update})
        return dataclasses.replace(candidate, response=response)

    cases = {
        "OBSERVED_EXPIRY_MISSING": variant(visual.model_copy(update={"option_chain": visual.option_chain.model_copy(update={"expiry": None})})),
        "CONTRACT_NOT_IN_OBSERVED_CHAIN": variant(visual.model_copy(update={"option_chain": visual.option_chain.model_copy(update={"rows": []})})),
        "T0_MISSING": variant(visual, generated_at=None),
        "ANALYSIS_FAILED": variant(visual, error="boom"),
        "ANALYSIS_HAS_NO_VISUAL": variant(None),
        "SCOPE_INCONSISTENT": variant(visual, symbol="OTHER"),
    }
    for reason, bad in cases.items():
        outcome = _capture(repo, bad, observation)
        assert outcome.status == CaptureStatus.INELIGIBLE and reason in outcome.reasons, (reason, outcome)
    no_underlying = variant(visual.model_copy(update={"option_chain": visual.option_chain.model_copy(update={"underlying_instrument_key": None})}))
    assert "UNDERLYING_IDENTITY_MISSING" in _capture(repo, no_underlying, observation).reasons
    assert _stored(repo) == []


def test_malformed_option_identity_and_missing_evidence_fail_closed(live: Live) -> None:
    candidate, observation, repo, _ = live
    _capture(repo, candidate, observation)
    (good,) = _stored(repo)
    assert verify_forward_observation(good) == ()
    assert "OPTION_RIGHT_INVALID" in verify_forward_observation(good.model_copy(update={"selected_right": "XX"}))
    assert "OPTION_STRIKE_INVALID" in verify_forward_observation(good.model_copy(update={"selected_strike": "-5"}))
    assert "OPTION_STRIKE_INVALID" in verify_forward_observation(good.model_copy(update={"selected_strike": None}))
    assert "EVIDENCE_AVAILABILITY_MISSING" in verify_forward_observation(good.model_copy(update={"evidence_availability": None}))
    assert "DIRECTION_INVALID" in verify_forward_observation(good.model_copy(update={"direction": "NEUTRAL"}))
    stale_chain = good.evidence_availability.model_copy(update={"classes": tuple(  # type: ignore[union-attr]
        c.model_copy(update={"state": AvailabilityState.STALE}) if c.evidence_class == EvidenceClass.OPTIONS_CHAIN else c
        for c in good.evidence_availability.classes  # type: ignore[union-attr]
    )})
    assert "OPTION_CHAIN_NOT_CURRENT_AT_T0" in verify_forward_observation(good.model_copy(update={"evidence_availability": stale_chain}))


def test_expired_contract_fails_closed(live: Live) -> None:
    candidate, observation, repo, _ = live
    visual = candidate.response.visual
    assert visual is not None
    expired = visual.model_copy(update={"option_chain": visual.option_chain.model_copy(update={"expiry": date(2026, 8, 27)})})
    bad = dataclasses.replace(candidate, response=candidate.response.model_copy(update={"visual": expired}))
    outcome = _capture(repo, bad, observation)
    assert outcome.status == CaptureStatus.INELIGIBLE and "EXPIRY_BEFORE_T0" in outcome.reasons


# -- 20: persistence ----------------------------------------------------------


def test_forward_observation_round_trips_through_persistence(live: Live) -> None:
    candidate, observation, repo, path = live
    _capture(repo, candidate, observation)
    (stored,) = _stored(repo)
    assert ResearchObservation.model_validate_json(stored.model_dump_json()) == stored
    reloaded = JsonlResearchOutcomeRepository(path.parent)
    assert asyncio.run(reloaded.get_observation(stored.observation_id)) == stored
    view = build_forward_capture_view(stored)
    assert view.verified and view.problems == [] and view.capture_kind == FORWARD_LIVE_CAPTURE


# -- 14-15: historical vs forward --------------------------------------------


def test_replay_and_legacy_observations_are_distinguishable_and_never_verified_as_forward(live: Live) -> None:
    candidate, observation, repo, _ = live
    _capture(repo, candidate, observation)
    (forward,) = _stored(repo)
    legacy = observation  # what the pre-Sprint-3.3 path persisted: LIVE source, no capture record
    replay = observation.model_copy(update={"source": "REPLAY"})
    assert observation_capture_kind(forward) == FORWARD_LIVE_CAPTURE
    assert observation_capture_kind(legacy) == LEGACY_LIVE_UNVERIFIED
    assert observation_capture_kind(replay) == HISTORICAL_REPLAY
    for other in (legacy, replay):
        assert verify_forward_observation(other) == ("NOT_A_FORWARD_CAPTURE",)
        assert build_forward_capture_view(other).verified is False
    # A replay observation can never be dressed up as a live capture.
    with pytest.raises(ValueError, match="source='LIVE'"):
        ResearchObservation.model_validate({**forward.model_dump(), "source": "REPLAY"})


# -- 21-22: outcome flow; capture never alters research state ----------------


def test_captured_observation_flows_into_the_existing_outcome_checkpoint_mechanism(tmp_path: Path) -> None:
    phase = {"value": "observation"}
    provider_obs = _provider(phase="observation")
    response = asyncio.run(run_analysis(
        "SYMBOLQ", provider=provider_obs, instrument_master=ro._MASTER, strategy=EMAVWAPAlignmentStrategy(),
        repositories=ro._repos(tmp_path / "a"), config=ro._FAST_CONFIG, as_of=T0,
    ))
    (candidate,) = rank_candidates({"SYMBOLQ": response})[0][:1]
    observation = build_research_observation(candidate, build_research_thesis(candidate), run_id="r", coverage_classification="HIGH")
    repo = JsonlResearchOutcomeRepository(tmp_path / "journal")
    assert _capture(repo, candidate, observation).status == CaptureStatus.CAPTURED
    (stored,) = _stored(repo)
    assert stored in asyncio.run(repo.query_observations_due_for_sweep(as_of=ro.CHECKPOINT_AS_OF))

    phase["value"] = "checkpoint"
    captured = asyncio.run(sweep_due_research_outcomes(
        stored, as_of=ro.CHECKPOINT_AS_OF, provider=_provider(phase="checkpoint"), instrument_master=ro._MASTER,
        strategy=EMAVWAPAlignmentStrategy(), repositories=ro._repos(tmp_path / "cp"), config=ro._FAST_CONFIG,
        outcome_repository=repo,
    ))
    assert [c.observation_id for c in captured] == [stored.observation_id]
    assert asyncio.run(repo.get_observation(stored.observation_id)) == stored  # outcomes never write back into T0


def _daily(
    tmp_path: Path, *, repo: JsonlResearchOutcomeRepository | None, provider: UpstoxProvider | None = None,
) -> DailyResearchResult:
    # 5 minutes inside the session: the scan's per-symbol as_of advances by wall-clock
    # microseconds, so an as_of sitting exactly on 15:30:00 would drift past the close.
    return asyncio.run(run_daily_research(
        ["SYMBOLQ"], provider=provider or _provider(), instrument_master=ro._MASTER, strategy=EMAVWAPAlignmentStrategy(),
        repositories=ro._repos(tmp_path), config=ro._FAST_CONFIG, as_of=T0 - timedelta(minutes=5),
        outcome_repository=repo,
    ))


def test_daily_research_captures_forward_observations_and_leaves_research_state_untouched(tmp_path: Path) -> None:
    without = _daily(tmp_path / "a", repo=None)
    repo = JsonlResearchOutcomeRepository(tmp_path / "journal")
    with_capture = _daily(tmp_path / "b", repo=repo)
    assert [(c.symbol, c.rank, c.direction, c.response.decision, c.response.research_state) for c in without.shortlist] == [
        (c.symbol, c.rank, c.direction, c.response.decision, c.response.research_state) for c in with_capture.shortlist
    ]
    assert with_capture.shortlist, "fixture must shortlist"
    assert [o.status for o in with_capture.forward_capture] == [CaptureStatus.CAPTURED]
    (stored,) = _stored(repo)
    assert stored.forward_capture is not None and verify_forward_observation(stored) == ()
    # Research state is recorded verbatim from the analysis, not re-derived or upgraded.
    assert stored.actionability == with_capture.shortlist[0].response.decision
    assert stored.direction == with_capture.shortlist[0].direction
    # T0 (analysis) precedes persistence, and the two stay distinct.
    assert stored.generated_at < stored.forward_capture.persisted_at
    assert without.forward_capture == []


def test_capture_infrastructure_failure_never_changes_the_research_result(tmp_path: Path) -> None:
    class _BrokenRepository(JsonlResearchOutcomeRepository):
        async def save_observation_once(self, observation: ResearchObservation) -> bool:
            raise OSError("disk full")

    baseline = _daily(tmp_path / "a", repo=None)
    broken = _daily(tmp_path / "b", repo=_BrokenRepository(tmp_path / "journal"))
    assert [(c.symbol, c.rank, c.response.decision, c.response.research_state) for c in baseline.shortlist] == [
        (c.symbol, c.rank, c.response.decision, c.response.research_state) for c in broken.shortlist
    ]
    assert [o.status for o in broken.forward_capture] == [CaptureStatus.ERROR]
    assert not (tmp_path / "journal" / "research_observations.jsonl").exists() or _stored(_BrokenRepository(tmp_path / "journal")) == []


def test_daily_research_does_not_capture_when_the_market_is_closed(tmp_path: Path) -> None:
    repo = JsonlResearchOutcomeRepository(tmp_path / "journal")
    result = _daily(tmp_path / "a", repo=repo, provider=_provider(market_status="NORMAL_CLOSE"))
    assert result.shortlist and [o.status for o in result.forward_capture] == [CaptureStatus.INELIGIBLE]
    assert _stored(repo) == []


# -- 23-26: neighbours unchanged ---------------------------------------------


def test_observation_watch_snapshot_ignores_the_new_identity_fields(tmp_path: Path) -> None:
    from app.domain.research.watch_record import snapshot_from_analyze_payload

    payload = _analyze(tmp_path).model_dump(mode="json")
    with_ids = snapshot_from_analyze_payload(payload)
    chain = payload["visual"]["option_chain"]
    chain.pop("underlying_instrument_key")
    for row in chain["rows"]:
        for leg in (row["call"], row["put"]):
            if leg:
                leg.pop("instrument_key")
    assert snapshot_from_analyze_payload(payload) == with_ids


def test_sprint_3_1_voting_policy_and_sprint_3_2_availability_semantics_are_unchanged() -> None:
    assert VOTING_GROUPS == {
        EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceGroup.FUTURES, EvidenceGroup.OPTIONS_OI,
        EvidenceGroup.GLOBAL, EvidenceGroup.RELATIVE_STRENGTH,
    }
    assert EvidenceGroup.OPTIONS_IV not in VOTING_GROUPS  # OPTIONS_IV still cannot become an independent voter
    assert [s.value for s in AvailabilityState] == ["AVAILABLE", "UNAVAILABLE", "INSUFFICIENT", "STALE", "CONFLICTING"]
    assert [c.value for c in EvidenceClass] == ["PRICE", "MARKET_CONTEXT", "OPTIONS_CHAIN", "FUTURES", "NEWS"]


def test_capture_module_has_no_broker_llm_or_scoring_dependency() -> None:
    """Checked on code (imports and identifiers), not prose: the docstring
    itself says what the module refuses to use."""
    import ast
    import inspect

    import app.orchestration.forward_capture as module

    tree = ast.parse(inspect.getsource(module))
    names = {n.id.lower() for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr.lower() for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.lower() for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").lower())
            names |= {a.name.lower() for a in node.names}
    joined = " ".join(sorted(names))
    for forbidden in ("place_order", "modify_order", "cancel_order", "qwen", "openai", "anthropic", "score", "probability", "confidence"):
        assert forbidden not in joined, forbidden


def test_forward_capture_provenance_model_is_frozen_and_typed() -> None:
    fields = set(ForwardCaptureProvenance.model_fields)
    assert {"persisted_at", "observed_expiry", "market_state", "session_window", "research_session_mode"} <= fields
    assert ForwardCaptureProvenance.model_config.get("frozen") is True



# -- read-only inspection endpoint --------------------------------------------


def test_capture_inspection_endpoint_reports_kind_provenance_and_verification(live: Live) -> None:
    from fastapi.testclient import TestClient

    from tests.integration.api.test_dashboard_api import _configured_app

    candidate, observation, repo, path = live
    _capture(repo, candidate, observation)
    (stored,) = _stored(repo)
    legacy = observation  # pre-Sprint-3.3 style: LIVE, no capture record
    asyncio.run(repo.save_observation(legacy))

    app = _configured_app(path.parent, provider=None, instrument_master=None)
    app.state.outcome_repository = repo
    client = TestClient(app)

    body = client.get(f"/api/research/{stored.observation_id}/capture").json()
    assert body["capture_kind"] == FORWARD_LIVE_CAPTURE and body["verified"] is True and body["problems"] == []
    assert body["provenance"]["observed_expiry"] == ro.EXPIRY.isoformat()
    assert body["evidence_availability"] is not None

    legacy_body = client.get(f"/api/research/{legacy.observation_id}/capture").json()
    assert legacy_body["capture_kind"] == LEGACY_LIVE_UNVERIFIED and legacy_body["verified"] is False

    assert client.get("/api/research/does-not-exist/capture").status_code == 404
    before = path.read_bytes()
    client.get(f"/api/research/{stored.observation_id}/capture")
    assert path.read_bytes() == before  # inspection writes nothing
