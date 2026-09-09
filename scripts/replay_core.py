"""Shared replay mechanism for `EMAVWAPAlignmentStrategy`.

Used by both the synthetic development demo (`scripts/dev_replay_demo.py`)
and the real-data replay pipeline (`scripts/real_data_replay.py`), so the
no-look-ahead, `as_of`-walking replay loop exists in exactly one place
regardless of where the candles came from. Nothing here is specific to
synthetic or real data — it operates only on already-built `Candle` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.domain.market.freshness import DataFreshness
from app.domain.market.market_state import assemble_market_state
from app.domain.market.models import Candle, Quote, Timeframe
from app.domain.strategy.contracts import Setup
from app.domain.strategy.ema_vwap_alignment import EMAVWAPAlignmentStrategy


@dataclass
class ReplayPoint:
    as_of: datetime
    setup: Setup | None
    insufficient_history: bool


@dataclass
class ReplaySummary:
    instrument_id: str
    timeframe: Timeframe
    strategy_name: str
    strategy_version: str
    candle_count: int
    points: list[ReplayPoint] = field(default_factory=list)

    @property
    def bullish(self) -> list[ReplayPoint]:
        return [p for p in self.points if p.setup is not None and p.setup.direction == "BULLISH"]

    @property
    def bearish(self) -> list[ReplayPoint]:
        return [p for p in self.points if p.setup is not None and p.setup.direction == "BEARISH"]

    @property
    def no_setup(self) -> list[ReplayPoint]:
        return [p for p in self.points if p.setup is None and not p.insufficient_history]

    @property
    def insufficient(self) -> list[ReplayPoint]:
        return [p for p in self.points if p.insufficient_history]


def run_replay(candles: list[Candle], *, strategy: EMAVWAPAlignmentStrategy, instrument_id: str) -> ReplaySummary:
    """Evaluates `strategy` at every candle's own timestamp as `as_of`,
    exposing only candles at or before that instant.

    `visible = candles[: i + 1]` never includes an index past `i` — the
    candle whose own timestamp defines `as_of` — so no evaluation point can
    ever see a candle timestamped after its `as_of`. This is the same
    no-look-ahead shape `assemble_candle_inputs()`/`bounded_series()` already
    enforce structurally; this loop just walks `as_of` forward one candle at
    a time instead of being called once at a single fixed instant.
    """
    requirement = strategy.required_timeframes()[0]
    summary = ReplaySummary(
        instrument_id=instrument_id,
        timeframe=requirement.timeframe,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        candle_count=len(candles),
    )

    for i, candle in enumerate(candles):
        as_of = candle.freshness.data_timestamp
        visible = candles[: i + 1]
        insufficient = len(visible) < requirement.minimum_candles

        quote = Quote(
            provider=candle.provider,
            freshness=DataFreshness(data_timestamp=as_of, received_timestamp=as_of),
            instrument_id=instrument_id,
            last_price=candle.close,
        )
        market_state = assemble_market_state(quote, as_of=as_of)

        setup = strategy.detect_setup(market_state=market_state, candles={requirement.timeframe: visible}, as_of=as_of)
        summary.points.append(ReplayPoint(as_of=as_of, setup=setup, insufficient_history=insufficient))

    return summary
