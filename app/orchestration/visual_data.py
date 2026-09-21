"""Sprint 3 — the Visual Data Contract.

Assembles JSON-serializable, chart-ready structures from an ALREADY-
COMPUTED `OptionsIntelligenceReport`. This module performs almost no new
computation of its own:

    - Every scalar/list field below is a direct, mechanical copy of a
      value the pipeline already computed (evidence rows, adversarial
      analysis, quality tiers, global context, support/resistance,
      requested-contract comparison, term structure).
    - The ONE piece of real work here is turning two existing PURE,
      already-tested primitives (`trend.ema_series()` and
      `structure.calculate_vwap()`) into chart LINES by calling them
      across the report's own candle series instead of only reading
      their final value, exactly as their own docstrings already support
      (`ema_series()` already returns a full aligned series;
      `calculate_vwap()` is documented as "computed over exactly the
      candle series given" — calling it once per growing prefix is that
      same contract, not a new one). No EMA/VWAP math is written here.
    - `chain_analysis.build_chain_rows()` is pure re-grouping of legs
      already fetched.

The frontend receives these already-computed values and renders them;
it never derives a financial figure of its own from raw inputs.

The running VWAP line uses the SAME multi-day-cumulative convention
(no session reset) `calculate_vwap()` already uses system-wide for the
single "VWAP: ABOVE/BELOW" value shown in every existing report — this is
not a new convention introduced for charting, just the existing one shown
as a line instead of one point.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.market.models import OptionQuote, Timeframe
from app.domain.market.price_consistency import price_source_user_label
from app.domain.news.geopolitical_transmission import TransmissionNote
from app.domain.news.models import NewsItem
from app.domain.options.chain_analysis import build_chain_rows
from app.domain.options.contract_analysis import (
    ContractAssessment,
    classify_contract_structural_quality,
    summarize_contract_preference,
)
from app.domain.options.decision_engine import EXTENDED_MIN_DAY_CHANGE_PCT
from app.domain.options.direction_analysis import DecayInterpretation
from app.domain.options.oi_migration import OIMigrationResult
from app.domain.options.realized_volatility import RealizedVolatilityResult
from app.domain.options.support_resistance import Level
from app.domain.technical.series import bounded_series
from app.domain.technical.structure import calculate_vwap
from app.domain.technical.trend import ema_series
from app.orchestration.options_intelligence_report import OptionsIntelligenceReport

_EMA_PERIODS = (9, 21, 50)


# ============================================================
# Price chart
# ============================================================


class CandlePoint(BaseModel):
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class SeriesPoint(BaseModel):
    timestamp: datetime
    value: Decimal


class LevelLine(BaseModel):
    kind: str
    strike: Decimal
    strength: str
    stability: str
    distance_from_spot_pct: Decimal | None


class PriceChartData(BaseModel):
    timeframe: str
    candles: list[CandlePoint] = Field(default_factory=list)
    ema: dict[str, list[SeriesPoint]] = Field(default_factory=dict)
    vwap: list[SeriesPoint] = Field(default_factory=list)
    support_levels: list[LevelLine] = Field(default_factory=list)
    resistance_levels: list[LevelLine] = Field(default_factory=list)
    insufficient_history: bool
    detail: str


def _build_price_chart(report: OptionsIntelligenceReport) -> PriceChartData:
    if not report.candles or report.underlying_instrument_key is None:
        return PriceChartData(timeframe="15m", insufficient_history=True, detail="no candle history available for this analysis")

    series = bounded_series(
        report.candles, instrument_id=report.underlying_instrument_key, timeframe=Timeframe.M15, as_of=report.generated_at
    )
    if not series:
        return PriceChartData(timeframe="15m", insufficient_history=True, detail="no candles at or before as_of")

    candle_points = [
        CandlePoint(timestamp=c.freshness.data_timestamp, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume)
        for c in series
    ]

    def ema_points(period: int) -> list[SeriesPoint]:
        values = ema_series([c.close for c in series], period)
        aligned = series[period - 1 :] if values else []
        return [SeriesPoint(timestamp=c.freshness.data_timestamp, value=v) for c, v in zip(aligned, values, strict=True)]

    vwap_points: list[SeriesPoint] = []
    for i in range(1, len(series) + 1):
        result = calculate_vwap(
            series[:i], instrument_id=report.underlying_instrument_key, timeframe=Timeframe.M15,
            as_of=series[i - 1].freshness.data_timestamp,
        )
        if result.value is not None:
            vwap_points.append(SeriesPoint(timestamp=series[i - 1].freshness.data_timestamp, value=result.value))

    stability_by_strike = {ls.level.strike: ls.state.value for ls in report.level_stability}

    def level_line(strike: Decimal, kind: str, strength: str, distance_pct: Decimal | None) -> LevelLine:
        return LevelLine(kind=kind, strike=strike, strength=strength, stability=stability_by_strike.get(strike, "UNCONFIRMED"), distance_from_spot_pct=distance_pct)

    return PriceChartData(
        timeframe="15m", candles=candle_points,
        ema={str(p): ema_points(p) for p in _EMA_PERIODS}, vwap=vwap_points,
        support_levels=[level_line(lv.strike, "support", lv.strength.value, lv.distance_from_spot_pct) for lv in report.support_levels],
        resistance_levels=[level_line(lv.strike, "resistance", lv.strength.value, lv.distance_from_spot_pct) for lv in report.resistance_levels],
        insufficient_history=False, detail="",
    )


# ============================================================
# Option chain table
# ============================================================


class ChainLegView(BaseModel):
    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    open_interest: int | None
    change_in_open_interest: int | None
    volume: int | None
    implied_volatility: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None


class ChainRowView(BaseModel):
    strike: Decimal
    is_atm: bool
    is_requested: bool
    is_support: bool
    is_resistance: bool
    call: ChainLegView | None
    put: ChainLegView | None


class OptionChainVisual(BaseModel):
    rows: list[ChainRowView] = Field(default_factory=list)
    atm_strike: Decimal | None = None
    detail: str = ""
    expiry: date | None = None


def _leg_view(leg: OptionQuote | None) -> ChainLegView | None:
    if leg is None:
        return None
    return ChainLegView(
        ltp=leg.last_price, bid=leg.bid_price, ask=leg.ask_price, open_interest=leg.open_interest,
        change_in_open_interest=leg.change_in_open_interest, volume=leg.volume, implied_volatility=leg.implied_volatility,
        delta=leg.delta, gamma=leg.gamma, theta=leg.theta, vega=leg.vega,
    )


def _build_option_chain(report: OptionsIntelligenceReport) -> OptionChainVisual:
    if report.option_chain is None:
        return OptionChainVisual(detail="no option chain fetched this run")

    requested_strike = report.requested_contract.requested_strike if report.requested_contract is not None else None
    support_strikes = {lv.strike for lv in report.support_levels}
    resistance_strikes = {lv.strike for lv in report.resistance_levels}

    rows = [
        ChainRowView(
            strike=row.strike, is_atm=(row.strike == report.atm_strike), is_requested=(row.strike == requested_strike),
            is_support=(row.strike in support_strikes), is_resistance=(row.strike in resistance_strikes),
            call=_leg_view(row.call), put=_leg_view(row.put),
        )
        for row in build_chain_rows(report.option_chain)
    ]
    return OptionChainVisual(
        rows=rows,
        atm_strike=report.atm_strike,
        detail="",
        expiry=report.option_chain.expiry,
    )


# ============================================================
# Requested contract + alternatives
# ============================================================


class DecayScenarioView(BaseModel):
    label: str
    move_multiple: Decimal
    underlying_move: Decimal
    estimated_premium_response: Decimal
    net_after_modeled_costs: Decimal


class ContractAssessmentView(BaseModel):
    strike: Decimal
    right: str
    moneyness: str | None
    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    # Sprint 7A, Objective 9 -- defaulted for backward compatibility with
    # any existing hand-built `ContractAssessmentView` fixture; real
    # values whenever built via `_contract_assessment_view()`.
    intrinsic_value: Decimal | None = None
    time_value: Decimal | None = None
    spread_pct: Decimal | None
    open_interest: int | None
    change_in_open_interest: int | None
    volume: int | None
    implied_volatility: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    liquidity_grade: str
    distance_to_support_pct: Decimal | None
    distance_to_resistance_pct: Decimal | None
    decay_verdict: str | None
    decay_viability_ratio: Decimal | None
    # Renamed from breakeven_underlying_move(_pct) -- a model-estimated
    # "underlying move needed to cover modeled decay+spread cost" figure,
    # NOT the contractual expiry breakeven. See decay_viability.py.
    required_underlying_move: Decimal | None
    required_underlying_move_pct: Decimal | None
    # The genuine contractual expiry breakeven (strike +/- premium) --
    # pure arithmetic, kept explicitly distinct from the field above.
    contractual_expiry_breakeven: Decimal | None
    expected_move_over_horizon: Decimal | None
    expected_move_over_horizon_pct: Decimal | None
    scenarios: list[DecayScenarioView]
    scope_note: str | None
    structural_quality: str
    data_quality_notes: list[str]


def _contract_assessment_view(assessment: ContractAssessment) -> ContractAssessmentView:
    dv = assessment.decay_viability
    return ContractAssessmentView(
        strike=assessment.strike, right=assessment.right.value, moneyness=assessment.moneyness.value if assessment.moneyness else None,
        ltp=assessment.ltp, bid=assessment.bid, ask=assessment.ask,
        intrinsic_value=assessment.intrinsic_value, time_value=assessment.time_value,
        spread_pct=assessment.spread_pct,
        open_interest=assessment.open_interest, change_in_open_interest=assessment.change_in_open_interest, volume=assessment.volume,
        implied_volatility=assessment.implied_volatility, delta=assessment.delta, gamma=assessment.gamma, theta=assessment.theta,
        vega=assessment.vega, liquidity_grade=assessment.liquidity.grade.value,
        distance_to_support_pct=assessment.distance_to_support_pct, distance_to_resistance_pct=assessment.distance_to_resistance_pct,
        decay_verdict=dv.verdict.value if dv is not None else None, decay_viability_ratio=dv.viability_ratio if dv is not None else None,
        required_underlying_move=dv.required_underlying_move if dv is not None else None,
        required_underlying_move_pct=dv.required_underlying_move_pct if dv is not None else None,
        contractual_expiry_breakeven=dv.contractual_expiry_breakeven if dv is not None else None,
        expected_move_over_horizon=dv.expected_move_over_horizon if dv is not None else None,
        expected_move_over_horizon_pct=dv.expected_move_over_horizon_pct if dv is not None else None,
        scenarios=[
            DecayScenarioView(label=s.label, move_multiple=s.move_multiple, underlying_move=s.underlying_move, estimated_premium_response=s.estimated_premium_response, net_after_modeled_costs=s.net_after_modeled_costs)
            for s in (dv.scenarios if dv is not None else [])
        ],
        scope_note=dv.scope_note if dv is not None else None,
        structural_quality=classify_contract_structural_quality(assessment).value,
        data_quality_notes=list(assessment.data_quality_notes),
    )


class RequestedContractVisual(BaseModel):
    requested_strike: Decimal
    requested_right: str
    found: bool
    detail: str | None
    assessment: ContractAssessmentView | None
    alternatives: list[ContractAssessmentView]
    preference: str
    preference_detail: str
    expiry: date | None = None


def _build_requested_contract(report: OptionsIntelligenceReport) -> RequestedContractVisual | None:
    comparison = report.requested_contract
    if comparison is None:
        return None

    preference, preference_detail = summarize_contract_preference(comparison)
    chain_expiry = report.option_chain.expiry if report.option_chain is not None else None
    return RequestedContractVisual(
        requested_strike=comparison.requested_strike, requested_right=comparison.requested_right.value,
        found=comparison.requested is not None, detail=comparison.requested_not_in_chain_detail,
        assessment=_contract_assessment_view(comparison.requested) if comparison.requested is not None else None,
        alternatives=[_contract_assessment_view(alt) for alt in comparison.alternatives],
        preference=preference.value, preference_detail=preference_detail,
        expiry=chain_expiry,
    )


# ============================================================
# Evidence / adversarial / quality / global context / S-R / term structure
# ============================================================


class EvidenceRowView(BaseModel):
    name: str
    group: str
    direction: str
    detail: str


class AdversarialView(BaseModel):
    bull_case: list[str]
    bear_case: list[str]
    contradictions: list[str]
    missing_data: list[str]
    key_risks: list[str]
    opposite_case_is_equally_supported: bool


class QualityView(BaseModel):
    data_quality: str
    evidence_quality: str
    decision_quality: str


class GlobalContextInputView(BaseModel):
    label: str
    day_change_pct: Decimal | None
    contributes_to_verdict: bool
    detail: str


class GlobalContextVisual(BaseModel):
    inputs: list[GlobalContextInputView]
    verdict: str | None
    detail: str | None


class LevelView(BaseModel):
    kind: str
    strike: Decimal
    strength: str
    # Renamed in spirit only via a clearer UI label -- see the frontend --
    # this remains the SAME `StabilityState` value (STABLE/STRENGTHENING/
    # WEAKENING/UNCONFIRMED) derived from a real 2-snapshot OI comparison.
    # NEVER a claim about historical price-action validation.
    stability: str
    # Kept SIGNED for backward compatibility (an existing consumer may
    # already rely on the sign). `distance_pct` below is the new,
    # presentation-correct UNSIGNED field -- since `kind` now guarantees
    # support is always below spot and resistance always above (the S/R
    # geometry fix), "below spot"/"above spot" is fully determined by
    # `kind` alone; no separate side field is needed.
    distance_from_spot_pct: Decimal | None
    distance_pct: Decimal | None
    evidence: str
    stability_detail: str
    # Sprint 6 -- OI_STRUCTURAL / CONFLUENCE (see
    # app.domain.options.support_resistance.LevelSource). Defaulted for
    # backward compatibility with any existing hand-built `LevelView`.
    # Never "confirmed support/resistance" from OI_STRUCTURAL alone.
    source: str = "OI_STRUCTURAL"


class TechnicalLevelView(BaseModel):
    """Sprint 6 -- a real technical price level (swing high/low from the
    same already-fetched M15 candles) with NO corroborating OI
    concentration nearby -- reported on its own, never silently dropped
    just because it isn't also an options-chain level."""

    kind: str
    price: Decimal
    evidence: str


class SupportResistanceVisual(BaseModel):
    support: list[LevelView]
    resistance: list[LevelView]
    technical_only: list[TechnicalLevelView] = Field(default_factory=list)


class TermStructureExpiryView(BaseModel):
    expiry: date
    is_weekly: bool
    days_remaining: int
    atm_strike: Decimal | None
    chain_iv: Decimal | None
    ce_pe_skew: Decimal | None
    pcr_oi: Decimal | None


class TermStructureVisual(BaseModel):
    expiries: list[TermStructureExpiryView]
    iv_slope: Decimal | None
    notes: list[str]


class FuturesVisual(BaseModel):
    instrument_key: str | None
    ltp: Decimal | None
    open_interest: Decimal | None
    volume: int | None = None
    oi_change: int | None = None
    expiry: str | None = None
    basis_pct: Decimal | None
    oi_interpretation: str | None
    # UAT finding (docs/TIRE_OPERATOR_UAT.md): when `instrument_key` is
    # `None`, the dashboard's Futures panel previously said only "no
    # futures resolved for this underlying" with no reason, even though
    # `analyze_symbol()` already computes and honestly records the real
    # reason (e.g. a weekly-only options expiry with no matching monthly
    # futures contract, common for NIFTY/BANKNIFTY) in `report.data_warnings`
    # -- it just never reached this specific panel. Surfaced here instead
    # of forcing the operator to open "Detailed Audit Report" and search a
    # multi-thousand-character text dump for the same sentence.
    no_futures_reason: str | None = None


# ============================================================
# Sprint 4 — news
# ============================================================


class NewsItemView(BaseModel):
    source: str
    published_at: datetime
    title: str
    summary: str | None
    url: str | None
    relevance: str
    direction: str
    evidence_quality: str
    # Sprint 7B, Objectives 5/6 -- deterministic event-type/recency
    # classification (see app.domain.news.event_classification's own
    # docstring: NOT sentiment, never touches `direction` above). `None`
    # only for a report built without `news_event_notes` populated (e.g.
    # an older hand-built test fixture).
    category: str | None = None
    recency: str | None = None
    retrieved_at: datetime | None = None


class NewsVisual(BaseModel):
    items: list[NewsItemView] = Field(default_factory=list)
    fetch_error: str | None = None


def _build_news(report: OptionsIntelligenceReport) -> NewsVisual:
    classification_by_item_id = {id(n.item): n for n in report.news_event_notes}

    def _item_view(i: NewsItem) -> NewsItemView:
        note = classification_by_item_id.get(id(i))
        return NewsItemView(
            source=i.source, published_at=i.published_at, title=i.title, summary=i.summary, url=i.url,
            relevance=i.relevance.value, direction=i.direction.value, evidence_quality=i.evidence_quality.value,
            category=note.category.value if note is not None else None, recency=note.recency.value if note is not None else None,
            retrieved_at=i.retrieved_at,
        )

    return NewsVisual(items=[_item_view(i) for i in report.news_items], fetch_error=report.news_fetch_error)


# ============================================================
# Sprint 4 — direction-neutral CE/PE comparison
# ============================================================


class DecayInterpretationView(BaseModel):
    theta_cost_statement: str
    move_coverage: str
    move_coverage_label: str
    move_coverage_detail: str


class ContractCaseView(BaseModel):
    could_work: list[str] = Field(default_factory=list)
    could_fail: list[str] = Field(default_factory=list)


class DirectionalPathStepView(BaseModel):
    label: str
    price: Decimal | None
    distance_pct: Decimal | None
    stability: str | None


class DirectionalPathsView(BaseModel):
    spot: Decimal | None
    bullish_path: list[DirectionalPathStepView] = Field(default_factory=list)
    bearish_path: list[DirectionalPathStepView] = Field(default_factory=list)


class DirectionComparisonVisual(BaseModel):
    reference_strike: Decimal
    verdict: str
    verdict_detail: str

    ce_assessment: ContractAssessmentView | None
    pe_assessment: ContractAssessmentView | None
    ce_alternatives: list[ContractAssessmentView] = Field(default_factory=list)
    pe_alternatives: list[ContractAssessmentView] = Field(default_factory=list)

    ce_decay: DecayInterpretationView | None
    pe_decay: DecayInterpretationView | None
    ce_case: ContractCaseView
    pe_case: ContractCaseView
    ce_hedge_context: str
    pe_hedge_context: str

    paths: DirectionalPathsView

    bullish_supporting_groups: int
    bearish_supporting_groups: int
    conflicting_groups: int

    preferred_direction: str | None
    preferred_contract: ContractAssessmentView | None
    no_defensible_direction: bool


def _decay_interpretation_view(interp: DecayInterpretation | None) -> DecayInterpretationView | None:
    if interp is None:
        return None
    return DecayInterpretationView(
        theta_cost_statement=interp.theta_cost_statement, move_coverage=interp.move_coverage.value,
        move_coverage_label=interp.move_coverage_label, move_coverage_detail=interp.move_coverage_detail,
    )


def _build_direction_comparison(report: OptionsIntelligenceReport) -> DirectionComparisonVisual | None:
    dc = report.direction_comparison
    if dc is None:
        return None
    return DirectionComparisonVisual(
        reference_strike=dc.reference_strike, verdict=dc.verdict.value, verdict_detail=dc.verdict_detail,
        ce_assessment=_contract_assessment_view(dc.ce_assessment) if dc.ce_assessment is not None else None,
        pe_assessment=_contract_assessment_view(dc.pe_assessment) if dc.pe_assessment is not None else None,
        ce_alternatives=[_contract_assessment_view(a) for a in dc.ce_alternatives.alternatives],
        pe_alternatives=[_contract_assessment_view(a) for a in dc.pe_alternatives.alternatives],
        ce_decay=_decay_interpretation_view(dc.ce_decay), pe_decay=_decay_interpretation_view(dc.pe_decay),
        ce_case=ContractCaseView(could_work=dc.ce_case.could_work, could_fail=dc.ce_case.could_fail),
        pe_case=ContractCaseView(could_work=dc.pe_case.could_work, could_fail=dc.pe_case.could_fail),
        ce_hedge_context=dc.ce_hedge_context, pe_hedge_context=dc.pe_hedge_context,
        paths=DirectionalPathsView(
            spot=dc.paths.spot,
            bullish_path=[DirectionalPathStepView(label=s.label, price=s.price, distance_pct=s.distance_pct, stability=s.stability) for s in dc.paths.bullish_path],
            bearish_path=[DirectionalPathStepView(label=s.label, price=s.price, distance_pct=s.distance_pct, stability=s.stability) for s in dc.paths.bearish_path],
        ),
        bullish_supporting_groups=dc.bullish_supporting_groups, bearish_supporting_groups=dc.bearish_supporting_groups,
        conflicting_groups=dc.conflicting_groups,
        preferred_direction=dc.preferred_direction.value if dc.preferred_direction is not None else None,
        preferred_contract=_contract_assessment_view(dc.preferred_contract) if dc.preferred_contract is not None else None,
        no_defensible_direction=dc.no_defensible_direction,
    )


class FreshnessVisual(BaseModel):
    market_state: str
    freshness_label: str | None
    data_age_seconds: float | None
    generated_at: datetime
    streams: list[StreamFreshnessView] = Field(default_factory=list)


class StreamFreshnessView(BaseModel):
    stream: str
    label: str
    data_timestamp: datetime | None = None
    retrieved_at: datetime | None = None
    session: str | None = None
    source: str | None = None
    blocks_entire_report: bool = False
    withheld_calculations: list[str] = Field(default_factory=list)
    detail: str = ""
    usable_for_display: bool = True
    usable_for_vote: bool = True
    completeness: str = "COMPLETE"
    blast_radius: str = "none"


class DevelopmentNarrativeView(BaseModel):
    pattern: str
    what_is_developing: str
    why_it_matters: str
    what_is_missing: str
    confirm_if: str
    invalidate_if: str
    freshness_note: str


class BlockerView(BaseModel):
    blocker_class: str
    explanation: str


class ExtensionDistanceView(BaseModel):
    """Section 27 -- purely informational: how far today's real day move
    is from the SAME `EXTENDED_MIN_DAY_CHANGE_PCT` threshold
    `derive_research_state()`/`determine_blockers()` already use to
    classify `EXTENDED` -- never a new threshold, never a trading
    signal. Lets the Quick View show "approaching extension" before the
    line is actually crossed, instead of only ever showing EXTENDED
    after the fact."""

    day_change_pct: Decimal
    extended_threshold_pct: Decimal
    distance_to_threshold_pct: Decimal
    status: str  # "EXTENDED" / "APPROACHING_EXTENSION" / "NORMAL"


class BlockerAssessmentView(BaseModel):
    """UAT finding (docs/TIRE_OPERATOR_UAT.md, Defect 2, generalized) --
    see `app.domain.options.research_blocker` for why `primary` is
    guaranteed not to contradict `missing_confirmation`/`secondary`: all
    three are derived from the same already-computed decision/freshness/
    development facts with one explicit priority order, never independent
    strings that can silently disagree."""

    primary: BlockerView
    secondary: list[BlockerView] = Field(default_factory=list)
    missing_confirmation: str | None = None


class SampleBreadthVisual(BaseModel):
    universe: str
    source: str
    expected_count: int
    observed_count: int
    missing_count: int
    missing_symbols: list[str]
    advances: int | None
    declines: int | None
    unchanged: int | None
    percent_up: Decimal | None
    median_day_change_pct: Decimal | None
    coverage: str
    as_of: datetime
    retrieved_at: datetime
    session: str
    detail: str


class DeliveryVisual(BaseModel):
    symbol: str
    trade_date: date | None
    traded_quantity: int | None
    deliverable_quantity: int | None
    delivery_pct: Decimal | None
    session: str
    source: str
    retrieved_at: datetime
    detail: str


class InstitutionalFlowVisual(BaseModel):
    availability: str
    fii_cash_net: Decimal | None
    dii_cash_net: Decimal | None
    index_fo_net: Decimal | None
    as_of_date: date | None
    source: str | None
    retrieved_at: datetime
    detail: str


def _build_support_resistance(report: OptionsIntelligenceReport) -> SupportResistanceVisual:
    detail_by_strike = {ls.level.strike: ls.detail for ls in report.level_stability}
    state_by_strike = {ls.level.strike: ls.state.value for ls in report.level_stability}
    # Sprint 6 -- source_by_key keyed on (kind, strike): a strike could in
    # principle appear on both sides across different runs, so kind is
    # part of the key even though support/resistance are already
    # geometrically disjoint within one run.
    source_by_key = {(lc.kind.value, lc.price): lc.source.value for lc in report.level_classifications}

    def to_view(lv: Level) -> LevelView:
        return LevelView(
            kind=lv.kind.value, strike=lv.strike, strength=lv.strength.value, stability=state_by_strike.get(lv.strike, "UNCONFIRMED"),
            distance_from_spot_pct=lv.distance_from_spot_pct,
            distance_pct=abs(lv.distance_from_spot_pct) if lv.distance_from_spot_pct is not None else None,
            evidence=lv.evidence, stability_detail=detail_by_strike.get(lv.strike, ""),
            source=source_by_key.get((lv.kind.value, lv.strike), "OI_STRUCTURAL"),
        )

    technical_only = [
        TechnicalLevelView(kind=lc.kind.value, price=lc.price, evidence=lc.detail)
        for lc in report.level_classifications
        if lc.source.value == "TECHNICAL_PRICE"
    ]
    return SupportResistanceVisual(
        support=[to_view(lv) for lv in report.support_levels], resistance=[to_view(lv) for lv in report.resistance_levels],
        technical_only=technical_only,
    )


def _build_term_structure(report: OptionsIntelligenceReport) -> TermStructureVisual | None:
    if report.term_structure is None:
        return None
    expiries = [
        TermStructureExpiryView(
            expiry=e.expiry, is_weekly=e.is_weekly, days_remaining=e.days_remaining, atm_strike=e.atm_strike,
            chain_iv=e.iv.chain_iv, ce_pe_skew=e.iv.ce_pe_skew, pcr_oi=e.totals.put_call_ratio_oi,
        )
        for e in report.term_structure.expiries
    ]
    notes = report.expiry_selection_note.flags if report.expiry_selection_note is not None else []
    return TermStructureVisual(expiries=expiries, iv_slope=report.term_structure.iv_slope(), notes=list(notes))


class LabeledPriceView(BaseModel):
    source: str  # PriceSource.value -- LATEST_QUOTE / LATEST_CANDLE / OPTION_CHAIN_REFERENCE / FUTURES
    user_label: str  # LIVE QUOTE / LAST COMPLETED CANDLE / ...
    price: Decimal | None
    timestamp: datetime | None


class PriceConsistencyVisual(BaseModel):
    """Sprint 6 -- see app.domain.market.price_consistency's own
    docstring for the full traced root cause (the real KAYNES
    discrepancy). Every price is individually labeled -- never a bare
    "spot"."""

    classification: str  # CONSISTENT / PARTIALLY_ALIGNED / INCONSISTENT / UNKNOWN
    detail: str
    prices: list[LabeledPriceView]
    max_pairwise_diff_pct: Decimal | None
    candle_excluded_as_stale: bool = False


class StrikeOIMigrationView(BaseModel):
    strike: Decimal
    current_oi: int
    previous_oi: int | None
    change_pct: Decimal | None


class OIMigrationVisual(BaseModel):
    """Sprint 7A, Objective 3 -- see app.domain.options.oi_migration's
    own docstring. Informational only -- never a directional vote."""

    right: str
    entries: list[StrikeOIMigrationView]
    direction: str  # MIGRATING_LOWER / MIGRATING_HIGHER / STABLE / INSUFFICIENT_DATA
    quality: str  # HIGH / MODERATE / LOW / INSUFFICIENT
    interpretation: str


class RealizedVolatilityView(BaseModel):
    window_label: str  # 5D / 10D / 20D
    trading_days_used: int
    annualized_vol_pct: Decimal | None
    status: str  # OK / INSUFFICIENT_DATA


class VolatilityRegimeVisual(BaseModel):
    """Sprint 7A, Objective 7 -- see
    app.domain.options.realized_volatility's own docstring. Never a
    "cheap"/"expensive" verdict."""

    atm_iv_pct: Decimal | None
    realized_5d: RealizedVolatilityView
    realized_10d: RealizedVolatilityView
    realized_20d: RealizedVolatilityView
    iv_vs_realized_state: str | None
    iv_vs_realized_detail: str | None


class MacroRegimeInputView(BaseModel):
    label: str
    day_change_pct: Decimal | None
    contributes: bool
    detail: str


class MacroRegimeVisual(BaseModel):
    """Sprint 7B, Objective 1 -- see
    app.domain.options.market_regime_context's own docstring.
    Informational only -- never a directional vote (see
    test_market_regime_and_transmission_never_appear_in_the_evidence_matrix)."""

    regime: str
    detail: str
    inputs: list[MacroRegimeInputView]


class TransmissionNoteView(BaseModel):
    """Sprint 7B, Objectives 2/3 -- see
    app.domain.news.geopolitical_transmission's own docstring.
    `sector_exposure`/`company_exposure` are honestly "UNKNOWN" today (no
    authorized data source) -- never inferred from a company/sector name."""

    event_title: str
    category: str
    channels: list[str]
    affected_market_variable: str | None
    market_variable_day_change_pct: Decimal | None
    direction: str
    sector_exposure: str
    company_exposure: str


class SectorContextVisual(BaseModel):
    classification: str
    industry: str | None
    source: str
    relative_strength_tier: str | None = None
    detail: str


class HistoricalStructureView(BaseModel):
    status: str
    session_count: int
    completed_session_count: int
    atr_pct: Decimal | None = None
    multi_day_range_pct: Decimal | None = None
    multi_day_compression: bool = False
    near_lookback_high: bool = False
    near_lookback_low: bool = False
    detail: str = ""


class StructuralReclaimView(BaseModel):
    """The dated break-and-reclaim structural fact behind
    `FAILED_BREAKDOWN_RECLAIM` (`app.domain.options.structural_reclaim`),
    exposed so a reader can see the actual level, the actual dates and
    the actual depth rather than a pattern name alone. `level` is also
    the one honest invalidation level for that pattern -- losing the
    reclaimed level again is exactly what its `invalidate_if` text says.
    `status` is reported verbatim (OK / NONE / INSUFFICIENT_HISTORY /
    CONFLICTING), so "we could not look", "nothing there" and "the tape
    reads both ways" stay three different answers."""

    status: str
    direction: str | None = None
    level_kind: str | None = None
    level: Decimal | None = None
    broken_on: date | None = None
    reclaimed_on: date | None = None
    break_depth_pct: Decimal | None = None
    sessions_since_reclaim: int | None = None
    detail: str = ""


class VisualData(BaseModel):
    price_chart: PriceChartData
    option_chain: OptionChainVisual
    requested_contract: RequestedContractVisual | None
    direction_comparison: DirectionComparisonVisual | None
    news: NewsVisual
    evidence: list[EvidenceRowView]
    convergence: str | None
    adversarial: AdversarialView | None
    quality: QualityView | None
    futures: FuturesVisual
    global_context: GlobalContextVisual | None
    support_resistance: SupportResistanceVisual
    term_structure: TermStructureVisual | None
    freshness: FreshnessVisual
    price_consistency: PriceConsistencyVisual | None = None
    ce_oi_migration: OIMigrationVisual | None = None
    pe_oi_migration: OIMigrationVisual | None = None
    volatility_regime: VolatilityRegimeVisual | None = None
    macro_regime: MacroRegimeVisual | None = None
    transmission_notes: list[TransmissionNoteView] = Field(default_factory=list)
    research_state: str | None = None
    development: DevelopmentNarrativeView | None = None
    blockers: BlockerAssessmentView | None = None
    extension_distance: ExtensionDistanceView | None = None
    sample_breadth: SampleBreadthVisual | None = None
    delivery: DeliveryVisual | None = None
    institutional_flows: InstitutionalFlowVisual | None = None
    sector: SectorContextVisual | None = None
    historical_structure: HistoricalStructureView | None = None
    structural_reclaim: StructuralReclaimView | None = None


_APPROACHING_EXTENSION_WITHIN_PCT = Decimal("1.5")


def _build_extension_distance(report: OptionsIntelligenceReport) -> ExtensionDistanceView | None:
    """Section 27 -- purely informational distance-to-EXTENDED. `None`
    when `day_change_pct` itself is unavailable (never fabricated)."""
    if report.day_change_pct is None:
        return None
    day_change_pct = abs(report.day_change_pct)
    distance = EXTENDED_MIN_DAY_CHANGE_PCT - day_change_pct
    if distance <= 0:
        status = "EXTENDED"
    elif distance <= _APPROACHING_EXTENSION_WITHIN_PCT:
        status = "APPROACHING_EXTENSION"
    else:
        status = "NORMAL"
    return ExtensionDistanceView(
        day_change_pct=report.day_change_pct, extended_threshold_pct=EXTENDED_MIN_DAY_CHANGE_PCT,
        distance_to_threshold_pct=distance, status=status,
    )


def _build_sector_context(report: OptionsIntelligenceReport) -> SectorContextVisual:
    info = report.sector_info
    srs = report.sector_relative_strength
    if info is None:
        return SectorContextVisual(
            classification="UNAVAILABLE",
            industry=None,
            source="NSE Nifty 500 constituent list",
            relative_strength_tier=None,
            detail="sector map was not supplied for this analysis.",
        )
    return SectorContextVisual(
        classification=info.classification.value,
        industry=info.industry,
        source=info.source,
        relative_strength_tier=srs.tier.value if srs is not None else None,
        detail=srs.detail if srs is not None else (
            f"official industry {info.industry}" if info.industry is not None else "symbol not in official NSE Nifty 500 constituent list"
        ),
    )


def build_visual_data(report: OptionsIntelligenceReport) -> VisualData:
    """The single entry point: everything the dashboard's charts/tables
    need, assembled from fields `analyze_symbol()` already computed. Never
    called for a failed analysis (`report.error is not None`) -- callers
    check that first, exactly like `build_analysis_snapshot()` does."""
    evidence_rows = (
        [EvidenceRowView(name=r.name, group=r.group.value, direction=r.direction.value, detail=r.detail) for r in report.matrix.rows]
        if report.matrix is not None
        else []
    )
    convergence = report.matrix.overall_convergence().value if report.matrix is not None else None

    aa = report.adversarial_analysis
    adversarial = (
        AdversarialView(
            bull_case=list(aa.bull_case), bear_case=list(aa.bear_case), contradictions=list(aa.contradictions),
            missing_data=list(aa.missing_data), key_risks=list(aa.key_risks), opposite_case_is_equally_supported=aa.opposite_case_is_equally_supported,
        )
        if aa is not None
        else None
    )

    qt = report.quality_tiers
    quality = QualityView(data_quality=qt.data_quality.value, evidence_quality=qt.evidence_quality.value, decision_quality=qt.decision_quality.value) if qt is not None else None

    gc = report.global_context
    global_context = (
        GlobalContextVisual(
            inputs=[GlobalContextInputView(label=i.label, day_change_pct=i.day_change_pct, contributes_to_verdict=i.contributes_to_verdict, detail=i.detail) for i in gc.inputs],
            verdict=gc.verdict.value, detail=gc.detail,
        )
        if gc is not None
        else None
    )

    no_futures_reason = None
    if report.futures_instrument_key is None:
        no_futures_reason = next(
            (w for w in report.data_warnings if w.startswith("no futures contract found")), None
        )
    futures = FuturesVisual(
        instrument_key=report.futures_instrument_key, ltp=report.futures_ltp, open_interest=report.futures_oi,
        volume=report.futures_volume, oi_change=report.futures_oi_change, expiry=report.futures_expiry,
        basis_pct=report.futures_basis_pct,
        oi_interpretation=report.futures_oi_observation.conventional_reading if report.futures_oi_observation is not None else None,
        no_futures_reason=no_futures_reason,
    )

    pc = report.price_consistency
    price_consistency = (
        PriceConsistencyVisual(
            classification=pc.classification.value, detail=pc.detail,
            prices=[
                LabeledPriceView(
                    source=p.source.value,
                    user_label=price_source_user_label(p.source),
                    price=p.price,
                    timestamp=p.timestamp,
                )
                for p in pc.prices
            ],
            max_pairwise_diff_pct=pc.max_pairwise_diff_pct,
            candle_excluded_as_stale=pc.candle_excluded_as_stale,
        )
        if pc is not None
        else None
    )

    def _oi_migration_view(m: OIMigrationResult | None) -> OIMigrationVisual | None:
        if m is None:
            return None
        return OIMigrationVisual(
            right=m.right.value,
            entries=[StrikeOIMigrationView(strike=e.strike, current_oi=e.current_oi, previous_oi=e.previous_oi, change_pct=e.change_pct) for e in m.entries],
            direction=m.direction.value, quality=m.quality.value, interpretation=m.interpretation,
        )

    def _realized_vol_view(r: RealizedVolatilityResult | None) -> RealizedVolatilityView:
        if r is None:
            return RealizedVolatilityView(window_label="", trading_days_used=0, annualized_vol_pct=None, status="INSUFFICIENT_DATA")
        return RealizedVolatilityView(window_label=r.window_label, trading_days_used=r.trading_days_used, annualized_vol_pct=r.annualized_vol_pct, status=r.status)

    volatility_regime = VolatilityRegimeVisual(
        atm_iv_pct=report.iv_summary.chain_iv if report.iv_summary is not None else None,
        realized_5d=_realized_vol_view(report.realized_vol_5d), realized_10d=_realized_vol_view(report.realized_vol_10d),
        realized_20d=_realized_vol_view(report.realized_vol_20d),
        iv_vs_realized_state=report.iv_vs_realized_state.value if report.iv_vs_realized_state is not None else None,
        iv_vs_realized_detail=report.iv_vs_realized_detail,
    )

    mrc = report.market_regime_context
    macro_regime = (
        MacroRegimeVisual(
            regime=mrc.regime.value, detail=mrc.detail,
            inputs=[MacroRegimeInputView(label=i.label, day_change_pct=i.day_change_pct, contributes=i.contributes, detail=i.detail) for i in mrc.inputs],
        )
        if mrc is not None
        else None
    )

    def _transmission_view(t: TransmissionNote) -> TransmissionNoteView:
        return TransmissionNoteView(
            event_title=t.event_title, category=t.category.value, channels=[c.value for c in t.channels],
            affected_market_variable=t.affected_market_variable, market_variable_day_change_pct=t.market_variable_day_change_pct,
            direction=t.direction, sector_exposure=t.sector_exposure, company_exposure=t.company_exposure,
        )

    sample_breadth = None
    if report.sample_breadth is not None:
        b = report.sample_breadth
        sample_breadth = SampleBreadthVisual(
            universe=b.universe, source=b.source, expected_count=b.expected_count, observed_count=b.observed_count,
            missing_count=b.missing_count, missing_symbols=list(b.missing_symbols), advances=b.advances,
            declines=b.declines, unchanged=b.unchanged, percent_up=b.percent_up,
            median_day_change_pct=b.median_day_change_pct, coverage=b.coverage.value, as_of=b.as_of,
            retrieved_at=b.retrieved_at, session=b.session, detail=b.detail,
        )
    delivery = None
    if report.delivery is not None:
        d = report.delivery
        delivery = DeliveryVisual(
            symbol=d.symbol, trade_date=d.trade_date, traded_quantity=d.traded_quantity,
            deliverable_quantity=d.deliverable_quantity, delivery_pct=d.delivery_pct, session=d.session.value,
            source=d.source, retrieved_at=d.retrieved_at, detail=d.detail,
        )
    flows = None
    if report.institutional_flows is not None:
        f = report.institutional_flows
        flows = InstitutionalFlowVisual(
            availability=f.availability.value, fii_cash_net=f.fii_cash_net, dii_cash_net=f.dii_cash_net,
            index_fo_net=f.index_fo_net, as_of_date=f.as_of_date, source=f.source, retrieved_at=f.retrieved_at,
            detail=f.detail,
        )

    return VisualData(
        price_chart=_build_price_chart(report), option_chain=_build_option_chain(report), requested_contract=_build_requested_contract(report),
        direction_comparison=_build_direction_comparison(report), news=_build_news(report),
        evidence=evidence_rows, convergence=convergence, adversarial=adversarial, quality=quality, futures=futures, global_context=global_context,
        support_resistance=_build_support_resistance(report), term_structure=_build_term_structure(report),
        freshness=FreshnessVisual(
            market_state=report.data_state.value, freshness_label=report.freshness_label.value if report.freshness_label is not None else None,
            data_age_seconds=report.data_age_seconds, generated_at=report.generated_at,
            streams=[
                StreamFreshnessView(
                    stream=s.stream.value, label=s.label.value, data_timestamp=s.data_timestamp,
                    retrieved_at=s.retrieved_at, session=s.session, source=s.source,
                    blocks_entire_report=s.blocks_entire_report, withheld_calculations=list(s.withheld_calculations),
                    detail=s.detail, usable_for_display=s.usable_for_display, usable_for_vote=s.usable_for_vote,
                    completeness=s.completeness, blast_radius=s.blast_radius,
                )
                for s in report.stream_freshness
            ],
        ),
        price_consistency=price_consistency,
        ce_oi_migration=_oi_migration_view(report.ce_oi_migration), pe_oi_migration=_oi_migration_view(report.pe_oi_migration),
        volatility_regime=volatility_regime, macro_regime=macro_regime,
        transmission_notes=[_transmission_view(t) for t in report.transmission_notes],
        research_state=report.research_state.value if report.research_state is not None else None,
        development=(
            DevelopmentNarrativeView(
                pattern=report.development.pattern.value,
                what_is_developing=report.development.what_is_developing,
                why_it_matters=report.development.why_it_matters,
                what_is_missing=report.development.what_is_missing,
                confirm_if=report.development.confirm_if,
                invalidate_if=report.development.invalidate_if,
                freshness_note=report.development.freshness_note,
            )
            if report.development is not None
            else None
        ),
        blockers=(
            BlockerAssessmentView(
                primary=BlockerView(blocker_class=report.blockers.primary.blocker_class.value, explanation=report.blockers.primary.explanation),
                secondary=[BlockerView(blocker_class=b.blocker_class.value, explanation=b.explanation) for b in report.blockers.secondary],
                missing_confirmation=report.blockers.missing_confirmation,
            )
            if report.blockers is not None
            else None
        ),
        extension_distance=_build_extension_distance(report),
        sample_breadth=sample_breadth, delivery=delivery, institutional_flows=flows,
        sector=_build_sector_context(report),
        historical_structure=(
            HistoricalStructureView(
                status=report.historical_structure.status.value,
                session_count=report.historical_structure.session_count,
                completed_session_count=report.historical_structure.completed_session_count,
                atr_pct=report.historical_structure.atr_pct,
                multi_day_range_pct=report.historical_structure.multi_day_range_pct,
                multi_day_compression=report.historical_structure.multi_day_compression,
                near_lookback_high=report.historical_structure.near_lookback_high,
                near_lookback_low=report.historical_structure.near_lookback_low,
                detail=report.historical_structure.detail,
            )
            if report.historical_structure is not None
            else None
        ),
        structural_reclaim=(
            StructuralReclaimView(
                status=report.structural_reclaim.status.value,
                direction=report.structural_reclaim.direction,
                level_kind=report.structural_reclaim.level_kind,
                level=report.structural_reclaim.level,
                broken_on=report.structural_reclaim.broken_on,
                reclaimed_on=report.structural_reclaim.reclaimed_on,
                break_depth_pct=report.structural_reclaim.break_depth_pct,
                sessions_since_reclaim=report.structural_reclaim.sessions_since_reclaim,
                detail=report.structural_reclaim.detail,
            )
            if report.structural_reclaim is not None
            else None
        ),
    )
