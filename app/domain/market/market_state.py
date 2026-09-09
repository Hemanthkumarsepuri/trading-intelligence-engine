"""MarketState — the raw, current-instant market-fact container.

ARCHITECTURE.md §5 defines `MarketState` as "instrument, OHLCV series, LTP,
VWAP, breadth." This module implements exactly the subset of that definition
that is backed by an already-existing, already-typed, already-tested domain
model, and deliberately excludes the rest — a documented, reported scope
reduction, not a silent one:

- **LTP / current-instant facts**: implemented, via `Quote`
  (`app/domain/market/models.py`) — already the canonical "current market
  instant" normalized type in this codebase (`last_price`, `previous_close`,
  `volume`, `open_interest`, `average_price`, plus its own `freshness`).
  ARCHITECTURE.md §3's data flow places normalization before MarketState
  assembly, and `Quote` is exactly what normalization already produces for
  this purpose — this is a direct composition of existing, resolved
  infrastructure, not a new invented type.
- **OHLCV series**: excluded. A historical candle series is already the
  `candles: Mapping[Timeframe, Sequence[Candle]]` responsibility
  (`candle_inputs.py`). Every prior review
  (`docs/architecture/STRATEGY_INPUT_CONTRACT_DECISION.md` §G) found that
  duplicating it inside `MarketState` risks two independently-fetched "same"
  series silently diverging — excluded on that same evidence here, not
  merely deferred.
- **VWAP**: excluded. `domain/technical/vwap_position.py` already computes
  this from candles; a second VWAP value living inside `MarketState` would
  either duplicate that computation or silently diverge from it.
- **Breadth**: excluded. No producer of any kind — no type, no computation,
  no data source — exists anywhere in this repository. Inventing a
  representation for it would be exactly the unsupported guess this
  project's evidence discipline forbids. Left out, not guessed.

Freshness/staleness is not re-modeled here: `quote.freshness` (an existing,
already-tested `DataFreshness`) is the sole source of truth for how fresh
this state's underlying data is — ARCHITECTURE.md §21's "as_of and
staleness_seconds per data type it's built from" is satisfied because there
is exactly one data type this MarketState is built from.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, model_validator

from app.domain.market.models import Quote


class MarketState(BaseModel):
    """Raw, current-instant market facts for one instrument at one `as_of`.

    Contains nothing beyond identity (`instrument_id`, `as_of`) and `quote`.
    No technical interpretation, no strategy signal, no options/regime/news
    content — see the module docstring for exactly what was excluded and why.
    """

    instrument_id: str
    as_of: datetime
    quote: Quote

    @model_validator(mode="after")
    def _require_as_of_timezone_aware(self) -> MarketState:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        return self

    @model_validator(mode="after")
    def _require_quote_matches_instrument(self) -> MarketState:
        if self.quote.instrument_id != self.instrument_id:
            raise ValueError(
                f"quote.instrument_id {self.quote.instrument_id!r} does not match "
                f"MarketState.instrument_id {self.instrument_id!r}"
            )
        return self

    @model_validator(mode="after")
    def _require_quote_not_from_the_future(self) -> MarketState:
        if self.quote.freshness.data_timestamp > self.as_of:
            raise ValueError(
                "quote.freshness.data_timestamp is after as_of — this would be a "
                "no-future-data-leakage violation (ARCHITECTURE.md Addendum A5)"
            )
        return self


def assemble_market_state(quote: Quote, *, as_of: datetime) -> MarketState:
    """Pure assembly: wraps an already-fetched, already-normalized `Quote`
    into a `MarketState`. Does not fetch, does not call a provider, does not
    read the clock — `quote` and `as_of` are both caller-supplied, exactly
    like every other assembly function in this codebase
    (`assemble_candle_inputs`, `assemble_technical_snapshot`).
    """
    return MarketState(instrument_id=quote.instrument_id, as_of=as_of, quote=quote)
