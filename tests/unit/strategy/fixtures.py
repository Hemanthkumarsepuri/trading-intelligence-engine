"""Shared candle-close fixtures for `EMAVWAPAlignmentStrategy` tests.

Each list's resulting `EMAAlignmentState`/`VWAPPositionState` pair was
verified by direct execution against the real `compute_ema_alignment()`/
`compute_vwap_position()` functions before being hard-coded here — not
hand-derived. Comments record the verified outcome so a future reader does
not need to re-derive it; changing these values invalidates that record.

Renamed 18 Sep 2026, when the strategy's inverted sequence-label mapping
was corrected (see `app.domain.technical.ema_alignment`'s own docstring).
The old names said the opposite of what the data was: `BULLISH_CLOSES` was
a series falling from 100 to 82.6, and a clean uptrend was called
`CONFLICTING_UPTREND_CLOSES`. These names now describe the PRICE PATH,
which is a fact, rather than a verdict about it — so a future inversion
cannot hide behind a fixture name again.
"""

from __future__ import annotations

# A clean uptrend: EMA9 > EMA21 > EMA50 (module label DESCENDING) and
# price ABOVE the session VWAP -> BULLISH setup.
RISING_CLOSES: list[float] = [100.0 + i for i in range(60)]

# A clean downtrend: EMA9 < EMA21 < EMA50 (module label ASCENDING) and
# price BELOW the session VWAP -> BEARISH setup.
FALLING_CLOSES: list[float] = [200.0 - i for i in range(60)]

# Falling structure with a closing bounce: the EMA ordering is still that
# of a falling series, but the last close is back ABOVE the session VWAP.
# The two facts disagree, so the strategy reports no setup -- exactly the
# case a FAILED_BREAKDOWN_RECLAIM describes structurally, and exactly why
# that pattern is decided by `structural_reclaim.py` rather than here.
BOUNCE_IN_FALLING_STRUCTURE_CLOSES: list[float] = [100 - 0.3 * i for i in range(59)] + [92]

# Rising structure with a closing dip: EMA ordering of a rising series,
# last close BELOW the session VWAP. Facts disagree -> no setup.
DIP_IN_RISING_STRUCTURE_CLOSES: list[float] = [100 + 0.3 * i for i in range(59)] + [108]

# One candle short of the strategy's minimum_candles=50 -> INSUFFICIENT_HISTORY
INSUFFICIENT_CLOSES: list[float] = [100.0 + i for i in range(49)]

# Exactly at the strategy's minimum_candles=50 boundary -> status OK
BOUNDARY_MINIMUM_CLOSES: list[float] = [100.0 + i for i in range(50)]
