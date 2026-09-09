"""Daily Market Researcher -- pure `rank_candidates()`/gate/thesis tests,
no I/O, no real pipeline fetch. `AnalyzeResponse`/`VisualData` objects are
constructed directly (the exact same real Pydantic models the pipeline
produces) with only the fields ranking logic actually reads set to
meaningful values -- everything else defaults to an honest
None/empty/"not computed" shape, matching how a real response could
legitimately look for fields this test doesn't exercise.

This deliberately tests the RANKING/GATING/THESIS layer in isolation,
the same way `tests/unit/options/test_direction_analysis.py` tests
`classify_direction_comparison()` against a hand-built `EvidenceMatrix`
rather than a full live fetch -- real fields, fast, deterministic,
threshold-independent.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.data.providers.base import RawOHLC, RawQuote
from app.domain.audit.research_models import (
    RejectionRecord,
    ResearchCheckpointLabel,
    ResearchObservation,
    ResearchOutcomeCheckpoint,
    ResearchProgression,
)
from app.orchestration.daily_research import (
    DEFAULT_RESEARCH_UNIVERSE,
    EARLY_STAGE_MIN_CANDLES,
    ScreeningConfig,
    _already_extended_intraday,
    _coverage_result_note,
    _discovery_buckets,
    _GatedCandidate,
    _quote_metrics,
    build_research_thesis,
    categorize_rejection,
    classify_early_stage_state,
    classify_event_risk,
    compute_research_coverage,
    list_fo_eligible_equity_underlyings,
    list_fo_eligible_underlyings,
    rank_candidates,
    summarize_early_stage_distribution,
    summarize_rejections,
)
from app.orchestration.dashboard_service import AnalyzeResponse, WatchConditionView
from app.orchestration.visual_data import (
    CandlePoint,
    ContractAssessmentView,
    ContractCaseView,
    DirectionalPathsView,
    DirectionComparisonVisual,
    EvidenceRowView,
    FreshnessVisual,
    FuturesVisual,
    GlobalContextVisual,
    LevelView,
    NewsItemView,
    NewsVisual,
    OptionChainVisual,
    PriceChartData,
    SupportResistanceVisual,
    VisualData,
)
from app.persistence.jsonl_file import JsonlResearchOutcomeRepository


def _contract(
    *, strike: str, right: str, structural_quality: str = "ACCEPTABLE", liquidity_grade: str = "excellent",
    decay_verdict: str | None = "DECAY_FAVORABLE", required_move_pct: str | None = "1.0",
    contractual_be: str | None = None, ltp: str = "10.0",
) -> ContractAssessmentView:
    return ContractAssessmentView(
        strike=Decimal(strike), right=right, moneyness=None, ltp=Decimal(ltp), bid=None, ask=None, spread_pct=Decimal("1.0"),
        open_interest=100000, change_in_open_interest=1000, volume=50000, implied_volatility=Decimal("20"),
        delta=Decimal("0.5"), gamma=None, theta=Decimal("-1.0"), vega=None, liquidity_grade=liquidity_grade,
        distance_to_support_pct=None, distance_to_resistance_pct=None, decay_verdict=decay_verdict,
        decay_viability_ratio=None, required_underlying_move=None,
        required_underlying_move_pct=Decimal(required_move_pct) if required_move_pct is not None else None,
        contractual_expiry_breakeven=Decimal(contractual_be) if contractual_be is not None else None,
        expected_move_over_horizon=None, expected_move_over_horizon_pct=None, scenarios=[], scope_note=None,
        structural_quality=structural_quality, data_quality_notes=[],
    )


def _dc(
    *, preferred_direction: str | None, preferred_contract: ContractAssessmentView | None,
    ce_assessment: ContractAssessmentView | None = None, pe_assessment: ContractAssessmentView | None = None,
    verdict: str = "BULLISH_SIDE_BETTER_SUPPORTED", no_defensible_direction: bool = False,
    bullish_supporting_groups: int = 2, bearish_supporting_groups: int = 0, spot: str = "1000.0",
    ce_case: ContractCaseView | None = None, pe_case: ContractCaseView | None = None,
) -> DirectionComparisonVisual:
    return DirectionComparisonVisual(
        reference_strike=Decimal("1000"), verdict=verdict, verdict_detail="test verdict detail",
        ce_assessment=ce_assessment, pe_assessment=pe_assessment, ce_alternatives=[], pe_alternatives=[],
        ce_decay=None, pe_decay=None, ce_case=ce_case or ContractCaseView(), pe_case=pe_case or ContractCaseView(),
        ce_hedge_context="", pe_hedge_context="", paths=DirectionalPathsView(spot=Decimal(spot)),
        bullish_supporting_groups=bullish_supporting_groups, bearish_supporting_groups=bearish_supporting_groups,
        conflicting_groups=0, preferred_direction=preferred_direction, preferred_contract=preferred_contract,
        no_defensible_direction=no_defensible_direction,
    )


def _candles(n: int, *, high: str, low: str) -> list[CandlePoint]:
    """A flat run of `n` M15 candles all sharing the SAME real high/low --
    a simple, deterministic "recent swing" for extension-math tests."""
    return [
        CandlePoint(
            timestamp=datetime(2026, 8, 31, 9, 15, tzinfo=UTC), open=Decimal(low), high=Decimal(high),
            low=Decimal(low), close=Decimal(low), volume=1000,
        )
        for _ in range(n)
    ]


def _evidence_row(name: str, direction: str) -> EvidenceRowView:
    return EvidenceRowView(name=name, group="underlying_price_structure", direction=direction, detail="test")


def _candles_with_false_breakout(direction: str = "BULLISH") -> list[CandlePoint]:
    """A real, deterministic broken-then-reclaimed M15 series: 20 flat
    prior candles establishing a swing (high 1010/low 990), then 4 recent
    candles that break past the swing and fall back through it by the
    last real close -- exactly what `_false_breakout_detected()` looks
    for, built from real candle fields, not a shortcut flag."""
    prior = _candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")
    if direction == "BULLISH":
        recent = [
            CandlePoint(timestamp=datetime(2026, 8, 31, 10, 0, tzinfo=UTC), open=Decimal("1010"), high=Decimal("1035"), low=Decimal("1015"), close=Decimal("1030"), volume=1000),
            CandlePoint(timestamp=datetime(2026, 8, 31, 10, 15, tzinfo=UTC), open=Decimal("1030"), high=Decimal("1032"), low=Decimal("1010"), close=Decimal("1015"), volume=1000),
            CandlePoint(timestamp=datetime(2026, 8, 31, 10, 30, tzinfo=UTC), open=Decimal("1015"), high=Decimal("1020"), low=Decimal("1000"), close=Decimal("1008"), volume=1000),
            CandlePoint(timestamp=datetime(2026, 8, 31, 10, 45, tzinfo=UTC), open=Decimal("1008"), high=Decimal("1010"), low=Decimal("995"), close=Decimal("1005"), volume=1000),
        ]
    else:
        recent = [
            CandlePoint(timestamp=datetime(2026, 8, 31, 10, 0, tzinfo=UTC), open=Decimal("990"), high=Decimal("985"), low=Decimal("965"), close=Decimal("970"), volume=1000),
            CandlePoint(timestamp=datetime(2026, 8, 31, 10, 15, tzinfo=UTC), open=Decimal("970"), high=Decimal("990"), low=Decimal("968"), close=Decimal("985"), volume=1000),
            CandlePoint(timestamp=datetime(2026, 8, 31, 10, 30, tzinfo=UTC), open=Decimal("985"), high=Decimal("1000"), low=Decimal("980"), close=Decimal("992"), volume=1000),
            CandlePoint(timestamp=datetime(2026, 8, 31, 10, 45, tzinfo=UTC), open=Decimal("992"), high=Decimal("1005"), low=Decimal("990"), close=Decimal("995"), volume=1000),
        ]
    return prior + recent


def _visual(
    *, dc: DirectionComparisonVisual | None, market_state: str = "LIVE_SNAPSHOT",
    support: list[LevelView] | None = None, resistance: list[LevelView] | None = None,
    evidence: list[EvidenceRowView] | None = None, candles: list[CandlePoint] | None = None,
    news_items: list[NewsItemView] | None = None, global_context: GlobalContextVisual | None = None,
) -> VisualData:
    return VisualData(
        price_chart=PriceChartData(timeframe="M15", insufficient_history=False, detail="", candles=candles or []),
        option_chain=OptionChainVisual(), requested_contract=None, direction_comparison=dc,
        news=NewsVisual(items=news_items or []), evidence=evidence or [], convergence=None, adversarial=None, quality=None,
        futures=FuturesVisual(instrument_key=None, ltp=None, open_interest=None, basis_pct=None, oi_interpretation=None),
        global_context=global_context, support_resistance=SupportResistanceVisual(support=support or [], resistance=resistance or []),
        term_structure=None, freshness=FreshnessVisual(market_state=market_state, freshness_label=None, data_age_seconds=1.0, generated_at=datetime(2026, 8, 31, tzinfo=UTC)),
    )


def _response(
    symbol: str, *, decision: str | None, visual: VisualData | None, watch_next: list[WatchConditionView] | None = None,
    error: str | None = None, generated_at: datetime | None = None,
) -> AnalyzeResponse:
    return AnalyzeResponse(
        query=symbol, parsed_symbol=symbol, parsed_strike=None, parsed_right=None, parsed_expiry_hint=None,
        has_specific_contract=False, parse_warnings=[], symbol=symbol, market_state=visual.freshness.market_state if visual else None,
        decision=decision, error=error, visual=visual, watch_next=watch_next or [], latency_seconds=0.01,
        audit_id=f"audit-{symbol}", generated_at=generated_at or datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
    )


def _level(*, kind: str, strike: str, distance_pct: str) -> LevelView:
    return LevelView(kind=kind, strike=Decimal(strike), strength="moderate", stability="STABLE", distance_from_spot_pct=Decimal(distance_pct), distance_pct=Decimal(distance_pct), evidence="test", stability_detail="test")


def _news_item(published_at: datetime) -> NewsItemView:
    return NewsItemView(
        source="test", published_at=published_at, title="test headline", summary=None, url=None,
        relevance="HIGH", direction="NEUTRAL", evidence_quality="MODERATE",
    )


def _raw_quote(
    *, last_price: float, previous_close: float | None = None, ohlc_high: float | None = None,
    ohlc_low: float | None = None, average_price: float | None = None, buy_qty: float | None = None, sell_qty: float | None = None,
) -> RawQuote:
    ohlc = RawOHLC(open=last_price, high=ohlc_high, low=ohlc_low, close=last_price) if ohlc_high is not None and ohlc_low is not None else None
    return RawQuote(
        security_id="X", last_price=last_price, previous_close=previous_close, average_price=average_price, ohlc=ohlc,
        total_buy_quantity=int(buy_qty) if buy_qty is not None else None,
        total_sell_quantity=int(sell_qty) if sell_qty is not None else None,
    )


# ============================================================
# Gating
# ============================================================


def test_empty_universe_yields_no_high_conviction() -> None:
    shortlist, rejected = rank_candidates({})
    assert shortlist == []
    assert rejected == []


def test_error_response_is_rejected_with_the_real_error() -> None:
    shortlist, rejected = rank_candidates({"X": _response("X", decision=None, visual=None, error="boom")})
    assert shortlist == []
    assert rejected[0].symbol == "X"
    assert "boom" in rejected[0].reason


def test_no_defensible_direction_is_rejected() -> None:
    dc = _dc(preferred_direction=None, preferred_contract=None, no_defensible_direction=True, verdict="CONFLICTED")
    responses = {"X": _response("X", decision="NO_TRADE", visual=_visual(dc=dc))}
    shortlist, rejected = rank_candidates(responses)
    assert shortlist == []
    assert "no defensible direction" in rejected[0].reason
    assert "CONFLICTED" in rejected[0].reason


def test_poor_structural_quality_is_rejected_even_with_a_preferred_direction() -> None:
    contract = _contract(strike="1000", right="CE", structural_quality="HIGH_RISK_STRUCTURE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract)
    responses = {"X": _response("X", decision="WATCH", visual=_visual(dc=dc))}
    shortlist, rejected = rank_candidates(responses)
    assert shortlist == []
    assert "structural quality is HIGH_RISK_STRUCTURE" in rejected[0].reason


def test_stale_data_is_rejected_never_ranked() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract)
    responses = {"X": _response("X", decision="TRADEABLE", visual=_visual(dc=dc, market_state="STALE_DATA"))}
    shortlist, rejected = rank_candidates(responses)
    assert shortlist == []
    assert "stale" in rejected[0].reason.lower()


# ============================================================
# Shortlist size (Section 6 -- never a forced quota)
# ============================================================


def _healthy(symbol: str, *, decision: str = "WATCH", required_move_pct: str = "1.0") -> AnalyzeResponse:
    contract = _contract(strike="1000", right="CE", required_move_pct=required_move_pct)
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    return _response(symbol, decision=decision, visual=_visual(dc=dc))


def test_one_strong_candidate_yields_shortlist_of_one() -> None:
    shortlist, _ = rank_candidates({"A": _healthy("A")})
    assert len(shortlist) == 1
    assert shortlist[0].rank == 1


def test_exactly_two_candidates_yields_two_never_padded() -> None:
    shortlist, _ = rank_candidates({"A": _healthy("A"), "B": _healthy("B")})
    assert len(shortlist) == 2
    assert {c.rank for c in shortlist} == {1, 2}


def test_exactly_three_candidates_yields_three() -> None:
    shortlist, _ = rank_candidates({"A": _healthy("A"), "B": _healthy("B"), "C": _healthy("C")})
    assert len(shortlist) == 3


def test_more_than_three_is_capped_at_top_n_not_padded_or_all_shown() -> None:
    responses = {s: _healthy(s) for s in ["A", "B", "C", "D", "E"]}
    shortlist, rejected = rank_candidates(responses, top_n=3)
    assert len(shortlist) == 3
    # the other two are real candidates that simply didn't make the cut --
    # they must not silently vanish; the caller can see deep_analyzed_count
    # vs shortlisted_count differ. (rank_candidates itself doesn't reject
    # them -- run_daily_research's caller distinguishes "shown" vs "not
    # shown" via top_n; rejected here only holds genuinely gated symbols.)
    assert rejected == []


def test_zero_qualifying_candidates_yields_no_high_conviction() -> None:
    dc = _dc(preferred_direction=None, preferred_contract=None, no_defensible_direction=True)
    shortlist, rejected = rank_candidates({"A": _response("A", decision="NO_TRADE", visual=_visual(dc=dc))})
    assert shortlist == []
    assert len(rejected) == 1


# ============================================================
# Ranking determinism and correctness
# ============================================================


def test_tradeable_ranks_above_watch_ranks_above_no_trade() -> None:
    responses = {
        "T": _healthy("T", decision="TRADEABLE"),
        "W": _healthy("W", decision="WATCH"),
        "N": _healthy("N", decision="NO_TRADE"),
    }
    shortlist, _ = rank_candidates(responses)
    assert [c.symbol for c in shortlist] == ["T", "W", "N"]


def test_more_supporting_groups_ranks_higher_within_the_same_decision_tier() -> None:
    contract = _contract(strike="1000", right="CE")
    strong = _response("STRONG", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=contract, bullish_supporting_groups=4)))
    weak = _response("WEAK", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=contract, bullish_supporting_groups=1)))
    shortlist, _ = rank_candidates({"STRONG": strong, "WEAK": weak})
    assert [c.symbol for c in shortlist] == ["STRONG", "WEAK"]


def test_better_liquidity_ranks_higher_all_else_equal() -> None:
    good = _response("GOOD", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=_contract(strike="1000", right="CE", liquidity_grade="excellent"))))
    poor = _response("POOR", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=_contract(strike="1000", right="CE", liquidity_grade="poor"))))
    shortlist, _ = rank_candidates({"GOOD": good, "POOR": poor})
    assert [c.symbol for c in shortlist] == ["GOOD", "POOR"]


def test_lower_required_move_ranks_higher_all_else_equal() -> None:
    cheap = _response("CHEAP", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=_contract(strike="1000", right="CE", required_move_pct="0.5"))))
    expensive = _response("EXPENSIVE", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=_contract(strike="1000", right="CE", required_move_pct="5.0"))))
    shortlist, _ = rank_candidates({"CHEAP": cheap, "EXPENSIVE": expensive})
    assert [c.symbol for c in shortlist] == ["CHEAP", "EXPENSIVE"]


def test_missing_required_move_never_ranks_above_a_known_value() -> None:
    """Missing evidence must never become positive evidence."""
    known = _response("KNOWN", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=_contract(strike="1000", right="CE", required_move_pct="50.0"))))
    missing = _response("MISSING", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=_contract(strike="1000", right="CE", required_move_pct=None))))
    shortlist, _ = rank_candidates({"KNOWN": known, "MISSING": missing})
    assert [c.symbol for c in shortlist] == ["KNOWN", "MISSING"]


def test_more_headroom_before_the_opposing_level_ranks_higher() -> None:
    near = _response("NEAR", decision="WATCH", visual=_visual(
        dc=_dc(preferred_direction="BULLISH", preferred_contract=_contract(strike="1000", right="CE")),
        resistance=[_level(kind="resistance", strike="1010", distance_pct="1.0")],
    ))
    far = _response("FAR", decision="WATCH", visual=_visual(
        dc=_dc(preferred_direction="BULLISH", preferred_contract=_contract(strike="1000", right="CE")),
        resistance=[_level(kind="resistance", strike="1100", distance_pct="10.0")],
    ))
    shortlist, _ = rank_candidates({"NEAR": near, "FAR": far})
    assert [c.symbol for c in shortlist] == ["FAR", "NEAR"]


def test_ce_stronger_selects_ce_pe_stronger_selects_pe() -> None:
    ce_stronger = _response("CE_WINS", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=_contract(strike="1000", right="CE"), verdict="BULLISH_SIDE_BETTER_SUPPORTED")))
    pe_stronger = _response("PE_WINS", decision="WATCH", visual=_visual(dc=_dc(preferred_direction="BEARISH", preferred_contract=_contract(strike="1000", right="PE"), verdict="BEARISH_SIDE_BETTER_SUPPORTED")))
    shortlist, _ = rank_candidates({"CE_WINS": ce_stronger, "PE_WINS": pe_stronger})
    by_symbol = {c.symbol: c for c in shortlist}
    assert by_symbol["CE_WINS"].contract.right == "CE"
    assert by_symbol["PE_WINS"].contract.right == "PE"


def test_ranking_is_deterministic_across_repeated_calls() -> None:
    # E=1%, C=2%, A=3%, D=4%, B=5% required move -- ascending (lower is
    # better) order is E, C, A, D, B; only the top 3 (default `top_n`)
    # are returned.
    responses = {s: _healthy(s, required_move_pct=str(i)) for i, s in enumerate(["E", "C", "A", "D", "B"], start=1)}
    first, _ = rank_candidates(responses)
    second, _ = rank_candidates(responses)
    assert [c.symbol for c in first] == [c.symbol for c in second]
    assert [c.symbol for c in first] == ["E", "C", "A"]


# ============================================================
# The researcher never overrides the decision engine
# ============================================================


def test_shortlisted_actionability_always_equals_the_real_decision_never_upgraded() -> None:
    contract = _contract(strike="1000", right="CE")
    responses = {"X": _response("X", decision="NO_TRADE", visual=_visual(dc=_dc(preferred_direction="BULLISH", preferred_contract=contract, bullish_supporting_groups=5)))}
    shortlist, _ = rank_candidates(responses)
    assert len(shortlist) == 1
    thesis = build_research_thesis(shortlist[0])
    assert thesis.actionability == "NO_TRADE"  # never upgraded to WATCH/TRADEABLE by a high rank


# ============================================================
# Thesis synthesis -- every field traceable to a real input
# ============================================================


def test_thesis_carries_confirming_and_opposing_evidence_verbatim() -> None:
    contract = _contract(strike="1000", right="CE")
    case = ContractCaseView(could_work=["VWAP: price above session VWAP"], could_fail=["PCR(OI)=0.42 <= 0.7 -- unvalidated"])
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_case=case)
    watch = [WatchConditionView(watch="Underlying remains above the nearest support candidate at 950.0", why="w", current="c", status="HOLDS")]
    response = _response("X", decision="WATCH", visual=_visual(dc=dc), watch_next=watch)
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.confirming_evidence == ["VWAP: price above session VWAP"]
    assert thesis.opposing_evidence == ["PCR(OI)=0.42 <= 0.7 -- unvalidated"]
    assert thesis.what_would_confirm is not None and "remains above the nearest support" in thesis.what_would_confirm
    assert thesis.risk == "PCR(OI)=0.42 <= 0.7 -- unvalidated"


def test_thesis_never_invents_a_watch_condition_when_none_matches() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc), watch_next=[])
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.what_would_confirm is None


# ============================================================
# Universe enumeration
# ============================================================


def test_default_universe_is_the_small_curated_set() -> None:
    assert set(DEFAULT_RESEARCH_UNIVERSE) == {"GAIL", "RELIANCE", "NIFTY", "BANKNIFTY", "UNOMINDA", "INDIGO"}


def test_list_fo_eligible_underlyings_reads_only_real_nse_fo_rows() -> None:
    master: list[dict[str, object]] = [
        {"segment": "NSE_FO", "underlying_symbol": "RELIANCE"},
        {"segment": "NSE_FO", "underlying_symbol": "RELIANCE"},  # duplicate leg -- de-duplicated
        {"segment": "NSE_FO", "underlying_symbol": "TCS"},
        {"segment": "NSE_EQ", "underlying_symbol": "WIPRO"},  # not F&O -- excluded
        {"segment": "NSE_INDEX", "name": "Nifty 50"},  # no underlying_symbol -- excluded
    ]
    assert list_fo_eligible_underlyings(master) == ["RELIANCE", "TCS"]


def test_list_fo_eligible_equity_underlyings_excludes_real_indices() -> None:
    """Objective 1 -- an underlying is an index only via a real
    cross-reference against a real NSE_INDEX row's `trading_symbol`
    (the exact field `resolve_symbol()` matches index queries against),
    never a hardcoded index-name list."""
    master: list[dict[str, object]] = [
        {"segment": "NSE_FO", "underlying_symbol": "RELIANCE"},
        {"segment": "NSE_FO", "underlying_symbol": "NIFTY"},
        {"segment": "NSE_FO", "underlying_symbol": "BANKNIFTY"},
        {"segment": "NSE_INDEX", "trading_symbol": "NIFTY", "name": "Nifty 50"},
        {"segment": "NSE_INDEX", "trading_symbol": "BANKNIFTY", "name": "Nifty Bank"},
        {"segment": "NSE_EQ", "trading_symbol": "RELIANCE"},
    ]
    assert list_fo_eligible_equity_underlyings(master) == ["RELIANCE"]


# ============================================================
# Both-side (bullish/bearish) research thesis -- never CE-biased
# ============================================================


def test_both_side_cases_are_populated_regardless_of_which_side_is_preferred() -> None:
    ce = _contract(strike="1000", right="CE", ltp="15.0")
    pe = _contract(strike="1000", right="PE", ltp="12.0")
    ce_case = ContractCaseView(could_work=[], could_fail=["bear evidence"])
    pe_case = ContractCaseView(could_work=["bull evidence for PE... no, bear thesis support"], could_fail=[])
    dc = _dc(
        preferred_direction="BEARISH", preferred_contract=pe, verdict="BEARISH_SIDE_BETTER_SUPPORTED",
        ce_assessment=ce, pe_assessment=pe, ce_case=ce_case, pe_case=pe_case,
        bullish_supporting_groups=0, bearish_supporting_groups=2,
    )
    response = _response("X", decision="WATCH", visual=_visual(dc=dc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])

    # The BULLISH case must still be reported, even though it lost.
    assert thesis.bullish_case.contract_right == "CE"
    assert thesis.bullish_case.preferred is False
    assert thesis.bullish_case.supporting_groups == 0
    assert thesis.bullish_case.could_fail == ["bear evidence"]

    assert thesis.bearish_case.contract_right == "PE"
    assert thesis.bearish_case.preferred is True
    assert thesis.bearish_case.supporting_groups == 2


def test_ce_stronger_case_also_reports_the_losing_bearish_case() -> None:
    """The mirror of the test above -- proves the researcher is not
    CE-biased in either direction: whichever side loses is still shown,
    never silently dropped."""
    ce = _contract(strike="1000", right="CE")
    pe = _contract(strike="1000", right="PE")
    dc = _dc(
        preferred_direction="BULLISH", preferred_contract=ce, verdict="BULLISH_SIDE_BETTER_SUPPORTED",
        ce_assessment=ce, pe_assessment=pe, bullish_supporting_groups=3, bearish_supporting_groups=0,
    )
    response = _response("X", decision="WATCH", visual=_visual(dc=dc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.bullish_case.preferred is True
    assert thesis.bearish_case.preferred is False
    assert thesis.bearish_case.contract_right == "PE"  # still reported, not omitted


# ============================================================
# Research Confidence -- a tier, never a numeric score
# ============================================================


def test_research_confidence_very_strong_requires_tradeable_and_strong_evidence() -> None:
    contract = _contract(strike="1000", right="CE", liquidity_grade="excellent", decay_verdict="DECAY_FAVORABLE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, bullish_supporting_groups=4)
    response = _response("X", decision="TRADEABLE", visual=_visual(dc=dc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.research_confidence == "VERY STRONG"


def test_research_confidence_strong_for_watch_with_good_liquidity() -> None:
    contract = _contract(strike="1000", right="CE", liquidity_grade="good", decay_verdict="DECAY_ACCEPTABLE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, bullish_supporting_groups=2)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.research_confidence == "STRONG"


def test_research_confidence_weak_for_poor_liquidity() -> None:
    contract = _contract(strike="1000", right="CE", liquidity_grade="poor")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="NO_TRADE", visual=_visual(dc=dc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.research_confidence == "WEAK"


def test_research_confidence_is_never_a_final_decision_override() -> None:
    """A VERY STRONG research confidence must never imply the decision
    engine's own NO_TRADE was upgraded."""
    contract = _contract(strike="1000", right="CE", liquidity_grade="excellent", decay_verdict="DECAY_FAVORABLE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, bullish_supporting_groups=5)
    response = _response("X", decision="NO_TRADE", visual=_visual(dc=dc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.actionability == "NO_TRADE"
    # confidence can still be high (a genuinely strong SETUP that the
    # stricter decision engine hasn't confirmed yet) -- this is the
    # exact distinction Objective 8 requires, not a contradiction.


def test_no_trade_decisive_candidate_reaches_the_shortlist() -> None:
    """Objective 8 -- `_gate()` never inspects `response.decision`: a
    decisive-but-NO_TRADE candidate with ACCEPTABLE contract quality
    reaches the shortlist (ranked lower via `_sort_key`'s decision tier,
    never excluded)."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="NO_TRADE", visual=_visual(dc=dc))
    shortlist, rejected = rank_candidates({"X": response})
    assert len(shortlist) == 1
    assert shortlist[0].symbol == "X"
    assert not any(r.symbol == "X" for r in rejected)


def test_research_confidence_capped_at_moderate_when_no_longer_early_stage() -> None:
    """A setup that already broke well past recent structure (EXTENDED)
    can never read VERY STRONG/STRONG, however strong its liquidity/decay/
    evidence-count otherwise look -- the maturity-capping rule."""
    contract = _contract(strike="1000", right="CE", liquidity_grade="excellent", decay_verdict="DECAY_FAVORABLE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, bullish_supporting_groups=4, spot="1075.0")
    response = _response("X", decision="TRADEABLE", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.early_stage_state == "EXTENDED"
    assert thesis.research_confidence == "MODERATE"


def test_research_confidence_capped_on_false_breakout_risk() -> None:
    contract = _contract(strike="1000", right="CE", liquidity_grade="excellent", decay_verdict="DECAY_FAVORABLE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, bullish_supporting_groups=4, spot="1005.0")
    response = _response("X", decision="TRADEABLE", visual=_visual(dc=dc, candles=_candles_with_false_breakout("BULLISH")))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.early_stage_state == "FALSE_BREAKOUT_RISK"
    assert thesis.research_confidence == "MODERATE"


# ============================================================
# Rejection-reason tally (Objective 7)
# ============================================================


def test_categorize_rejection_buckets_real_reason_strings() -> None:
    assert categorize_rejection("no defensible direction (verdict: CONFLICTED)") == "NO_DIRECTIONAL_CONVERGENCE"
    assert categorize_rejection("contract structural quality is HIGH_RISK_STRUCTURE, not ACCEPTABLE") == "POOR_CONTRACT_QUALITY"
    assert categorize_rejection("stale/unavailable data (STALE_DATA)") == "STALE_DATA"
    assert categorize_rejection("screened out at stage 1 (not among the top 30...)").startswith("STAGE_1_SCREENED_OUT")
    assert categorize_rejection("something genuinely novel") == "OTHER"


def test_summarize_rejections_tallies_real_counts_not_invented_ones() -> None:
    rejected = [
        RejectionRecord(symbol="A", reason="no defensible direction (verdict: CONFLICTED)"),
        RejectionRecord(symbol="B", reason="no defensible direction (verdict: BOTH_SIDES_WEAK)"),
        RejectionRecord(symbol="C", reason="contract structural quality is HIGH_RISK_STRUCTURE, not ACCEPTABLE"),
    ]
    summary = summarize_rejections(rejected)
    assert summary["NO_DIRECTIONAL_CONVERGENCE"] == 2
    assert summary["POOR_CONTRACT_QUALITY"] == 1
    assert sum(summary.values()) == len(rejected)


# ============================================================
# Stage 1 -- early-stage discovery (real quote-derived metrics, buckets,
# and the "already extended intraday" hard exclusion). Pure, no fetch.
# ============================================================

_CFG = ScreeningConfig()


def test_quote_metrics_never_fabricates_a_value_from_missing_fields() -> None:
    m = _quote_metrics(_raw_quote(last_price=100.0))  # no previous_close/ohlc/average_price/buy-sell
    assert m.day_change_pct is None
    assert m.vwap_distance_pct is None
    assert m.position_in_day_range is None
    assert m.buy_sell_ratio is None


def test_already_extended_intraday_requires_both_large_move_and_pinned_position() -> None:
    # Large move but NOT pinned at the extreme -- not extended under this rule.
    m = _quote_metrics(_raw_quote(last_price=107.0, previous_close=100.0, ohlc_high=115.0, ohlc_low=90.0))
    assert _already_extended_intraday(m, _CFG) is False
    # Large move AND pinned near today's high -- extended.
    m2 = _quote_metrics(_raw_quote(last_price=110.0, previous_close=100.0, ohlc_high=110.5, ohlc_low=99.0))
    assert _already_extended_intraday(m2, _CFG) is True
    # Symmetric: large down move pinned near today's low.
    m3 = _quote_metrics(_raw_quote(last_price=90.0, previous_close=100.0, ohlc_high=101.0, ohlc_low=89.5))
    assert _already_extended_intraday(m3, _CFG) is True


def test_unpinned_eight_percent_session_move_is_already_late_for_stage_one() -> None:
    m = _quote_metrics(_raw_quote(last_price=108.0, previous_close=100.0, ohlc_high=115.0, ohlc_low=90.0))
    assert _already_extended_intraday(m, _CFG) is True


def test_high_day_change_alone_is_not_a_discovery_bucket_match() -> None:
    """Objective 4 -- a large move that is NOT extended-pinned and falls
    outside the momentum band, with no real participation/reversal
    signal, matches NO bucket -- high day-change alone is not enough."""
    m = _quote_metrics(_raw_quote(last_price=107.0, previous_close=100.0, ohlc_high=112.0, ohlc_low=99.0, average_price=104.0))
    assert _discovery_buckets(m, _CFG) == []
    assert _already_extended_intraday(m, _CFG) is False


def test_low_day_change_can_still_be_a_stage_one_candidate_via_participation() -> None:
    """Objective 3 -- a real order-flow imbalance qualifies a candidate
    even with a tiny day-change (no momentum-band requirement)."""
    m = _quote_metrics(_raw_quote(last_price=100.1, previous_close=100.0, buy_qty=1_300_000, sell_qty=700_000))
    assert "ORDER_FLOW_PARTICIPATION" in _discovery_buckets(m, _CFG)


def test_early_reversal_bucket_is_symmetric_both_directions() -> None:
    # Red on the day but well off today's low -- recovering.
    m = _quote_metrics(_raw_quote(last_price=99.0, previous_close=100.0, ohlc_high=99.5, ohlc_low=95.0))
    assert "EARLY_REVERSAL" in _discovery_buckets(m, _CFG)
    # Green on the day but well off today's high -- fading.
    m2 = _quote_metrics(_raw_quote(last_price=101.0, previous_close=100.0, ohlc_high=105.0, ohlc_low=100.5))
    assert "EARLY_REVERSAL" in _discovery_buckets(m2, _CFG)


def test_missing_ohlc_or_average_price_cannot_fabricate_a_bucket_match() -> None:
    """Objective 5 -- missing data must never produce a false developing
    signal. A real, moderate day-change with NO real `ohlc`/
    `average_price`/buy-sell data matches nothing (no bucket can be
    evaluated without the real fields it needs)."""
    m = _quote_metrics(_raw_quote(last_price=101.5, previous_close=100.0))
    assert _discovery_buckets(m, _CFG) == []


# ============================================================
# Early-stage state classifier (Stage 2) -- deterministic, no numeric blend.
# ============================================================


def test_early_stage_insufficient_data_on_short_candle_history() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES - 1, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    state, ext = classify_early_stage_state(candidate)
    assert state == "INSUFFICIENT_DATA"
    assert ext is None


def test_early_stage_breakout_confirmation_when_moderately_past_the_swing() -> None:
    """A moderate real breakout (~3.96%, between the 2.0% not-yet-broken-out
    ceiling and the 5.0% breakout-hold-max) with the latest candle holding
    beyond it -- BREAKOUT_CONFIRMATION, not yet EXTENDED."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1050.0")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    state, ext = classify_early_stage_state(candidate)
    assert state == "BREAKOUT_CONFIRMATION"
    assert ext is not None and Decimal("2.0") < ext <= Decimal("5.0")


def test_early_stage_extended_beyond_the_breakout_hold_range() -> None:
    """A larger real move (~6.44%, past the 5.0% breakout-hold-max but
    below the 8.0% exhaustion threshold) -- EXTENDED."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1075.0")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    state, ext = classify_early_stage_state(candidate)
    assert state == "EXTENDED"
    assert ext is not None and Decimal("5.0") < ext <= Decimal("8.0")


def test_early_stage_exhaustion_risk_on_a_severe_real_margin() -> None:
    """A severe real margin (~13.86%, beyond the 8.0% exhaustion
    threshold) -- EXHAUSTION_RISK, a distinct severity tier from EXTENDED,
    never a new signal."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1150.0")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    state, ext = classify_early_stage_state(candidate)
    assert state == "EXHAUSTION_RISK"
    assert ext is not None and ext > Decimal("8.0")


def test_early_stage_false_breakout_risk_bullish_on_a_real_broken_then_reclaimed_series() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1005.0")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles_with_false_breakout("BULLISH")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    state, ext = classify_early_stage_state(candidate)
    assert state == "FALSE_BREAKOUT_RISK"
    assert ext is None


def test_early_stage_false_breakout_risk_bearish_on_a_real_broken_then_reclaimed_series() -> None:
    contract = _contract(strike="1000", right="PE")
    dc = _dc(preferred_direction="BEARISH", preferred_contract=contract, pe_assessment=contract, spot="995.0", verdict="BEARISH_SIDE_BETTER_SUPPORTED")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles_with_false_breakout("BEARISH")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BEARISH")
    state, ext = classify_early_stage_state(candidate)
    assert state == "FALSE_BREAKOUT_RISK"
    assert ext is None


def test_early_stage_developing_momentum_when_regime_trend_and_vwap_all_align() -> None:
    """Real regime already TRENDING_BULLISH (via the 'Market regime'
    evidence row) AND both M15 trend and VWAP already read bullish, with
    price still within recent structure -- DEVELOPING_MOMENTUM."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1000.0")
    evidence = [
        _evidence_row("M15 trend", "BULLISH"), _evidence_row("VWAP", "BULLISH"),
        EvidenceRowView(name="Market regime", group="underlying_price_structure", direction="BULLISH", detail="TRENDING_BULLISH: real ATR/EMA/VWAP context"),
    ]
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, evidence=evidence, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    state, _ = classify_early_stage_state(candidate)
    assert state == "DEVELOPING_MOMENTUM"


def test_early_stage_early_directional_build_when_only_one_of_trend_or_vwap_aligns() -> None:
    """This is the 'quiet before move' state -- one of trend/VWAP already
    confirms, the rest of the picture is still forming, price has not
    broken past recent structure."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1000.0")
    evidence = [_evidence_row("M15 trend", "NEUTRAL"), _evidence_row("VWAP", "BULLISH")]
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, evidence=evidence, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    state, _ = classify_early_stage_state(candidate)
    assert state == "EARLY_DIRECTIONAL_BUILD"


def test_early_stage_early_directional_build_bearish_direction_too() -> None:
    contract = _contract(strike="1000", right="PE")
    dc = _dc(preferred_direction="BEARISH", preferred_contract=contract, pe_assessment=contract, spot="1000.0", verdict="BEARISH_SIDE_BETTER_SUPPORTED")
    evidence = [_evidence_row("M15 trend", "BEARISH"), _evidence_row("VWAP", "NEUTRAL")]
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, evidence=evidence, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BEARISH")
    state, _ = classify_early_stage_state(candidate)
    assert state == "EARLY_DIRECTIONAL_BUILD"


def test_early_stage_range_bound_when_neither_trend_nor_vwap_confirm() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1000.0")
    evidence = [_evidence_row("M15 trend", "NEUTRAL"), _evidence_row("VWAP", "NEUTRAL")]
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, evidence=evidence, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    state, _ = classify_early_stage_state(candidate)
    assert state == "RANGE_BOUND"


def test_early_stage_is_deterministic() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1000.0")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    first = classify_early_stage_state(candidate)
    second = classify_early_stage_state(candidate)
    assert first == second


def test_early_stage_reachable_for_both_bullish_and_bearish_thesis() -> None:
    ce = _contract(strike="1000", right="CE")
    dc_bull = _dc(preferred_direction="BULLISH", preferred_contract=ce, ce_assessment=ce, spot="1000.0")
    response_bull = _response("X", decision="WATCH", visual=_visual(dc=dc_bull, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    bull_candidate = _GatedCandidate(symbol="X", response=response_bull, dc=dc_bull, contract=ce, direction="BULLISH")
    bull_state, _ = classify_early_stage_state(bull_candidate)
    assert bull_state != "INSUFFICIENT_DATA"

    pe = _contract(strike="1000", right="PE")
    dc_bear = _dc(preferred_direction="BEARISH", preferred_contract=pe, pe_assessment=pe, spot="1000.0", verdict="BEARISH_SIDE_BETTER_SUPPORTED")
    response_bear = _response("Y", decision="WATCH", visual=_visual(dc=dc_bear, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    bear_candidate = _GatedCandidate(symbol="Y", response=response_bear, dc=dc_bear, contract=pe, direction="BEARISH")
    bear_state, _ = classify_early_stage_state(bear_candidate)
    assert bear_state != "INSUFFICIENT_DATA"


def test_early_stage_state_never_overrides_direction_comparison() -> None:
    """Stage 1/the early-stage classifier never touch `direction_comparison`
    -- the SAME `dc.verdict`/`preferred_direction` this candidate started
    with are unchanged after classification."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1000.0", verdict="BULLISH_SIDE_BETTER_SUPPORTED")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    classify_early_stage_state(candidate)
    assert candidate.dc.verdict == "BULLISH_SIDE_BETTER_SUPPORTED"
    assert candidate.dc.preferred_direction == "BULLISH"


def test_already_extended_rejection_language_is_factual_not_predictive() -> None:
    """Objective -- 'already extended' language must never claim a
    future outcome."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1050.0")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    candidate = _GatedCandidate(symbol="X", response=response, dc=dc, contract=contract, direction="BULLISH")
    state, ext = classify_early_stage_state(candidate)
    from app.orchestration.daily_research import _early_stage_narrative
    _, _, why_not_extended = _early_stage_narrative(state, "BULLISH", ext, None, None)
    forbidden = ["will rise", "will fall", "will boom", "guaranteed", "certain to"]
    assert not any(f in why_not_extended.lower() for f in forbidden)


# ============================================================
# Final Hardening Pass, Phase 19 -- quiet-setup-vs-chasing audit.
# ============================================================


def test_early_stage_distribution_counts_across_a_real_shortlist() -> None:
    """Two candidates with no real candle history both classify
    INSUFFICIENT_DATA -- the aggregation counts them, never fabricates a
    developing/extended read from missing history."""
    shortlist, _ = rank_candidates({"A": _healthy("A"), "B": _healthy("B")})
    dist = summarize_early_stage_distribution(shortlist)
    assert dist.state_counts == {"INSUFFICIENT_DATA": 2}
    assert dist.early_or_developing_count == 0
    assert dist.already_extended_or_later_count == 0


def test_early_stage_distribution_separates_developing_from_already_extended() -> None:
    """The concrete Phase 19 proof: a genuinely EARLY_DIRECTIONAL_BUILD
    candidate and a genuinely BREAKOUT_CONFIRMATION (already-moved)
    candidate in the SAME shortlist are correctly split, not blended into
    one number."""
    early_contract = _contract(strike="1000", right="CE")
    early_dc = _dc(preferred_direction="BULLISH", preferred_contract=early_contract, ce_assessment=early_contract, spot="1000.0")
    early_evidence = [_evidence_row("M15 trend", "NEUTRAL"), _evidence_row("VWAP", "BULLISH")]
    early_response = _response(
        "EARLY", decision="WATCH",
        visual=_visual(dc=early_dc, evidence=early_evidence, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")),
    )

    extended_contract = _contract(strike="1000", right="CE")
    extended_dc = _dc(preferred_direction="BULLISH", preferred_contract=extended_contract, ce_assessment=extended_contract, spot="1050.0")
    extended_response = _response(
        "EXTENDED", decision="WATCH",
        visual=_visual(dc=extended_dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")),
    )

    shortlist, _ = rank_candidates({"EARLY": early_response, "EXTENDED": extended_response})
    dist = summarize_early_stage_distribution(shortlist)
    assert dist.early_or_developing_count == 1
    assert dist.already_extended_or_later_count == 1
    assert sum(dist.state_counts.values()) == len(shortlist)


def test_early_stage_distribution_empty_shortlist() -> None:
    dist = summarize_early_stage_distribution([])
    assert dist.state_counts == {}
    assert dist.early_or_developing_count == 0
    assert dist.already_extended_or_later_count == 0


# ============================================================
# Event risk -- real, non-directional, derived from real news presence.
# ============================================================


def test_event_risk_no_known_event_risk_when_no_real_news() -> None:
    response = _response("X", decision="WATCH", visual=_visual(dc=None), generated_at=datetime(2026, 8, 31, 10, 0, tzinfo=UTC))
    risk, reason = classify_event_risk(response)
    assert risk == "NO_KNOWN_EVENT"
    assert "absence of news is not proof no event exists" in reason


def test_event_risk_major_with_multiple_real_recent_items() -> None:
    generated_at = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
    items = [_news_item(generated_at - timedelta(hours=1)), _news_item(generated_at - timedelta(hours=10))]
    response = _response("X", decision="WATCH", visual=_visual(dc=None, news_items=items), generated_at=generated_at)
    risk, reason = classify_event_risk(response)
    assert risk == "MAJOR_EVENT_RISK"
    assert "2 real news item" in reason


def test_event_risk_no_known_event_risk_when_news_items_are_old() -> None:
    generated_at = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
    items = [_news_item(generated_at - timedelta(days=10))]
    response = _response("X", decision="WATCH", visual=_visual(dc=None, news_items=items), generated_at=generated_at)
    risk, _ = classify_event_risk(response)
    assert risk == "STALE_EVENT"


def test_event_risk_news_data_unavailable_on_fetch_error() -> None:
    v = _visual(dc=None)
    v.news.fetch_error = "boom"
    response = _response("X", decision="WATCH", visual=v)
    risk, reason = classify_event_risk(response)
    assert risk == "NEWS_DATA_UNAVAILABLE"
    assert "boom" in reason


def test_sector_note_unavailable_when_visual_has_no_sector() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert "SECTOR_DATA_UNAVAILABLE" in thesis.sector_note
    assert "Sector classification unavailable" in thesis.sector_note


def test_sector_note_uses_official_industry_when_present() -> None:
    from app.orchestration.visual_data import SectorContextVisual
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    visual = _visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990"))
    visual = visual.model_copy(update={
        "sector": SectorContextVisual(
            classification="KNOWN", industry="Oil Gas & Consumable Fuels",
            source="NSE Nifty 500 constituent list", relative_strength_tier="STOCK_LEADING",
            detail="stock leading sector and market",
        ),
    })
    response = _response("ONGC", decision="WATCH", visual=visual)
    shortlist, _ = rank_candidates({"ONGC": response})
    thesis = build_research_thesis(shortlist[0])
    assert "Oil Gas & Consumable Fuels" in thesis.sector_note
    assert "SECTOR_DATA_UNAVAILABLE" not in thesis.sector_note


# ============================================================
# Market context (Objective 15) -- reported only, never a ranking input.
# ============================================================


def test_market_context_supportive_when_tailwind_matches_bullish_thesis() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    gc = GlobalContextVisual(inputs=[], verdict="GLOBAL_TAILWIND", detail="NIFTY/BANKNIFTY/VIX real context: tailwind")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, global_context=gc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.market_context == "SUPPORTIVE"
    assert thesis.market_context_detail == "NIFTY/BANKNIFTY/VIX real context: tailwind"


def test_market_context_opposing_when_headwind_opposes_bullish_thesis() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    gc = GlobalContextVisual(inputs=[], verdict="GLOBAL_HEADWIND", detail="real headwind context")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, global_context=gc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.market_context == "OPPOSING"


def test_market_context_supportive_for_bearish_thesis_under_headwind() -> None:
    contract = _contract(strike="1000", right="PE")
    dc = _dc(preferred_direction="BEARISH", preferred_contract=contract, pe_assessment=contract, verdict="BEARISH_SIDE_BETTER_SUPPORTED")
    gc = GlobalContextVisual(inputs=[], verdict="GLOBAL_HEADWIND", detail="real headwind context")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, global_context=gc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.market_context == "SUPPORTIVE"


def test_market_context_neutral_when_mixed() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    gc = GlobalContextVisual(inputs=[], verdict="MIXED", detail="real mixed context")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, global_context=gc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.market_context == "NEUTRAL"


def test_market_context_unknown_when_no_global_context_computed() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, global_context=None))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.market_context == "UNKNOWN"


def test_market_context_is_never_a_ranking_input() -> None:
    """Two otherwise-identical candidates differing ONLY in real
    `global_context.verdict` must rank identically -- market context is
    reported, never a second scoring axis on top of `dc.verdict`."""
    contract = _contract(strike="1000", right="CE")
    dc_a = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    dc_b = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    gc_supportive = GlobalContextVisual(inputs=[], verdict="GLOBAL_TAILWIND", detail="tailwind")
    gc_opposing = GlobalContextVisual(inputs=[], verdict="GLOBAL_HEADWIND", detail="headwind")
    response_a = _response("A", decision="WATCH", visual=_visual(dc=dc_a, global_context=gc_supportive))
    response_b = _response("B", decision="WATCH", visual=_visual(dc=dc_b, global_context=gc_opposing))
    shortlist, _ = rank_candidates({"A": response_a, "B": response_b})
    # Identical real rationale (both real tie-break fields equal) -- order
    # falls back to the stable-sort input order, unaffected by market context.
    assert shortlist[0].rationale == shortlist[1].rationale


# ============================================================
# Structural room to breakeven (Objective 11)
# ============================================================


def test_room_to_breakeven_within_recent_structure() -> None:
    contract = _contract(strike="1000", right="CE", contractual_be="1005")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1000.0")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.room_to_breakeven is not None
    assert "within the recent M15 structure" in thesis.room_to_breakeven


def test_room_to_breakeven_beyond_recent_structure() -> None:
    contract = _contract(strike="1000", right="CE", contractual_be="1050")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract, spot="1000.0")
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.room_to_breakeven is not None
    assert "has not recently traded through this level" in thesis.room_to_breakeven


def test_room_to_breakeven_none_when_no_breakeven_computed() -> None:
    contract = _contract(strike="1000", right="CE", contractual_be=None)
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc, candles=_candles(EARLY_STAGE_MIN_CANDLES, high="1010", low="990")))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.room_to_breakeven is None


# ============================================================
# Participation note (Objective 4) -- honest, never "accumulation".
# ============================================================


def test_participation_note_reports_real_stage_one_ratio() -> None:
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc))
    shortlist, _ = rank_candidates({"X": response}, stage_one_buy_sell_ratio={"X": Decimal("1.5")})
    thesis = build_research_thesis(shortlist[0])
    assert "1.50:1" in thesis.participation_note
    assert "accumulation" not in thesis.participation_note.lower() or "not confirmed accumulation" in thesis.participation_note.lower()


def test_participation_note_honest_when_no_stage_one_signal() -> None:
    """No Stage-1 ratio (explicit `?symbols=` override, or missing real
    buy/sell quantity data) -- an honest 'no signal' note, never a
    fabricated observation."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc))
    shortlist, _ = rank_candidates({"X": response})
    thesis = build_research_thesis(shortlist[0])
    assert thesis.participation_note == "no real Stage-1 order-flow signal available this run (either Stage 1 was skipped for an explicit symbol query, or no real buy/sell quantity data was present on the quote)."


# ============================================================
# Sprint 1, Phase 4/5/8 -- RESEARCH COVERAGE: metadata about how reliably
# a run evaluated the real universe, never a stock-quality signal, never a
# ranking input. `compute_research_coverage()` is a pure function over
# already-computed rejection lists/counts -- tested directly here, no I/O.
# ============================================================


def _rej(symbol: str, reason: str) -> RejectionRecord:
    return RejectionRecord(symbol=symbol, reason=reason)


def test_coverage_high_when_almost_everything_reliably_evaluated() -> None:
    """100/100 reliably evaluated (real evidence-based rejections/screen-
    outs only, no infrastructure failures) -- HIGH."""
    stage_one_rejected = [_rej(f"S1REJ{i}", "screened out at stage 1 (matched a real bucket...)") for i in range(50)]
    stage_two_rejected = [_rej(f"S2REJ{i}", "no defensible direction (verdict: CONFLICTED)") for i in range(50)]
    coverage = compute_research_coverage(
        universe_count=100, stage_one_attempted=100, stage_one_rejected=stage_one_rejected,
        stage_two_attempted=50, stage_two_rejected=stage_two_rejected,
    )
    assert coverage.classification == "HIGH"
    assert coverage.stage1_failed == 0
    assert coverage.stage2_failed == 0


def test_coverage_good_between_80_and_95_percent() -> None:
    """15/100 lost to real infrastructure failures (85% reliably
    evaluated) -- GOOD, not HIGH."""
    stage_one_rejected = [_rej(f"FAIL{i}", "quote batch fetch failed: timeout") for i in range(15)]
    coverage = compute_research_coverage(
        universe_count=100, stage_one_attempted=100, stage_one_rejected=stage_one_rejected,
        stage_two_attempted=0, stage_two_rejected=[],
    )
    assert coverage.classification == "GOOD"
    assert coverage.stage1_failed == 15


def test_coverage_degraded_between_50_and_80_percent() -> None:
    """35/100 lost to real infrastructure failures (65% reliably
    evaluated) -- DEGRADED."""
    stage_one_rejected = [_rej(f"FAIL{i}", "quote batch fetch failed: timeout") for i in range(35)]
    coverage = compute_research_coverage(
        universe_count=100, stage_one_attempted=100, stage_one_rejected=stage_one_rejected,
        stage_two_attempted=0, stage_two_rejected=[],
    )
    assert coverage.classification == "DEGRADED"


def test_coverage_poor_under_50_percent() -> None:
    """60/100 lost to real infrastructure failures (40% reliably
    evaluated) -- POOR."""
    stage_one_rejected = [_rej(f"FAIL{i}", "quote batch fetch failed: timeout") for i in range(60)]
    coverage = compute_research_coverage(
        universe_count=100, stage_one_attempted=100, stage_one_rejected=stage_one_rejected,
        stage_two_attempted=0, stage_two_rejected=[],
    )
    assert coverage.classification == "POOR"


def test_coverage_double_rejection_for_the_same_symbol_is_not_double_counted() -> None:
    """Robustness test: even if two `RejectionRecord`s existed for the
    same real symbol (`screen_universe()`'s own batch-failure path
    produced exactly this, a "quote batch fetch failed" record plus a
    separate "no quote data returned" record, before Sprint 3's dedup
    fix), coverage math must still count that real symbol once, not
    twice."""
    stage_one_rejected = [
        _rej("SAME", "quote batch fetch failed: timeout"),
        _rej("SAME", "no quote data returned for this symbol"),
    ]
    coverage = compute_research_coverage(
        universe_count=10, stage_one_attempted=10, stage_one_rejected=stage_one_rejected,
        stage_two_attempted=0, stage_two_rejected=[],
    )
    assert coverage.stage1_failed == 1  # one real symbol, not two rejection records


def test_coverage_structural_exclusions_do_not_count_against_reliability() -> None:
    """A symbol with no real F&O expiry / no instrument key is a fixed,
    structural fact -- re-running cannot change it, so it must not count
    as a "coverage" failure (that would wrongly suggest a retry could
    help)."""
    stage_one_rejected = [
        _rej("NOEXP", "no valid upcoming F&O expiry found"),
        _rej("NOKEY", "no NSE_EQ instrument key found for this symbol"),
    ]
    coverage = compute_research_coverage(
        universe_count=10, stage_one_attempted=10, stage_one_rejected=stage_one_rejected,
        stage_two_attempted=0, stage_two_rejected=[],
    )
    assert coverage.stage1_failed == 0
    assert coverage.classification == "HIGH"


def test_coverage_timeout_and_timestamp_quality_sub_tallies_are_real_and_distinct() -> None:
    stage_two_rejected = [
        _rej("A", "analysis error: real quote fetch failed: Upstox request to /v2/market-quote/quotes timed out"),
        _rej("B", "analysis error: unexpected failure: 1 validation error for DataFreshness\nValue error, received_timestamp must not be before data_timestamp"),
    ]
    coverage = compute_research_coverage(
        universe_count=2, stage_one_attempted=None, stage_one_rejected=[],
        stage_two_attempted=2, stage_two_rejected=stage_two_rejected,
    )
    assert coverage.timeout_failures == 1
    assert coverage.timestamp_quality_failures == 1
    assert coverage.stage2_failed == 2


def test_coverage_stage_one_fields_are_none_when_stage_one_skipped() -> None:
    """Explicit `?symbols=` override -- Stage 1 correctly skipped -- must
    read as `None`, never a fabricated zero, matching the existing
    `stage_one_survivor_count` convention."""
    coverage = compute_research_coverage(
        universe_count=3, stage_one_attempted=None, stage_one_rejected=[],
        stage_two_attempted=3, stage_two_rejected=[],
    )
    assert coverage.stage1_attempted is None
    assert coverage.stage1_successful is None
    assert coverage.stage1_failed is None


def test_coverage_note_high_and_no_high_conviction() -> None:
    coverage = compute_research_coverage(
        universe_count=10, stage_one_attempted=10, stage_one_rejected=[], stage_two_attempted=10, stage_two_rejected=[],
    )
    note = _coverage_result_note(no_high_conviction=True, coverage=coverage)
    assert "NO HIGH CONVICTION" in note
    assert "reliably screened" in note
    assert "degraded" not in note.lower()
    assert "HIGH -- 10/10 symbols reliably evaluated" in note  # Sprint 5, Objective 8 -- explicit real count


def test_coverage_note_degraded_and_no_high_conviction() -> None:
    stage_one_rejected = [_rej(f"FAIL{i}", "quote batch fetch failed: timeout") for i in range(35)]
    coverage = compute_research_coverage(
        universe_count=100, stage_one_attempted=100, stage_one_rejected=stage_one_rejected,
        stage_two_attempted=0, stage_two_rejected=[],
    )
    note = _coverage_result_note(no_high_conviction=True, coverage=coverage)
    assert "NO HIGH CONVICTION" in note
    assert "reliably evaluated universe" in note
    assert "degraded" in note.lower()
    assert "DEGRADED -- 65/100 symbols reliably evaluated" in note  # Sprint 5, Objective 8 -- explicit real count


def test_coverage_note_poor_never_claims_complete_market_research() -> None:
    """The core Phase 5 requirement: a POOR-coverage run must never be
    presented as a full market scan -- the headline changes to RESEARCH
    INCOMPLETE, regardless of `no_high_conviction`."""
    stage_one_rejected = [_rej(f"FAIL{i}", "quote batch fetch failed: timeout") for i in range(60)]
    coverage = compute_research_coverage(
        universe_count=100, stage_one_attempted=100, stage_one_rejected=stage_one_rejected,
        stage_two_attempted=0, stage_two_rejected=[],
    )
    note_empty = _coverage_result_note(no_high_conviction=True, coverage=coverage)
    note_with_candidates = _coverage_result_note(no_high_conviction=False, coverage=coverage)
    for note in (note_empty, note_with_candidates):
        assert "RESEARCH INCOMPLETE" in note
        assert "full" not in note.lower()  # never claims a complete/full scan
        assert "60" in note and "100" in note  # the real, specific counts


def test_coverage_is_never_a_ranking_input() -> None:
    """`rank_candidates()` is called, and produces its shortlist, entirely
    independently of coverage -- `compute_research_coverage()` doesn't
    even accept a `responses`/`shortlist` argument, structurally
    guaranteeing it cannot influence which candidate ranks where."""
    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc))
    shortlist_before, rejected_before = rank_candidates({"X": response})
    # Coverage computed AFTER ranking, over unrelated counts -- calling it
    # (with any real values) cannot retroactively change the shortlist
    # already produced above.
    compute_research_coverage(
        universe_count=1, stage_one_attempted=1,
        stage_one_rejected=[_rej(f"FAIL{i}", "quote batch fetch failed: timeout") for i in range(60)],
        stage_two_attempted=1, stage_two_rejected=[],
    )
    shortlist_after, rejected_after = rank_candidates({"X": response})
    assert [c.symbol for c in shortlist_before] == [c.symbol for c in shortlist_after]
    assert [r.reason for r in rejected_before] == [r.reason for r in rejected_after]


def test_ranking_is_identical_with_and_without_historical_outcome_records(tmp_path: Path) -> None:
    """Sprint 5, Objective 5 -- the single most important guardrail this
    sprint adds: historical outcome records must NEVER be able to change
    today's ranking. First, a structural proof: `rank_candidates()`
    accepts no outcome-repository parameter at all, so it is IMPOSSIBLE
    for persisted history to reach it. Second, a behavioral proof:
    seeding a real `JsonlResearchOutcomeRepository` with an adversarial
    FAILED_SETUP history for the exact symbol under test changes nothing
    about the real shortlist `rank_candidates()` produces for the exact
    same input."""
    assert "outcome_repository" not in inspect.signature(rank_candidates).parameters
    assert "outcome" not in inspect.signature(rank_candidates).parameters

    contract = _contract(strike="1000", right="CE")
    dc = _dc(preferred_direction="BULLISH", preferred_contract=contract, ce_assessment=contract)
    response = _response("X", decision="WATCH", visual=_visual(dc=dc))

    shortlist_before, rejected_before = rank_candidates({"X": response})

    repo = JsonlResearchOutcomeRepository(tmp_path / "outcomes")
    observation = ResearchObservation(
        run_id="run1", audit_id="audit1", generated_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC), symbol="X",
        direction="BULLISH", selected_right="CE", selected_strike="1000", early_stage_state="EARLY_DIRECTIONAL_BUILD",
        research_confidence="STRONG", actionability="WATCH", spot_at_observation="1000.0",
        contractual_expiry_breakeven="1020.0", nearest_level_kind="resistance", nearest_level_value="1050.0",
        market_context="NEUTRAL", participation_note="none", coverage_classification="HIGH", thesis="test thesis",
    )
    asyncio.run(repo.save_observation(observation))
    checkpoint = ResearchOutcomeCheckpoint(
        observation_id=observation.observation_id, checkpoint_label=ResearchCheckpointLabel.PLUS_1_SESSION,
        target_trading_session_date=date(2026, 8, 25), captured_at=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
        data_state="LIVE_SNAPSHOT", spot_at_checkpoint="900.0", move_pct_from_observation="-10.0",
        max_favorable_move_pct=None, max_adverse_move_pct=None, swing_level_broken=True, breakeven_reached=False,
        became_extended=False, next_observed_early_stage_state="FALSE_BREAKOUT_RISK",
        progression=ResearchProgression.FAILED_SETUP,
    )
    asyncio.run(repo.save_checkpoint(checkpoint))

    shortlist_after, rejected_after = rank_candidates({"X": response})
    assert [c.symbol for c in shortlist_before] == [c.symbol for c in shortlist_after]
    assert [c.rank for c in shortlist_before] == [c.rank for c in shortlist_after]
    assert [r.reason for r in rejected_before] == [r.reason for r in rejected_after]


# ============================================================
# Sprint 2 -- market-state-aware staleness: the pure calendar helper is
# now tested at its canonical home,
# tests/unit/market/test_trading_calendar.py (Sprint 3 promoted it out of
# this module -- see app.domain.market.trading_calendar).
# ============================================================

def test_coverage_note_is_unaffected_by_market_state_only_by_the_real_ratio() -> None:
    """Objective 4/12.J -- `compute_research_coverage()`/`_coverage_result_note()`
    take no market-state parameter at all -- a market-closed run with a
    real, high reliably-evaluated ratio reads exactly the same HIGH/GOOD
    note a market-open run with the same ratio would, structurally
    proving market state cannot itself push coverage toward POOR."""
    coverage = compute_research_coverage(
        universe_count=10, stage_one_attempted=10, stage_one_rejected=[], stage_two_attempted=10, stage_two_rejected=[],
    )
    assert coverage.classification == "HIGH"
    note = _coverage_result_note(no_high_conviction=True, coverage=coverage)
    assert "RESEARCH INCOMPLETE" not in note
    assert "degraded" not in note.lower()
