"""Timeframe/lookback requirement — a strategy's declaration of how much raw
candle history it needs per timeframe, and a checker verifying a given
candle input mapping actually satisfies that declaration.

Deliberately does NOT declare indicator periods. Per
docs/architecture/STRATEGY_INPUT_CONTRACT_DECISION.md §F, a lookback-*count*
declaration is safe in a way a *periods* declaration is not: under-declaring
lookback fails safely through the same `INSUFFICIENT_HISTORY`/empty-series
pattern already proven throughout `domain/technical` (this module's
`satisfied` flag mirrors it), whereas a periods declaration risks silently
drifting from what a strategy's own code actually computes internally
(the reason the earlier `TechnicalRequirement` proposal was rejected).
`TimeframeRequirement` has no field capable of expressing an indicator
period at all — this is a structural guarantee, not just a documented one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import bounded_series


class TimeframeRequirement(BaseModel):
    timeframe: Timeframe
    minimum_candles: int = Field(gt=0)


class TimeframeRequirementResult(BaseModel):
    timeframe: Timeframe
    minimum_candles: int
    candles_available: int
    satisfied: bool
    latest_candle_timestamp: datetime | None


class TimeframeRequirementCheck(BaseModel):
    instrument_id: str
    as_of: datetime
    results: list[TimeframeRequirementResult]

    @property
    def all_satisfied(self) -> bool:
        return all(r.satisfied for r in self.results)

    @property
    def unsatisfied(self) -> list[TimeframeRequirementResult]:
        return [r for r in self.results if not r.satisfied]


def check_timeframe_requirements(
    candles_by_timeframe: Mapping[Timeframe, Sequence[Candle]],
    *,
    instrument_id: str,
    as_of: datetime,
    requirements: Sequence[TimeframeRequirement],
) -> TimeframeRequirementCheck:
    """Independently re-applies `bounded_series()` to each requirement's
    timeframe before counting — belt-and-suspenders, matching
    `snapshot.py`'s established pattern: even if the caller already passed
    an as_of-bounded mapping (e.g. via `assemble_candle_inputs()`), this
    function never trusts that blindly, and can never report a requirement
    satisfied using candles beyond `as_of`.

    Raises `InvalidCandleSeriesError` (propagated from `bounded_series()`)
    for the same malformed-input cases as `assemble_candle_inputs()` — this
    is not a second, more lenient validation path.
    """
    results: list[TimeframeRequirementResult] = []
    for requirement in requirements:
        raw = candles_by_timeframe.get(requirement.timeframe, [])
        bounded = bounded_series(raw, instrument_id=instrument_id, timeframe=requirement.timeframe, as_of=as_of)
        results.append(
            TimeframeRequirementResult(
                timeframe=requirement.timeframe,
                minimum_candles=requirement.minimum_candles,
                candles_available=len(bounded),
                satisfied=len(bounded) >= requirement.minimum_candles,
                latest_candle_timestamp=bounded[-1].freshness.data_timestamp if bounded else None,
            )
        )
    return TimeframeRequirementCheck(instrument_id=instrument_id, as_of=as_of, results=results)
