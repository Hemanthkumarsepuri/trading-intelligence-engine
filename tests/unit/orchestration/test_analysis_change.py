from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.data.providers.upstox_provider import ExchangeStatus
from app.domain.market.data_state import MarketDataState
from app.domain.market.models import Timeframe
from app.domain.strategy.contracts import Setup
from app.domain.strategy.current_analysis import CurrentAnalysisResult
from app.domain.technical.ema_alignment import EMAAlignmentState
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.vwap_position import VWAPPositionState
from app.orchestration.analysis_change import ChangeKind, detect_changes
from app.orchestration.watchlist_snapshot import InstrumentSnapshot

AS_OF = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _analysis(
    *, ema_alignment: EMAAlignmentState | None, price_vs_vwap: VWAPPositionState | None, setup: Setup | None,
    last_completed: datetime | None = AS_OF,
) -> CurrentAnalysisResult:
    return CurrentAnalysisResult(
        instrument_id="X", timeframe=Timeframe.M15, as_of=AS_OF, strategy_name="ema_vwap_alignment",
        strategy_version="0.1.0", last_completed_candle_timestamp=last_completed, candles_available=100,
        ema9=Decimal("100"), ema21=Decimal("99"), ema50=Decimal("98"), ema_alignment=ema_alignment,
        ema_status=IndicatorStatus.OK, vwap_value=Decimal("100"), price=Decimal("101"),
        price_vs_vwap=price_vs_vwap, vwap_status=IndicatorStatus.OK, setup=setup,
        data_freshness_seconds=0.0, current_partial_candle_period_start=None, current_partial_candle_close=None,
    )


def _setup(direction: str) -> Setup:
    return Setup(
        strategy_name="ema_vwap_alignment", strategy_version="0.1.0", direction=direction,
        origin_timestamp=AS_OF, trigger_level=Decimal("100"), structural_basis="test",
    )


def _snapshot(*, state: MarketDataState, exchange_status: ExchangeStatus, analysis: CurrentAnalysisResult | None) -> InstrumentSnapshot:
    return InstrumentSnapshot(
        requested_symbol="X", instrument=None, state=state, exchange_status=exchange_status, quote=None,
        previous_close=None, day_change=None, day_change_pct=None, data_age_seconds=None, analysis=analysis, error=None,
    )


def test_no_previous_snapshot_produces_no_changes() -> None:
    current = _snapshot(state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN, analysis=None)
    assert detect_changes(previous=None, current=current) == []


def test_identical_snapshots_produce_no_changes() -> None:
    analysis = _analysis(ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=None)
    prev = _snapshot(state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN, analysis=analysis)
    curr = _snapshot(state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN, analysis=analysis)
    assert detect_changes(previous=prev, current=curr) == []


def test_market_status_change_detected() -> None:
    prev = _snapshot(state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN, analysis=None)
    curr = _snapshot(state=MarketDataState.MARKET_CLOSED_LATEST_DATA, exchange_status=ExchangeStatus.CLOSING_END, analysis=None)

    events = detect_changes(previous=prev, current=curr)

    kinds = {e.kind for e in events}
    assert ChangeKind.MARKET_STATUS_CHANGED in kinds
    assert ChangeKind.DATA_STATE_CHANGED in kinds


def test_ema_alignment_change_detected() -> None:
    prev = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=None),
    )
    curr = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(ema_alignment=EMAAlignmentState.DESCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=None),
    )

    events = detect_changes(previous=prev, current=curr)
    assert any(e.kind == ChangeKind.EMA_ALIGNMENT_CHANGED for e in events)


def test_price_vwap_relation_change_detected() -> None:
    prev = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.BELOW, setup=None),
    )
    curr = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=None),
    )

    events = detect_changes(previous=prev, current=curr)
    assert any(e.kind == ChangeKind.PRICE_VWAP_RELATION_CHANGED for e in events)


def test_setup_appeared_disappeared_and_direction_changed() -> None:
    no_setup = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=None),
    )
    bullish = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=_setup("BULLISH")),
    )
    bearish = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(ema_alignment=EMAAlignmentState.DESCENDING, price_vs_vwap=VWAPPositionState.BELOW, setup=_setup("BEARISH")),
    )

    appeared = detect_changes(previous=no_setup, current=bullish)
    assert any(e.kind == ChangeKind.SETUP_APPEARED for e in appeared)

    disappeared = detect_changes(previous=bullish, current=no_setup)
    assert any(e.kind == ChangeKind.SETUP_DISAPPEARED for e in disappeared)

    direction_changed = detect_changes(previous=bullish, current=bearish)
    assert any(e.kind == ChangeKind.SETUP_DIRECTION_CHANGED for e in direction_changed)


def test_new_completed_candle_detected() -> None:
    from datetime import timedelta

    prev = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=None, last_completed=AS_OF),
    )
    curr = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(
            ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=None,
            last_completed=AS_OF + timedelta(minutes=15),
        ),
    )

    events = detect_changes(previous=prev, current=curr)
    assert any(e.kind == ChangeKind.NEW_COMPLETED_CANDLE for e in events)


def test_bare_price_or_ema_value_change_is_not_reported_as_a_change() -> None:
    """A number moving slightly (LTP, EMA values themselves) must never be
    treated as a named change -- only the specific, structural transitions
    this module defines are.
    """
    prev = _snapshot(
        state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN,
        analysis=_analysis(ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=None),
    )
    curr_analysis = _analysis(ema_alignment=EMAAlignmentState.ASCENDING, price_vs_vwap=VWAPPositionState.ABOVE, setup=None)
    curr_analysis = curr_analysis.model_copy(update={"ema9": Decimal("100.01"), "price": Decimal("101.5")})
    curr = _snapshot(state=MarketDataState.LIVE_SNAPSHOT, exchange_status=ExchangeStatus.NORMAL_OPEN, analysis=curr_analysis)

    assert detect_changes(previous=prev, current=curr) == []
