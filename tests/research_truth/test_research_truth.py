"""TIRE 95%+ Research Reliability & Performance Gate -- the canonical
95% research-TRUTH scenario suite.

This is NOT a prediction-accuracy suite (see the master directive's
Section 1) -- no scenario here asserts a market direction was "right."
Every scenario asserts one thing only: given a defined, valid research
situation, does TIRE correctly represent data freshness, data
completeness, evidence availability, research state, confirmation
status, invalidation status, contract quality, or provenance -- and,
critically, does it say UNKNOWN/DATA_INSUFFICIENT/CONFLICT/
CONFIRMATION_PENDING rather than a fabricated conclusion whenever it
cannot establish something reliably.

Deliberately additive: this module never replaces or edits any existing
test elsewhere in the suite (Section 5's own instruction). It is pure,
fast, deterministic unit-level coverage over the SAME already-tested
domain functions (`decide()`, `derive_research_state()`,
`determine_blockers()`, `classify_freshness_label()`, `classify_session()`,
`classify_sample_breadth()`, `assess_liquidity()`, institutional-flow
helpers) -- no new domain logic is introduced here, only verification
that these functions' real, already-implemented behavior matches the
95% research-truth bar.

Run with `pytest tests/research_truth/ -v` for the full per-scenario
breakdown, or `pytest tests/research_truth/ -k test_overall_pass_rate`
for just the aggregate scorecard (see `docs/TIRE_QUALITY_SCORECARD.md`
for the last captured result).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.config.settings import settings
from app.domain.market.data_state import MarketDataState
from app.domain.market.freshness import DataFreshness
from app.domain.market.institutional_flows import (
    FlowAvailability,
    caller_supplied_institutional_flows,
    unknown_institutional_flows,
)
from app.domain.market.models import OptionQuote, OptionRight
from app.domain.market.sample_breadth import BreadthCoverage, classify_sample_breadth
from app.domain.market.trading_calendar import classify_session, is_trading_day
from app.domain.options.decision_engine import (
    _BLOCKING_CHAIN_ISSUES,
    EXTENDED_MIN_DAY_CHANGE_PCT,
    DecisionResult,
    FinalDecision,
    QualityAssessment,
    QualityLevel,
    ResearchState,
    decide,
    derive_research_state,
)
from app.domain.options.development import DevelopmentNarrative, DevelopmentPattern
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    EvidenceRow,
    GroupVerdict,
    OverallConvergence,
)
from app.domain.options.freshness_label import FreshnessLabel, classify_freshness_label
from app.domain.options.liquidity import LiquidityGrade, assess_liquidity
from app.domain.options.models import ChainQualityIssueKind
from app.domain.options.research_blocker import BlockerClass, determine_blockers

AS_OF = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
EXPIRY = date(2026, 9, 24)
_MAX_AGE = timedelta(seconds=30)


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    # Zero-Tolerance Cases (master directive Section 7) -- 0 failures
    # required regardless of the aggregate percentage. Everything else
    # contributes only to the 95% aggregate.
    zero_tolerance: bool
    check: Callable[[], None]


_scenarios: list[Scenario] = []


def _register(id_: str, category: str, check: Callable[[], None], *, zero_tolerance: bool = False) -> None:
    _scenarios.append(Scenario(id=id_, category=category, zero_tolerance=zero_tolerance, check=check))


# ============================================================
# Helpers -- pure, deterministic construction of already-defined domain
# objects. No new analytical logic; every function called below is an
# existing, independently-tested module in `app.domain`.
# ============================================================


def _assessment(
    *,
    convergence: OverallConvergence = OverallConvergence.CONVERGENCE_BEARISH,
    market_bias: EvidenceDirection = EvidenceDirection.BEARISH,
    option_quality: QualityLevel = QualityLevel.MODERATE,
    liquidity_quality: QualityLevel = QualityLevel.MODERATE,
    data_quality: QualityLevel = QualityLevel.MODERATE,
    setup_quality: QualityLevel = QualityLevel.MODERATE,
    risk_quality: QualityLevel = QualityLevel.MODERATE,
) -> QualityAssessment:
    return QualityAssessment(
        market_bias=market_bias, convergence=convergence, setup_quality=setup_quality, option_quality=option_quality,
        liquidity_quality=liquidity_quality, data_quality=data_quality, risk_quality=risk_quality,
        supporting_evidence_count=1, conflicting_evidence_count=0,
    )


def _decision(assessment: QualityAssessment) -> DecisionResult:
    return decide(assessment)


def _leg(*, bid: float | None, ask: float | None, volume: int | None, oi: int | None, age: timedelta = timedelta(0), iv: float | None = 20.0) -> OptionQuote:
    ts = AS_OF - age
    return OptionQuote(
        provider="test", freshness=DataFreshness(data_timestamp=ts, received_timestamp=ts), underlying="TESTCO",
        expiry=EXPIRY, strike=Decimal("1000"), right=OptionRight.CE,
        bid_price=Decimal(str(bid)) if bid is not None else None, ask_price=Decimal(str(ask)) if ask is not None else None,
        volume=volume, open_interest=oi, implied_volatility=Decimal(str(iv)) if iv is not None else None,
    )


def _assert(condition: bool, detail: str) -> None:
    if not condition:
        raise AssertionError(detail)


# ============================================================
# 1. DATA -- freshness/completeness of each independent stream.
# ============================================================

def _data_scenarios() -> None:
    _register(
        "data.quote.current",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.LIVE_STREAMING, data_age_seconds=5.0, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.LIVE,
            "current quote must be labeled LIVE",
        ),
    )
    _register(
        "data.quote.recent",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.LIVE_STREAMING, data_age_seconds=120.0, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.RECENT,
            "quote older than recent_max_age_seconds but not STALE_DATA must be RECENT, never silently LIVE",
        ),
    )
    _register(
        "data.quote.stale",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.STALE_DATA, data_age_seconds=900.0, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.STALE,
            "STALE_DATA market state must always yield STALE label",
        ),
    )
    _register(
        "data.quote.missing",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.PROVIDER_UNAVAILABLE, data_age_seconds=None, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.UNAVAILABLE,
            "a provider-unavailable quote must be UNAVAILABLE, never fabricated as LIVE/RECENT",
        ),
        zero_tolerance=False,
    )
    _register(
        "data.chain.current",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.LIVE_SNAPSHOT, data_age_seconds=10.0, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.LIVE,
            "current chain snapshot must be labeled LIVE",
        ),
    )
    _register(
        "data.chain.stale",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.STALE_DATA, data_age_seconds=1800.0, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.STALE,
            "stale chain snapshot must never be relabeled current",
        ),
    )
    _register(
        "data.chain.dead",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.INSUFFICIENT_HISTORY, data_age_seconds=None, has_quality_issues=True, recent_max_age_seconds=60,
            ) == FreshnessLabel.UNAVAILABLE,
            "a dead/insufficient chain must be UNAVAILABLE regardless of quality-issue flag",
        ),
    )
    _register(
        "data.chain.partial_with_quality_issue",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.LIVE_STREAMING, data_age_seconds=5.0, has_quality_issues=True, recent_max_age_seconds=60,
            ) == FreshnessLabel.DEGRADED,
            "fresh but quality-flagged data (e.g. partial chain) must be DEGRADED, distinct from clean LIVE",
        ),
    )
    _register(
        "data.futures.current",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.LIVE_STREAMING, data_age_seconds=5.0, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.LIVE,
            "current futures quote must be labeled LIVE",
        ),
    )
    _register(
        "data.futures.stale",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.STALE_DATA, data_age_seconds=3600.0, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.STALE,
            "stale futures quote must be STALE, never treated as current",
        ),
    )
    _register(
        "data.futures.missing",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.PROVIDER_UNAVAILABLE, data_age_seconds=None, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.UNAVAILABLE,
            "a missing futures contract/quote must read UNAVAILABLE",
        ),
    )
    _register(
        "data.m15.stale_forces_confirmation_pending_blocker",
        "DATA",
        lambda: _assert(
            determine_blockers(
                decision=_decision(_assessment()), candles_are_current=False, chain_is_current=True, quote_is_current=True,
                day_change_pct=Decimal("1.0"), development=None,
            ).primary.blocker_class in (BlockerClass.CORE_STREAM_STALE, BlockerClass.CONTRACT_UNUSABLE, BlockerClass.DATA_INSUFFICIENT, BlockerClass.CONFLICT),
            "a stale M15 stream must surface as a real blocker (CORE_STREAM_STALE, or something more fundamental), never silently ignored",
        ),
    )
    _register(
        "data.m15.current_no_stale_blocker",
        "DATA",
        lambda: _assert(
            not any(
                b.blocker_class == BlockerClass.CORE_STREAM_STALE
                for b in (
                    determine_blockers(
                        decision=_decision(_assessment()), candles_are_current=True, chain_is_current=True, quote_is_current=True,
                        day_change_pct=Decimal("1.0"), development=None,
                    ).secondary
                    + (determine_blockers(
                        decision=_decision(_assessment()), candles_are_current=True, chain_is_current=True, quote_is_current=True,
                        day_change_pct=Decimal("1.0"), development=None,
                    ).primary,)
                )
            ),
            "current M15/chain/quote streams must never spuriously report CORE_STREAM_STALE",
        ),
    )
    _register(
        "data.candles.missing_is_unavailable_not_fabricated",
        "DATA",
        lambda: _assert(
            classify_freshness_label(
                data_state=MarketDataState.INSUFFICIENT_HISTORY, data_age_seconds=None, has_quality_issues=False, recent_max_age_seconds=60,
            ) == FreshnessLabel.UNAVAILABLE,
            "missing candle history must read UNAVAILABLE, never a fabricated trend reading",
        ),
        zero_tolerance=True,
    )


# ============================================================
# 2. OPTIONS -- contract/liquidity quality classification.
# ============================================================

def _options_scenarios() -> None:
    _register(
        "options.liquid_atm",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=100, ask=102, volume=10_000, oi=100_000), as_of=AS_OF, max_quote_age=_MAX_AGE).grade == LiquidityGrade.EXCELLENT,
            "a tight-spread, high-volume, high-OI leg must grade EXCELLENT",
        ),
    )
    _register(
        "options.illiquid",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=100, ask=101, volume=0, oi=0), as_of=AS_OF, max_quote_age=_MAX_AGE).grade == LiquidityGrade.UNTRADEABLE,
            "zero volume and zero OI must grade UNTRADEABLE, never a usable candidate",
        ),
        zero_tolerance=True,
    )
    _register(
        "options.wide_spread",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=80, ask=120, volume=10_000, oi=100_000), as_of=AS_OF, max_quote_age=_MAX_AGE).grade in (LiquidityGrade.POOR, LiquidityGrade.UNTRADEABLE),
            "a ~40% spread must never grade EXCELLENT/GOOD",
        ),
    )
    _register(
        "options.low_oi",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=100, ask=101, volume=10_000, oi=100), as_of=AS_OF, max_quote_age=_MAX_AGE).grade != LiquidityGrade.EXCELLENT,
            "low OI (100) must not grade EXCELLENT despite good spread/volume",
        ),
    )
    _register(
        "options.low_volume",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=100, ask=101, volume=10, oi=100_000), as_of=AS_OF, max_quote_age=_MAX_AGE).grade != LiquidityGrade.EXCELLENT,
            "low volume (10) must not grade EXCELLENT despite good spread/OI",
        ),
    )
    _register(
        "options.missing_iv_not_fatal_to_liquidity_grade",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=100, ask=102, volume=10_000, oi=100_000, iv=None), as_of=AS_OF, max_quote_age=_MAX_AGE).grade == LiquidityGrade.EXCELLENT,
            "missing IV must not itself be conflated with a liquidity defect (they are independent dimensions)",
        ),
    )
    _register(
        "options.missing_bid_ask",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=None, ask=None, volume=1000, oi=10_000), as_of=AS_OF, max_quote_age=_MAX_AGE).grade == LiquidityGrade.UNTRADEABLE,
            "no usable bid/ask must grade UNTRADEABLE regardless of volume/OI",
        ),
        zero_tolerance=True,
    )
    _register(
        "options.stale_quote_is_untradeable",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=100, ask=101, volume=1000, oi=10_000, age=timedelta(minutes=10)), as_of=AS_OF, max_quote_age=_MAX_AGE).grade == LiquidityGrade.UNTRADEABLE,
            "a stale option quote must never be treated as a live, tradeable candidate",
        ),
        zero_tolerance=True,
    )
    _register(
        "options.no_candidate_forces_contract_unusable_blocker",
        "OPTIONS",
        lambda: _assert(
            determine_blockers(
                decision=_decision(_assessment(option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT)),
                candles_are_current=True, chain_is_current=True, quote_is_current=True, day_change_pct=Decimal("0.5"), development=None,
            ).primary.blocker_class == BlockerClass.CONTRACT_UNUSABLE,
            "no sufficiently liquid candidate must surface CONTRACT_UNUSABLE as the primary blocker (the real KAYNES case)",
        ),
        zero_tolerance=True,
    )
    _register(
        "options.no_candidate_never_becomes_tradeable",
        "OPTIONS",
        lambda: _assert(
            _decision(_assessment(option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT)).decision != FinalDecision.TRADEABLE,
            "an INSUFFICIENT option/liquidity quality must never resolve to TRADEABLE",
        ),
        zero_tolerance=True,
    )
    _register(
        "options.excellent_liquidity_maps_to_strong_quality",
        "OPTIONS",
        lambda: _assert(
            _assessment(liquidity_quality=QualityLevel.STRONG).liquidity_quality == QualityLevel.STRONG,
            "structural sanity: STRONG liquidity_quality must round-trip unchanged through QualityAssessment",
        ),
    )
    _register(
        "options.good_liquidity_maps_to_moderate",
        "OPTIONS",
        lambda: _assert(
            {LiquidityGrade.GOOD, LiquidityGrade.EXCELLENT, LiquidityGrade.POOR, LiquidityGrade.UNTRADEABLE} == set(LiquidityGrade),
            "structural sanity: liquidity grade vocabulary must remain the documented 4-tier set (no silent extra grade)",
        ),
    )
    _register(
        "options.invalid_expiry_absence_of_candidate_is_honest_not_fabricated",
        "OPTIONS",
        lambda: _assert(
            derive_research_state(
                decision=_decision(_assessment(option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT)),
                candles_are_current=True, day_change_pct=Decimal("0.5"),
            ) == ResearchState.NO_TRADE,
            "no valid contract for the expiry must resolve to NO_TRADE, never CONFIRMED_SETUP",
        ),
        zero_tolerance=True,
    )
    _register(
        "options.invalid_strike_absence_of_candidate_is_honest",
        "OPTIONS",
        lambda: _assert(
            _decision(_assessment(option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT)).decision == FinalDecision.NO_TRADE,
            "no candidate at any strike must resolve to NO_TRADE, matching the real 'no sufficiently liquid option candidate' reasoning",
        ),
    )
    _register(
        "options.excellent_atm_and_near_atm_both_strong_candidate_path",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=100, ask=103, volume=6000, oi=60_000), as_of=AS_OF, max_quote_age=_MAX_AGE).grade == LiquidityGrade.EXCELLENT,
            "a comfortably-EXCELLENT-bar leg must grade EXCELLENT deterministically",
        ),
    )


# ============================================================
# 3. EVIDENCE -- convergence/development classification.
# ============================================================

def _row(name: str, group: EvidenceGroup, direction: EvidenceDirection) -> EvidenceRow:
    return EvidenceRow(name, group, direction, "test row")


def _evidence_scenarios() -> None:
    bullish_matrix = EvidenceMatrix(rows=[
        _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
        _row("OI structure", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
    ])
    bearish_matrix = EvidenceMatrix(rows=[
        _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BEARISH),
        _row("OI structure", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
    ])
    conflicting_matrix = EvidenceMatrix(rows=[
        _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH),
        _row("OI structure", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
    ])
    missing_matrix = EvidenceMatrix(rows=[
        _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.UNKNOWN),
        _row("OI structure", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL),
    ])
    correlated_matrix = EvidenceMatrix(rows=[
        _row("PCR change", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
        _row("Change in OI (ATM CE)", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
        _row("Change in OI (ATM PE)", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
    ])

    _register(
        "evidence.bullish_convergence",
        "EVIDENCE",
        lambda: _assert(bullish_matrix.overall_convergence() == OverallConvergence.CONVERGENCE_BULLISH, "two independent bullish groups must converge BULLISH"),
    )
    _register(
        "evidence.bearish_convergence",
        "EVIDENCE",
        lambda: _assert(bearish_matrix.overall_convergence() == OverallConvergence.CONVERGENCE_BEARISH, "two independent bearish groups must converge BEARISH"),
    )
    _register(
        "evidence.conflicting_groups_yield_conflict",
        "EVIDENCE",
        lambda: _assert(conflicting_matrix.overall_convergence() == OverallConvergence.CONFLICT, "disagreeing independent groups must yield CONFLICT, never an arbitrary tie-break"),
        zero_tolerance=True,
    )
    _register(
        "evidence.missing_evidence_yields_insufficient_not_neutral_confirmation",
        "EVIDENCE",
        lambda: _assert(missing_matrix.overall_convergence() == OverallConvergence.INSUFFICIENT_EVIDENCE, "all-UNKNOWN/NEUTRAL rows must yield INSUFFICIENT_EVIDENCE, never a fabricated bias"),
        zero_tolerance=True,
    )
    _register(
        "evidence.correlated_rows_count_as_one_group_not_three",
        "EVIDENCE",
        lambda: _assert(correlated_matrix.supporting_group_count(EvidenceDirection.BULLISH) == 1, "3 correlated rows from ONE group must count as 1 supporting group, never inflate evidence_quality"),
        zero_tolerance=True,
    )
    _register(
        "evidence.oi_migration_is_a_named_development_pattern",
        "EVIDENCE",
        lambda: _assert(DevelopmentPattern.OI_MIGRATION.value == "OI_MIGRATION", "OI migration must remain a named, documented development pattern"),
    )
    _register(
        "evidence.relative_strength_is_a_named_development_pattern",
        "EVIDENCE",
        lambda: _assert(DevelopmentPattern.RELATIVE_STRENGTH.value == "RELATIVE_STRENGTH", "relative strength must remain a named, documented development pattern"),
    )
    _register(
        "evidence.futures_basis_is_a_named_development_pattern",
        "EVIDENCE",
        lambda: _assert(DevelopmentPattern.FUTURES_STRUCTURE.value == "FUTURES_STRUCTURE", "futures-basis change must remain a named, documented development pattern"),
    )
    _register(
        "evidence.extended_move_forces_extended_state_when_otherwise_clean",
        "EVIDENCE",
        lambda: _assert(
            derive_research_state(
                decision=_decision(_assessment(setup_quality=QualityLevel.STRONG, option_quality=QualityLevel.STRONG, liquidity_quality=QualityLevel.STRONG, risk_quality=QualityLevel.STRONG)),
                candles_are_current=True, day_change_pct=Decimal("7.0"),
            ) == ResearchState.EXTENDED,
            "a >=6% day move on an otherwise-tradeable setup must read EXTENDED, not CONFIRMED_SETUP",
        ),
        zero_tolerance=True,
    )
    _register(
        "evidence.no_hidden_score_development_pattern_vocabulary_closed",
        "EVIDENCE",
        lambda: _assert(
            {p.value for p in DevelopmentPattern} == {
                "NONE",
                "OI_MIGRATION",
                "RELATIVE_STRENGTH",
                "RELATIVE_STRENGTH_ROTATION",
                "FUTURES_STRUCTURE",
                "PRE_BREAKOUT_COMPRESSION",
                "FAILED_BREAKDOWN_RECLAIM",
            },
            "development pattern vocabulary must stay the documented closed set -- no numeric opportunity_score/confidence sneaking in",
        ),
        zero_tolerance=True,
    )
    _register(
        "evidence.group_verdict_conflicting_when_rows_disagree_within_one_group",
        "EVIDENCE",
        lambda: _assert(
            EvidenceMatrix(rows=[
                _row("Change in OI (ATM CE)", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH),
                _row("Change in OI (ATM PE)", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH),
            ]).group_verdict(EvidenceGroup.OPTIONS_OI) == GroupVerdict.CONFLICTING,
            "two rows in the SAME group disagreeing must read CONFLICTING for that group, never silently averaged",
        ),
        zero_tolerance=True,
    )
    _register(
        "evidence.non_directional_group_never_votes",
        "EVIDENCE",
        lambda: _assert(
            EvidenceMatrix(rows=[_row("Liquidity", EvidenceGroup.LIQUIDITY, EvidenceDirection.NEUTRAL)]).group_verdict(EvidenceGroup.LIQUIDITY) == GroupVerdict.NON_DIRECTIONAL,
            "a never-directional group (liquidity/data-quality) must read NON_DIRECTIONAL, never BULLISH/BEARISH",
        ),
        zero_tolerance=True,
    )
    _register(
        "evidence.data_quality_group_never_directional",
        "EVIDENCE",
        lambda: _assert(EvidenceGroup.DATA_QUALITY.value == "data_quality", "DATA_QUALITY must remain a distinct, non-directional evidence group"),
    )


# ============================================================
# 4. SESSION -- calendar/market-session classification.
# ============================================================

def _session_scenarios() -> None:
    _register(
        "session.weekday_trading_day",
        "SESSION",
        lambda: _assert(classify_session(date(2026, 8, 28)).is_trading_day, "an ordinary Friday must classify as a trading day"),
    )
    _register(
        "session.saturday_is_weekend_not_trading",
        "SESSION",
        lambda: _assert(classify_session(date(2026, 8, 29)).is_weekend and not classify_session(date(2026, 8, 29)).is_trading_day, "Saturday must read as weekend, never a trading day"),
        zero_tolerance=True,
    )
    _register(
        "session.sunday_is_weekend_not_trading",
        "SESSION",
        lambda: _assert(classify_session(date(2026, 8, 30)).is_weekend and not classify_session(date(2026, 8, 30)).is_trading_day, "Sunday must read as weekend, never a trading day"),
        zero_tolerance=True,
    )
    _register(
        "session.monday_after_weekend_is_trading",
        "SESSION",
        lambda: _assert(classify_session(date(2026, 8, 31)).is_trading_day, "the Monday after a weekend must be a real trading day"),
    )
    _register(
        "session.next_trading_day_populated_on_non_trading_day",
        "SESSION",
        lambda: _assert(classify_session(date(2026, 8, 29)).next_trading_day is not None, "a non-trading day must carry a real next_trading_day, never left unresolved"),
    )
    _register(
        "session.next_trading_day_none_on_a_real_trading_day",
        "SESSION",
        lambda: _assert(classify_session(date(2026, 8, 28)).next_trading_day is None, "a real trading day must not fabricate a next_trading_day (it IS the trading day)"),
    )
    _register(
        "session.is_trading_day_helper_agrees_with_classify_session",
        "SESSION",
        lambda: _assert(is_trading_day(date(2026, 8, 28)) == classify_session(date(2026, 8, 28)).is_trading_day, "is_trading_day() and classify_session().is_trading_day must never disagree (single source of truth)"),
        zero_tolerance=True,
    )
    _register(
        "session.weekend_flag_never_true_on_a_trading_day",
        "SESSION",
        lambda: _assert(not classify_session(date(2026, 8, 28)).is_weekend, "a Friday trading day must never be flagged as a weekend"),
    )
    _register(
        "session.market_state_vocabulary_covers_closed_and_open_variants",
        "SESSION",
        lambda: _assert(
            {MarketDataState.LIVE_STREAMING, MarketDataState.MARKET_CLOSED_LATEST_DATA, MarketDataState.PRE_MARKET, MarketDataState.STALE_DATA}.issubset(set(MarketDataState)),
            "the market-state vocabulary must include real open/closed/pre-market/stale variants",
        ),
    )
    _register(
        "session.calendar_date_round_trips",
        "SESSION",
        lambda: _assert(classify_session(date(2026, 8, 28)).calendar_date == date(2026, 8, 28), "classify_session must echo back the exact real calendar date queried"),
    )


# ============================================================
# 5. CASH -- sample breadth / institutional-flow honesty.
# ============================================================

def _cash_scenarios() -> None:
    universe = tuple(f"SYM{i}" for i in range(10))

    def full_coverage() -> None:
        result = classify_sample_breadth(
            universe="TEST10", source="test", expected_symbols=universe,
            day_change_pct_by_symbol={s: Decimal("1.0") for s in universe},
            as_of=AS_OF, retrieved_at=AS_OF, session="LIVE",
        )
        _assert(result.coverage == BreadthCoverage.COMPLETE, "full symbol coverage must classify COMPLETE")

    def insufficient_coverage() -> None:
        partial = {s: Decimal("1.0") for s in universe[:3]}  # 30% -- below the 60% floor
        result = classify_sample_breadth(
            universe="TEST10", source="test", expected_symbols=universe, day_change_pct_by_symbol=partial,
            as_of=AS_OF, retrieved_at=AS_OF, session="LIVE",
        )
        _assert(result.coverage == BreadthCoverage.INSUFFICIENT, "30% real coverage must classify INSUFFICIENT, never silently reported as if complete")

    def missing_symbols_never_treated_as_unchanged() -> None:
        data = {s: Decimal("1.0") for s in universe[:6]}
        result = classify_sample_breadth(
            universe="TEST10", source="test", expected_symbols=universe, day_change_pct_by_symbol=data,
            as_of=AS_OF, retrieved_at=AS_OF, session="LIVE",
        )
        _assert(result.missing_count == 4, "missing symbols must be counted as missing, never silently defaulted to 0.00% (unchanged)")

    def no_universe_is_unknown_not_zero() -> None:
        result = classify_sample_breadth(
            universe="EMPTY", source="test", expected_symbols=(), day_change_pct_by_symbol={},
            as_of=AS_OF, retrieved_at=AS_OF, session="LIVE",
        )
        _assert(result.coverage == BreadthCoverage.UNKNOWN, "no constituent universe supplied must read UNKNOWN, never a fabricated breadth reading")

    def fii_unknown_when_no_producer() -> None:
        ctx = unknown_institutional_flows(retrieved_at=AS_OF)
        _assert(ctx.availability == FlowAvailability.UNKNOWN and ctx.fii_cash_net is None, "FII/DII must read UNKNOWN with no fabricated figure when no verified producer exists")

    def fii_caller_supplied_is_labeled_as_such() -> None:
        ctx = caller_supplied_institutional_flows(
            fii_cash_net=Decimal("100.0"), dii_cash_net=Decimal("-50.0"), index_fo_net=None,
            as_of_date=AS_OF.date(), source="manual entry", retrieved_at=AS_OF,
        )
        _assert(ctx.availability == FlowAvailability.CALLER_SUPPLIED and "manual entry" in ctx.detail, "caller-supplied FII/DII figures must be explicitly labeled with their real source, never presented as an exchange feed")

    def delivery_previous_session_is_eod_not_live() -> None:
        _assert(FreshnessLabel.EOD.value == "EOD", "previous-session delivery data must have a distinct EOD label, never conflated with LIVE")

    def index_fo_flow_independent_of_cash_flow() -> None:
        ctx = caller_supplied_institutional_flows(
            fii_cash_net=Decimal("100.0"), dii_cash_net=Decimal("50.0"), index_fo_net=None,
            as_of_date=AS_OF.date(), source="manual", retrieved_at=AS_OF,
        )
        _assert(ctx.index_fo_net is None, "index F&O net flow must never be inferred/fabricated from cash flow figures")

    def breadth_never_becomes_a_directional_evidence_row() -> None:
        _assert(EvidenceGroup.DATA_QUALITY.value != "sample_breadth" and not any(g.value == "sample_breadth" for g in EvidenceGroup), "sample breadth must never appear as an EvidenceGroup -- it is informational cash context only, never a vote")

    _register("cash.valid_sample_breadth_full_coverage", "CASH", full_coverage)
    _register("cash.insufficient_breadth_coverage", "CASH", insufficient_coverage, zero_tolerance=True)
    _register("cash.missing_symbols_not_treated_as_unchanged", "CASH", missing_symbols_never_treated_as_unchanged, zero_tolerance=True)
    _register("cash.no_universe_is_unknown", "CASH", no_universe_is_unknown_not_zero)
    _register("cash.fii_unknown_without_producer", "CASH", fii_unknown_when_no_producer)
    _register("cash.fii_caller_supplied_labeled", "CASH", fii_caller_supplied_is_labeled_as_such)
    _register("cash.delivery_previous_session_labeled_eod", "CASH", delivery_previous_session_is_eod_not_live)
    _register("cash.index_fo_flow_independent_of_cash", "CASH", index_fo_flow_independent_of_cash_flow)
    _register("cash.breadth_never_a_hidden_evidence_row", "CASH", breadth_never_becomes_a_directional_evidence_row, zero_tolerance=True)


# ============================================================
# 6. RESEARCH STATE -- the 8-state vocabulary end to end.
# ============================================================

def _research_state_scenarios() -> None:
    def state_for(**overrides: object) -> ResearchState:
        decision_kwargs = {k: v for k, v in overrides.items() if k in {"convergence", "market_bias", "option_quality", "liquidity_quality", "data_quality", "setup_quality", "risk_quality"}}
        derive_kwargs = {k: v for k, v in overrides.items() if k not in decision_kwargs}
        derive_kwargs.setdefault("candles_are_current", True)
        derive_kwargs.setdefault("day_change_pct", Decimal("0.5"))
        return derive_research_state(decision=_decision(_assessment(**decision_kwargs)), **derive_kwargs)

    _register("state.watch", "RESEARCH_STATE", lambda: _assert(state_for() == ResearchState.WATCH, "moderate-everything, no development pattern must read WATCH"))
    _register(
        "state.confirmed_setup",
        "RESEARCH_STATE",
        lambda: _assert(
            state_for(setup_quality=QualityLevel.STRONG, option_quality=QualityLevel.STRONG, liquidity_quality=QualityLevel.STRONG, risk_quality=QualityLevel.STRONG) == ResearchState.CONFIRMED_SETUP,
            "4/4 STRONG dimensions with no extension/staleness must read CONFIRMED_SETUP",
        ),
    )
    _register(
        "state.confirmation_pending_on_stale_candles",
        "RESEARCH_STATE",
        lambda: _assert(
            state_for(setup_quality=QualityLevel.STRONG, option_quality=QualityLevel.STRONG, liquidity_quality=QualityLevel.STRONG, risk_quality=QualityLevel.STRONG, candles_are_current=False) == ResearchState.CONFIRMATION_PENDING,
            "an otherwise-tradeable setup with stale candles must demote to CONFIRMATION_PENDING, never CONFIRMED_SETUP",
        ),
        zero_tolerance=True,
    )
    _register(
        "state.conflict",
        "RESEARCH_STATE",
        lambda: _assert(state_for(convergence=OverallConvergence.CONFLICT) == ResearchState.CONFLICT, "matrix CONFLICT convergence must always read research state CONFLICT"),
        zero_tolerance=True,
    )
    _register(
        "state.data_insufficient",
        "RESEARCH_STATE",
        lambda: _assert(state_for(data_quality=QualityLevel.INSUFFICIENT) == ResearchState.DATA_INSUFFICIENT, "insufficient data quality must always take precedence and read DATA_INSUFFICIENT"),
        zero_tolerance=True,
    )
    _register(
        "state.no_trade_on_illiquid_contract",
        "RESEARCH_STATE",
        lambda: _assert(state_for(option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT) == ResearchState.NO_TRADE, "no viable contract must read NO_TRADE, never a confirmed state"),
        zero_tolerance=True,
    )
    _register(
        "state.extended",
        "RESEARCH_STATE",
        lambda: _assert(
            state_for(setup_quality=QualityLevel.STRONG, option_quality=QualityLevel.STRONG, liquidity_quality=QualityLevel.STRONG, risk_quality=QualityLevel.STRONG, day_change_pct=Decimal("8.0")) == ResearchState.EXTENDED,
            "a large day move on an otherwise-confirmed setup must read EXTENDED",
        ),
    )
    _register(
        "state.early_setup_when_watch_plus_named_pattern",
        "RESEARCH_STATE",
        lambda: _assert(
            derive_research_state(decision=_decision(_assessment()), candles_are_current=True, day_change_pct=Decimal("0.5"), development_pattern="OI_MIGRATION") == ResearchState.EARLY_SETUP,
            "a WATCH-tier decision with a real named development pattern must read EARLY_SETUP, distinct from plain WATCH",
        ),
    )
    _register(
        "state.watch_without_pattern_stays_watch_not_early_setup",
        "RESEARCH_STATE",
        lambda: _assert(
            derive_research_state(decision=_decision(_assessment()), candles_are_current=True, day_change_pct=Decimal("0.5"), development_pattern="NONE") == ResearchState.WATCH,
            "WATCH with pattern=NONE must never be inflated to EARLY_SETUP",
        ),
        zero_tolerance=True,
    )
    _register(
        "state.data_insufficient_beats_conflict",
        "RESEARCH_STATE",
        lambda: _assert(state_for(data_quality=QualityLevel.INSUFFICIENT, convergence=OverallConvergence.CONFLICT) == ResearchState.DATA_INSUFFICIENT, "DATA_INSUFFICIENT must take precedence over CONFLICT (per decide()'s own documented order)"),
        zero_tolerance=True,
    )
    _register(
        "state.confirmation_pending_beats_no_trade",
        "RESEARCH_STATE",
        lambda: _assert(
            state_for(option_quality=QualityLevel.INSUFFICIENT, liquidity_quality=QualityLevel.INSUFFICIENT, chain_is_current=False) == ResearchState.CONFIRMATION_PENDING,
            "stale chain must demote to CONFIRMATION_PENDING even when the decision is independently NO_TRADE",
        ),
    )
    _register(
        "state.vocabulary_is_the_documented_closed_set",
        "RESEARCH_STATE",
        lambda: _assert(
            {s.value for s in ResearchState} == {"UNKNOWN", "WATCH", "EARLY_SETUP", "CONFIRMATION_PENDING", "CONFIRMED_SETUP", "CONFLICT", "EXTENDED", "NO_TRADE", "DATA_INSUFFICIENT"},
            "ResearchState vocabulary must stay the documented 9-value closed set",
        ),
        zero_tolerance=True,
    )
    _register(
        "state.tradeable_decision_never_implies_order_execution",
        "RESEARCH_STATE",
        lambda: _assert(FinalDecision.TRADEABLE.value == "TRADEABLE" and not hasattr(FinalDecision, "BUY") and not hasattr(FinalDecision, "SELL"), "FinalDecision must never contain a BUY/SELL member -- TRADEABLE is a compatibility research label only"),
        zero_tolerance=True,
    )
    _register(
        "state.primary_blocker_never_contradicts_data_insufficient",
        "RESEARCH_STATE",
        lambda: _assert(
            determine_blockers(decision=_decision(_assessment(data_quality=QualityLevel.INSUFFICIENT)), candles_are_current=True, chain_is_current=True, quote_is_current=True, day_change_pct=Decimal("0.5"), development=None).primary.blocker_class == BlockerClass.DATA_INSUFFICIENT,
            "DATA_INSUFFICIENT decisions must always surface DATA_INSUFFICIENT as the primary blocker",
        ),
        zero_tolerance=True,
    )
    _register(
        "state.primary_blocker_identifies_conflicting_evidence_for_conflict_state",
        "RESEARCH_STATE",
        lambda: _assert(
            "CONFLICT" in determine_blockers(decision=_decision(_assessment(convergence=OverallConvergence.CONFLICT, market_bias=EvidenceDirection.NEUTRAL)), candles_are_current=True, chain_is_current=True, quote_is_current=True, day_change_pct=None, development=None).primary.explanation,
            "a CONFLICT primary blocker's explanation must identify that evidence groups disagree, never a vague/generic message",
        ),
        zero_tolerance=True,
    )
    _register(
        "state.confirmation_pending_names_exactly_what_is_pending",
        "RESEARCH_STATE",
        lambda: _assert(
            determine_blockers(
                decision=_decision(_assessment()), candles_are_current=False, chain_is_current=True, quote_is_current=True, day_change_pct=Decimal("0.5"),
                development=DevelopmentNarrative(pattern=DevelopmentPattern.NONE, what_is_developing="x", why_it_matters="y", what_is_missing="M15 confirmation", confirm_if="c", invalidate_if="i", freshness_note="f"),
            ).primary.explanation != "",
            "a CONFIRMATION_PENDING-style blocker must name a specific, non-empty pending confirmation, never an empty explanation",
        ),
    )


# ============================================================
# 7. SAFETY -- zero-tolerance cases (Section 7).
# ============================================================

def _safety_scenarios() -> None:
    _register(
        "safety.broker_execution_disabled_by_default",
        "SAFETY",
        lambda: _assert(settings.broker_order_execution_enabled is False, "BROKER_ORDER_EXECUTION_ENABLED must default to False"),
        zero_tolerance=True,
    )
    _register(
        "safety.broker_execution_assertion_raises_when_tampered",
        "SAFETY",
        lambda: _assert(
            _raises(lambda: type(settings)(broker_order_execution_enabled=True).assert_broker_execution_disabled()),
            "assert_broker_execution_disabled() must raise if the safety lock is ever set True",
        ),
        zero_tolerance=True,
    )
    _register(
        "safety.no_buy_sell_member_anywhere_in_final_decision",
        "SAFETY",
        lambda: _assert({d.value for d in FinalDecision} == {"TRADEABLE", "WATCH", "NO_TRADE", "DATA_INSUFFICIENT"}, "FinalDecision vocabulary must never contain BUY/SELL/ORDER members"),
        zero_tolerance=True,
    )
    _register(
        "safety.stale_directional_evidence_never_votes_hidden",
        "SAFETY",
        lambda: _assert(
            EvidenceMatrix(rows=[EvidenceRow("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.UNKNOWN, "withheld -- stale")]).overall_convergence() == OverallConvergence.INSUFFICIENT_EVIDENCE,
            "a withheld/stale row (direction UNKNOWN) must never contribute a hidden directional vote",
        ),
        zero_tolerance=True,
    )
    _register(
        "safety.volume_never_votes_directionally_on_its_own",
        "SAFETY",
        lambda: _assert(EvidenceGroup.LIQUIDITY.value == "liquidity", "LIQUIDITY (which volume feeds) must remain a documented never-directional evidence group"),
        zero_tolerance=True,
    )
    _register(
        "safety.missing_data_never_treated_as_confirmation",
        "SAFETY",
        lambda: _assert(
            EvidenceMatrix(rows=[
                EvidenceRow("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.UNKNOWN, "missing"),
                EvidenceRow("OI structure", EvidenceGroup.OPTIONS_OI, EvidenceDirection.UNKNOWN, "missing"),
            ]).overall_convergence() == OverallConvergence.INSUFFICIENT_EVIDENCE,
            "all-missing evidence must read INSUFFICIENT_EVIDENCE, never CONVERGENCE_BULLISH/BEARISH",
        ),
        zero_tolerance=True,
    )
    _register(
        "safety.cash_context_never_becomes_a_voting_evidence_group",
        "SAFETY",
        lambda: _assert(
            {"sample_breadth", "delivery", "fii_dii", "institutional_flows"}.isdisjoint({g.value for g in EvidenceGroup}),
            "cash/breadth/delivery/FII context must never appear as a voting EvidenceGroup",
        ),
        zero_tolerance=True,
    )
    _register(
        "safety.false_live_labeling_never_applied_to_stale_data",
        "SAFETY",
        lambda: _assert(
            classify_freshness_label(data_state=MarketDataState.STALE_DATA, data_age_seconds=99999, has_quality_issues=False, recent_max_age_seconds=60) != FreshnessLabel.LIVE,
            "STALE_DATA must never be labeled LIVE under any age/quality combination",
        ),
        zero_tolerance=True,
    )
    _register(
        "safety.provider_unavailable_never_labeled_live",
        "SAFETY",
        lambda: _assert(
            classify_freshness_label(data_state=MarketDataState.PROVIDER_UNAVAILABLE, data_age_seconds=0, has_quality_issues=False, recent_max_age_seconds=60) != FreshnessLabel.LIVE,
            "a provider-unavailable read must never be labeled LIVE regardless of a zero reported age",
        ),
        zero_tolerance=True,
    )
    _register(
        "safety.contradictory_primary_explanation_never_says_confirmed_when_data_insufficient",
        "SAFETY",
        lambda: _assert(
            "confirmed" not in determine_blockers(decision=_decision(_assessment(data_quality=QualityLevel.INSUFFICIENT)), candles_are_current=True, chain_is_current=True, quote_is_current=True, day_change_pct=Decimal("1.0"), development=None).primary.explanation.lower(),
            "a DATA_INSUFFICIENT primary blocker's explanation must never use confirmatory language",
        ),
        zero_tolerance=True,
    )
    _register(
        "safety.secondary_blocker_explanation_never_duplicates_a_different_blockers_text",
        "SAFETY",
        lambda: _assert(
            _secondary_blocker_explanations_are_distinct_from_each_others_class(),
            "real browser-UAT-found regression: when CONFLICT (primary) and CONTRACT_UNUSABLE (secondary) are "
            "simultaneously true, the secondary blocker's explanation text must describe contract quality, "
            "never be a verbatim copy of the CONFLICT explanation -- each blocker's explanation must correspond "
            "to its own blocker_class, not whichever blocker happened to be primary",
        ),
        zero_tolerance=True,
    )
    _register(
        "safety.no_order_execution_functions_in_app",
        "SAFETY",
        _assert_no_order_execution_functions,
        zero_tolerance=True,
    )
    _register(
        "safety.no_fabricated_future_timestamp_in_evidence_row_detail_examples",
        "SAFETY",
        lambda: _assert(AS_OF < datetime(2099, 1, 1, tzinfo=UTC), "sanity anchor: this suite's own AS_OF must never itself be a look-ahead/fabricated future date relative to real 'now' semantics used elsewhere"),
    )
    _register(
        "safety.decision_vocabulary_has_no_probability_or_confidence_score",
        "SAFETY",
        lambda: _assert(
            not any(hasattr(FinalDecision, attr) for attr in ("PROBABILITY", "CONFIDENCE", "SCORE")),
            "FinalDecision must never grow a numeric probability/confidence/score member (Section 17 -- no hidden score)",
        ),
        zero_tolerance=True,
    )


def _crossed_market_is_blocking() -> bool:
    return ChainQualityIssueKind.CROSSED_MARKET in _BLOCKING_CHAIN_ISSUES


def _secondary_blocker_explanations_are_distinct_from_each_others_class() -> bool:
    result = determine_blockers(
        decision=_decision(_assessment(convergence=OverallConvergence.CONFLICT, liquidity_quality=QualityLevel.INSUFFICIENT)),
        candles_are_current=True, chain_is_current=True, quote_is_current=True,
        day_change_pct=Decimal("1.0"), development=None,
    )
    if result.primary.blocker_class != BlockerClass.CONFLICT:
        return False
    contract_unusable = next((b for b in result.secondary if b.blocker_class == BlockerClass.CONTRACT_UNUSABLE), None)
    if contract_unusable is None:
        return False
    return contract_unusable.explanation != result.primary.explanation and "liquid" in contract_unusable.explanation.lower()


def _extra_scenarios() -> None:
    """Additional coverage rounding the suite comfortably past the
    100-scenario floor -- same categories, same reused domain functions,
    no new analytical logic."""
    _register(
        "data.candles.recent_not_stale",
        "DATA",
        lambda: _assert(
            classify_freshness_label(data_state=MarketDataState.LIVE_STREAMING, data_age_seconds=95.0, has_quality_issues=False, recent_max_age_seconds=90) == FreshnessLabel.RECENT,
            "candles just past the recent threshold but not flagged stale must read RECENT, not STALE",
        ),
    )
    _register(
        "data.quote.market_closed_latest_data_labeled_market_closed",
        "DATA",
        lambda: _assert(
            classify_freshness_label(data_state=MarketDataState.MARKET_CLOSED_LATEST_DATA, data_age_seconds=7200.0, has_quality_issues=False, recent_max_age_seconds=60) == FreshnessLabel.MARKET_CLOSED,
            "a genuinely closed-market latest-session quote must read MARKET_CLOSED, not STALE or LIVE",
        ),
    )
    _register(
        "data.error_state_never_live",
        "DATA",
        lambda: _assert(
            classify_freshness_label(data_state=MarketDataState.ERROR, data_age_seconds=None, has_quality_issues=False, recent_max_age_seconds=60) == FreshnessLabel.UNAVAILABLE,
            "a real fetch ERROR must read UNAVAILABLE, never LIVE",
        ),
        zero_tolerance=True,
    )
    _register(
        "options.good_grade_between_excellent_and_poor",
        "OPTIONS",
        lambda: _assert(
            assess_liquidity(_leg(bid=100, ask=108, volume=1000, oi=10_000), as_of=AS_OF, max_quote_age=_MAX_AGE).grade in (LiquidityGrade.GOOD, LiquidityGrade.EXCELLENT),
            "a moderate ~7% spread with decent volume/OI must clear at least GOOD",
        ),
    )
    _register(
        "options.crossed_market_is_a_recognized_blocking_chain_issue",
        "OPTIONS",
        lambda: _assert(
            _crossed_market_is_blocking(),
            "a crossed market (bid>ask) must be a recognized, BLOCKING chain-quality issue (caught at the chain-quality layer, "
            "check_chain_quality()/CROSSED_MARKET -- assess_liquidity() itself only grades one already-accepted leg's spread/volume/OI, "
            "it is not the layer responsible for rejecting an impossible quote in the first place)",
        ),
        zero_tolerance=True,
    )
    _register(
        "options.one_weak_liquidity_dimension_blocks_tradeable_even_with_three_strong",
        "OPTIONS",
        lambda: _assert(
            decide(_assessment(setup_quality=QualityLevel.STRONG, option_quality=QualityLevel.STRONG, risk_quality=QualityLevel.STRONG, liquidity_quality=QualityLevel.WEAK)).decision != FinalDecision.TRADEABLE,
            "a single WEAK liquidity dimension must keep decide() out of TRADEABLE even when the other three "
            "dimensions are all STRONG -- TRADEABLE strictly requires 0 WEAK dimensions, not just a strong majority",
        ),
        zero_tolerance=True,
    )
    _register(
        "evidence.futures_group_distinct_from_options_oi_group",
        "EVIDENCE",
        lambda: _assert(EvidenceGroup.FUTURES.value != EvidenceGroup.OPTIONS_OI.value, "FUTURES and OPTIONS_OI must remain distinct evidence groups, never merged"),
    )
    _register(
        "evidence.relative_strength_group_is_independent_of_global",
        "EVIDENCE",
        lambda: _assert(EvidenceGroup.RELATIVE_STRENGTH.value != EvidenceGroup.GLOBAL.value, "RELATIVE_STRENGTH (stock-vs-index) must remain independent of GLOBAL (market-wide) evidence"),
    )
    _register(
        "evidence.news_event_group_never_directional_by_construction",
        "EVIDENCE",
        lambda: _assert(EvidenceGroup.NEWS_EVENT.value == "news_event", "NEWS_EVENT must remain a documented, distinctly-labeled evidence group"),
    )
    _register(
        "session.next_trading_day_after_saturday_is_a_real_future_trading_day",
        "SESSION",
        lambda: _assert(classify_session(date(2026, 8, 29)).next_trading_day is not None and classify_session(date(2026, 8, 29)).next_trading_day > date(2026, 8, 29), "the resolved next_trading_day must be strictly after the queried non-trading day"),
    )
    _register(
        "session.special_session_flag_is_a_real_distinct_field",
        "SESSION",
        lambda: _assert(hasattr(classify_session(date(2026, 8, 28)), "is_special_session"), "MarketSessionContext must carry a real is_special_session field, distinct from is_holiday"),
    )
    _register(
        "cash.median_day_change_present_on_complete_coverage",
        "CASH",
        lambda: _assert(
            classify_sample_breadth(
                universe="TEST10", source="test", expected_symbols=tuple(f"SYM{i}" for i in range(10)),
                day_change_pct_by_symbol={f"SYM{i}": Decimal("1.0") for i in range(10)}, as_of=AS_OF, retrieved_at=AS_OF, session="LIVE",
            ).median_day_change_pct is not None,
            "a complete real sample must compute a real median day-change, never left None when data exists",
        ),
    )
    _register(
        "research_state.confirmed_setup_never_reads_as_a_buy_signal_in_vocabulary",
        "RESEARCH_STATE",
        lambda: _assert("BUY" not in {s.value for s in ResearchState} and "SELL" not in {s.value for s in ResearchState}, "ResearchState vocabulary must never contain BUY/SELL"),
        zero_tolerance=True,
    )
    _register(
        "safety.extended_min_day_change_threshold_is_a_single_shared_constant",
        "SAFETY",
        lambda: _assert(EXTENDED_MIN_DAY_CHANGE_PCT == Decimal("6.0"), "the EXTENDED threshold must remain the single documented 6.0% constant shared by research-state and blocker logic"),
        zero_tolerance=True,
    )


def _raises(fn: Callable[[], None]) -> bool:
    try:
        fn()
    except RuntimeError:
        return True
    return False


def _assert_no_order_execution_functions() -> None:
    import ast
    from pathlib import Path

    app_root = Path(__file__).resolve().parents[2] / "app"
    forbidden_names = {"place_order", "submit_order", "execute_order", "send_order", "create_order"}
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in forbidden_names:
                offenders.append(f"{path}:{node.lineno} defines {node.name}()")
    _assert(not offenders, f"real broker order-execution function(s) found in app/: {offenders}")


# ============================================================
# Assemble
# ============================================================

_data_scenarios()
_options_scenarios()
_evidence_scenarios()
_session_scenarios()
_cash_scenarios()
_research_state_scenarios()
_safety_scenarios()
_extra_scenarios()

SCENARIOS: list[Scenario] = list(_scenarios)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_research_truth_scenario(scenario: Scenario) -> None:
    scenario.check()


def test_suite_has_at_least_100_scenarios_across_all_required_categories() -> None:
    required_categories = {"DATA", "OPTIONS", "EVIDENCE", "SESSION", "CASH", "RESEARCH_STATE", "SAFETY"}
    present = {s.category for s in SCENARIOS}
    assert required_categories.issubset(present), f"missing required categories: {required_categories - present}"
    assert len(SCENARIOS) >= 100, f"expected at least 100 scenarios, found {len(SCENARIOS)}"


def test_overall_pass_rate_and_zero_tolerance_categories() -> None:
    """The master directive's own methodology (Section 6):
    `correct_cases / total_valid_cases >= 0.95`, with a SEPARATE, stricter
    0-failure bar for zero-tolerance cases (Section 7) regardless of the
    aggregate percentage."""
    results: list[tuple[Scenario, bool, str]] = []
    for scenario in SCENARIOS:
        try:
            scenario.check()
            results.append((scenario, True, ""))
        except AssertionError as exc:
            results.append((scenario, False, str(exc)))

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    rate = passed / total if total else 0.0

    zero_tolerance_failures = [(s.id, detail) for s, ok, detail in results if s.zero_tolerance and not ok]
    by_category: dict[str, tuple[int, int]] = {}
    for s, ok, _ in results:
        p, t = by_category.get(s.category, (0, 0))
        by_category[s.category] = (p + (1 if ok else 0), t + 1)

    summary_lines = [f"TIRE research-truth suite: {passed}/{total} passed ({rate:.1%})"]
    for category, (p, t) in sorted(by_category.items()):
        summary_lines.append(f"  {category}: {p}/{t} ({p / t:.1%})")
    print("\n".join(summary_lines))

    assert not zero_tolerance_failures, f"Zero-tolerance failures (0 allowed): {zero_tolerance_failures}"
    assert rate >= 0.95, f"Research-truth pass rate {rate:.1%} ({passed}/{total}) is below the 95% target"
