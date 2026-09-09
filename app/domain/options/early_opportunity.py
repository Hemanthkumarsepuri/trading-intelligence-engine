"""Early-opportunity classification -- buckets and timing, never a score.

Separates four questions the daily scanner previously collapsed into a
ranked shortlist:

1. DIRECTION -- already answered by the evidence matrix / research state.
2. TIMING -- is this early, developing, confirmed, late, or already moved?
3. CONTRACT QUALITY -- already answered by liquidity/decay/structure.
4. CONFIRMATION -- already answered by development narrative / blockers.

This module does not fetch, does not vote directionally, and does not
rank. Developing setups and events-to-monitor are different buckets:
fresh news without a named structural pattern is never a developing setup.

`ALREADY_MOVED` is intentionally a *timing/bucket* label, not a new
`ResearchState` member -- adding it to `derive_research_state()` would
collapse timing into maturity and break the documented precedence in
`docs/research/RESEARCH_STATE.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class ResearchBucket(str, Enum):
    HIGH_QUALITY_DEVELOPING = "HIGH_QUALITY_DEVELOPING"
    DEVELOPING = "DEVELOPING"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    CONFIRMED = "CONFIRMED"
    EXTENDED = "EXTENDED"
    ALREADY_MOVED = "ALREADY_MOVED"
    CONFLICT = "CONFLICT"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    NOT_INTERESTING = "NOT_INTERESTING"


class TimingStage(str, Enum):
    VERY_EARLY = "VERY_EARLY"
    EARLY = "EARLY"
    DEVELOPING = "DEVELOPING"
    CONFIRMING = "CONFIRMING"
    CONFIRMED = "CONFIRMED"
    MATURE = "MATURE"
    ALREADY_MOVED = "ALREADY_MOVED"
    EXTENDED = "EXTENDED"
    UNKNOWN = "UNKNOWN"


class MoveContext(str, Enum):
    """Displacement context from already-computed day-move / structure.

    Not a score. A +2% contained move can still be a developing setup;
    a +5% expansion can already be late without meeting the EXTENDED
    research-state threshold.
    """

    CONTAINED = "CONTAINED"
    BUILDING = "BUILDING"
    MATURE = "MATURE"
    EXTENDED = "EXTENDED"
    UNKNOWN = "UNKNOWN"


class UniverseTier(str, Enum):
    TIER_1_FO_LIQUID = "TIER_1_FO_LIQUID"
    TIER_1_FO_ILLIQUID = "TIER_1_FO_ILLIQUID"
    TIER_3_HIGH_VOLATILITY = "TIER_3_HIGH_VOLATILITY"
    TIER_4_ILLIQUID = "TIER_4_ILLIQUID"
    TIER_5_FO_RESTRICTED = "TIER_5_FO_RESTRICTED"
    UNKNOWN = "UNKNOWN"


_EARLY_TIMING_STATES = frozenset({"RANGE_BOUND", "EARLY_DIRECTIONAL_BUILD", "DEVELOPING_MOMENTUM"})
_ALREADY_MOVED_STATES = frozenset({"BREAKOUT_CONFIRMATION"})
_EXTENDED_TIMING_STATES = frozenset({"EXTENDED", "EXHAUSTION_RISK"})
_ILLIQUID_GRADES = frozenset({"UNTRADEABLE", "POOR", "untradeable", "poor"})
_ACCEPTABLE_LIQUIDITY = frozenset({"EXCELLENT", "GOOD", "MODERATE", "excellent", "good", "moderate"})
_FRESH_EVENT_LABELS = frozenset({"EVENT_RISK", "MAJOR_EVENT_RISK", "FRESH_MATERIAL_EVENT"})
_NAMED_DEVELOPING_PATTERNS = frozenset({
    "PRE_BREAKOUT_COMPRESSION",
    "FAILED_BREAKDOWN_RECLAIM",
    "OI_MIGRATION",
    "RELATIVE_STRENGTH",
    "RELATIVE_STRENGTH_ROTATION",
    "FUTURES_STRUCTURE",
})


@dataclass(frozen=True)
class EarlyOpportunityAssessment:
    bucket: ResearchBucket
    timing: TimingStage
    universe_tier: UniverseTier
    why_investigate_now: str
    already_moved: bool
    move_context: MoveContext = MoveContext.UNKNOWN


def classify_move_context(
    *,
    day_change_pct: Decimal | None,
    structural_context: str | None = None,
    session_range_pct: Decimal | None = None,
) -> MoveContext:
    """Deterministic displacement context -- not a score.

    Why each rule exists:
    - Missing day-change → UNKNOWN. Never invent a move size.
    - |day-change| >= 6% → EXTENDED. Same documented bar as
      `derive_research_state()` / Stage-1 exclusion. Kept, not deleted;
      it is necessary but not sufficient on its own, so later rules can
      still call a smaller expansion MATURE.
    - Intraday range >= 5% without compression → MATURE. A stock can
      already be extended *inside* the day even if close-to-prior-close
      is still under 6%.
    - |day-change| >= 4% without RANGE_COMPRESSION → MATURE. A large
      expansion that is not still compressing is late relative to an
      early-opportunity screen.
    - Compression keeps a 4–6% print in BUILDING: the structure has not
      yet expanded.
    - |day-change| >= 1% → BUILDING. Observable but not yet mature.
    - Else CONTAINED.

    Structure-break already-moved (BREAKOUT_CONFIRMATION) is classified
    in `classify_timing_stage` / `classify_research_bucket`, not here.
    Time-elapsed-since-move-began is not applied unless a real duration
    is supplied by a caller -- this function does not fabricate one.
    """
    if day_change_pct is None:
        return MoveContext.UNKNOWN
    magnitude = abs(day_change_pct)
    if magnitude >= Decimal("6"):
        return MoveContext.EXTENDED
    compressing = structural_context == "RANGE_COMPRESSION"
    if session_range_pct is not None and abs(session_range_pct) >= Decimal("5") and not compressing:
        return MoveContext.MATURE
    if magnitude >= Decimal("4") and not compressing:
        return MoveContext.MATURE
    if magnitude >= Decimal("1"):
        return MoveContext.BUILDING
    return MoveContext.CONTAINED


def classify_timing_stage(
    *,
    research_state: str,
    early_stage_state: str,
    move_context: MoveContext = MoveContext.UNKNOWN,
    development_pattern: str | None = None,
    pre_breakout_signal: bool = False,
) -> TimingStage:
    named_pattern = _has_named_structural_pattern(development_pattern, pre_breakout_signal)
    if research_state == "DATA_INSUFFICIENT" or early_stage_state == "INSUFFICIENT_DATA":
        return TimingStage.UNKNOWN
    if research_state == "EXTENDED" or early_stage_state in _EXTENDED_TIMING_STATES:
        return TimingStage.EXTENDED
    if early_stage_state in _ALREADY_MOVED_STATES:
        return TimingStage.ALREADY_MOVED
    if research_state == "CONFIRMED_SETUP":
        return TimingStage.CONFIRMED
    if move_context == MoveContext.EXTENDED:
        return TimingStage.EXTENDED
    if move_context == MoveContext.MATURE:
        return TimingStage.MATURE
    if research_state == "CONFIRMATION_PENDING":
        return TimingStage.CONFIRMING
    if early_stage_state == "RANGE_BOUND":
        return TimingStage.VERY_EARLY
    if research_state == "EARLY_SETUP" or early_stage_state == "EARLY_DIRECTIONAL_BUILD":
        return TimingStage.EARLY
    # WATCH without a named pattern is not DEVELOPING timing. Price-stage
    # DEVELOPING_MOMENTUM is still shown separately as early_stage_state.
    if named_pattern and (early_stage_state == "DEVELOPING_MOMENTUM" or research_state == "WATCH"):
        return TimingStage.DEVELOPING
    return TimingStage.UNKNOWN


def classify_universe_tier(*, fo_eligible: bool, liquidity_grade: str | None, fo_restricted: bool = False) -> UniverseTier:
    """Tier from facts this system actually has. Cash-only (Tier 2) is
    UNKNOWN here because the daily scanner's universe is F&O-eligible
    equities only -- never invent a cash-market classification without a
    cash universe. F&O ban/restriction is UNKNOWN unless the caller has
    a real restriction flag (none is wired from Upstox today)."""
    if fo_restricted:
        return UniverseTier.TIER_5_FO_RESTRICTED
    if not fo_eligible:
        return UniverseTier.UNKNOWN
    if liquidity_grade is None:
        return UniverseTier.UNKNOWN
    if liquidity_grade in _ILLIQUID_GRADES:
        return UniverseTier.TIER_1_FO_ILLIQUID
    if liquidity_grade in _ACCEPTABLE_LIQUIDITY:
        return UniverseTier.TIER_1_FO_LIQUID
    return UniverseTier.UNKNOWN


def _has_named_structural_pattern(development_pattern: str | None, pre_breakout_signal: bool) -> bool:
    if pre_breakout_signal:
        return True
    return development_pattern in _NAMED_DEVELOPING_PATTERNS


def classify_research_bucket(
    *,
    research_state: str | None,
    early_stage_state: str,
    development_pattern: str | None,
    pre_breakout_signal: bool,
    event_risk: str,
    liquidity_grade: str | None,
    fo_eligible: bool = True,
    day_change_pct: Decimal | None = None,
    structural_context: str | None = None,
) -> EarlyOpportunityAssessment:
    """Deterministic precedence -- first match wins. Never a count of
    evidence groups, never a numeric threshold.

    WATCH + pattern NONE is never a developing setup. Fresh news without
    a named structural pattern is EVENT_DRIVEN (events to monitor).
    """
    state = research_state or "UNKNOWN"
    move_context = classify_move_context(
        day_change_pct=day_change_pct, structural_context=structural_context,
    )
    timing = classify_timing_stage(
        research_state=state,
        early_stage_state=early_stage_state,
        move_context=move_context,
        development_pattern=development_pattern,
        pre_breakout_signal=pre_breakout_signal,
    )
    tier = classify_universe_tier(fo_eligible=fo_eligible, liquidity_grade=liquidity_grade)
    named_pattern = _has_named_structural_pattern(development_pattern, pre_breakout_signal)
    liquid_enough = liquidity_grade in _ACCEPTABLE_LIQUIDITY if liquidity_grade is not None else False
    early_timing = early_stage_state in _EARLY_TIMING_STATES
    not_late = timing not in (TimingStage.MATURE, TimingStage.EXTENDED, TimingStage.ALREADY_MOVED, TimingStage.CONFIRMED)

    def _result(
        bucket: ResearchBucket, *, why: str, already_moved: bool = False, timing_override: TimingStage | None = None,
    ) -> EarlyOpportunityAssessment:
        return EarlyOpportunityAssessment(
            bucket=bucket,
            timing=timing_override if timing_override is not None else timing,
            universe_tier=tier,
            why_investigate_now=why,
            already_moved=already_moved,
            move_context=move_context,
        )

    if state == "DATA_INSUFFICIENT" or early_stage_state == "INSUFFICIENT_DATA":
        return _result(
            ResearchBucket.DATA_INSUFFICIENT,
            why="Not enough trustworthy data to call this developing or extended.",
        )
    if state == "CONFLICT":
        return _result(
            ResearchBucket.CONFLICT,
            why="Independent evidence groups disagree -- not an early opportunity.",
        )
    if timing == TimingStage.EXTENDED or state == "EXTENDED":
        return _result(
            ResearchBucket.EXTENDED,
            why="The move is already extended by this system's own structure/day-move tests -- not an early opportunity.",
            already_moved=True,
            timing_override=TimingStage.EXTENDED,
        )
    if timing == TimingStage.ALREADY_MOVED:
        return _result(
            ResearchBucket.ALREADY_MOVED,
            why="Price has already broken recent structure. Investigate only if a NEW continuation setup is independently detected.",
            already_moved=True,
            timing_override=TimingStage.ALREADY_MOVED,
        )
    if early_stage_state == "FALSE_BREAKOUT_RISK" and development_pattern != "FAILED_BREAKDOWN_RECLAIM":
        return _result(
            ResearchBucket.NOT_INTERESTING,
            why="A breakout attempt already failed to hold -- that is a risk warning, not an early opportunity, unless a named reclaim pattern is independently present.",
            timing_override=TimingStage.UNKNOWN,
        )
    if tier == UniverseTier.TIER_1_FO_ILLIQUID:
        return _result(
            ResearchBucket.NOT_INTERESTING,
            why="F&O-eligible but the selected contract is not liquid enough to research as an options opportunity.",
        )
    if state == "CONFIRMED_SETUP":
        return _result(
            ResearchBucket.CONFIRMED,
            why="Independent current evidence already satisfies confirmation -- timing may already be late relative to an early-opportunity screen.",
            timing_override=TimingStage.CONFIRMED,
        )
    if (
        early_timing
        and not_late
        and liquid_enough
        and pre_breakout_signal
        and state not in ("NO_TRADE", "CONFIRMATION_PENDING")
    ):
        return _result(
            ResearchBucket.HIGH_QUALITY_DEVELOPING,
            why=(
                "Range compression + early-stage structure + proximity to a real opposing level "
                "(the documented pre-breakout combo), with acceptable contract liquidity, and the move not yet extended."
            ),
            timing_override=TimingStage.EARLY if timing == TimingStage.VERY_EARLY else timing,
        )
    if named_pattern and early_timing and not_late and state in ("EARLY_SETUP", "WATCH"):
        return _result(
            ResearchBucket.DEVELOPING,
            why=f"Named pattern {development_pattern} is present and the move is not yet extended. Confirmation is still incomplete.",
        )
    if event_risk in _FRESH_EVENT_LABELS and not named_pattern:
        return _result(
            ResearchBucket.EVENT_DRIVEN,
            why="A fresh material news item exists within the recency window -- monitor transmission; this is not by itself a directional setup.",
        )
    return _result(
        ResearchBucket.NOT_INTERESTING,
        why="No named structural development pattern, confirmed setup, or independent event-monitor classification applied. Pattern NONE is not a developing setup.",
    )


_EARLY_BUCKETS = frozenset({
    ResearchBucket.HIGH_QUALITY_DEVELOPING,
    ResearchBucket.DEVELOPING,
})


def is_early_opportunity_bucket(bucket: ResearchBucket) -> bool:
    """True only for genuine structural developing setups -- never events."""
    return bucket in _EARLY_BUCKETS


def is_event_to_monitor_bucket(bucket: ResearchBucket) -> bool:
    return bucket == ResearchBucket.EVENT_DRIVEN
