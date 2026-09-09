"""Sprint 6 -- underlying-price CONSISTENCY validation.

Traced root cause (real, live-observed KAYNES example): this system
legitimately fetches THREE separate real prices for one underlying in a
single analysis run -- the direct quote LTP (`quote.last_price`), the
last M15 historical candle's own close (`candles[-1].close`), and the
option-chain snapshot's own reported underlying price
(`snapshot.underlying_last_price`) -- each independently timestamped
(`DataFreshness.data_timestamp`), each fetched via its own real provider
call. Different parts of the existing pipeline already consume DIFFERENT
ones of these without ever cross-checking them against each other:

  - `report.spot` (the "Spot:" line) and every `decay_viability`
    calculation (required move, contractual breakeven, first-order
    scenarios) use `quote.last_price`.
  - `direction_comparison.paths.spot` (what the frontend's headline
    number and the CE/PE directional paths read) and every contract's
    `distance_to_support_pct`/`distance_to_resistance_pct` use
    `snapshot.underlying_last_price`.
  - The M15 chart's own last candle close is a THIRD value, shown as the
    dashboard's own big headline number.

This module never fabricates a single "true" spot by picking or
averaging between them -- it only classifies how closely the real,
already-fetched values agree, and returns every one of them individually
labeled (see `PriceSource`), so no caller (report renderer, UI) can ever
present one of these as an unqualified, context-free "spot" again.
Futures LTP is preserved/labeled here too but deliberately EXCLUDED from
the consistency comparison itself -- a futures contract legitimately
trades at a real premium/discount (basis) to the underlying by design,
so a futures/spot difference is not evidence of a data problem.

Thresholds (`max_consistent_diff_pct`/`max_partially_aligned_diff_pct`)
are ENGINEERING HEURISTICS per this project's fact/metric/heuristic/
threshold discipline (see `contract_analysis.py`'s own docstring for the
same discipline applied elsewhere) -- round, documented, config-driven,
never fitted or fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class PriceSource(str, Enum):
    LATEST_QUOTE = "LATEST_QUOTE"
    LATEST_CANDLE = "LATEST_CANDLE"
    OPTION_CHAIN_REFERENCE = "OPTION_CHAIN_REFERENCE"
    FUTURES = "FUTURES"


PRICE_SOURCE_USER_LABEL: dict[PriceSource, str] = {
    PriceSource.LATEST_QUOTE: "LIVE QUOTE",
    PriceSource.LATEST_CANDLE: "LAST COMPLETED CANDLE",
    PriceSource.OPTION_CHAIN_REFERENCE: "OPTION CHAIN REFERENCE",
    PriceSource.FUTURES: "FUTURES",
}


def price_source_user_label(source: PriceSource) -> str:
    return PRICE_SOURCE_USER_LABEL[source]


class DataConsistency(str, Enum):
    """Never a claim about which price is "correct" -- only how closely
    the real, already-fetched underlying-equity prices agree with each
    other. `UNKNOWN` means fewer than two real underlying-equity prices
    were available to compare at all (never treated as CONSISTENT by
    default -- silence is not agreement)."""

    CONSISTENT = "CONSISTENT"
    PARTIALLY_ALIGNED = "PARTIALLY_ALIGNED"
    INCONSISTENT = "INCONSISTENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LabeledPrice:
    """One real, already-fetched price, clearly attributed to its own
    source -- never presented as a bare, context-free "spot"."""

    source: PriceSource
    price: Decimal | None
    timestamp: datetime | None


@dataclass(frozen=True)
class PriceConsistencyResult:
    classification: DataConsistency
    detail: str
    # Every source that was even attempted this run, whether or not a
    # real value came back -- so a caller can render "FUTURES: n/a" as
    # honestly as "FUTURES: 3660.0", never silently omitting a source.
    prices: list[LabeledPrice]
    max_pairwise_diff_pct: Decimal | None
    # Phase 2 -- a stale M15 close is still LABELED in `prices` so the
    # analyst can see previous-session structure, but it is excluded from
    # the live-vs-live pairwise comparison when False. Live quote vs a
    # previous-session candle is a freshness fact, not INCONSISTENT data.
    candle_excluded_as_stale: bool = False


def classify_price_consistency(
    *,
    quote_price: Decimal | None, quote_timestamp: datetime | None,
    candle_price: Decimal | None, candle_timestamp: datetime | None,
    option_chain_price: Decimal | None, option_chain_timestamp: datetime | None,
    futures_price: Decimal | None, futures_timestamp: datetime | None,
    max_consistent_diff_pct: Decimal, max_partially_aligned_diff_pct: Decimal,
    candle_is_current: bool = True,
    quote_is_current: bool = True,
    chain_is_current: bool = True,
) -> PriceConsistencyResult:
    """Compares the real, already-fetched UNDERLYING-EQUITY prices
    (quote/candle/option-chain-reference -- never futures, see module
    docstring) pairwise, using the LARGEST real pairwise percentage
    difference found. Never combines them into a single fabricated
    "true" spot.

    `candle_is_current=False` / `quote_is_current=False` /
    `chain_is_current=False` keep that source labeled in `prices` but drop
    it from the live-vs-live comparison. A previous-session print vs a
    current print is a freshness fact, not a contradiction between two
    current sources.
    """
    prices = [
        LabeledPrice(PriceSource.LATEST_QUOTE, quote_price, quote_timestamp),
        LabeledPrice(PriceSource.LATEST_CANDLE, candle_price, candle_timestamp),
        LabeledPrice(PriceSource.OPTION_CHAIN_REFERENCE, option_chain_price, option_chain_timestamp),
        LabeledPrice(PriceSource.FUTURES, futures_price, futures_timestamp),
    ]

    comparable = [
        (p.source, p.price)
        for p in prices
        if p.source != PriceSource.FUTURES
        and p.price is not None
        and p.price > 0
        and not (p.source == PriceSource.LATEST_CANDLE and not candle_is_current)
        and not (p.source == PriceSource.LATEST_QUOTE and not quote_is_current)
        and not (p.source == PriceSource.OPTION_CHAIN_REFERENCE and not chain_is_current)
    ]
    stale_bits: list[str] = []
    if not candle_is_current:
        stale_bits.append("stale M15 candle close shown but excluded from live-vs-live comparison")
    if not quote_is_current:
        stale_bits.append("stale underlying quote shown but excluded from live-vs-live comparison")
    if not chain_is_current:
        stale_bits.append("stale option-chain reference shown but excluded from live-vs-live comparison")
    stale_note = f" ({'; '.join(stale_bits)})" if stale_bits else ""

    if len(comparable) < 2:
        return PriceConsistencyResult(
            classification=DataConsistency.UNKNOWN,
            detail=(
                f"only {len(comparable)} real current underlying-equity price source(s) available this run -- "
                f"not enough to assess consistency{stale_note}"
            ),
            prices=prices, max_pairwise_diff_pct=None, candle_excluded_as_stale=not candle_is_current,
        )

    diffs: list[tuple[str, str, Decimal]] = []
    for i in range(len(comparable)):
        for j in range(i + 1, len(comparable)):
            src_a, price_a = comparable[i]
            src_b, price_b = comparable[j]
            diff_pct = abs(price_a - price_b) / price_a * Decimal(100)
            diffs.append((src_a.value, src_b.value, diff_pct))
    worst_a, worst_b, max_diff = max(diffs, key=lambda d: d[2])

    if max_diff <= max_consistent_diff_pct:
        classification = DataConsistency.CONSISTENT
        detail = (
            f"real current underlying-equity prices agree within {max_diff:.2f}% "
            f"(largest gap: {worst_a} vs {worst_b}){stale_note}"
        )
    elif max_diff <= max_partially_aligned_diff_pct:
        classification = DataConsistency.PARTIALLY_ALIGNED
        detail = (
            f"real current underlying-equity prices differ by up to {max_diff:.2f}% ({worst_a} vs {worst_b}) -- "
            "within a plausible intraday-movement range, but treat downstream levels/breakevens with caution"
            f"{stale_note}"
        )
    else:
        classification = DataConsistency.INCONSISTENT
        detail = (
            f"real current underlying-equity prices differ by {max_diff:.2f}% ({worst_a} vs {worst_b}) -- materially "
            "inconsistent; this is NOT combined into a single spot, and breakeven/support/resistance distances "
            "computed from any one of these sources should be treated as unreliable until this resolves"
            f"{stale_note}"
        )

    return PriceConsistencyResult(
        classification=classification, detail=detail, prices=prices, max_pairwise_diff_pct=max_diff,
        candle_excluded_as_stale=not candle_is_current,
    )
