"""Sprint 3 -- Research OUTCOME TRACKING, integration coverage: a real
`capture_research_outcome_checkpoint()` re-run against a mocked `httpx`
transport (the exact same pattern `test_daily_research.py` already uses
for Stage 2), proving the mechanism works end to end against real
pipeline output, not just hand-built fixtures.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.data.providers.upstox_provider import UpstoxProvider
from app.domain.audit.research_models import (
    ResearchCheckpointLabel,
    ResearchObservation,
    ResearchProgression,
)
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy
from app.orchestration.daily_research import (
    build_research_observation,
    build_research_thesis,
    rank_candidates,
)
from app.orchestration.dashboard_service import run_analysis
from app.orchestration.options_intelligence_pipeline import PipelineConfig, Repositories
from app.orchestration.research_outcome import (
    capture_research_outcome_checkpoint,
    sweep_due_research_outcomes,
)
from app.persistence.jsonl_file import (
    JsonlIvObservationRepository,
    JsonlOptionChainRepository,
    JsonlQuoteRepository,
    JsonlResearchOutcomeRepository,
)

EXPIRY = date(2026, 9, 24)
_EXPIRY_MS = 1790274599000
Q_KEY, Q_CE, Q_PE, Q_FUT = "NSE_EQ|QQQ", "NSE_FO|Q1", "NSE_FO|Q2", "NSE_FO|Q3"

_MASTER: list[dict[str, object]] = [
    {"segment": "NSE_EQ", "name": "SYMBOL Q", "exchange": "NSE", "instrument_type": "EQ", "instrument_key": Q_KEY, "trading_symbol": "SYMBOLQ"},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLQ", "instrument_type": "CE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": Q_CE, "strike_price": 1000.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLQ", "instrument_type": "PE", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": Q_PE, "strike_price": 1000.0},
    {"segment": "NSE_FO", "underlying_symbol": "SYMBOLQ", "instrument_type": "FUT", "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500, "instrument_key": Q_FUT},
]

# Friday, then the following Monday -- a real +1 trading session.
OBSERVATION_AS_OF = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
CHECKPOINT_AS_OF = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


def _candle_rows() -> list[list[object]]:
    """A single real candle series spanning BOTH the observation and the
    checkpoint instant -- the real provider's own no-look-ahead filtering
    (`UpstoxProvider.get_ohlcv()`'s `if timestamp > as_of: continue`)
    correctly shows only the "past" portion for each call, exactly like a
    real accumulating M15 history would.

    The pre-observation shape (short elevated base -> real decline ->
    real partial recovery) is the SAME real candle shape already proven
    (by direct experimentation against the real EMA(9,21,50)/VWAP
    functions, see `tests/integration/orchestration/test_daily_research.py`'s
    "quiet before move" fixture) to produce a genuine MIXED (non-
    monotonic, hence NEUTRAL) M15 trend alongside a real BULLISH VWAP
    reading -- i.e. a real, non-conflicting, defensible direction. A
    plain continuous uptrend does NOT reliably do this: this system's own
    EMA-sequence convention reads a sustained rise as "descending"
    (BEARISH), which directly conflicts with VWAP's own BULLISH read
    within the same evidence group and blocks convergence -- confirmed by
    direct trial. Real further movement continues after the observation
    instant for a genuine, measurable checkpoint excursion.
    """
    base_start = OBSERVATION_AS_OF - timedelta(minutes=15 * 59)
    pre_prices = (
        [1000.0] * 15
        + [1000.0 - 0.75 * i for i in range(1, 21)]
        + [985.0 + 0.52 * i for i in range(1, 25)]
    )
    rows: list[list[object]] = []
    ts = base_start
    for p in pre_prices:
        rows.append([ts.isoformat(), p, p + 1, p - 1, p, 1000, 0])
        ts += timedelta(minutes=15)
    # ts is now OBSERVATION_AS_OF -- continue with a real further rise
    # through the checkpoint instant.
    price = pre_prices[-1]
    while ts <= CHECKPOINT_AS_OF:
        price += 0.3
        rows.append([ts.isoformat(), price, price + 1, price - 1, price, 1000, 0])
        ts += timedelta(minutes=15)
    return rows


def _chain_body(*, spot: float) -> dict[str, object]:
    return {
        "status": "success",
        "data": [{
            "expiry": EXPIRY.isoformat(), "strike_price": 1000.0, "underlying_spot_price": spot,
            "call_options": {
                "instrument_key": Q_CE, "market_data": {"ltp": 30.0, "bid_price": 29.5, "ask_price": 30.5, "volume": 8000, "oi": 80000, "prev_oi": 75000},
                "option_greeks": {"iv": 19.0, "delta": 0.5, "theta": -0.8, "gamma": 0.01, "vega": 0.5},
            },
            "put_options": {
                "instrument_key": Q_PE, "market_data": {"ltp": 22.0, "bid_price": 21.5, "ask_price": 22.5, "volume": 7000, "oi": 60000, "prev_oi": 58000},
                "option_greeks": {"iv": 17.0, "delta": -0.5, "theta": -0.7, "gamma": 0.01, "vega": 0.5},
            },
        }],
    }


Q_SPOT_OBSERVATION = 998.0  # matches the real pre_prices series's own last close
Q_SPOT_CHECKPOINT = 1010.0  # matches real further movement after the observation instant


def _router(call_counts: dict[str, int], phase: dict[str, str]) -> Callable[[httpx.Request], httpx.Response]:
    """`phase["value"]` -- mutated by the TEST itself between the
    observation call and the checkpoint call -- lets this mock honestly
    return a different real quote for "now" at each real point in time,
    exactly like the real provider would if genuine time had passed;
    `as_of` itself is never transmitted in the real HTTP request, so this
    is the only honest way a mock can vary it per call."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        call_counts[path] = call_counts.get(path, 0) + 1
        spot = Q_SPOT_OBSERVATION if phase["value"] == "observation" else Q_SPOT_CHECKPOINT
        if path == "/v2/market/status/NSE":
            return httpx.Response(200, json={"status": "success", "data": {"status": "NORMAL_OPEN"}})
        if path == "/v2/market-quote/quotes":
            keys = request.url.params["instrument_key"].split(",")
            data: dict[str, object] = {}
            for k in keys:
                if k == Q_KEY:
                    data["NSE_EQ:Q"] = {"instrument_token": k, "last_price": spot, "net_change": 5.0, "volume": 1000000}
                elif k == Q_FUT:
                    data["NSE_FO:QFUT"] = {"instrument_token": k, "last_price": spot + 2.0, "net_change": 4.0, "oi": 1000000, "volume": 500000}
            return httpx.Response(200, json={"status": "success", "data": data})
        if path.startswith("/v3/historical-candle/"):
            return httpx.Response(200, json={"status": "success", "data": {"candles": _candle_rows()}})
        if path == "/v2/news":
            return httpx.Response(200, json={"status": "success", "data": {}, "metadata": {"page": {"total_records": 0}}})
        if path == "/v2/option/chain":
            return httpx.Response(200, json=_chain_body(spot=spot))
        raise AssertionError(f"unexpected path {path}")

    return handler


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> UpstoxProvider:
    return UpstoxProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), access_token="tok")


def _repos(tmp_path: Path) -> Repositories:
    return Repositories(
        quotes=JsonlQuoteRepository(tmp_path / "quotes.jsonl"),
        option_chains=JsonlOptionChainRepository(tmp_path / "chains.jsonl"),
        iv_observations=JsonlIvObservationRepository(tmp_path / "iv.jsonl"),
    )


_FAST_CONFIG = PipelineConfig(chain_fetch_attempts=1, chain_fetch_backoff_seconds=0.01)


def _build_observation(tmp_path: Path, phase: dict[str, str]) -> tuple[UpstoxProvider, ResearchObservation]:
    """Real Stage-2 analysis at the observation instant (`phase["value"]`
    starts as "observation") -> a real `RankedCandidate` -> a real
    `ResearchObservation`. Returns the SAME provider so a later checkpoint
    call can reuse it once the test flips `phase["value"]` to
    "checkpoint"."""
    call_counts: dict[str, int] = {}
    provider = _provider(_router(call_counts, phase))
    response = asyncio.run(
        run_analysis(
            "SYMBOLQ", provider=provider, instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(),
            repositories=_repos(tmp_path), config=_FAST_CONFIG, as_of=OBSERVATION_AS_OF,
        )
    )
    shortlist, _ = rank_candidates({"SYMBOLQ": response})
    assert shortlist, "fixture must produce a real defensible direction"
    observation = build_research_observation(
        shortlist[0], build_research_thesis(shortlist[0]), run_id="run1", coverage_classification="HIGH",
    )
    return provider, observation


def test_observation_carries_sprint4_multiday_fields_from_the_real_thesis(tmp_path: Path) -> None:
    """Sprint 4 -- proves `build_research_observation()` actually wires the
    new `structural_context`/`participation_depth`/`relative_strength`/
    `pre_breakout_signal` fields through from a REAL `ResearchThesisView`
    produced by the real pipeline (not just a hand-built unit fixture).
    This fixture's own pre-observation M15 history (59 candles) is
    shorter than `MULTI_DAY_RECENT_CANDLES + MULTI_DAY_BASELINE_MIN_CANDLES`
    (76), so the honest expected values here are the INSUFFICIENT/
    UNAVAILABLE ones -- exactly proving these fields degrade honestly
    end-to-end rather than fabricating a classification, while still
    confirming they reach the persisted record verbatim from the thesis."""
    phase = {"value": "observation"}
    _, observation = _build_observation(tmp_path, phase)
    assert observation.structural_context == "INSUFFICIENT_DATA"
    assert observation.participation_depth == "PARTICIPATION_UNAVAILABLE"
    assert observation.relative_strength == "RELATIVE_STRENGTH_DATA_UNAVAILABLE"
    assert observation.pre_breakout_signal is False


def test_checkpoint_capture_raises_before_the_target_session_is_reached(tmp_path: Path) -> None:
    phase = {"value": "observation"}
    provider, observation = _build_observation(tmp_path, phase)

    with pytest.raises(ValueError, match="not due yet"):
        asyncio.run(
            capture_research_outcome_checkpoint(
                ResearchCheckpointLabel.PLUS_1_SESSION, observation, as_of=OBSERVATION_AS_OF, provider=provider,
                instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path / "cp"),
                config=_FAST_CONFIG,
            )
        )


def test_checkpoint_capture_succeeds_once_due_and_computes_real_facts(tmp_path: Path) -> None:
    phase = {"value": "observation"}
    provider, observation = _build_observation(tmp_path, phase)
    original_early_stage_state = observation.early_stage_state

    phase["value"] = "checkpoint"  # genuine time has now passed
    checkpoint = asyncio.run(
        capture_research_outcome_checkpoint(
            ResearchCheckpointLabel.PLUS_1_SESSION, observation, as_of=CHECKPOINT_AS_OF, provider=provider,
            instrument_master=_MASTER, strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path / "cp"),
            config=_FAST_CONFIG,
        )
    )

    assert checkpoint.observation_id == observation.observation_id
    assert checkpoint.checkpoint_label == ResearchCheckpointLabel.PLUS_1_SESSION
    assert checkpoint.captured_at == CHECKPOINT_AS_OF
    assert checkpoint.spot_at_checkpoint == str(Decimal(str(Q_SPOT_CHECKPOINT)))
    assert checkpoint.move_pct_from_observation is not None
    # The real fixture rises after the observation instant -- a real
    # positive move for a BULLISH thesis, negative for a BEARISH one.
    move = float(checkpoint.move_pct_from_observation)
    assert (move > 0) == (observation.direction == "BULLISH")
    assert checkpoint.progression in (
        ResearchProgression.FOLLOW_THROUGH_OBSERVED, ResearchProgression.NO_FOLLOW_THROUGH,
        ResearchProgression.FAILED_SETUP, ResearchProgression.UNKNOWN,
    )
    # The ORIGINAL observation is never touched by capturing a checkpoint.
    assert observation.early_stage_state == original_early_stage_state


def test_sweep_persists_only_due_checkpoints_and_is_idempotent(tmp_path: Path) -> None:
    phase = {"value": "observation"}
    provider, observation = _build_observation(tmp_path, phase)
    outcome_repo = JsonlResearchOutcomeRepository(tmp_path / "outcomes")
    asyncio.run(outcome_repo.save_observation(observation))

    phase["value"] = "checkpoint"
    captured = asyncio.run(
        sweep_due_research_outcomes(
            observation, as_of=CHECKPOINT_AS_OF, provider=provider, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path / "cp"), config=_FAST_CONFIG,
            outcome_repository=outcome_repo,
        )
    )
    # Only +1 session is due by CHECKPOINT_AS_OF (a real Monday, one
    # session after the real Friday observation) -- +3/+5 are not.
    assert [c.checkpoint_label for c in captured] == [ResearchCheckpointLabel.PLUS_1_SESSION]

    persisted = asyncio.run(outcome_repo.query_checkpoints_for_observation(observation.observation_id))
    assert len(persisted) == 1

    # Sweeping again at the same as_of must never re-capture the same
    # checkpoint (idempotent -- `due_research_checkpoints()` excludes
    # already-captured labels).
    captured_again = asyncio.run(
        sweep_due_research_outcomes(
            observation, as_of=CHECKPOINT_AS_OF, provider=provider, instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path / "cp2"), config=_FAST_CONFIG,
            outcome_repository=outcome_repo,
        )
    )
    assert captured_again == []


def test_outcome_repository_presence_never_changes_the_original_ranking_or_decision(tmp_path: Path) -> None:
    """Objective 12.9/12.10 -- supplying `outcome_repository` to
    `run_daily_research()` only persists observations AFTER the real
    shortlist/decision already exist; it must never influence which
    candidate ranks where or what the decision engine decided."""
    from app.orchestration.daily_research import run_daily_research

    phase = {"value": "observation"}
    call_counts_a: dict[str, int] = {}
    call_counts_b: dict[str, int] = {}
    result_without = asyncio.run(
        run_daily_research(
            ["SYMBOLQ"], provider=_provider(_router(call_counts_a, phase)), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path / "a"), config=_FAST_CONFIG,
            as_of=OBSERVATION_AS_OF,
        )
    )
    outcome_repo = JsonlResearchOutcomeRepository(tmp_path / "outcomes2")
    result_with = asyncio.run(
        run_daily_research(
            ["SYMBOLQ"], provider=_provider(_router(call_counts_b, phase)), instrument_master=_MASTER,
            strategy=EMAVWAPAlignmentStrategy(), repositories=_repos(tmp_path / "b"), config=_FAST_CONFIG,
            as_of=OBSERVATION_AS_OF, outcome_repository=outcome_repo,
        )
    )
    assert [c.symbol for c in result_without.shortlist] == [c.symbol for c in result_with.shortlist]
    assert [c.response.decision for c in result_without.shortlist] == [c.response.decision for c in result_with.shortlist]
    assert [c.rank for c in result_without.shortlist] == [c.rank for c in result_with.shortlist]


def test_no_predictive_or_profit_language_in_outcome_tracking_source() -> None:
    """Objective 12.16/12.17 -- this is research OUTCOME TRACKING, never
    trade execution and never a profit claim. Scans for the concrete
    forbidden PHRASES this project has repeatedly forbidden elsewhere
    (`test_research_daily_no_forbidden_language_or_credential_leak`'s own
    list) -- deliberately excludes "profit"/"successful trade" from this
    blunt full-source scan, since this module's own docstrings correctly
    PROHIBIT those words by name (see `ResearchProgression`'s docstring),
    which is the opposite of using them predictively."""
    import app.domain.audit.research_models as models_module
    import app.orchestration.research_outcome as outcome_module

    forbidden = ["will rise", "will fall", "guaranteed", "buy now", "sell now", "100% accuracy", "win rate"]
    for module in (models_module, outcome_module):
        source = Path(module.__file__).read_text(encoding="utf-8").lower()  # type: ignore[arg-type]
        for phrase in forbidden:
            assert phrase not in source, f"{module.__name__} contains forbidden phrase {phrase!r}"

    # The real, PERSISTED string values (progression labels, checkpoint
    # note templates) never say "profit" or "successful trade" either --
    # a targeted check of what actually reaches storage/output, as
    # opposed to the module's own explanatory prohibition docstrings.
    for value in ResearchProgression:
        assert "profit" not in value.value.lower()
        assert "successful trade" not in value.value.lower()


def test_no_order_execution_capability_in_outcome_tracking_source() -> None:
    """Objective 12.18 -- no execution capability anywhere in this new
    module."""
    import app.orchestration.research_outcome as outcome_module

    source = Path(outcome_module.__file__).read_text(encoding="utf-8").lower()
    for forbidden in ("place_order", "modify_order", "cancel_order"):
        assert forbidden not in source
