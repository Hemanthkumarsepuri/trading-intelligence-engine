"""Multi-expiry term-structure analysis (Phase 4 of the Options
Intelligence Agent milestone) — compares the nearest/next/monthly expiries
of one underlying rather than only ever looking at the nearest one.

Pure composition of already-existing, unmodified functions
(`chain_analysis`, `iv_context`, `data_quality`) over multiple real
`OptionChainSnapshot`s the orchestration layer has already fetched — this
module fetches nothing itself and does not decide which expiries to fetch
(that remains `app.data.providers.upstox_fo_master.list_expiries()`'s job,
already built and unmodified).

Does not recommend an expiry "because it's nearest" — see
`ExpirySelectionNote`, which surfaces the real, observable trade-offs
(liquidity, spread quality, days remaining) for a human/downstream decision
to weigh, never a single auto-picked "best" expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.chain_analysis import atm_strike, chain_totals, top_oi_strikes
from app.domain.options.data_quality import check_chain_quality
from app.domain.options.iv_context import AtmIvSummary, atm_iv_summary
from app.domain.options.models import ChainQualityIssue, ChainTotals, StrikeOI


@dataclass(frozen=True)
class ExpirySummary:
    expiry: date
    is_weekly: bool  # FACT — from the instrument master, not inferred
    days_remaining: int  # METRIC — (expiry - as_of.date()).days; may be 0 on expiry day itself
    atm_strike: Decimal | None
    iv: AtmIvSummary
    totals: ChainTotals
    top_call_oi: list[StrikeOI]
    top_put_oi: list[StrikeOI]
    chain_quality_issues: list[ChainQualityIssue]


def build_expiry_summary(
    snapshot: OptionChainSnapshot,
    *,
    is_weekly: bool,
    as_of: datetime,
    max_chain_age_for_quality_check: timedelta,
    max_spread_fraction_for_quality_check: Decimal,
    top_n_strikes: int = 3,
) -> ExpirySummary:
    """`max_chain_age_for_quality_check`/`max_spread_fraction_for_quality_check`
    are forwarded verbatim to `check_chain_quality()` (THRESHOLD-class,
    required there for the same reason) — this function does not repeat
    that module's rationale, only its requirement that the caller decide.
    """
    totals = chain_totals(snapshot)
    return ExpirySummary(
        expiry=snapshot.expiry,
        is_weekly=is_weekly,
        days_remaining=(snapshot.expiry - as_of.date()).days,
        atm_strike=atm_strike(snapshot),
        iv=atm_iv_summary(snapshot),
        totals=totals,
        top_call_oi=top_oi_strikes(snapshot, right=OptionRight.CE, limit=top_n_strikes),
        top_put_oi=top_oi_strikes(snapshot, right=OptionRight.PE, limit=top_n_strikes),
        chain_quality_issues=check_chain_quality(
            snapshot, as_of=as_of, max_age=max_chain_age_for_quality_check,
            max_spread_fraction=max_spread_fraction_for_quality_check,
        ),
    )


@dataclass(frozen=True)
class TermStructure:
    """`expiries` is always ascending by expiry date. At least one entry
    is required (an empty term structure is a caller error, not a valid
    state to represent)."""

    expiries: list[ExpirySummary]

    def __post_init__(self) -> None:
        if not self.expiries:
            raise ValueError("TermStructure requires at least one ExpirySummary")

    @property
    def nearest(self) -> ExpirySummary:
        return self.expiries[0]

    def iv_slope(self) -> Decimal | None:
        """METRIC: nearest expiry's chain IV minus the farthest available
        expiry's chain IV. `None` if fewer than 2 expiries have a known
        `chain_iv`. Positive = near-dated IV richer than far-dated
        (commonly associated with near-term event/uncertainty pricing);
        negative = far-dated richer (calmer near-term) — a real,
        mathematically derived observation, not itself a trading signal.
        """
        known = [(e.expiry, e.iv.chain_iv) for e in self.expiries if e.iv.chain_iv is not None]
        if len(known) < 2:
            return None
        known.sort(key=lambda pair: pair[0])
        near_iv = known[0][1]
        far_iv = known[-1][1]
        assert near_iv is not None and far_iv is not None
        return near_iv - far_iv


@dataclass(frozen=True)
class ExpirySelectionNote:
    """Observable trade-offs across the term structure — never a single
    auto-picked "best" expiry. `flags` are plain, evidence-based
    observations (e.g. "nearest expiry has 1 chain-quality issue"),
    intentionally not scored or ranked against each other — ranking
    "liquidity" against "days remaining" against "IV level" requires a
    preference this module has no basis to assert on the user's behalf.
    """

    flags: list[str]


def build_expiry_selection_note(term_structure: TermStructure) -> ExpirySelectionNote:
    flags: list[str] = []
    for summary in term_structure.expiries:
        label = f"{summary.expiry.isoformat()} ({'weekly' if summary.is_weekly else 'monthly'}, {summary.days_remaining}d)"
        if summary.chain_quality_issues:
            flags.append(f"{label}: {len(summary.chain_quality_issues)} chain-quality issue(s)")
        if summary.totals.legs_with_known_oi == 0:
            flags.append(f"{label}: no legs with known OI")
        if summary.iv.chain_iv is None:
            flags.append(f"{label}: ATM IV unavailable")
    slope = term_structure.iv_slope()
    if slope is not None:
        direction = "near-dated richer" if slope > 0 else "far-dated richer" if slope < 0 else "flat"
        flags.append(f"IV term-structure slope: {slope:.2f} pts ({direction})")
    return ExpirySelectionNote(flags=flags)
