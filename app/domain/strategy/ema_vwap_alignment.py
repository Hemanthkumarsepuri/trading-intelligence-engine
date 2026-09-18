"""EMA/VWAP Alignment Strategy — the first concrete `StrategyDefinition`.

A single-timeframe strategy that combines exactly two existing, already-
implemented, fact-only `domain/technical` outputs:

- `ema_alignment.compute_ema_alignment()` over ARCHITECTURE.md §9's own
  textually-specified Trend periods, `[9, 21, 50]` — `[A]`-grounded.
- `vwap_position.compute_vwap_position()` — no threshold, a pure `>`/`<`/`==`
  comparison, exactly as the underlying module already computes it.

No new indicator math, no new thresholds, no RSI/MACD/ATR/relative-volume
input (avoiding any period that isn't already textually specified), and no
multi-timeframe combination (single timeframe only — `Timeframe.M15`, the
"structure/trend layer" per ARCHITECTURE.md §10 — chosen specifically to
avoid needing any MTF-combination logic at all in this first strategy, per
this milestone's explicit instruction not to build MTF combination
prematurely).

**This strategy's interpretation rule is its own — `[C]`, a design choice
requiring the strategy's own future replay validation
(`STRATEGY_FRAMEWORK.md`'s "Replay Requirements" section), not a claim about
what these facts "mean" in any general trading sense:**

    BULLISH  setup  <=>  EMA(9,21,50) alignment == DESCENDING AND  price ABOVE 15m VWAP
    BEARISH  setup  <=>  EMA(9,21,50) alignment == ASCENDING  AND  price BELOW 15m VWAP
    no setup        <=>  either fact is INSUFFICIENT_HISTORY, MIXED, AT, or the two facts
                          disagree (e.g. DESCENDING but BELOW VWAP)

`DESCENDING` is the sequence EMA9 > EMA21 > EMA50 read in period order --
the faster averages sitting ABOVE the slower ones, which is the ordering a
RISING series produces. The pairing above is therefore "price structure and
session position point the same way", not a contrarian rule. It was
originally written with the two sequence labels swapped, so the strategy
paired a falling EMA ordering with price above VWAP and called it BULLISH;
that is corrected here.

`ema_alignment.py`'s own docstring is explicit that its `ASCENDING`/
`DESCENDING` labels describe a numeric EMA-value sequence only, and do NOT
correspond to "price is rising"/"bullish" in any general sense — this
strategy's mapping of that sequence label to `BULLISH`/`BEARISH` is this
strategy's own interpretation, made here for the first time, not a
restatement of any fact already implied by the technical layer.

`trigger_level` is the latest bounded candle's close (`vwap_position`'s own
`current_price`). `origin_timestamp` is the latest bounded candle's
timestamp — this strategy reports "the condition holds as of this instant,"
not "the first instant the condition became true" (which would require an
unspecified look-back-scan rule this document does not invent); documented
here as an explicit simplification, not hidden.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from app.domain.market.market_state import MarketState
from app.domain.market.models import Candle, Timeframe
from app.domain.market.timeframe_requirement import TimeframeRequirement
from app.domain.strategy.contracts import Setup
from app.domain.technical.ema_alignment import EMAAlignmentState, compute_ema_alignment
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.vwap_position import VWAPPositionState, compute_vwap_position

_TIMEFRAME = Timeframe.M15
_EMA_PERIODS = (9, 21, 50)
_MINIMUM_CANDLES = 50  # exactly EMA(50)'s own minimum viable series length — no added margin


class EMAVWAPAlignmentStrategy:
    """First concrete strategy. Not yet replay-validated — see
    `STRATEGY_FRAMEWORK.md`'s "Replay Requirements" section, which applies to
    every `StrategyDefinition` including this one before real-time use.
    """

    name = "ema_vwap_alignment"
    version = "0.1.0"
    # Exposed publicly (mirroring the module-level constants used internally
    # below) so an external audit/reporting layer — e.g.
    # app/domain/strategy/current_analysis.py — can recompute the same facts
    # for display without hardcoding a second, independently-drifting copy
    # of these numbers. This is the exact "declare/use drift" risk this
    # project's own architecture documents have repeatedly flagged and
    # avoided elsewhere; exposing the strategy's own already-fixed constants
    # is how a reporting layer avoids reintroducing it here.
    ema_periods: tuple[int, int, int] = _EMA_PERIODS
    timeframe: Timeframe = _TIMEFRAME

    def required_timeframes(self) -> Sequence[TimeframeRequirement]:
        return [TimeframeRequirement(timeframe=_TIMEFRAME, minimum_candles=_MINIMUM_CANDLES)]

    def detect_setup(
        self,
        *,
        market_state: MarketState,
        candles: Mapping[Timeframe, Sequence[Candle]],
        as_of: datetime,
    ) -> Setup | None:
        if market_state.as_of != as_of:
            raise ValueError(
                f"market_state.as_of ({market_state.as_of.isoformat()}) does not match "
                f"as_of ({as_of.isoformat()}) — every input to detect_setup must share one as_of"
            )

        series = candles.get(_TIMEFRAME, [])

        alignment = compute_ema_alignment(
            series,
            instrument_id=market_state.instrument_id,
            timeframe=_TIMEFRAME,
            periods=_EMA_PERIODS,
            as_of=as_of,
        )
        vwap_position = compute_vwap_position(
            series,
            instrument_id=market_state.instrument_id,
            timeframe=_TIMEFRAME,
            as_of=as_of,
        )

        if alignment.status != IndicatorStatus.OK or vwap_position.status != IndicatorStatus.OK:
            return None
        if alignment.state is None or vwap_position.state is None:
            return None  # defensive: OK status always pairs with a non-None state today

        # `ASCENDING` is the numeric sequence EMA9 < EMA21 < EMA50 -- the
        # faster average BELOW the slower ones, i.e. a falling series.
        # These two branches were the wrong way round, which paired a
        # falling EMA ordering with "price above VWAP" and called the
        # result a BULLISH setup. See `row_m15_trend()`'s own comment in
        # `app.domain.options.evidence_matrix` for the measurement.
        if alignment.state == EMAAlignmentState.DESCENDING and vwap_position.state == VWAPPositionState.ABOVE:
            direction = "BULLISH"
        elif alignment.state == EMAAlignmentState.ASCENDING and vwap_position.state == VWAPPositionState.BELOW:
            direction = "BEARISH"
        else:
            return None

        assert vwap_position.latest_candle_timestamp is not None  # guaranteed by status == OK
        assert vwap_position.current_price is not None  # guaranteed by status == OK

        return Setup(
            strategy_name=self.name,
            strategy_version=self.version,
            direction=direction,
            origin_timestamp=vwap_position.latest_candle_timestamp,
            trigger_level=vwap_position.current_price,
            structural_basis=(
                f"{_TIMEFRAME.value} EMA{_EMA_PERIODS} alignment={alignment.state.value}; "
                f"price {vwap_position.state.value} {_TIMEFRAME.value} VWAP"
            ),
        )
