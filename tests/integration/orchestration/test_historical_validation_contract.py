"""Sprint 3.2 -- Historical Validation Contract, end to end through the REAL
replay path (`replay_symbol_session()` -> `run_analysis()` ->
`analyze_symbol()` -> observation builders) plus persistence.

A replayed observation must say which evidence classes were actually
available at its own `as_of`; absent evidence must stay UNAVAILABLE and never
become NEUTRAL, a vote, a confirmation or an invalidation; and none of the
pre-existing replay behavior may change.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from app.data.providers.historical_replay_provider import HistoricalReplayProvider
from app.domain.audit.research_models import ResearchObservation
from app.domain.market.freshness import DataFreshness
from app.domain.market.models import Candle, Timeframe
from app.domain.options.evidence_availability import AvailabilityState, EvidenceClass
from app.domain.options.evidence_matrix import EvidenceDirection, EvidenceGroup
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.daily_research import build_price_only_observation
from app.orchestration.dashboard_service import AnalyzeResponse
from app.orchestration.historical_replay import ReplaySessionResult, replay_symbol_session
from app.orchestration.options_intelligence_pipeline import PipelineConfig, analyze_symbol
from app.orchestration.outcome_horizons import OutcomeHorizonLabel, compute_price_path_outcome
from app.orchestration.replay_dataset import capture_knew_then
from app.persistence.caching import CachedCandleRepository
from app.persistence.jsonl_file import JsonlResearchOutcomeRepository
from tests.integration.orchestration.test_historical_replay import (
    _INSTRUMENT_KEY,
    _MASTER,
    _SESSION_START,
    _reclaim_candles,
    _repositories,
    _seed,
)

_SESSION = date(2026, 8, 27)


def _replay(tmp_path: Path, candles: list[Candle], *, run_id: str = "contract-run") -> ReplaySessionResult:
    return asyncio.run(replay_symbol_session(
        "RELIANCE", _SESSION, candle_repository=_seed(tmp_path, candles), instrument_master=_MASTER,
        repositories=_repositories(tmp_path), config=None, run_id=run_id,
    ))


def _subdir(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.mkdir()
    return path


def _comparable(observation: ResearchObservation) -> dict[str, object]:
    dumped = observation.model_dump(mode="json")
    dumped.pop("observation_id")
    return dumped


# -- 1-6: the contract recorded on real replay observations ------------------


def test_price_only_replay_records_price_available_and_derivatives_news_unavailable(tmp_path: Path) -> None:
    result = _replay(tmp_path, _reclaim_candles(_SESSION_START))
    assert result.observations
    for obs in result.observations:
        assert obs.evidence_availability is not None
        a = obs.evidence_availability
        assert a.state_of(EvidenceClass.PRICE) == AvailabilityState.AVAILABLE
        assert a.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.UNAVAILABLE
        assert a.state_of(EvidenceClass.FUTURES) == AvailabilityState.UNAVAILABLE
        assert a.state_of(EvidenceClass.NEWS) == AvailabilityState.UNAVAILABLE
        # One truth, two representations: the pre-existing bool agrees.
        assert obs.derivatives_evidence_available is False


def test_unavailable_streams_produce_no_neutral_or_directional_evidence(tmp_path: Path) -> None:
    repo = CachedCandleRepository(_seed(tmp_path, _reclaim_candles(_SESSION_START)))
    provider = HistoricalReplayProvider(candles=repo)
    as_of = _SESSION_START + timedelta(minutes=15 * 10)
    provider.advance_to(as_of)
    report = asyncio.run(analyze_symbol(
        "RELIANCE", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
        repositories=_repositories(tmp_path), as_of=as_of,
        config=PipelineConfig(chain_fetch_attempts=1, underlying_quote_fetch_attempts=1),
    ))
    assert report.error is None
    assert report.matrix is not None and report.evidence_availability is not None
    a = report.evidence_availability
    assert a.state_of(EvidenceClass.OPTIONS_CHAIN) == AvailabilityState.UNAVAILABLE

    # Every row of a stream the provider could not supply is UNKNOWN -- never
    # NEUTRAL ("evaluated, no direction") and never a vote.
    for group in (EvidenceGroup.OPTIONS_OI, EvidenceGroup.OPTIONS_IV, EvidenceGroup.FUTURES, EvidenceGroup.NEWS_EVENT):
        rows = [r for r in report.matrix.rows if r.group == group]
        assert rows, group
        assert all(r.direction == EvidenceDirection.UNKNOWN for r in rows), (group, [(r.name, r.direction) for r in rows])
    assert report.matrix.supporting_group_count(EvidenceDirection.BULLISH) <= 1  # price structure at most
    assert a.contradicted_by(report.matrix) == []


# -- 7-8: existing replay behavior unchanged ---------------------------------

# Captured by running the identical scenario on the Sprint 3.1 commit
# (48e679e), BEFORE this sprint's change: 25 observations plus their +1D
# price-path outcomes (confirmation/invalidation) against a later breakdown
# session. `evidence_availability` and `observation_id` are excluded because
# neither existed / was deterministic at that commit.
_BASELINE_OBSERVATION_COUNT = 25
_BASELINE_SHA256 = "8e369f8ff4852bbec6af04aa2cdd085c415a845c5a0426fa51415bf628e1d7dc"


def test_existing_pattern_replay_and_outcomes_are_byte_identical_to_the_pre_sprint_baseline(tmp_path: Path) -> None:
    candles = _reclaim_candles(_SESSION_START)
    result = _replay(tmp_path, candles, run_id="golden-run")

    rows = []
    for obs in result.observations:
        dumped = obs.model_dump(mode="json")
        dumped.pop("observation_id")
        dumped.pop("evidence_availability")
        rows.append(dumped)

    after = list(candles)
    next_day = _SESSION_START + timedelta(days=1)
    for bar in range(26):
        ts = next_day + timedelta(minutes=15 * bar)
        after.append(Candle(
            provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts),
            instrument_id=_INSTRUMENT_KEY, timeframe=Timeframe.M15,
            open=Decimal("1285"), high=Decimal("1286"), low=Decimal("1280"), close=Decimal("1282"), volume=20000,
        ))
    outcomes = [
        repr(compute_price_path_outcome(o, OutcomeHorizonLabel.PLUS_1D, after, as_of=next_day + timedelta(days=2)))
        for o in result.observations
    ]
    blob = json.dumps({"obs": rows, "outcomes": outcomes}, sort_keys=True, default=str)
    assert len(rows) == _BASELINE_OBSERVATION_COUNT
    assert hashlib.sha256(blob.encode()).hexdigest() == _BASELINE_SHA256


# -- 9-10: as_of discipline and determinism ----------------------------------


def test_availability_is_determined_at_as_of_and_ignores_future_data(tmp_path: Path) -> None:
    candles = _reclaim_candles(_SESSION_START)
    full = _replay(_subdir(tmp_path, "full"), candles)
    assert full.observations
    target = full.observations[len(full.observations) // 2]
    cutoff = target.generated_at

    # The same store with everything after `cutoff` removed: nothing in the
    # contract may depend on data the analysis at `cutoff` could not see.
    truncated = _replay(_subdir(tmp_path, "truncated"), [c for c in candles if c.freshness.data_timestamp <= cutoff])
    twin = next(o for o in truncated.observations if o.generated_at == cutoff)

    assert twin.evidence_availability == target.evidence_availability
    assert _comparable(twin) == _comparable(target)


def test_same_as_of_replay_yields_identical_availability(tmp_path: Path) -> None:
    candles = _reclaim_candles(_SESSION_START)
    first = _replay(_subdir(tmp_path, "a"), candles)
    second = _replay(_subdir(tmp_path, "b"), candles)
    assert [o.evidence_availability for o in first.observations] == [o.evidence_availability for o in second.observations]
    assert [_comparable(o) for o in first.observations] == [_comparable(o) for o in second.observations]


# -- 13-14: serialization and backward compatibility -------------------------


def test_contract_survives_the_persistence_round_trip(tmp_path: Path) -> None:
    result = _replay(tmp_path, _reclaim_candles(_SESSION_START))
    observation = result.observations[0]
    repo = JsonlResearchOutcomeRepository(tmp_path / "journal")
    asyncio.run(repo.save_observation(observation))
    (loaded,) = asyncio.run(repo.query_all_observations())
    assert loaded == observation
    assert loaded.evidence_availability == observation.evidence_availability
    assert ResearchObservation.model_validate_json(observation.model_dump_json()) == observation


def test_observation_persisted_before_this_sprint_loads_with_availability_not_recorded(tmp_path: Path) -> None:
    result = _replay(tmp_path, _reclaim_candles(_SESSION_START))
    legacy = json.loads(result.observations[0].model_dump_json())
    del legacy["evidence_availability"]  # exactly what an older JSONL line looks like
    loaded = ResearchObservation.model_validate_json(json.dumps(legacy))
    # `None` = "not recorded". It must NOT be read as UNAVAILABLE (or anything else).
    assert loaded.evidence_availability is None
    assert loaded.derivatives_evidence_available is False  # the legacy field is untouched


# -- 11: live analysis never inherits replay state ---------------------------


def test_live_analysis_does_not_inherit_historical_unavailable_states(tmp_path: Path) -> None:
    from tests.integration.orchestration import test_options_intelligence_pipeline as live

    # A replay run first (same process) must leave no trace on a later live analysis.
    _replay(_subdir(tmp_path, "replay"), _reclaim_candles(_SESSION_START))
    report = asyncio.run(analyze_symbol(
        "RELIANCE", provider=live._provider(live._router(candle_count=60)), instrument_master=live._MASTER,
        strategy=EMAVWAPAlignmentStrategy(), repositories=live._repos(_subdir(tmp_path, "live")),
        as_of=live.AS_OF, config=live._FAST_CONFIG,
    ))
    assert report.error is None
    assert report.evidence_availability is not None
    a = report.evidence_availability
    assert a.state_of(EvidenceClass.OPTIONS_CHAIN) in (AvailabilityState.AVAILABLE, AvailabilityState.CONFLICTING)
    assert a.state_of(EvidenceClass.FUTURES) in (AvailabilityState.AVAILABLE, AvailabilityState.CONFLICTING)
    assert a.state_of(EvidenceClass.PRICE) != AvailabilityState.UNAVAILABLE
    assert report.derivatives_history_available is True


# -- an observation never invents a contract ---------------------------------


def test_observation_built_from_a_response_without_a_contract_records_none_not_unavailable() -> None:
    from tests.unit.orchestration.test_price_only_observation import _response

    obs = build_price_only_observation("RELIANCE", _response(), run_id="run1", coverage_classification="REPLAY")
    assert obs is not None
    assert obs.evidence_availability is None


# -- the research dataset carries the contract with "what TIRE knew then" ----


def test_replay_dataset_knew_then_carries_the_availability_contract(tmp_path: Path) -> None:
    captured: list[tuple[ResearchObservation, dict[str, object]]] = []

    def _observe(observation: ResearchObservation, response: AnalyzeResponse) -> None:
        captured.append((observation, capture_knew_then(observation, response)))

    asyncio.run(replay_symbol_session(
        "RELIANCE", _SESSION, candle_repository=_seed(tmp_path, _reclaim_candles(_SESSION_START)),
        instrument_master=_MASTER, repositories=_repositories(tmp_path), config=None, on_observation=_observe,
    ))
    assert captured
    for observation, knew_then in captured:
        assert observation.evidence_availability is not None
        assert knew_then["evidence_availability"] == observation.evidence_availability.model_dump(mode="json")
        assert knew_then["derivatives_evidence_available"] is False


# -- 12: Observation Watch is untouched --------------------------------------


def test_observation_watch_snapshot_ignores_the_availability_contract() -> None:
    from app.domain.research.watch_record import snapshot_from_analyze_payload
    from tests.unit.orchestration.test_price_only_observation import _response

    payload = _response().model_dump(mode="json")
    without = snapshot_from_analyze_payload(payload)
    contract = replay_availability_payload()
    payload["visual"]["evidence_availability"] = contract
    assert snapshot_from_analyze_payload(payload) == without


def replay_availability_payload() -> dict[str, object]:
    from app.domain.options.evidence_availability import assess_evidence_availability

    return assess_evidence_availability(
        price_history_sufficient=True, candles_present=True, candles_are_current=True, market_context_present=False,
        chain_present=False, chain_is_current=True, provider_has_chain_history=False,
        futures_present=False, futures_are_current=True, provider_has_futures_history=False,
        news_fetch_failed=True, provider_has_news_history=False, matrix=None,
    ).model_dump(mode="json")
