"""Direction-Neutral CE/PE Intelligence (Sprint 4, Parts D-K) — the CORE
requirement of this milestone: independently construct a CE thesis and a
PE thesis for the SAME reference strike, without ever assuming bullish
implies CE or bearish implies PE, and without ever privileging whichever
side the user happened to type.

This module reuses, rather than duplicates:
    - `contract_analysis.assess_contract()`/`compare_contracts()` (already
      fully symmetric — takes an explicit `right`, never infers one).
    - `evidence_matrix.EvidenceMatrix` (already direction-neutral by
      design — `group_verdicts()`/`supporting_count()` are reused directly
      for `classify_direction_comparison()`, never a new score).
    - `adversarial_analysis.AdversarialAnalysis` (its `bull_case`/
      `bear_case` become, respectively, CE's "could work"/PE's "could
      fail" and PE's "could work"/CE's "could fail" — a relabeling, never
      a recomputation).
    - `decay_viability.DecayViabilityAssessment` (already symmetric, via
      `ContractAssessment.decay_viability`).
    - `support_resistance.Level`/`LevelStability` (already computed).

=== WHY THIS MODULE EXISTS SEPARATELY FROM `candidate_engine.py` ===
`candidate_engine.generate_candidates()` deliberately only ever builds
candidates for the bias-determined side (Sprint 2's own audit finding) —
that is correct FOR ITS PURPOSE (feeding the bias-consistent `FinalDecision`
flow, which must never recommend against the evidence). This module is a
SEPARATE, parallel, always-both-sides analysis layer, exactly like
`contract_analysis.py` was for the single requested-contract case in
Sprint 2 — it never feeds `decision_engine.decide()`, and `decide()` never
reads it. Building both is deliberate, not an oversight.

=== NO SCORING ===
`classify_direction_comparison()` returns one of a fixed, documented
vocabulary (`DirectionComparisonVerdict`) derived from evidence-GROUP
counts (the anti-gaming primitive `EvidenceMatrix` already provides) plus
one required, documented THRESHOLD — never a synthesized number like
"CE=72, PE=41". A genuine trade-off is reported as `CONFLICTED`, never
resolved by an invented tie-breaker.

=== THETA LANGUAGE (Part F) ===
For a LONG option, theta is a cost, period — for CE and for PE alike.
`build_decay_interpretation()` never says "PE decay is positive"; it says
theta remains a cost and states which underlying direction could offset
it, reusing the already-computed decay numbers verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.adversarial_analysis import AdversarialAnalysis
from app.domain.options.contract_analysis import (
    ContractAssessment,
    ContractComparison,
    compare_contracts,
)
from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceMatrix,
    GroupVerdict,
    OverallConvergence,
)
from app.domain.options.liquidity import LiquidityGrade
from app.domain.options.support_resistance import Level, LevelStability

# ============================================================
# Part E — CE vs PE comparison verdict (no scoring)
# ============================================================


class DirectionComparisonVerdict(str, Enum):
    BULLISH_SIDE_BETTER_SUPPORTED = "BULLISH_SIDE_BETTER_SUPPORTED"
    BEARISH_SIDE_BETTER_SUPPORTED = "BEARISH_SIDE_BETTER_SUPPORTED"
    BOTH_SIDES_WEAK = "BOTH_SIDES_WEAK"
    BOTH_SIDES_STRONG = "BOTH_SIDES_STRONG"
    CONFLICTED = "CONFLICTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def classify_direction_comparison(matrix: EvidenceMatrix, *, min_supporting_rows_for_strong: int) -> DirectionComparisonVerdict:
    """Reuses `EvidenceMatrix.overall_convergence()` (per-GROUP, anti-gamed)
    directly for the clean-convergence cases. Only when groups genuinely
    conflict does this look at raw row counts (`supporting_count()`,
    already computed) as a magnitude signal to distinguish a well-evidenced
    stand-off (`BOTH_SIDES_STRONG`) from a thin one (`BOTH_SIDES_WEAK`) --
    `min_supporting_rows_for_strong` (THRESHOLD, required, undefaulted) is
    that magnitude cutoff; no single correct value exists independent of
    how many evidence rows this system happens to compute for a given
    underlying.
    """
    convergence = matrix.overall_convergence()
    if convergence == OverallConvergence.CONVERGENCE_BULLISH:
        return DirectionComparisonVerdict.BULLISH_SIDE_BETTER_SUPPORTED
    if convergence == OverallConvergence.CONVERGENCE_BEARISH:
        return DirectionComparisonVerdict.BEARISH_SIDE_BETTER_SUPPORTED
    if convergence == OverallConvergence.INSUFFICIENT_EVIDENCE:
        return DirectionComparisonVerdict.INSUFFICIENT_DATA

    bull_rows = matrix.supporting_count(EvidenceDirection.BULLISH)
    bear_rows = matrix.supporting_count(EvidenceDirection.BEARISH)
    bull_strong = bull_rows >= min_supporting_rows_for_strong
    bear_strong = bear_rows >= min_supporting_rows_for_strong
    if bull_strong and bear_strong:
        return DirectionComparisonVerdict.BOTH_SIDES_STRONG
    if not bull_strong and not bear_strong:
        return DirectionComparisonVerdict.BOTH_SIDES_WEAK
    return DirectionComparisonVerdict.CONFLICTED


# ============================================================
# Part H — support/resistance directional paths
# ============================================================


@dataclass(frozen=True)
class DirectionalPathStep:
    label: str
    price: Decimal | None
    distance_pct: Decimal | None
    stability: str | None


@dataclass(frozen=True)
class DirectionalPaths:
    spot: Decimal | None
    bullish_path: list[DirectionalPathStep] = field(default_factory=list)
    bearish_path: list[DirectionalPathStep] = field(default_factory=list)


def build_directional_paths(
    *, spot: Decimal | None, resistance_levels: list[Level], support_levels: list[Level], level_stability: list[LevelStability]
) -> DirectionalPaths:
    """Never infers that a level must hold or break (Part H) — `stability`
    is copied verbatim from the already-computed `LevelStability`
    (`STABLE`/`WEAKENING`/`TESTED`/`BROKEN`/`UNCONFIRMED`), or `None` when
    no stability read exists for that level yet.
    """
    stability_by_strike = {ls.level.strike: ls.state.value for ls in level_stability}

    def step(label: str, level: Level) -> DirectionalPathStep:
        return DirectionalPathStep(
            label=label, price=level.strike, distance_pct=level.distance_from_spot_pct,
            stability=stability_by_strike.get(level.strike),
        )

    resistances_asc = sorted(resistance_levels, key=lambda lv: lv.strike)
    supports_desc = sorted(support_levels, key=lambda lv: lv.strike, reverse=True)

    bullish_path = [DirectionalPathStep(label="current", price=spot, distance_pct=Decimal("0"), stability=None)]
    bullish_path += [step(f"resistance {i + 1}", lv) for i, lv in enumerate(resistances_asc[:2])]

    bearish_path = [DirectionalPathStep(label="current", price=spot, distance_pct=Decimal("0"), stability=None)]
    bearish_path += [step(f"support {i + 1}", lv) for i, lv in enumerate(supports_desc[:2])]

    return DirectionalPaths(spot=spot, bullish_path=bullish_path, bearish_path=bearish_path)


class MoveCoverage(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ============================================================
# Part F/G — direction-neutral decay interpretation
# ============================================================


@dataclass(frozen=True)
class DecayInterpretation:
    right: OptionRight
    theta_cost_statement: str
    move_coverage: MoveCoverage
    move_coverage_label: str
    move_coverage_detail: str


_BIAS_WORD = {OptionRight.CE: "bullish", OptionRight.PE: "bearish"}


def build_decay_interpretation(assessment: ContractAssessment | None) -> DecayInterpretation | None:
    """Never says decay "becomes positive" for either side (Part F) --
    theta is always framed as a cost; only the underlying-movement
    language differs by `right`. `move_coverage` is a FIRST-ORDER
    ANALYTICAL COMPARISON (Part G) -- `expected_move_over_horizon` vs
    `required_underlying_move`, both already computed by
    `decay_viability.py` -- never a probability, never "PROFITABLE."
    `required_underlying_move` is a model-estimated cost-coverage figure,
    NOT the contractual expiry breakeven (`contractual_expiry_breakeven`,
    a separate field) -- see decay_viability.py's field docstrings.
    """
    if assessment is None:
        return None
    right = assessment.right
    bias_word = _BIAS_WORD[right]
    cost_statement = (
        f"{right.value} theta remains a cost; {bias_word} underlying movement may offset or exceed that cost -- "
        "it does not become positive merely because this side was bought."
    )

    dv = assessment.decay_viability
    if dv is None or dv.expected_move_over_horizon is None or dv.required_underlying_move is None:
        return DecayInterpretation(
            right=right, theta_cost_statement=cost_statement, move_coverage=MoveCoverage.INSUFFICIENT_DATA,
            move_coverage_label="FIRST-ORDER MOVE COVERAGE: INSUFFICIENT_DATA",
            move_coverage_detail="expected move or required underlying move unavailable",
        )

    covers = dv.expected_move_over_horizon >= dv.required_underlying_move
    coverage = MoveCoverage.PRESENT if covers else MoveCoverage.ABSENT
    # Sprint 7A, Objective 10 -- the explicit disclaimer this comparison
    # has always needed: "coverage" is a real, first-order arithmetic
    # comparison, never a claim of positive expectancy/profitability.
    detail = (
        f"expected move over the holding horizon ({dv.expected_move_over_horizon:.2f} pts) "
        f"{'meets or exceeds' if covers else 'falls short of'} the underlying move required to cover modeled "
        f"decay+spread cost ({dv.required_underlying_move:.2f} pts -- NOT the contractual expiry breakeven). "
        "Coverage only means the modeled move is larger than the modeled first-order cost burden -- it does NOT "
        "establish positive expectancy or profitability."
    )
    return DecayInterpretation(
        right=right, theta_cost_statement=cost_statement, move_coverage=coverage,
        move_coverage_label=f"FIRST-ORDER MOVE COVERAGE: {coverage.value}", move_coverage_detail=detail,
    )


# ============================================================
# Part I — hedge context (explicitly NOT an automated recommendation)
# ============================================================


_PRIMARY_RISK = {
    OptionRight.CE: "theta + downside underlying movement + IV contraction",
    OptionRight.PE: "theta + upside underlying movement + IV contraction",
}


def build_hedge_context(right: OptionRight) -> str:
    """Part I: analytical context only. This system does not evaluate an
    actual hedge structure (pricing a paired leg, a different expiry,
    etc.) -- that would require genuinely implemented multi-leg pricing
    this codebase does not have, and inventing one would be exactly the
    kind of fabrication this project forbids.
    """
    return f"primary structural risk = {_PRIMARY_RISK[right]}. HEDGE STRUCTURE NOT EVALUATED."


# ============================================================
# Part J — adversarial CE/PE case (reuses AdversarialAnalysis, never recomputed)
# ============================================================


@dataclass(frozen=True)
class ContractCase:
    could_work: list[str] = field(default_factory=list)
    could_fail: list[str] = field(default_factory=list)


def build_contract_case(*, right: OptionRight, adversarial: AdversarialAnalysis, assessment: ContractAssessment | None) -> ContractCase:
    """CE's "could work" IS the underlying's bull_case; CE's "could fail"
    is the bear_case plus CE-specific structural risk notes. PE is the
    exact mirror. Nothing here re-derives evidence -- it only relabels
    `AdversarialAnalysis`'s already-computed lists from a per-contract
    perspective and appends structural (never directional) risk notes
    already available on `ContractAssessment`.
    """
    if right == OptionRight.CE:
        could_work, could_fail = list(adversarial.bull_case), list(adversarial.bear_case)
    else:
        could_work, could_fail = list(adversarial.bear_case), list(adversarial.bull_case)

    if assessment is not None:
        if assessment.liquidity.grade in (LiquidityGrade.POOR, LiquidityGrade.UNTRADEABLE):
            could_fail.append(f"{right.value} liquidity grade is {assessment.liquidity.grade.value.upper()} -- execution risk independent of direction")
        if assessment.decay_viability is not None and assessment.decay_viability.verdict.value in ("DECAY_HEADWIND", "DECAY_UNFAVORABLE"):
            could_fail.append(f"{right.value} decay viability is {assessment.decay_viability.verdict.value} -- time/spread cost may dominate even if direction is right")

    return ContractCase(could_work=could_work, could_fail=could_fail)


# ============================================================
# Part D/E/K — the top-level assembly
# ============================================================


@dataclass(frozen=True)
class DirectionComparison:
    reference_strike: Decimal
    verdict: DirectionComparisonVerdict
    verdict_detail: str

    ce_alternatives: ContractComparison
    pe_alternatives: ContractComparison

    ce_case: ContractCase
    pe_case: ContractCase

    ce_decay: DecayInterpretation | None
    pe_decay: DecayInterpretation | None

    ce_hedge_context: str
    pe_hedge_context: str

    paths: DirectionalPaths

    bullish_supporting_groups: int
    bearish_supporting_groups: int
    conflicting_groups: int

    preferred_direction: EvidenceDirection | None
    preferred_contract: ContractAssessment | None
    no_defensible_direction: bool

    @property
    def ce_assessment(self) -> ContractAssessment | None:
        return self.ce_alternatives.requested

    @property
    def pe_assessment(self) -> ContractAssessment | None:
        return self.pe_alternatives.requested


_UNTRADEABLE_LIQUIDITY = {LiquidityGrade.UNTRADEABLE}


def build_direction_comparison(
    snapshot: OptionChainSnapshot,
    *,
    matrix: EvidenceMatrix,
    adversarial: AdversarialAnalysis,
    reference_strike: Decimal,
    expiry: date,
    as_of: datetime,
    max_quote_age: timedelta,
    strikes_each_side: int,
    support_levels: list[Level],
    resistance_levels: list[Level],
    level_stability: list[LevelStability],
    min_supporting_rows_for_strong: int,
    decay_viability_holding_horizon_hours: Decimal,
    decay_viability_iv_scenario_points: Decimal,
    decay_viability_favorable_min_ratio: Decimal,
    decay_viability_acceptable_min_ratio: Decimal,
    decay_viability_headwind_min_ratio: Decimal,
) -> DirectionComparison:
    """The single entry point. `reference_strike` is an ANCHOR, never a
    command to prefer that side (Part K) -- BOTH `ce_alternatives` and
    `pe_alternatives` are built at the exact same strike via the SAME
    already-symmetric `compare_contracts()`, regardless of which right (if
    any) the user actually typed.
    """
    def _compare(right: OptionRight) -> ContractComparison:
        return compare_contracts(
            snapshot, requested_strike=reference_strike, requested_right=right, strikes_each_side=strikes_each_side,
            expiry=expiry, as_of=as_of, max_quote_age=max_quote_age, support_levels=support_levels,
            resistance_levels=resistance_levels, decay_viability_holding_horizon_hours=decay_viability_holding_horizon_hours,
            decay_viability_iv_scenario_points=decay_viability_iv_scenario_points,
            decay_viability_favorable_min_ratio=decay_viability_favorable_min_ratio,
            decay_viability_acceptable_min_ratio=decay_viability_acceptable_min_ratio,
            decay_viability_headwind_min_ratio=decay_viability_headwind_min_ratio,
        )

    ce_alternatives = _compare(OptionRight.CE)
    pe_alternatives = _compare(OptionRight.PE)

    verdict = classify_direction_comparison(matrix, min_supporting_rows_for_strong=min_supporting_rows_for_strong)
    group_verdicts = matrix.group_verdicts().values()
    bullish_groups = sum(1 for v in group_verdicts if v == GroupVerdict.BULLISH)
    bearish_groups = sum(1 for v in group_verdicts if v == GroupVerdict.BEARISH)
    conflicting_groups = sum(1 for v in group_verdicts if v == GroupVerdict.CONFLICTING)

    verdict_detail = (
        f"{bullish_groups} independent group(s) bullish, {bearish_groups} bearish, {conflicting_groups} internally conflicting "
        f"(min_supporting_rows_for_strong={min_supporting_rows_for_strong})"
    )

    preferred_direction: EvidenceDirection | None = None
    preferred_contract: ContractAssessment | None = None
    if verdict == DirectionComparisonVerdict.BULLISH_SIDE_BETTER_SUPPORTED:
        preferred_direction = EvidenceDirection.BULLISH
        if ce_alternatives.requested is not None and ce_alternatives.requested.liquidity.grade not in _UNTRADEABLE_LIQUIDITY:
            preferred_contract = ce_alternatives.requested
    elif verdict == DirectionComparisonVerdict.BEARISH_SIDE_BETTER_SUPPORTED:
        preferred_direction = EvidenceDirection.BEARISH
        if pe_alternatives.requested is not None and pe_alternatives.requested.liquidity.grade not in _UNTRADEABLE_LIQUIDITY:
            preferred_contract = pe_alternatives.requested

    return DirectionComparison(
        reference_strike=reference_strike, verdict=verdict, verdict_detail=verdict_detail,
        ce_alternatives=ce_alternatives, pe_alternatives=pe_alternatives,
        ce_case=build_contract_case(right=OptionRight.CE, adversarial=adversarial, assessment=ce_alternatives.requested),
        pe_case=build_contract_case(right=OptionRight.PE, adversarial=adversarial, assessment=pe_alternatives.requested),
        ce_decay=build_decay_interpretation(ce_alternatives.requested), pe_decay=build_decay_interpretation(pe_alternatives.requested),
        ce_hedge_context=build_hedge_context(OptionRight.CE), pe_hedge_context=build_hedge_context(OptionRight.PE),
        paths=build_directional_paths(
            spot=snapshot.underlying_last_price, resistance_levels=resistance_levels, support_levels=support_levels,
            level_stability=level_stability,
        ),
        bullish_supporting_groups=bullish_groups, bearish_supporting_groups=bearish_groups, conflicting_groups=conflicting_groups,
        preferred_direction=preferred_direction, preferred_contract=preferred_contract,
        no_defensible_direction=preferred_contract is None,
    )
