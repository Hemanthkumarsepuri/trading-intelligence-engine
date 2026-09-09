# MarketState — Implementation Status: Blocked

Status: **Documentation-only proposal, per this milestone's own explicit instruction not to guess at unspecified fields.** No `MarketState` code exists after this milestone. This is not a new architecture gate document — it records one concrete decision reached while implementing the Strategy Input Foundation.

---

## Decision

**`MarketState` was not implemented this milestone.** Three prior architecture documents (`STRATEGY_BOUNDARY_RESOLUTION.md` §6, `STRATEGY_INPUT_CONTRACT_FINAL.md` §10, `STRATEGY_INPUT_CONTRACT_DECISION.md` §G) each independently concluded that its field *types* — LTP, VWAP, breadth, the exact shape of "staleness_seconds... per data type it's built from" — are `[D]`, unresolved by any document. This milestone re-confirmed that finding rather than overriding it.

## Why a minimal stub was rejected rather than attempted

A version containing only `instrument_id: str` and `as_of: datetime` (the two genuinely `[A]`/`[B]`-grounded fields) was considered. It was rejected: ARCHITECTURE.md §5 defines `MarketState` *as* "instrument, OHLCV series, LTP, VWAP, breadth" — a stub omitting every one of those named fields wouldn't be a minimal version of that type, it would be a different, near-empty object wearing the same name. Shipping it risks a worse outcome than not shipping it: a future reader could reasonably assume `MarketState` exists and is usable, when it structurally cannot serve the purpose ARCHITECTURE.md defines for it.

## What is ready, once the owner supplies field types

| Field | Grounding | What's still needed |
|---|---|---|
| `instrument_id: str` | `[B]` — matches every other domain type's convention | Nothing — ready as-is |
| `as_of: datetime` | `[A]` — ARCHITECTURE.md §21 | Nothing — ready as-is |
| Freshness | `[B]` — the existing `app.domain.market.freshness.DataFreshness` type (already implemented, already tested) is a plausible direct fit for §21's "as_of and staleness_seconds" | Confirmation that reusing `DataFreshness` (rather than a new, MarketState-specific freshness shape) is the intended design — plausible, not confirmed |
| LTP | `[A]`-named, `[D]`-typed | Exact type (likely `Decimal`, matching `Quote.last_price`) and source (which existing provider field populates it) |
| VWAP | `[A]`-named, `[D]`-typed | Exact type, and clarification of whether this duplicates `vwap_position.py`'s output (a real risk flagged in the prior documents, unresolved) |
| Breadth | `[A]`-named, `[D]`-typed | No producer, no type, anywhere in the repository — the least-specified field |
| "OHLCV" | `[A]`-named, `[C]` recommended reading | Confirmation of the "current/running bar only, not a historical series" reading recommended in `STRATEGY_INPUT_CONTRACT_FINAL.md` §10 — the alternative (a true historical series) remains open and would duplicate the already-standard `list[Candle]` responsibility if chosen |

## What this milestone implemented instead

The parts of the Strategy Input Foundation that *were* fully specifiable without inventing anything — `assemble_candle_inputs()` and `TimeframeRequirement`/`check_timeframe_requirements()` (`app/domain/market/candle_inputs.py`, `app/domain/market/timeframe_requirement.py`) — do not depend on `MarketState` existing. A future `StrategyDefinition.detect_setup()` can receive `candles`/`as_of` from this milestone's work today; it will need `market_state` supplied once the above table's gaps are closed, not before.
