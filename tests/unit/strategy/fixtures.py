"""Shared candle-close fixtures for `EMAVWAPAlignmentStrategy` tests.

Each list's resulting `EMAAlignmentState`/`VWAPPositionState` pair was
verified by direct execution against the real `compute_ema_alignment()`/
`compute_vwap_position()` functions before being hard-coded here — not
hand-derived. Comments record the verified outcome so a future reader does
not need to re-derive it; changing these values invalidates that record.
"""

from __future__ import annotations

# EMAAlignmentState.ASCENDING + VWAPPositionState.ABOVE -> BULLISH setup
BULLISH_CLOSES: list[float] = [100 - 0.3 * i for i in range(59)] + [92]

# EMAAlignmentState.DESCENDING + VWAPPositionState.BELOW -> BEARISH setup
BEARISH_CLOSES: list[float] = [100 + 0.3 * i for i in range(59)] + [108]

# EMAAlignmentState.DESCENDING + VWAPPositionState.ABOVE -> facts conflict
# with this strategy's rule (BEARISH needs BELOW) -> no setup
CONFLICTING_UPTREND_CLOSES: list[float] = [100.0 + i for i in range(60)]

# EMAAlignmentState.ASCENDING + VWAPPositionState.BELOW -> facts conflict
# with this strategy's rule (BULLISH needs ABOVE) -> no setup
CONFLICTING_DOWNTREND_CLOSES: list[float] = [200.0 - i for i in range(60)]

# One candle short of the strategy's minimum_candles=50 -> INSUFFICIENT_HISTORY
INSUFFICIENT_CLOSES: list[float] = [100.0 + i for i in range(49)]

# Exactly at the strategy's minimum_candles=50 boundary -> status OK
BOUNDARY_MINIMUM_CLOSES: list[float] = [100.0 + i for i in range(50)]
