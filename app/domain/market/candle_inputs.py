"""Candle input contract — the strategy-facing candle mapping.

Builds a `Mapping[Timeframe, list[Candle]]` for handing to a future
`StrategyDefinition.detect_setup()` (per
docs/architecture/STRATEGY_INPUT_CONTRACT_DECISION.md §D/§H), applying the
existing `bounded_series()` contract independently to each timeframe's
series. This is not a new candle type and not a second filtering
implementation — it is a thin, tested wrapper guaranteeing every timeframe
in the resulting mapping is bounded to the same `as_of`, structurally
validated, and never silently repaired if malformed.

`bounded_series()`/`InvalidCandleSeriesError` are reused directly from
`app.domain.technical.series` rather than reimplemented here, per the
decision document's explicit instruction not to create a second bounding
mechanism. Note: this creates an import from `domain/market` into
`domain/technical`, which is not precisely how ARCHITECTURE.md §6's
dependency diagram is drawn (market shown feeding *into* technical, not the
reverse). No explicit rule in §6's text is violated — only `data/*`,
`persistence/*`, `llm/*`, and `orchestration/*` imports are forbidden from
`domain/*` — but this is flagged here as a genuine observation:
`bounded_series()` is, in substance, a generic candle-boundary utility with
no indicator-specific logic in it, and is arguably better homed somewhere
both layers can reach without either importing the other. Not moved in this
milestone — flagged for a future, separately-proposed minimal refactor, not
decided unilaterally here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from app.domain.market.models import Candle, Timeframe
from app.domain.technical.series import InvalidCandleSeriesError, bounded_series

__all__ = ["InvalidCandleSeriesError", "assemble_candle_inputs"]


def assemble_candle_inputs(
    candles_by_timeframe: Mapping[Timeframe, Sequence[Candle]],
    *,
    instrument_id: str,
    as_of: datetime,
) -> dict[Timeframe, list[Candle]]:
    """Applies `bounded_series()` independently to each timeframe's series.

    A timeframe absent from `candles_by_timeframe` is simply absent from the
    result too — this function does not invent empty entries for keys it was
    never given. A caller wanting "requested but zero candles available" to
    be representable should pass an explicit empty sequence for that key
    (`bounded_series()` accepts `[]` and returns `[]`, not an error).

    Raises `InvalidCandleSeriesError` if any timeframe's series is
    malformed — out-of-order timestamps, duplicate timestamps, or candles
    whose own `instrument_id`/`timeframe` fields don't match the key they
    were filed under. Nothing here catches, sorts, or otherwise repairs such
    a violation; it propagates directly to the caller.
    """
    return {
        timeframe: bounded_series(candles, instrument_id=instrument_id, timeframe=timeframe, as_of=as_of)
        for timeframe, candles in candles_by_timeframe.items()
    }
