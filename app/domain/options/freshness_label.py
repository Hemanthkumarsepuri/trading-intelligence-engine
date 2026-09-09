"""Section 21's richer freshness vocabulary — LIVE/RECENT/STALE/
MARKET_CLOSED/DEGRADED/UNAVAILABLE — layered strictly ON TOP OF the
already-existing, already-tested `MarketDataState` classifier
(`app.domain.market.data_state`), never replacing or modifying it. That
type is used throughout the codebase's live-session/watchlist paths;
this module adds a report-facing refinement without touching it.

`DEGRADED` is new vocabulary this module introduces: real, live-fresh
data that nonetheless carries an acknowledged data-quality issue (e.g. a
wide-spread or missing-Greeks flag from `check_chain_quality`) — genuinely
different from a clean `LIVE` state, and different from `STALE` (which is
about age, not quality). Never silently folded into either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.domain.market.data_state import MarketDataState


class FreshnessLabel(str, Enum):
    LIVE = "LIVE"
    RECENT = "RECENT"
    STALE = "STALE"
    MARKET_CLOSED = "MARKET_CLOSED"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    # Phase 2 -- per-stream labels that the original report-wide classifier
    # never needed. `EOD` is previous-session official archive data (delivery).
    # `NOT_APPLICABLE` is a stream that does not exist for this instrument
    # (e.g. no futures). `UNKNOWN` is "not assessed / producer unverified".
    # `ERROR` is a failed fetch that did not take down the rest of the report.
    EOD = "EOD"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


class DataStream(str, Enum):
    """Named independently-fresh streams on one analysis report. Staleness
    of one stream must not be silently treated as staleness of every other
    stream -- see `StreamFreshness.blocks_entire_report`."""

    UNDERLYING_QUOTE = "underlying_quote"
    CANDLES_M15 = "candles_m15"
    OPTION_CHAIN = "option_chain"
    FUTURES = "futures"
    MACRO = "macro"
    NEWS = "news"
    SECTOR_MAP = "sector_map"
    SAMPLE_BREADTH = "sample_breadth"
    DELIVERY = "delivery"
    FII_DII = "fii_dii"


@dataclass(frozen=True)
class StreamFreshness:
    """One stream's freshness, provenance, and blast radius.

    `blocks_entire_report` is True only when THIS stream's failure/staleness
    makes the whole analysis unusable (e.g. no underlying quote at all).
    A stale M15 series sets this False and lists withheld calculations
    instead -- live quotes/chains remain usable.
    """

    stream: DataStream
    label: FreshnessLabel
    data_timestamp: datetime | None
    retrieved_at: datetime | None
    session: str | None
    source: str | None
    blocks_entire_report: bool
    withheld_calculations: tuple[str, ...]
    detail: str
    usable_for_display: bool
    usable_for_vote: bool
    completeness: str
    blast_radius: str


def stream_freshness(
    *,
    stream: DataStream,
    label: FreshnessLabel,
    detail: str,
    data_timestamp: datetime | None = None,
    retrieved_at: datetime | None = None,
    session: str | None = None,
    source: str | None = None,
    blocks_entire_report: bool = False,
    withheld_calculations: tuple[str, ...] = (),
    usable_for_display: bool = True,
    usable_for_vote: bool = True,
    completeness: str = "COMPLETE",
    blast_radius: str | None = None,
) -> StreamFreshness:
    if blast_radius is None:
        if blocks_entire_report:
            blast_radius = "entire_report"
        elif withheld_calculations:
            blast_radius = ",".join(withheld_calculations)
        else:
            blast_radius = "none"
    return StreamFreshness(
        stream=stream, label=label, data_timestamp=data_timestamp, retrieved_at=retrieved_at,
        session=session, source=source, blocks_entire_report=blocks_entire_report,
        withheld_calculations=withheld_calculations, detail=detail,
        usable_for_display=usable_for_display, usable_for_vote=usable_for_vote,
        completeness=completeness, blast_radius=blast_radius,
    )


def classify_freshness_label(
    *, data_state: MarketDataState, data_age_seconds: float | None, has_quality_issues: bool, recent_max_age_seconds: float
) -> FreshnessLabel:
    """`recent_max_age_seconds` (THRESHOLD, required, undefaulted): above
    this age (but still within whatever staleness bound already qualified
    the data as non-`STALE_DATA`), data is labeled `RECENT` rather than
    `LIVE` — there is no single correct "still feels live" cutoff
    independent of what the data is being used for.

    Precedence: an unusable/insufficient state always wins; market-closed
    dominates over any age reading (matching `MarketDataState`'s own
    precedence); explicit staleness next; then quality issues
    (`DEGRADED`); only once all of those pass does age (`LIVE` vs
    `RECENT`) matter — mirroring `classify_market_data_state`'s own
    documented precedence order.
    """
    if data_state in (MarketDataState.INSUFFICIENT_HISTORY, MarketDataState.PROVIDER_UNAVAILABLE, MarketDataState.ERROR):
        return FreshnessLabel.UNAVAILABLE
    if data_state == MarketDataState.MARKET_CLOSED_LATEST_DATA:
        return FreshnessLabel.MARKET_CLOSED
    if data_state == MarketDataState.STALE_DATA:
        return FreshnessLabel.STALE
    if has_quality_issues:
        return FreshnessLabel.DEGRADED
    if data_age_seconds is not None and data_age_seconds > recent_max_age_seconds:
        return FreshnessLabel.RECENT
    return FreshnessLabel.LIVE
