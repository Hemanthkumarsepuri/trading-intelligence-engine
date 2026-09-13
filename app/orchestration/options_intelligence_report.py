"""The Options Intelligence report — one dataclass holding everything the
pipeline computed for one symbol at one `as_of`, plus a `render_text()`
that formats it in the requested report layout. Machine-readable (every
field is a real typed object from the domain layer, nothing stringified
prematurely) and human-readable (`render_text()`).

Never claims accuracy, a win rate, or profitability anywhere — this report
states evidence, quality, and uncertainty, never a guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.domain.market.data_state import MarketDataState
from app.domain.market.delivery_context import DeliveryObservation
from app.domain.market.institutional_flows import InstitutionalFlowContext
from app.domain.market.models import Candle, OptionChainSnapshot
from app.domain.market.price_consistency import PriceConsistencyResult
from app.domain.market.sample_breadth import SampleBreadthResult
from app.domain.news.event_classification import NewsEventCategory, NewsRecency
from app.domain.news.geopolitical_transmission import TransmissionNote
from app.domain.news.models import NewsItem
from app.domain.options.adversarial_analysis import AdversarialAnalysis
from app.domain.options.anomaly_detection import AnomalyResult
from app.domain.options.candidate_engine import OptionCandidate
from app.domain.options.contract_analysis import (
    ContractAssessment,
    ContractComparison,
    classify_contract_structural_quality,
    summarize_contract_preference,
)
from app.domain.options.decay_engine import DecayAttribution
from app.domain.options.decay_viability import DecayViabilityAssessment
from app.domain.options.decision_engine import (
    DecisionResult,
    FinalDecision,
    QualityLevel,
    QualityTiers,
    ResearchState,
)
from app.domain.options.development import DevelopmentNarrative
from app.domain.options.direction_analysis import DirectionComparison
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceMatrix,
    EvidenceRow,
    OverallConvergence,
)
from app.domain.options.freshness_label import FreshnessLabel, StreamFreshness
from app.domain.options.global_context import GlobalContextAssessment
from app.domain.options.historical_structure import HistoricalStructure
from app.domain.options.iv_context import AtmIvSummary, IvRankResult, IvTrend
from app.domain.options.market_regime import MarketRegimeResult
from app.domain.options.market_regime_context import MacroRegimeResult
from app.domain.options.models import ChainQualityIssue
from app.domain.options.oi_migration import OIMigrationResult
from app.domain.options.price_oi_interpretation import BasisChangeObservation, PriceOIObservation
from app.domain.options.realized_volatility import RealizedVolatilityResult, VolatilityState
from app.domain.options.research_blocker import BlockerAssessment
from app.domain.options.sector_strength import SectorInfo, SectorRelativeStrength
from app.domain.options.support_resistance import (
    Level,
    LevelClassification,
    LevelStability,
    TechnicalLevel,
)
from app.domain.options.temporal_evidence import TemporalObservation
from app.domain.options.term_structure import ExpirySelectionNote, TermStructure
from app.domain.strategy.current_analysis import CurrentAnalysisResult
from app.domain.technical.momentum import RSIResult
from app.utils.time import to_ist

_DATA_LABEL = {
    MarketDataState.LIVE_STREAMING: "LIVE",
    MarketDataState.LIVE_SNAPSHOT: "LIVE",
    MarketDataState.MARKET_CLOSED_LATEST_DATA: "MARKET_CLOSED",
    MarketDataState.PRE_MARKET: "PRE_MARKET",
    MarketDataState.STALE_DATA: "DELAYED",
    MarketDataState.INSUFFICIENT_HISTORY: "UNAVAILABLE",
    MarketDataState.PROVIDER_UNAVAILABLE: "UNAVAILABLE",
    MarketDataState.ERROR: "UNAVAILABLE",
}


@dataclass(frozen=True)
class StageLatency:
    stage: str
    seconds: float


@dataclass(frozen=True)
class NewsEventNote:
    """Sprint 7B, Objectives 5/6 -- one real news item plus its
    deterministic event-type/recency classification. Never mutates or
    replaces `item` (still carries the real, unmodified `NewsItem`,
    `direction` permanently `UNKNOWN`)."""

    item: NewsItem
    category: NewsEventCategory
    recency: NewsRecency


@dataclass
class OptionsIntelligenceReport:
    symbol: str
    underlying_instrument_key: str | None
    generated_at: datetime  # this system's own as_of for the whole analysis
    data_state: MarketDataState
    data_age_seconds: float | None

    spot: Decimal | None = None
    day_change: Decimal | None = None
    day_change_pct: Decimal | None = None
    analysis: CurrentAnalysisResult | None = None
    rsi: RSIResult | None = None
    regime: MarketRegimeResult | None = None
    # The raw M15 candle series this analysis was computed from (Sprint 3) --
    # already `as_of`-bounded by the pipeline's own fetch (`end=as_of`); kept
    # here as-is so a chart can be built from EXACTLY the same candles every
    # other technical result on this report was derived from, never a
    # separately (and possibly inconsistently) re-fetched series.
    candles: list[Candle] = field(default_factory=list)
    # Sprint 7A -- whether the LATEST real M15 candle is current enough,
    # relative to the live session, to be trusted as evidence (see
    # `evidence_matrix.row_m15_trend()`'s own docstring). Candles above
    # are still always shown for charting/history regardless of this
    # flag -- it only gates evidence-matrix participation.
    candles_are_current: bool = True

    futures_instrument_key: str | None = None
    futures_ltp: Decimal | None = None
    futures_oi: Decimal | None = None
    futures_basis_pct: Decimal | None = None
    futures_oi_observation: PriceOIObservation | None = None
    futures_timestamp: datetime | None = None
    # Final Hardening Pass, Phase 6 -- see price_oi_interpretation.py's
    # own docstring. Informational only, same discipline as OI migration/
    # realized volatility -- never fed into the evidence matrix.
    futures_basis_change: BasisChangeObservation | None = None

    # Sprint 6 -- see app.domain.market.price_consistency's own docstring.
    # Computed once every real, already-fetched price source for this run
    # is available; never re-fetches anything.
    price_consistency: PriceConsistencyResult | None = None

    expiry: str | None = None
    atm_strike: Decimal | None = None
    total_call_oi: int | None = None
    total_put_oi: int | None = None
    pcr_oi: Decimal | None = None
    # The raw option-chain snapshot (Sprint 3) -- stored so a full CALL |
    # STRIKE | PUT visual table can be built without a second fetch; every
    # aggregate elsewhere on this report (`atm_strike`, `pcr_oi`, `candidates`,
    # `requested_contract`, ...) was already derived from this SAME snapshot.
    option_chain: OptionChainSnapshot | None = None
    # Phase 3 gap-closure -- `True` unless the PROVIDER itself structurally
    # cannot supply option-chain history for this `as_of` (a
    # `HistoricalReplayProvider` replaying a date before any real snapshot
    # was ever captured -- see `AnalysisProvider.capabilities`). Distinct
    # from `option_chain is None`, which can ALSO be true for a genuine
    # live fetch failure (a real, transient problem) or simply because no
    # chain has been fetched yet at this point in construction --
    # `derivatives_history_available=False` is the one honest signal that
    # means "this provider was never going to have chain data for this
    # instant, by design, not by accident." Never set to `False` for a
    # live `UpstoxProvider` fetch failure -- that path is unchanged and
    # remains fatal (`report.error`), exactly as before this phase.
    derivatives_history_available: bool = True

    support_levels: list[Level] = field(default_factory=list)
    resistance_levels: list[Level] = field(default_factory=list)
    level_stability: list[LevelStability] = field(default_factory=list)
    # Sprint 6 -- OI-structural vs technical-price vs confluence
    # classification (see app.domain.options.support_resistance's own
    # docstring). Empty when technical levels couldn't be computed
    # (insufficient real candle history) -- never fabricated.
    level_classifications: list[LevelClassification] = field(default_factory=list)
    # 95% sprint, Sprint 1 -- the real candle-derived (swing/VWAP/EMA)
    # technical levels `technical_price_levels()`/`vwap_ema_levels()`
    # already compute internally to build `level_classifications` above
    # and the `PRE_BREAKOUT_COMPRESSION` proximity check, now ALSO kept
    # here directly. Unlike `support_levels`/`resistance_levels` (OI/
    # chain-derived, empty with no chain -- see stage 9's own comment),
    # this list is populated purely from real M15 candles and is
    # therefore available even for a price-only (no-derivatives) replay
    # observation -- see `app.orchestration.daily_research
    # .build_price_only_observation()`. Never a second TA engine: the
    # SAME functions, called once, stored twice for two different real
    # consumers.
    technical_levels: list[TechnicalLevel] = field(default_factory=list)

    # Sprint 7A, Objective 3 -- multi-strike OI migration, informational
    # only (see app.domain.options.oi_migration's own docstring for why
    # this is deliberately kept OUT of the evidence matrix/decision
    # engine).
    ce_oi_migration: OIMigrationResult | None = None
    pe_oi_migration: OIMigrationResult | None = None

    # Sprint 7A, Objective 7 -- realized volatility vs implied volatility,
    # informational (see app.domain.options.realized_volatility's own
    # docstring -- never a "cheap"/"expensive" verdict).
    realized_vol_5d: RealizedVolatilityResult | None = None
    realized_vol_10d: RealizedVolatilityResult | None = None
    realized_vol_20d: RealizedVolatilityResult | None = None
    iv_vs_realized_state: VolatilityState | None = None
    iv_vs_realized_detail: str | None = None

    iv_summary: AtmIvSummary | None = None
    iv_rank: IvRankResult | None = None
    iv_trend: IvTrend | None = None

    chain_quality_issues: list[ChainQualityIssue] = field(default_factory=list)

    temporal_observations: list[TemporalObservation] = field(default_factory=list)
    decay_attributions: list[DecayAttribution] = field(default_factory=list)
    decay_viability: list[DecayViabilityAssessment] = field(default_factory=list)
    anomalies: list[AnomalyResult] = field(default_factory=list)
    term_structure: TermStructure | None = None
    expiry_selection_note: ExpirySelectionNote | None = None
    freshness_label: FreshnessLabel | None = None
    global_context: GlobalContextAssessment | None = None
    # Sprint 7B, Objective 1 -- see market_regime_context.py's own
    # docstring. Informational -- never fed into the evidence matrix/
    # decision engine (same discipline as OI migration/realized vol).
    market_regime_context: MacroRegimeResult | None = None
    # Sprint 7B, Objectives 2/5/6 -- see event_classification.py/
    # geopolitical_transmission.py's own docstrings. Both informational.
    news_event_notes: list[NewsEventNote] = field(default_factory=list)
    transmission_notes: list[TransmissionNote] = field(default_factory=list)
    # Sprint 8, Objective P1 -- see sector_strength.py's own docstring.
    # Both informational -- structural context, never fed into the
    # evidence matrix/decision engine.
    sector_info: SectorInfo | None = None
    sector_relative_strength: SectorRelativeStrength | None = None

    matrix: EvidenceMatrix | None = None
    adversarial_analysis: AdversarialAnalysis | None = None
    candidates: list[OptionCandidate] = field(default_factory=list)
    invalidation_level: Decimal | None = None
    requested_contract: ContractComparison | None = None
    direction_comparison: DirectionComparison | None = None
    decision: DecisionResult | None = None
    quality_tiers: QualityTiers | None = None
    # Phase 2 -- research maturity, separate from `FinalDecision` (kept
    # for compatibility, including the historical TRADEABLE label).
    research_state: ResearchState | None = None
    development: DevelopmentNarrative | None = None
    historical_structure: HistoricalStructure | None = None
    # UAT finding (docs/TIRE_OPERATOR_UAT.md, Defect 2 -- generalized, not
    # KAYNES-specific): the single, deterministically-prioritized reason
    # nothing more decisive can be said yet, reconciled across the
    # decision engine, freshness, and development narrative so the Quick
    # View never shows a "what is missing" explanation that contradicts
    # the real, more fundamental blocking reason (e.g. an illiquid
    # contract) shown elsewhere on the same page. See
    # `app.domain.options.research_blocker` for the precedence rule.
    blockers: BlockerAssessment | None = None
    stream_freshness: list[StreamFreshness] = field(default_factory=list)
    sample_breadth: SampleBreadthResult | None = None
    delivery: DeliveryObservation | None = None
    institutional_flows: InstitutionalFlowContext | None = None

    news_items: list[NewsItem] = field(default_factory=list)
    news_fetch_error: str | None = None

    data_warnings: list[str] = field(default_factory=list)
    stage_latencies: list[StageLatency] = field(default_factory=list)
    total_latency_seconds: float = 0.0

    error: str | None = None

    @property
    def market_status_label(self) -> str:
        return _DATA_LABEL.get(self.data_state, "UNAVAILABLE")

    def _render_market_context(self) -> list[str]:
        """Informational cash confirmation -- never an EvidenceGroup vote."""
        lines: list[str] = ["MARKET CONTEXT (informational -- not a directional vote)", ""]
        if self.sample_breadth is None:
            lines.append("Breadth: UNKNOWN (sample not computed this run)")
        else:
            b = self.sample_breadth
            ad = (
                f"{b.advances} up / {b.declines} down / {b.unchanged} unchanged"
                if b.advances is not None
                else "A/D withheld"
            )
            lines.append(
                f"Breadth: {ad}  ({b.observed_count}/{b.expected_count} observed, {b.coverage.value})"
            )
            lines.append(f"  {b.detail}")
            lines.append(f"  source={b.source} session={b.session}")
        if self.delivery is None:
            lines.append("Delivery: UNKNOWN")
        else:
            d = self.delivery
            pct = f"{d.delivery_pct}%" if d.delivery_pct is not None else "n/a"
            trade = d.trade_date.isoformat() if d.trade_date is not None else "n/a"
            lines.append(f"Delivery: {d.symbol} {pct}  {d.session.value} trade_date={trade}")
            lines.append(f"  {d.detail}")
        if self.institutional_flows is None:
            lines.append("FII/DII: UNKNOWN")
        else:
            f = self.institutional_flows
            lines.append(f"FII/DII CASH FLOW: {f.availability.value}")
            lines.append(f"  {f.detail}")
        lines.append("")
        return lines

    def render_text(self) -> str:
        def fmt(value: Decimal | None) -> str:
            # Display-only rounding to 2 decimal places. Two independent,
            # pre-existing (not introduced this milestone) sources of
            # excess precision reach this renderer: `UpstoxProvider.
            # get_quotes()` derives `previous_close` via float subtraction
            # (`last_price - net_change`), which can leave float-rounding
            # noise baked into the resulting Decimal string (e.g.
            # `84.799999999998`) — the existing test suite already
            # tolerates this with `pytest.approx`, so it is a known,
            # accepted characteristic of that provider method, not a new
            # bug; and `domain/technical/trend.calculate_ema()`'s Decimal
            # recursion legitimately accumulates many real decimal places
            # over its smoothing window. Neither is touched here — full
            # precision is still what every computation actually uses;
            # only this human-facing rendering rounds for readability.
            if value is None:
                return "n/a"
            return f"{value:.2f}"

        def pct2(value: Decimal | None) -> str:
            return f"{value:+.2f}" if value is not None else "n/a"

        if self.error is not None:
            return (
                f"{'=' * 60}\nOPTIONS INTELLIGENCE\n{self.symbol}\n{'=' * 60}\n\n"
                f"UNAVAILABLE: {self.error}\n\nFINAL DECISION:\nDATA_INSUFFICIENT\n"
            )

        lines: list[str] = []
        add = lines.append
        ist_now = to_ist(self.generated_at).strftime("%Y-%m-%d %H:%M:%S")
        age = f"{self.data_age_seconds:.0f}s" if self.data_age_seconds is not None else "n/a"

        add("=" * 40)
        add("OPTIONS INTELLIGENCE")
        add(self.symbol)
        add(f"TIME: {ist_now} IST")
        freshness_str = self.freshness_label.value if self.freshness_label is not None else self.market_status_label
        add(f"DATA AGE: {age}  ({freshness_str})")
        add("=" * 40)
        add("")
        add("RESEARCH STATE")
        add(self.research_state.value if self.research_state is not None else "UNKNOWN")
        add("This is research maturity, not a buy/sell instruction. TRADEABLE below is a compatibility label only.")
        add("")
        if self.blockers is not None:
            b = self.blockers
            add("PRIMARY BLOCKER")
            add(f"{b.primary.blocker_class.value}: {b.primary.explanation}")
            if b.secondary:
                add("ALSO TRUE RIGHT NOW (secondary, not primary):")
                for sb in b.secondary:
                    add(f"  {sb.blocker_class.value}: {sb.explanation}")
            if b.missing_confirmation is not None:
                add(f"CONFIRMATION STILL MISSING: {b.missing_confirmation}")
            add("")
        if self.development is not None:
            d = self.development
            add("DEVELOPING OBSERVATION")
            add(f"Pattern: {d.pattern.value}")
            add(f"What is developing: {d.what_is_developing}")
            add(f"Why it matters: {d.why_it_matters}")
            add(f"What is missing: {d.what_is_missing}")
            add(f"CONFIRM IF: {d.confirm_if}")
            add(f"INVALIDATE IF: {d.invalidate_if}")
            add(f"Freshness: {d.freshness_note}")
            add("")
        if self.stream_freshness:
            add("PER-STREAM FRESHNESS (staleness of one stream does not automatically invalidate the others)")
            for sf in self.stream_freshness:
                withheld = f"  withheld={','.join(sf.withheld_calculations)}" if sf.withheld_calculations else ""
                block = "  BLOCKS_REPORT" if sf.blocks_entire_report else ""
                add(f"  {sf.stream.value}: {sf.label.value}{block}{withheld} -- {sf.detail}")
            add("")

        add("UNDERLYING")
        add(f"Spot: {fmt(self.spot)}")
        if self.day_change is not None and self.day_change_pct is not None:
            add(f"Day change: {fmt(self.day_change)} ({self.day_change_pct:.2f}%)")
        else:
            add("Day change: n/a")
        a = self.analysis
        if not self.candles_are_current:
            add("M15 trend: WITHHELD (M15 = STALE -- TECHNICAL_CONFIRMATION = PENDING)")
            add("EMA structure: n/a (not current-session evidence)")
        elif a is not None and a.ema_alignment is not None:
            add(f"M15 trend: {a.ema_alignment.value}")
            add(f"EMA structure: {fmt(a.ema9)}/{fmt(a.ema21)}/{fmt(a.ema50)}")
        else:
            add("M15 trend: UNAVAILABLE (insufficient history)")
            add("EMA structure: n/a")
        if not self.candles_are_current:
            add("ROLLING_INTRADAY_VWAP: WITHHELD (derived from stale M15, not session VWAP)")
        elif a is not None and a.vwap_value is not None:
            add(
                f"ROLLING_INTRADAY_VWAP: {fmt(a.vwap_value)}  "
                f"(price {a.price_vs_vwap.value if a.price_vs_vwap else '?'}; multi-day M15 series, not session VWAP)"
            )
        else:
            add("ROLLING_INTRADAY_VWAP: UNAVAILABLE")
        if self.rsi is not None and self.rsi.value is not None:
            add(f"Momentum (RSI-14): {self.rsi.value:.1f}  (zone: {_rsi_zone_label(self.rsi.value)} -- context only, never implies a reversal by itself)")
        else:
            add("Momentum (RSI-14): UNAVAILABLE")
        add(f"Market regime: {self.regime.regime.value if self.regime else 'UNAVAILABLE'}")
        add("")

        add("GLOBAL / MARKET-WIDE CONTEXT (not stock-specific sector relevance -- see detail)")
        if self.global_context is not None:
            add(f"Verdict: {self.global_context.verdict.value} -- {self.global_context.detail}")
            for gi in self.global_context.inputs:
                change_str = f"{gi.day_change_pct:+.2f}%" if gi.day_change_pct is not None else "n/a"
                add(f"  {gi.label}: {change_str}  {'(contributed)' if gi.contributes_to_verdict else '(informational)'}")
        else:
            add("not assessed this run")
        add("")

        add("SECTOR")
        if self.sector_info is None:
            add("not assessed this run")
        elif self.sector_info.classification.value == "UNKNOWN":
            add(f"UNKNOWN -- {self.sector_info.source} has no entry for this symbol")
        else:
            add(f"Industry (official, {self.sector_info.source}): {self.sector_info.industry}")
            srs = self.sector_relative_strength
            if srs is not None:
                add(f"3-level relative strength: {srs.tier.value} -- {srs.detail}")
            else:
                add("relative strength: not assessed this run")
        add("")

        add("FUTURES")
        if self.futures_instrument_key is None:
            add("No futures contract available for this underlying/expiry")
        else:
            add(f"LTP: {fmt(self.futures_ltp)}")
            add(f"Premium/discount: {self.futures_basis_pct:.2f}%" if self.futures_basis_pct is not None else "Premium/discount: n/a")
            add(f"OI: {self.futures_oi if self.futures_oi is not None else 'n/a'}")
            if self.futures_oi_observation is not None:
                add(f"OI change: {self.futures_oi_observation.conventional_reading}")
            if self.futures_basis_change is not None:
                add(f"Basis change: {self.futures_basis_change.interpretation}")
            else:
                add("OI change: n/a")
        add("")

        add("OPTIONS STRUCTURE")
        add(f"Expiry: {self.expiry or 'n/a'}")
        add(f"ATM: {self.atm_strike if self.atm_strike is not None else 'n/a'}")
        add(f"PCR: {self.pcr_oi:.3f}" if self.pcr_oi is not None else "PCR: n/a (call OI zero/unknown)")
        add(f"Total CE OI: {self.total_call_oi:,}" if self.total_call_oi is not None else "Total CE OI: n/a")
        add(f"Total PE OI: {self.total_put_oi:,}" if self.total_put_oi is not None else "Total PE OI: n/a")
        add("")

        lines.extend(render_requested_contract_section(self.requested_contract))
        lines.extend(render_news_section(self.news_items, self.news_fetch_error))
        lines.extend(render_directional_analysis_section(self))

        add("TERM STRUCTURE (multi-expiry)")
        if self.term_structure is not None:
            for exp in self.term_structure.expiries:
                kind = "weekly" if exp.is_weekly else "monthly"
                iv_str = f"{exp.iv.chain_iv:.2f}%" if exp.iv.chain_iv is not None else "n/a"
                pcr_str = f"{exp.totals.put_call_ratio_oi:.3f}" if exp.totals.put_call_ratio_oi is not None else "n/a"
                add(
                    f"- {exp.expiry.isoformat()} ({kind}, {exp.days_remaining}d): ATM={fmt(exp.atm_strike) if exp.atm_strike else 'n/a'} "
                    f"ATM-IV={iv_str} PCR={pcr_str} quality_issues={len(exp.chain_quality_issues)}"
                )
            slope = self.term_structure.iv_slope()
            add(f"IV term-structure slope (near - far): {slope:.2f} pts" if slope is not None else "IV term-structure slope: n/a (fewer than 2 expiries with known IV)")
            if self.expiry_selection_note is not None and self.expiry_selection_note.flags:
                add("Notes: " + "; ".join(self.expiry_selection_note.flags))
        else:
            add("not compared this run")
        add("")

        add("TEMPORAL EVIDENCE (ATM CE/PE, vs. real prior persisted snapshots)")

        def pct(value: Decimal | None) -> str:
            return f"{value:+.2f}%" if value is not None else "n/a"

        if self.temporal_observations:
            for obs in self.temporal_observations:
                add(
                    f"- {obs.right.value} {obs.strike}  interval={obs.interval}  "
                    f"price {pct(obs.price_change_pct)}  OI {pct(obs.oi_change_pct)}  vol_change={obs.volume_change if obs.volume_change is not None else 'n/a'}  "
                    f"quality={obs.quality.value}"
                )
                add(f"    observation: {obs.price_oi.quadrant.value}")
                add(f"    interpretation (hedged, unconfirmed): {obs.price_oi.conventional_reading}")
        else:
            add("no prior persisted snapshot within the lookback window yet -- this is expected on an early run")
        add("")

        add("DECAY ATTRIBUTION (ATM CE/PE, approximate Taylor-expansion decomposition -- never exact)")
        if self.decay_attributions:
            for da in self.decay_attributions:
                add(
                    f"- {da.right.value} {da.strike}  interval={da.interval}  observed={pct2(da.observed_price_change)}  "
                    f"delta={pct2(da.delta_effect)}  gamma={pct2(da.gamma_effect)}  vega={pct2(da.vega_effect)}  "
                    f"theta={pct2(da.theta_effect)}  residual={pct2(da.residual)}  confidence={da.confidence.value}"
                )
                add(f"    {da.detail}")
        else:
            add("no prior persisted snapshot within the lookback window yet -- this is expected on an early run")
        add("")

        add("UNUSUAL ACTIVITY (vs. real historical baseline only -- never a fabricated average)")
        if self.anomalies:
            for anomaly in self.anomalies:
                add(f"- {anomaly.metric_name}: {anomaly.verdict.value} -- {anomaly.detail}")
        else:
            add("not enough real historical observations yet to assess")
        add("")

        stability_by_level = {id(ls.level): ls for ls in self.level_stability}

        add("SUPPORT")
        if self.support_levels:
            for i, lv in enumerate(self.support_levels, 1):
                stab = stability_by_level.get(id(lv))
                stab_str = f"  [{stab.state.value}: {stab.detail}]" if stab is not None else ""
                add(f"{i}. {lv.strike}  OI={lv.open_interest:,}  ({lv.strength.value}) -- {lv.evidence}{stab_str}")
        else:
            add("none identified")
        add("")

        add("RESISTANCE")
        if self.resistance_levels:
            for i, lv in enumerate(self.resistance_levels, 1):
                stab = stability_by_level.get(id(lv))
                stab_str = f"  [{stab.state.value}: {stab.detail}]" if stab is not None else ""
                add(f"{i}. {lv.strike}  OI={lv.open_interest:,}  ({lv.strength.value}) -- {lv.evidence}{stab_str}")
        else:
            add("none identified")
        add("")

        add("VOLATILITY")
        iv = self.iv_summary
        add(f"ATM IV: {iv.chain_iv:.2f}%" if iv and iv.chain_iv is not None else "ATM IV: n/a")
        add(f"CE IV: {iv.atm_ce_iv:.2f}%" if iv and iv.atm_ce_iv is not None else "CE IV: n/a")
        add(f"PE IV: {iv.atm_pe_iv:.2f}%" if iv and iv.atm_pe_iv is not None else "PE IV: n/a")
        add(f"Skew (CE-PE): {iv.ce_pe_skew:.2f} pts" if iv and iv.ce_pe_skew is not None else "Skew: n/a")
        if self.iv_rank is not None:
            rank_str = f"{self.iv_rank.value:.1f}" if self.iv_rank.value is not None else "UNAVAILABLE"
            add(f"IV Rank: {rank_str}  ({self.iv_rank.detail})")
            add(f"IV history available: {'yes' if self.iv_rank.observations_used > 0 else 'no'} ({self.iv_rank.observations_used} observation(s))")
        add(f"IV trend (vs. most recent persisted observation): {self.iv_trend.value if self.iv_trend else 'UNAVAILABLE'}")
        add("")

        add("LIQUIDITY")
        if self.candidates:
            best = self.candidates[0]
            add(f"Best candidate spread: {best.spread_pct:.2f}%" if best.spread_pct is not None else "Best candidate spread: n/a")
            add(f"OI: {best.open_interest:,}" if best.open_interest is not None else "OI: n/a")
            add(f"Volume: {best.volume:,}" if best.volume is not None else "Volume: n/a")
            add(f"Liquidity grade: {best.liquidity.grade.value}")
        else:
            add("no candidate to assess")
        add("")

        add("EVIDENCE MATRIX")
        if self.matrix is not None:
            for direction in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH, EvidenceDirection.NEUTRAL, EvidenceDirection.UNKNOWN):
                rows = [r for r in self.matrix.rows if r.direction == direction]
                if not rows:
                    continue
                add(f"{direction.value}:")
                for r in rows:
                    add(f"  - [{_evidence_tier(r.group)}] {r.name} ({r.group.value}): {r.detail}")
            add(f"Convergence: {self.matrix.overall_convergence().value}")
            add(
                "Evidence hierarchy (does not change the convergence rule above): PRIMARY = synchronized price "
                "structure/VWAP/trend/relative strength; SECONDARY = futures positioning/OI migration/PCR "
                "context/IV/skew/volume; CONTEXT = global market/sector/news."
            )
        add("")

        viability_by_key = {(dv.strike, dv.right): dv for dv in self.decay_viability}

        add("CANDIDATES")
        if self.candidates:
            for i, c in enumerate(self.candidates[:3], 1):
                add(
                    f"{i}. {c.right.value} {c.strike}  LTP={c.ltp}  bid={c.bid} ask={c.ask} "
                    f"spread={c.spread_pct:.2f}% OI={c.open_interest} chgOI={c.change_in_open_interest} "
                    f"vol={c.volume} IV={c.implied_volatility} delta={c.delta} liquidity={c.liquidity.grade.value}"
                    if c.spread_pct is not None else
                    f"{i}. {c.right.value} {c.strike}  LTP={c.ltp}  liquidity={c.liquidity.grade.value}"
                )
                add(f"   supports: {'; '.join(c.supporting_evidence) or 'none listed'}")
                add(f"   contradicts: {'; '.join(c.contradicting_evidence) or 'none listed'}")
                add(f"   invalidation: {c.invalidation_condition}")
                add(f"   risks: {'; '.join(c.risks)}")
                dv = viability_by_key.get((c.strike, c.right))
                if dv is not None:
                    add(f"   DECAY VIABILITY: {dv.verdict.value}  (component-cost check only -- see note below, NOT overall trade viability)")
                    add(f"      {dv.detail}")
                    theta_pct = f"{dv.theta_to_premium_pct_per_hour:.3f}%/hr" if dv.theta_to_premium_pct_per_hour is not None else "n/a"
                    add(f"      theta/premium: {theta_pct}  (premium ref: {fmt(dv.premium_reference)})")
                    required_move = f"{fmt(dv.required_underlying_move)} pts ({dv.required_underlying_move_pct:.2f}%)" if dv.required_underlying_move is not None and dv.required_underlying_move_pct is not None else "n/a"
                    add(f"      underlying move required to cover modeled decay+spread cost: {required_move}  (NOT the contractual expiry breakeven)")
                    contractual_be = fmt(dv.contractual_expiry_breakeven) if dv.contractual_expiry_breakeven is not None else "n/a"
                    add(f"      contractual expiry breakeven (strike {'+' if c.right.value == 'CE' else '-'} premium): {contractual_be}")
                    iv_up = fmt(dv.iv_up_scenario_premium_effect)
                    iv_down = fmt(dv.iv_down_scenario_premium_effect)
                    add(f"      IV scenario (approx, vega-based): IV+1pt -> {iv_up}  IV-1pt -> {iv_down}")
                    if dv.scenarios:
                        add("      FIRST-ORDER SCENARIO ESTIMATE (not a profit prediction):")
                        for s in dv.scenarios:
                            add(f"        {s.label}: move={fmt(s.underlying_move)}  est. response={fmt(s.estimated_premium_response)}  net after modeled costs={fmt(s.net_after_modeled_costs)}")
                    add(f"      {dv.scope_note}")
        else:
            # Analytical Consistency Audit -- real, traced distinction:
            # `generate_candidates()` returns `[]` IMMEDIATELY when there
            # is no BULLISH/BEARISH bias, before any per-strike liquidity/
            # spread/data-quality check ever runs (candidate_engine.py's
            # own documented behavior) -- the prior wording here ("no
            # candidate passed the minimum bar") was provably inaccurate
            # for that case, implying checks ran and failed when none
            # were attempted at all. This does not change which
            # candidates are generated or any threshold -- only the
            # honesty of the explanation for why none exist.
            bias = self.decision.assessment.market_bias.value if self.decision is not None else None
            if bias not in ("BULLISH", "BEARISH"):
                add(
                    "none -- no directional bias was established (see Market bias below), so no specific-contract "
                    "candidate was generated; this system only builds candidates once a bias exists, by design. "
                    "The REQUESTED CONTRACT section above already assessed this exact contract's own quality "
                    "(liquidity, decay viability, structural quality) independently of that bias."
                )
            else:
                add("none -- a directional bias exists, but no candidate at this strike passed the minimum liquidity/spread/data-quality bar")
        add("")

        add("FINAL ASSESSMENT")
        if self.decision is not None:
            qa = self.decision.assessment
            add(f"Market bias: {qa.market_bias.value}")
            add(f"Setup quality: {qa.setup_quality.value}")
            add(f"Option quality: {qa.option_quality.value}")
            add(f"Liquidity quality: {qa.liquidity_quality.value}")
            add(f"Risk quality: {qa.risk_quality.value}")
            add(f"Data quality: {qa.data_quality.value}")
            evidence_note = "" if qa.market_bias.value in ("BULLISH", "BEARISH") else "  (no decided bias -- see the real per-row evidence above for the actual bullish/bearish split)"
            add(f"Evidence: {qa.supporting_evidence_count} supporting / {qa.conflicting_evidence_count} conflicting{evidence_note}")
        add("")

        lines.extend(self._render_market_context())

        add("RESEARCH STATE:")
        add(self.research_state.value if self.research_state is not None else "UNKNOWN")
        add("")
        add("COMPATIBILITY DECISION (not a buy/sell instruction):")
        add(self.decision.decision.value if self.decision is not None else FinalDecision.DATA_INSUFFICIENT.value)
        add("")
        add("WHY:")
        add(self.decision.reasoning if self.decision is not None else "no decision could be computed")
        add("")

        # Sprint 7A, Objective 13 -- the structured "why no trade"
        # explanation, only for the decisions where a single sentence
        # genuinely isn't enough (NO_TRADE/DATA_INSUFFICIENT).
        no_trade_explanation = None
        if self.decision is not None and self.decision.decision in (FinalDecision.NO_TRADE, FinalDecision.DATA_INSUFFICIENT):
            no_trade_explanation = build_no_trade_explanation(self)
            if no_trade_explanation is not None:
                lines.extend(_render_no_trade_explanation(no_trade_explanation))
                lines.extend(_render_no_trade_reason_recheck(no_trade_explanation))

        # Sprint 7B, Objectives 13/14/15 -- the 5-standard-questions
        # explanation, for EVERY decision (not only no-trade).
        final_explanation = build_final_decision_explanation(self)
        if final_explanation is not None:
            lines.extend(_render_final_decision_explanation(final_explanation))

        add("INVALIDATION:")
        add(self.candidates[0].invalidation_condition if self.candidates else "n/a -- no active candidate")
        add("")

        add("RISKS:")
        add("; ".join(self.candidates[0].risks) if self.candidates else "n/a -- no active candidate")
        add("")

        add("DATA WARNINGS:")
        if self.data_warnings:
            for w in self.data_warnings:
                add(f"- {w}")
        else:
            add("none")
        add("")

        add("LATENCY:")
        for sl in self.stage_latencies:
            add(f"  {sl.stage}: {sl.seconds * 1000:.0f}ms")
        add(f"  TOTAL: {self.total_latency_seconds:.2f}s")
        add("=" * 40)

        return "\n".join(lines)

    def render_compact(self) -> str:
        """A short, glanceable 9-section report — the same underlying,
        already-validated data as `render_text()`, restructured for a
        human to read in seconds rather than for full audit depth.
        Anything this codebase has no legitimate real basis for (news,
        event risk, IV-trend/term-structure without persisted history,
        etc.) is shown as `UNKNOWN`/`INSUFFICIENT_DATA`/"NOT AVAILABLE
        FROM CURRENT AUTHORIZED DATA SOURCES" here exactly as it is
        everywhere else in this codebase — a compact layout is never a
        license to fabricate a value `render_text()` would have left
        honestly blank.
        """
        if self.error is not None:
            return f"{self.symbol} -- UNAVAILABLE: {self.error}\n\nFINAL DECISION:\nDATA_INSUFFICIENT"

        lines: list[str] = []
        add = lines.append
        ist_now = to_ist(self.generated_at).strftime("%H:%M:%S")
        age = f"{self.data_age_seconds:.1f} sec" if self.data_age_seconds is not None else "n/a"
        freshness = self.freshness_label.value if self.freshness_label is not None else self.market_status_label

        add(f"{self.symbol} — OPTIONS INTELLIGENCE")
        add(f"Time: {ist_now} IST")
        add(f"Data age: {age}")
        add(f"Market: {freshness}")
        add(f"Research state: {self.research_state.value if self.research_state is not None else 'UNKNOWN'}")
        add("")

        a = self.analysis
        add("-" * 30)
        add("1. MARKET DIRECTION")
        add("-" * 30)
        if not self.candles_are_current:
            add("Trend:             WITHHELD (M15 STALE)")
            add("VWAP:              WITHHELD (ROLLING_INTRADAY_VWAP, not session VWAP)")
            add("EMA 9/21/50:       WITHHELD")
        else:
            add(f"Trend:             {self.regime.regime.value if self.regime else 'UNKNOWN'}")
            add(f"VWAP:              {a.price_vs_vwap.value if a and a.price_vs_vwap else 'UNAVAILABLE'} (rolling M15 series, not session VWAP)")
            add(f"EMA 9/21/50:       {a.ema_alignment.value if a and a.ema_alignment else 'UNAVAILABLE'}")
        add(f"Momentum:          {_momentum_label(self.rsi.value if self.rsi else None)}")
        add(f"RSI:               {self.rsi.value:.1f}" if self.rsi and self.rsi.value is not None else "RSI:               n/a")
        # Sprint 7A, Objective 14 -- context only; never implies a
        # reversal by itself (see `_rsi_zone_label()`'s own docstring).
        add(f"RSI zone (context only, not a reversal signal): {_rsi_zone_label(self.rsi.value if self.rsi else None)}")
        add(f"ATR:               {_atr_label(self.regime)}")
        trend_quality = self.decision.assessment.setup_quality.value if self.decision else "UNKNOWN"
        add(f"Trend quality:     {trend_quality}")
        add("")

        add("-" * 30)
        add("2. SUPPORT / RESISTANCE")
        add("-" * 30)
        stability_by_strike = {ls.level.strike: ls.state.value for ls in self.level_stability}
        for i, lv in enumerate(self.support_levels[:2], 1):
            dist = f"{lv.distance_from_spot_pct:.2f}%" if lv.distance_from_spot_pct is not None else "n/a"
            add(f"Support {i}:         Rs.{lv.strike}  stability={stability_by_strike.get(lv.strike, 'UNCONFIRMED')}  distance={dist}")
        if not self.support_levels:
            add("Support:           none identified")
        for i, lv in enumerate(self.resistance_levels[:2], 1):
            dist = f"{lv.distance_from_spot_pct:.2f}%" if lv.distance_from_spot_pct is not None else "n/a"
            add(f"Resistance {i}:      Rs.{lv.strike}  stability={stability_by_strike.get(lv.strike, 'UNCONFIRMED')}  distance={dist}")
        if not self.resistance_levels:
            add("Resistance:        none identified")
        add("")

        add("-" * 30)
        add("3. OPTIONS CHAIN")
        add("-" * 30)
        add(f"ATM:               {self.atm_strike if self.atm_strike is not None else 'n/a'}")
        add(f"PCR:               {self.pcr_oi:.2f}" if self.pcr_oi is not None else "PCR:               n/a")
        add(f"Call OI:           {self.total_call_oi:,}" if self.total_call_oi is not None else "Call OI:           n/a")
        add(f"Put OI:            {self.total_put_oi:,}" if self.total_put_oi is not None else "Put OI:            n/a")
        ce_temporal = next((t for t in self.temporal_observations if t.right.value == "CE"), None)
        pe_temporal = next((t for t in self.temporal_observations if t.right.value == "PE"), None)
        add(f"Call OI change:    {ce_temporal.price_oi.conventional_reading if ce_temporal else 'INSUFFICIENT_DATA (no prior persisted snapshot yet)'}")
        add(f"Put OI change:     {pe_temporal.price_oi.conventional_reading if pe_temporal else 'INSUFFICIENT_DATA (no prior persisted snapshot yet)'}")
        oi_row = self._matrix_row("Call/Put OI structure")
        add(f"OI interpretation: {oi_row.direction.value if oi_row else 'UNKNOWN'}  ({oi_row.detail if oi_row else 'n/a'})")
        add("")

        lines.extend(render_requested_contract_section(self.requested_contract))

        add("-" * 30)
        add("4. IV / VOLATILITY")
        add("-" * 30)
        iv = self.iv_summary
        add(f"ATM IV:            {iv.chain_iv:.2f}%" if iv and iv.chain_iv is not None else "ATM IV:            n/a")
        rank_str = f"{self.iv_rank.value:.1f}" if self.iv_rank and self.iv_rank.value is not None else "UNAVAILABLE"
        add(f"IV percentile:     {rank_str}")
        add(f"IV trend:          {self.iv_trend.value if self.iv_trend else 'UNKNOWN'}")
        add(f"Skew (CE-PE):      {iv.ce_pe_skew:.2f} pts" if iv and iv.ce_pe_skew is not None else "Skew:              n/a")
        if self.term_structure is not None:
            slope = self.term_structure.iv_slope()
            add(f"Term structure:    {len(self.term_structure.expiries)} expiries compared, slope={slope:.2f} pts" if slope is not None else f"Term structure:    {len(self.term_structure.expiries)} expiries compared, slope n/a")
        else:
            add("Term structure:    not compared this run")
        add(f"Volatility regime: {_volatility_regime_label(self.iv_trend)}")
        add("")

        add("-" * 30)
        add("5. DECAY")
        add("-" * 30)
        dv = self.decay_viability[0] if self.decay_viability else None
        if dv is not None:
            add(f"Candidate:         {dv.right.value} {dv.strike}")
            add(f"DTE:               {dv.dte_days}" if dv.dte_days is not None else "DTE:               n/a")
            add(f"Theta/hr:          {fmt(dv.theta_decay_per_trading_hour)}")
            theta_pct = f"{dv.theta_to_premium_pct_per_hour:.3f}%/hr" if dv.theta_to_premium_pct_per_hour is not None else "n/a"
            add(f"Theta/premium/hr:  {theta_pct}")
            required_move = f"{fmt(dv.required_underlying_move)} pts ({dv.required_underlying_move_pct:.2f}%)" if dv.required_underlying_move is not None and dv.required_underlying_move_pct is not None else "n/a"
            add(f"Required move (covers modeled cost, NOT contractual breakeven): {required_move}")
            add(f"Contractual expiry breakeven: {fmt(dv.contractual_expiry_breakeven)}" if dv.contractual_expiry_breakeven is not None else "Contractual expiry breakeven: n/a")
            add(f"Expected 1SD move: {fmt(dv.expected_move_over_horizon)} pts over {dv.holding_horizon_hours}h" if dv.expected_move_over_horizon is not None else "Expected 1SD move: n/a")
            add(f"Decay viability:   {dv.verdict.value}")
            add(f"Decay warning:     {_decay_warning_label(dv.verdict.value)}")
            add(f"  ({dv.scope_note})")
        else:
            add("no active candidate -- decay not assessed")
        add("")

        add("-" * 30)
        add("6. GLOBAL CONTEXT")
        add("-" * 30)
        if self.global_context is not None:
            for gi in self.global_context.inputs:
                change_str = f"{gi.day_change_pct:+.2f}%" if gi.day_change_pct is not None else "NOT AVAILABLE"
                add(f"{gi.label:16s} {change_str}")
            add(f"Global influence:  {self.global_context.verdict.value}")
        else:
            add("not assessed this run")
        add("")

        lines.extend(render_news_section(self.news_items, self.news_fetch_error))
        lines.extend(render_directional_analysis_section(self))

        add("-" * 30)
        add("8. EVIDENCE CONVERGENCE")
        add("-" * 30)
        if self.matrix is not None:
            price_row = self._matrix_row("M15 trend")
            sr_support = self._matrix_row("Support")
            sr_resistance = self._matrix_row("Resistance")
            iv_row = self._matrix_row("IV")
            global_row = self._matrix_row("Global context")
            add(f"PRICE ACTION       -> {price_row.direction.value if price_row else 'UNKNOWN'}")
            add(f"OPTIONS OI         -> {oi_row.direction.value if oi_row else 'UNKNOWN'}")
            add(f"IV                 -> {iv_row.direction.value if iv_row else 'UNKNOWN'}")
            add(f"GLOBAL             -> {global_row.direction.value if global_row else 'UNKNOWN'}")
            sr_label = sr_support.direction.value if sr_support else (sr_resistance.direction.value if sr_resistance else "UNKNOWN")
            add(f"S/R                -> {sr_label}")
            add(f"DECAY              -> {dv.verdict.value if dv else 'INSUFFICIENT_DATA'}  (informational only -- never part of convergence)")
            news_row = self._matrix_row("News/Events")
            add(f"NEWS               -> {news_row.direction.value if news_row else 'UNKNOWN'}  (never directional by construction -- see news_row detail below)")
            add("")
            add(f"CONVERGENCE:       {self.matrix.overall_convergence().value}")
        else:
            add("not computed this run")
        add("")

        add("-" * 30)
        add("ADVERSARIAL ANALYSIS")
        add("-" * 30)
        aa = self.adversarial_analysis
        if aa is not None:
            add("BULL CASE:")
            for line in aa.bull_case:
                add(f"  + {line}")
            if not aa.bull_case:
                add("  (none)")
            add("BEAR CASE:")
            for line in aa.bear_case:
                add(f"  - {line}")
            if not aa.bear_case:
                add("  (none)")
            add("CONTRADICTIONS:")
            for line in aa.contradictions:
                add(f"  ! {line}")
            if not aa.contradictions:
                add("  (none)")
            add("MISSING DATA:")
            for line in aa.missing_data:
                add(f"  ? {line}")
            if not aa.missing_data:
                add("  (none)")
            add("KEY RISKS:")
            for line in aa.key_risks:
                add(f"  * {line}")
            if not aa.key_risks:
                add("  (none identified)")
            add(f"OPPOSITE CASE EQUALLY SUPPORTED: {'YES -- prefer NO_TRADE' if aa.opposite_case_is_equally_supported else 'no'}")
        else:
            add("not computed this run")
        add("")

        add("-" * 30)
        add("QUALITY SUMMARY")
        add("-" * 30)
        qt = self.quality_tiers
        add(f"DATA QUALITY:      {qt.data_quality.value if qt else 'UNKNOWN'}")
        add(f"EVIDENCE QUALITY:  {qt.evidence_quality.value if qt else 'UNKNOWN'}")
        add(f"DECISION QUALITY:  {qt.decision_quality.value if qt else 'UNKNOWN'}")
        add("")
        lines.extend(self._render_market_context())

        add("-" * 30)
        add("9. RESEARCH STATE / COMPATIBILITY DECISION")
        add("-" * 30)
        add(f"Research state: {self.research_state.value if self.research_state is not None else 'UNKNOWN'}")
        add("Compatibility decision (historical TRADEABLE vocabulary, not a buy instruction):")
        if self.decision is not None:
            bias = self.decision.assessment.market_bias
            ce_verdict = self.decision.decision.value if bias == EvidenceDirection.BULLISH else "AVOID"
            pe_verdict = self.decision.decision.value if bias == EvidenceDirection.BEARISH else "AVOID"
            # Final Pre-Freeze Audit, Issue 2 -- "AVOID" here means only
            # "the current directional bias does not favor this side," not
            # "this contract itself is poor." When the contract's own
            # structural quality was already computed (`direction_comparison`,
            # built for every analysis that has option data, anchored at the
            # requested strike or ATM), append it so a healthy-but-
            # unconfirmed contract never reads identically to a genuinely
            # bad one in this compact report -- same real field the Quick
            # View STATE badge already reads, never a new computation.
            dc = self.direction_comparison
            ce_quality = classify_contract_structural_quality(dc.ce_assessment).value if dc is not None and dc.ce_assessment is not None else None
            pe_quality = classify_contract_structural_quality(dc.pe_assessment).value if dc is not None and dc.pe_assessment is not None else None
            ce_note = f" (contract structural quality: {ce_quality} -- direction not currently confirmed, not a contract-quality rejection)" if ce_verdict == "AVOID" and ce_quality is not None else ""
            pe_note = f" (contract structural quality: {pe_quality} -- direction not currently confirmed, not a contract-quality rejection)" if pe_verdict == "AVOID" and pe_quality is not None else ""
            add(f"CE:       {ce_verdict}{ce_note}")
            add(f"PE:       {pe_verdict}{pe_note}")
            preferred = self.decision.decision in (FinalDecision.NO_TRADE, FinalDecision.DATA_INSUFFICIENT)
            add(f"NO TRADE: {'PREFERRED' if preferred else 'not preferred'}")
            add("")
            add("Reason:")
            add(self.decision.reasoning)
            add("")
            add("DO NOT TRADE" if preferred else self.decision.decision.value)
        else:
            add("CE:       AVOID")
            add("PE:       AVOID")
            add("NO TRADE: PREFERRED")
            add("")
            add("Reason:")
            add("no decision could be computed")
            add("")
            add("DO NOT TRADE")

        return "\n".join(lines)

    def _matrix_row(self, name: str) -> EvidenceRow | None:
        if self.matrix is None:
            return None
        return next((r for r in self.matrix.rows if r.name == name), None)


def fmt(value: Decimal | None) -> str:
    """Display-only rounding to 2 decimal places for `render_compact()` —
    see `render_text()`'s own nested `fmt()` for why some real values
    passing through here can legitimately carry excess Decimal precision;
    same rationale, module-level so `render_compact()` can share it.
    """
    return "n/a" if value is None else f"{value:.2f}"


def _momentum_label(rsi_value: Decimal | None) -> str:
    """HEURISTIC, illustrative-only bands (not empirically calibrated):
    |RSI-50| >= 20 -> STRONG, >= 10 -> MODERATE, else WEAK.
    """
    if rsi_value is None:
        return "UNKNOWN"
    distance = abs(rsi_value - Decimal(50))
    if distance >= Decimal(20):
        return "STRONG"
    if distance >= Decimal(10):
        return "MODERATE"
    return "WEAK"


# Sprint 7A, Objective 12 -- evidence hierarchy. NEVER changes the
# existing deterministic convergence/scoring rule (`EvidenceMatrix.
# overall_convergence()` is completely unmodified) -- purely a label so
# the explanation can tell a reader which evidence is primary vs
# contextual. LIQUIDITY/DATA_QUALITY are never directional at all (see
# `row_liquidity()`/`row_data_freshness()`), so they carry no tier.
_EVIDENCE_TIER: dict[EvidenceGroup, str] = {
    EvidenceGroup.UNDERLYING_PRICE_STRUCTURE: "PRIMARY",
    EvidenceGroup.RELATIVE_STRENGTH: "PRIMARY",
    EvidenceGroup.FUTURES: "SECONDARY",
    EvidenceGroup.OPTIONS_OI: "SECONDARY",
    EvidenceGroup.OPTIONS_IV: "SECONDARY",
    EvidenceGroup.GLOBAL: "CONTEXT",
    EvidenceGroup.NEWS_EVENT: "CONTEXT",
    EvidenceGroup.LIQUIDITY: "N/A",
    EvidenceGroup.DATA_QUALITY: "N/A",
}


def _evidence_tier(group: EvidenceGroup) -> str:
    return _EVIDENCE_TIER.get(group, "N/A")


def _rsi_zone_label(rsi_value: Decimal | None, *, oversold_below: Decimal = Decimal(30), overbought_above: Decimal = Decimal(70)) -> str:
    """Sprint 7A, Objective 14 -- context only, NEVER directional
    evidence: RSI has no evidence-matrix row anywhere in this codebase
    (confirmed by inspection -- `report.rsi` feeds only display text),
    so "OVERSOLD"/"OVERBOUGHT" here can never automatically imply a
    reversal read; a real price-structure/confluence signal is always
    required for that (see `evidence_matrix.row_m15_trend()`/
    `row_vwap()`). 30/70 are the conventional textbook RSI bands, not
    empirically validated by this system.
    """
    if rsi_value is None:
        return "UNKNOWN"
    if rsi_value < oversold_below:
        return "OVERSOLD"
    if rsi_value > overbought_above:
        return "OVERBOUGHT"
    return "NEUTRAL"


def _atr_label(regime: MarketRegimeResult | None) -> str:
    if regime is None:
        return "UNKNOWN"
    if regime.regime.value == "HIGH_VOLATILITY":
        return "HIGH"
    if regime.regime.value == "LOW_VOLATILITY":
        return "LOW"
    if regime.atr_pct_of_price is not None:
        return "NORMAL"
    return "UNKNOWN"


def _volatility_regime_label(iv_trend: IvTrend | None) -> str:
    if iv_trend is None or iv_trend == IvTrend.INSUFFICIENT_HISTORY:
        return "UNKNOWN"
    return {IvTrend.RISING: "EXPANDING", IvTrend.FALLING: "CONTRACTING", IvTrend.STABLE: "NORMAL"}[iv_trend]


def _decay_warning_label(verdict_value: str) -> str:
    return {
        "DECAY_FAVORABLE": "LOW", "DECAY_ACCEPTABLE": "LOW", "DECAY_HEADWIND": "MEDIUM",
        "DECAY_UNFAVORABLE": "HIGH", "INSUFFICIENT_DATA": "UNKNOWN",
    }.get(verdict_value, "UNKNOWN")


def _contract_assessment_lines(assessment: ContractAssessment) -> list[str]:
    """Sprint 2's per-contract fact sheet — every field is either a real
    value from `assess_contract()` or the honest `n/a`/`UNKNOWN` this
    codebase uses everywhere else for missing data, never a guess.
    """
    lines: list[str] = []
    add = lines.append
    add(f"Contract:          {assessment.right.value} {assessment.strike}")
    add(f"Moneyness:         {assessment.moneyness.value if assessment.moneyness else 'n/a'}")
    add(f"LTP:               {fmt(assessment.ltp)}")
    # Sprint 7A, Objective 9 -- real premium decomposition (see
    # contract_analysis._decompose_premium()'s own docstring).
    if assessment.intrinsic_value is not None and assessment.time_value is not None:
        add(f"Intrinsic/Time value: {fmt(assessment.intrinsic_value)} / {fmt(assessment.time_value)}")
    else:
        add("Intrinsic/Time value: n/a")
    add(f"Bid/Ask:           {fmt(assessment.bid)} / {fmt(assessment.ask)}  (spread {assessment.spread_pct:.2f}%)" if assessment.spread_pct is not None else f"Bid/Ask:           {fmt(assessment.bid)} / {fmt(assessment.ask)}")
    add(f"OI / Change OI:    {assessment.open_interest if assessment.open_interest is not None else 'n/a'} / {assessment.change_in_open_interest if assessment.change_in_open_interest is not None else 'n/a'}")
    add(f"Volume:            {assessment.volume if assessment.volume is not None else 'n/a'}")
    add(f"IV:                {assessment.implied_volatility:.2f}%" if assessment.implied_volatility is not None else "IV:                n/a")
    add(f"Delta/Gamma:       {fmt(assessment.delta)} / {fmt(assessment.gamma)}")
    add(f"Theta/Vega:        {fmt(assessment.theta)} / {fmt(assessment.vega)}")
    add(f"Distance to S/R:   support={assessment.distance_to_support_pct:.2f}%  resistance={assessment.distance_to_resistance_pct:.2f}%" if assessment.distance_to_support_pct is not None and assessment.distance_to_resistance_pct is not None else "Distance to S/R:   n/a")
    add(f"Liquidity:         {assessment.liquidity.grade.value.upper()}")
    if assessment.decay_viability is not None:
        add(f"Decay viability:   {assessment.decay_viability.verdict.value}  (ratio {assessment.decay_viability.viability_ratio:.2f})" if assessment.decay_viability.viability_ratio is not None else f"Decay viability:   {assessment.decay_viability.verdict.value}")
    else:
        add("Decay viability:   n/a")
    structural = classify_contract_structural_quality(assessment)
    add(f"Structural quality: {structural.value}")
    if assessment.data_quality_notes:
        add(f"Data quality notes: {'; '.join(assessment.data_quality_notes)}")
    return lines


def render_requested_contract_section(comparison: ContractComparison | None) -> list[str]:
    """Shared by `render_compact()` and `render_text()` — the exact same
    Sprint-2 requested-contract/alternatives content either way, so the
    two report formats can never silently disagree with each other.
    """
    lines: list[str] = []
    add = lines.append

    add("-" * 30)
    add("REQUESTED CONTRACT")
    add("-" * 30)
    if comparison is None:
        add("no specific contract was requested this run")
    elif comparison.requested is None:
        add(f"REQUESTED: {comparison.requested_right.value} {comparison.requested_strike}")
        add(comparison.requested_not_in_chain_detail or "INSUFFICIENT_DATA")
    else:
        lines.extend(_contract_assessment_lines(comparison.requested))
        # Sprint 7A, Objective 9 -- the critical product differentiator:
        # a correct underlying direction does not, by itself, guarantee a
        # good option outcome.
        add(
            "NOTE: a correct underlying direction does not by itself guarantee a good option outcome -- theta decay, "
            "IV contraction, insufficient underlying movement, the bid/ask spread, or simply the wrong expiry/strike "
            "can all still produce a poor result even when the direction call was right."
        )
    add("")

    add("-" * 30)
    add("CONTRACT ALTERNATIVES")
    add("-" * 30)
    if comparison is None or comparison.requested is None:
        add("not applicable")
    elif not comparison.alternatives:
        add("no alternative strikes were available for comparison")
    else:
        for alt in comparison.alternatives:
            decay_label = alt.decay_viability.verdict.value if alt.decay_viability is not None else "n/a"
            add(f"{alt.right.value} {alt.strike}: LTP={fmt(alt.ltp)}  liquidity={alt.liquidity.grade.value.upper()}  decay={decay_label}")
        preference, detail = summarize_contract_preference(comparison)
        add("")
        add(f"PREFERENCE: {preference.value}")
        add(detail)
    add("")
    return lines


def render_news_section(items: list[NewsItem], fetch_error: str | None) -> list[str]:
    """Sprint 4, Part N — real, first-party news (Upstox `/v2/news`, see
    `docs/data-sources/PROVIDER_DECISION.md`) when available, the honest
    unavailable state otherwise. `direction`/`event risk` are always
    `UNKNOWN` -- this system has no legitimate sentiment/event-classification
    capability and never fabricates one (see `app.domain.news.models`).
    """
    lines: list[str] = []
    add = lines.append
    add("-" * 30)
    add("NEWS / EVENTS")
    add("-" * 30)
    if fetch_error is not None:
        add(f"NEWS/EVENT DATA: fetch failed ({fetch_error})")
    elif not items:
        add("NEWS/EVENT DATA: NOT AVAILABLE FROM CURRENT AUTHORIZED DATA SOURCES (no recent company news found)")
    else:
        for item in items[:5]:
            add(f"- [{item.published_at.isoformat()}] {item.title}")
            add(f"    source={item.source}  relevance={item.relevance.value}  direction=UNKNOWN  event_risk=UNKNOWN  evidence_quality={item.evidence_quality.value}")
            if item.url:
                add(f"    {item.url}")
    add("Sector/macro news beyond the underlying's own headlines: NOT AVAILABLE FROM CURRENT AUTHORIZED DATA SOURCES")
    add("")
    return lines


def _contract_view_lines(label: str, assessment: ContractAssessment | None) -> list[str]:
    if assessment is None:
        return [f"{label}: n/a"]
    dv = assessment.decay_viability
    return [
        f"{label}: {assessment.right.value} {assessment.strike}  LTP={fmt(assessment.ltp)}  liquidity={assessment.liquidity.grade.value.upper()}",
        f"  IV={fmt(assessment.implied_volatility)}%  delta={fmt(assessment.delta)}  theta={fmt(assessment.theta)}  gamma={fmt(assessment.gamma)}  vega={fmt(assessment.vega)}",
        (
            f"  required_move={fmt(dv.required_underlying_move) if dv else 'n/a'} pts (covers modeled cost, NOT breakeven)  "
            f"contractual_breakeven={fmt(dv.contractual_expiry_breakeven) if dv else 'n/a'}  "
            f"expected_move={fmt(dv.expected_move_over_horizon) if dv else 'n/a'} pts  decay={dv.verdict.value if dv else 'n/a'}"
        ),
    ]


def render_directional_analysis_section(report: OptionsIntelligenceReport) -> list[str]:
    """Sprint 4, Parts D-K — the CE-vs-PE, direction-neutral view. Never
    computes anything: every value is read from `report.direction_
    comparison`, already built by `direction_analysis.build_direction_
    comparison()`.
    """
    lines: list[str] = []
    add = lines.append
    dc = report.direction_comparison

    add("-" * 30)
    add("DIRECTIONAL ANALYSIS (CE vs PE)")
    add("-" * 30)
    if dc is None:
        add("not computed this run (no reference strike available)")
        add("")
        return lines

    add(f"Reference strike: {dc.reference_strike}")
    add("")
    add("CE / BULLISH")
    add(f"  Underlying evidence: {dc.bullish_supporting_groups} independent group(s) bullish")
    lines.extend(f"  {line}" for line in _contract_view_lines("Contract", dc.ce_assessment))
    if dc.ce_decay is not None:
        add(f"  {dc.ce_decay.theta_cost_statement}")
        add(f"  {dc.ce_decay.move_coverage_label} ({dc.ce_decay.move_coverage_detail})")
    add(f"  {dc.ce_hedge_context}")
    add("")
    add("PE / BEARISH")
    add(f"  Underlying evidence: {dc.bearish_supporting_groups} independent group(s) bearish")
    lines.extend(f"  {line}" for line in _contract_view_lines("Contract", dc.pe_assessment))
    if dc.pe_decay is not None:
        add(f"  {dc.pe_decay.theta_cost_statement}")
        add(f"  {dc.pe_decay.move_coverage_label} ({dc.pe_decay.move_coverage_detail})")
    add(f"  {dc.pe_hedge_context}")
    add("")

    add(f"CONFLICTING GROUPS: {dc.conflicting_groups}")
    add(f"VERDICT: {dc.verdict.value}")
    add(dc.verdict_detail)
    if dc.no_defensible_direction:
        add("NO DEFENSIBLE DIRECTION -- neither side is preferred by the evidence")
    elif dc.preferred_contract is not None:
        add(f"PREFERRED: {dc.preferred_direction.value if dc.preferred_direction else 'n/a'} -- {dc.preferred_contract.right.value} {dc.preferred_contract.strike}")
    add("")

    add("SUPPORT/RESISTANCE PATHS")
    add("  BULLISH PATH: " + " -> ".join(f"{s.label}={fmt(s.price)}" + (f" ({s.stability})" if s.stability else "") for s in dc.paths.bullish_path))
    add("  BEARISH PATH: " + " -> ".join(f"{s.label}={fmt(s.price)}" + (f" ({s.stability})" if s.stability else "") for s in dc.paths.bearish_path))
    add("")

    add("WHY CE COULD WORK: " + ("; ".join(dc.ce_case.could_work) or "(none)"))
    add("WHY CE COULD FAIL: " + ("; ".join(dc.ce_case.could_fail) or "(none)"))
    add("WHY PE COULD WORK: " + ("; ".join(dc.pe_case.could_work) or "(none)"))
    add("WHY PE COULD FAIL: " + ("; ".join(dc.pe_case.could_fail) or "(none)"))
    add("")
    return lines


# ============================================================
# Sprint 7A, Objective 13 -- "WHY NO TRADE" structured explanation.
#
# Purely a SYNTHESIS/presentation layer over fields the pipeline already
# computed (evidence-matrix rows, the quality assessment, chain-quality
# issues, candidates) -- no new computation, no new fetch, and this
# NEVER feeds back into `decide()`/ranking; it only explains a decision
# that has already been made. Informational only.
# ============================================================


@dataclass(frozen=True)
class NoTradeExplanation:
    what_we_know: list[str]
    what_supports_bullish: list[str]
    what_supports_bearish: list[str]
    what_is_unreliable: list[str]
    decisive_missing_confirmation: list[str]
    what_would_change_the_state: list[str]
    why_option_not_yet_confirmed: list[str]


def build_no_trade_explanation(report: OptionsIntelligenceReport) -> NoTradeExplanation | None:
    """`None` only when there is genuinely no real matrix/decision to
    explain (a failed analysis) -- never a fabricated explanation."""
    if report.matrix is None or report.decision is None:
        return None
    matrix = report.matrix
    assessment = report.decision.assessment

    what_we_know = [
        f"[{_evidence_tier(r.group)}] {r.name}: {r.direction.value} -- {r.detail}"
        for r in matrix.rows if r.direction != EvidenceDirection.UNKNOWN
    ]
    what_supports_bullish = [f"{r.name}: {r.detail}" for r in matrix.rows if r.direction == EvidenceDirection.BULLISH]
    what_supports_bearish = [f"{r.name}: {r.detail}" for r in matrix.rows if r.direction == EvidenceDirection.BEARISH]
    what_is_unreliable = [f"{r.name}: {r.detail}" for r in matrix.rows if r.direction == EvidenceDirection.UNKNOWN]
    what_is_unreliable.extend(f"chain quality: {i.kind.value} -- {i.detail}" for i in report.chain_quality_issues)

    decisive_missing_confirmation: list[str] = []
    what_would_change_the_state: list[str] = []
    if assessment.data_quality == QualityLevel.INSUFFICIENT:
        decisive_missing_confirmation.append("data quality itself is insufficient to reason about this instrument right now")
        what_would_change_the_state.append(
            "the underlying data-state/chain-quality/price-consistency issue driving INSUFFICIENT data quality would need to resolve"
        )
    elif assessment.convergence == OverallConvergence.CONFLICT:
        decisive_missing_confirmation.append("the real evidence groups genuinely disagree with each other (CONFLICT) -- no coherent bias exists to act on")
        what_would_change_the_state.append("the conflicting evidence groups (see WHAT SUPPORTS BULLISH/BEARISH above) would need to resolve toward agreement on one direction")
    elif assessment.convergence == OverallConvergence.INSUFFICIENT_EVIDENCE:
        decisive_missing_confirmation.append("too few real evidence groups produced a directional read at all (INSUFFICIENT_EVIDENCE)")
        what_would_change_the_state.append("more of the evidence groups listed under WHAT IS UNRELIABLE above would need to produce a genuine BULLISH or BEARISH read")
    elif not report.candidates:
        decisive_missing_confirmation.append("a real directional bias exists, but no candidate strike cleared the minimum liquidity/data-quality bar")
        what_would_change_the_state.append("a real candidate strike clearing the liquidity/spread/data-quality bar would need to appear on a later real chain")
    else:
        decisive_missing_confirmation.append(report.decision.reasoning)
        what_would_change_the_state.append("the specific quality dimension(s) named in WHY above would need to improve to STRONG")

    why_option_not_yet_confirmed: list[str] = []
    if report.candidates:
        c = report.candidates[0]
        why_option_not_yet_confirmed.append(f"selected candidate {c.right.value} {c.strike}: liquidity={c.liquidity.grade.value}")
        dv = next((d for d in report.decay_viability if d.strike == c.strike and d.right == c.right), None)
        if dv is not None:
            why_option_not_yet_confirmed.append(f"decay viability: {dv.verdict.value} ({dv.scope_note})")
    else:
        why_option_not_yet_confirmed.append("no candidate contract currently exists to evaluate -- see DECISIVE MISSING CONFIRMATION above")

    return NoTradeExplanation(
        what_we_know=what_we_know, what_supports_bullish=what_supports_bullish, what_supports_bearish=what_supports_bearish,
        what_is_unreliable=what_is_unreliable, decisive_missing_confirmation=decisive_missing_confirmation,
        what_would_change_the_state=what_would_change_the_state, why_option_not_yet_confirmed=why_option_not_yet_confirmed,
    )


def _render_no_trade_explanation(explanation: NoTradeExplanation) -> list[str]:
    lines: list[str] = []
    add = lines.append

    def section(title: str, items: list[str]) -> None:
        add(title + ":")
        if items:
            for item in items:
                add(f"  - {item}")
        else:
            add("  (none)")
        add("")

    add("-" * 30)
    add("WHY NO TRADE -- STRUCTURED EXPLANATION (informational only)")
    add("-" * 30)
    section("WHAT WE KNOW", explanation.what_we_know)
    section("WHAT SUPPORTS BULLISH", explanation.what_supports_bullish)
    section("WHAT SUPPORTS BEARISH", explanation.what_supports_bearish)
    section("WHAT IS UNRELIABLE", explanation.what_is_unreliable)
    section("DECISIVE MISSING CONFIRMATION", explanation.decisive_missing_confirmation)
    section("WHAT WOULD CHANGE THE STATE", explanation.what_would_change_the_state)
    section("WHY THE OPTION ITSELF IS NOT YET ATTRACTIVE/CONFIRMED", explanation.why_option_not_yet_confirmed)
    return lines


# ============================================================
# Sprint 7B, Objectives 13/14/15 -- every analysis, not just NO_TRADE,
# answers the same 5 standard questions, PLUS (when a candidate exists)
# the DIRECTION -> OPTION SIDE -> CONTRACT FIT -> RISK -> INVALIDATION ->
# RECHECK CONDITION chain. Purely a synthesis/presentation layer over
# fields the pipeline already computed (evidence-matrix rows,
# market_regime_context, transmission_notes, candidates) -- no new
# computation, no new fetch, never feeds back into decide()/ranking.
# Auditability (Objective 15): every bullet below is a direct
# `{name}: {direction} -- {detail}` render of a real EvidenceRow, or a
# real field off `report` -- never a bare label with no traceable source.
# ============================================================

_OPTIONS_MARKET_GROUPS = (EvidenceGroup.OPTIONS_OI, EvidenceGroup.OPTIONS_IV, EvidenceGroup.FUTURES, EvidenceGroup.LIQUIDITY)


@dataclass(frozen=True)
class FinalDecisionExplanation:
    what_is_the_market_doing: list[str]
    what_is_the_stock_doing_relative_to_market: list[str]
    what_is_the_options_market_showing: list[str]
    what_can_invalidate_this: list[str]
    what_data_is_missing: list[str]
    # Populated only when a real candidate exists.
    direction: str | None
    option_side: str | None
    contract_fit: str | None
    risk: str | None
    invalidation: str | None
    recheck_condition: str | None


def build_final_decision_explanation(report: OptionsIntelligenceReport) -> FinalDecisionExplanation | None:
    """`None` only when there is no real matrix/decision to explain at
    all (a failed analysis) -- never a fabricated explanation."""
    if report.matrix is None or report.decision is None:
        return None
    matrix = report.matrix

    # Q1 -- what is the market doing (macro regime, reused verbatim).
    if report.market_regime_context is not None:
        mrc = report.market_regime_context
        what_is_market_doing = [f"Macro regime: {mrc.regime.value} -- {mrc.detail}"]
        what_is_market_doing.extend(f"  {i.label}: {i.detail}" for i in mrc.inputs)
    else:
        what_is_market_doing = ["Macro regime: UNKNOWN -- not computed this run"]

    # Q2 -- what is this stock doing relative to market/sector (reuses the
    # SAME real "Relative strength" evidence row -- sector comparison is
    # honestly UNKNOWN inside that row's own detail, see evidence_matrix.py).
    rs_rows = [r for r in matrix.rows if r.group == EvidenceGroup.RELATIVE_STRENGTH]
    what_is_stock_doing = [f"{r.name}: {r.direction.value} -- {r.detail}" for r in rs_rows] or ["Relative strength: UNKNOWN -- not computed this run"]

    # Q3 -- what is the options market showing (OI/IV/futures/liquidity
    # evidence groups, verbatim).
    options_rows = [r for r in matrix.rows if r.group in _OPTIONS_MARKET_GROUPS]
    what_options_market_showing = [f"[{_evidence_tier(r.group)}] {r.name}: {r.direction.value} -- {r.detail}" for r in options_rows]

    # Q4 -- what can invalidate this (the active candidate's own real
    # invalidation condition, else the evidence that would need to flip).
    if report.candidates:
        what_can_invalidate = [report.candidates[0].invalidation_condition]
    else:
        what_can_invalidate = [f"{r.name} ({r.direction.value}) reversing would remove this evidence group's contribution" for r in matrix.rows if r.direction in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH)] or ["no directional evidence currently exists to invalidate"]

    # Q5 -- what data is missing (UNKNOWN rows + chain-quality issues,
    # same source `build_no_trade_explanation()` uses for WHAT IS UNRELIABLE).
    what_data_missing = [f"{r.name}: {r.detail}" for r in matrix.rows if r.direction == EvidenceDirection.UNKNOWN]
    what_data_missing.extend(f"chain quality: {i.kind.value} -- {i.detail}" for i in report.chain_quality_issues)
    if not what_data_missing:
        what_data_missing = ["no known data gaps this run"]

    direction: str | None = None
    option_side: str | None = None
    contract_fit: str | None = None
    risk: str | None = None
    invalidation: str | None = None
    recheck_condition: str | None = None
    if report.candidates:
        c = report.candidates[0]
        direction = report.decision.assessment.market_bias.value
        option_side = c.right.value
        contract_fit = f"liquidity={c.liquidity.grade.value}"
        dv = next((d for d in report.decay_viability if d.strike == c.strike and d.right == c.right), None)
        if dv is not None:
            contract_fit += f", decay viability={dv.verdict.value}"
        risk = "; ".join(c.risks) if c.risks else "none listed"
        invalidation = c.invalidation_condition
        recheck_condition = "re-run once the invalidation condition above has been checked against the next real quote/chain snapshot"

    return FinalDecisionExplanation(
        what_is_the_market_doing=what_is_market_doing, what_is_the_stock_doing_relative_to_market=what_is_stock_doing,
        what_is_the_options_market_showing=what_options_market_showing, what_can_invalidate_this=what_can_invalidate,
        what_data_is_missing=what_data_missing, direction=direction, option_side=option_side, contract_fit=contract_fit,
        risk=risk, invalidation=invalidation, recheck_condition=recheck_condition,
    )


def _render_final_decision_explanation(explanation: FinalDecisionExplanation) -> list[str]:
    lines: list[str] = []
    add = lines.append

    def section(title: str, items: list[str]) -> None:
        add(title + ":")
        for item in items:
            add(f"  - {item}")
        add("")

    add("-" * 30)
    add("FINAL DECISION EXPLANATION -- 5 STANDARD QUESTIONS (informational only)")
    add("-" * 30)
    section("1. WHAT IS THE MARKET DOING", explanation.what_is_the_market_doing)
    section("2. WHAT IS THIS STOCK DOING RELATIVE TO MARKET/SECTOR", explanation.what_is_the_stock_doing_relative_to_market)
    section("3. WHAT IS THE OPTIONS MARKET SHOWING", explanation.what_is_the_options_market_showing)
    section("4. WHAT CAN INVALIDATE THIS", explanation.what_can_invalidate_this)
    section("5. WHAT DATA IS MISSING", explanation.what_data_is_missing)

    if explanation.direction is not None:
        add("DIRECTION -> OPTION SIDE -> CONTRACT FIT -> RISK -> INVALIDATION -> RECHECK CONDITION:")
        add(f"  DIRECTION: {explanation.direction}")
        add(f"  OPTION SIDE: {explanation.option_side}")
        add(f"  CONTRACT FIT: {explanation.contract_fit}")
        add(f"  RISK: {explanation.risk}")
        add(f"  INVALIDATION: {explanation.invalidation}")
        add(f"  RECHECK CONDITION: {explanation.recheck_condition}")
        add("")

    return lines


def _render_no_trade_reason_recheck(explanation: NoTradeExplanation) -> list[str]:
    """Sprint 7B, Objective 14 -- the same already-computed
    `NoTradeExplanation` content (Objective 13, Sprint 7A), reformatted as
    the compact Reason:/Recheck: bullet pairs the product spec asks for,
    instead of duplicating that logic a second time."""
    lines: list[str] = []
    add = lines.append
    add("NO-TRADE SUMMARY (Reason / Recheck):")
    for reason in explanation.decisive_missing_confirmation:
        add(f"  Reason: {reason}")
    for recheck in explanation.what_would_change_the_state:
        add(f"  Recheck: {recheck}")
    add("")
    return lines
