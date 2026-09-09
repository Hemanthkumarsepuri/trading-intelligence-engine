"""Option Decay Viability Engine (Stage 8) — the FORWARD-LOOKING
counterpart to `decay_engine.py`'s backward-looking attribution. Given a
candidate option's CURRENT Greeks/IV/spread/DTE/premium, estimates whether
a plausible near-term move plausibly overcomes the modeled cost of
holding the position over ONE documented horizon.

=== SCOPE (read before using any field on `DecayViabilityAssessment`) ===
This assesses ONLY the decay/spread/expected-move relationship. It is
NOT an overall trade recommendation, NOT a probability of profit, and
does NOT account for news, event risk, market regime, or the rest of the
cross-evidence matrix — those remain the decision engine's job
(`decision_engine.py` never reads anything from this module: decay
viability is one informational input a reader weighs, never something
this codebase lets override the independent-evidence-group decision).
`scope_note` below is attached to every single result specifically so a
caller cannot render this assessment without also rendering that
disclaimer.

=== CORRECTNESS HISTORY (read before changing the formulas again) ===
An earlier version of this module computed the expected move over the
option's FULL remaining DTE while computing decay+spread cost over only
one `holding_horizon_hours` — a real, confirmed time-horizon mismatch
that made the resulting ratio scale with `sqrt(DTE)` in the numerator
while the denominator stayed flat, so a far-dated option would ALWAYS
read favorably regardless of its actual near-term economics (confirmed:
real RELIANCE 31-DTE data produced 47.83x, real NIFTY 3-DTE data produced
8.36x, purely reflecting the DTE gap, not a genuine economic difference).
Fixed here: `expected_move_over_horizon` is now computed over the SAME
`holding_horizon_hours` used for the cost side — DTE still matters (via
each leg's own real theta, which the provider itself prices more steeply
near expiry), but no longer inflates the move side independently of the
cost side.

Threshold classification (per this project's fact/metric/heuristic/
threshold discipline):
    FACT      — the leg's own reported Greeks/IV/bid/ask/LTP, taken as
                given.
    METRIC    — the expected-move formula (`spot * (IV/100) *
                sqrt(horizon-as-a-year-fraction)`, the standard options-
                market convention for "expected move" over a stated
                horizon) and the delta+gamma first/second-order premium-
                response approximation (same Taylor-expansion convention
                `decay_engine.py` already uses, for consistency within
                this package).
    HEURISTIC — NSE's real ~6.25-hour trading session (09:15-15:30 IST, a
                real, sourced fact) is used both to convert theta
                (assumed reported "per calendar day," per
                `decay_engine.py`'s own documented, not-independently-
                confirmed assumption) into a per-trading-hour rate, AND
                to convert `holding_horizon_hours` into an equivalent
                calendar-day fraction for the expected-move formula (one
                NSE trading day ~= one calendar day for this purpose —
                an approximation that ignores weekend/holiday gaps,
                documented rather than silently assumed). Both are this
                module's own judgement calls, not provider facts.
    THRESHOLD — the viability-ratio cutoffs, the holding horizon, and the
                IV scenario step (`iv_scenario_points`) — all required,
                undefaulted; no universally correct value exists for any
                of them independent of the trader's own intended
                timeframe and risk tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from app.domain.market.models import OptionRight

# NSE's real cash/F&O session length (09:15-15:30 IST) -- a FACT, not invented.
NSE_TRADING_HOURS_PER_DAY = Decimal("6.25")

SCOPE_NOTE = (
    "DECAY VIABILITY assesses ONLY whether a modeled near-term move plausibly "
    "overcomes time-decay and spread costs over the stated holding horizon. It is "
    "NOT an overall trade recommendation, NOT a probability of profit, and does not "
    "account for news, event risk, market regime, or the rest of the cross-evidence "
    "matrix -- see the FINAL DECISION section for the combined assessment."
)


class DecayViabilityVerdict(str, Enum):
    DECAY_FAVORABLE = "DECAY_FAVORABLE"
    DECAY_ACCEPTABLE = "DECAY_ACCEPTABLE"
    DECAY_HEADWIND = "DECAY_HEADWIND"
    DECAY_UNFAVORABLE = "DECAY_UNFAVORABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class DecayScenario:
    """One row of the FIRST-ORDER SCENARIO ESTIMATE table (never a
    profit prediction — see `SCOPE_NOTE`). `move_multiple` is expressed
    in units of `expected_move_over_horizon`; positive is the direction
    favorable to this leg (underlying up for a CE, down for a PE).
    """

    label: str
    move_multiple: Decimal
    underlying_move: Decimal
    estimated_premium_response: Decimal  # delta + gamma terms, signed
    net_after_modeled_costs: Decimal  # estimated_premium_response - total_modeled_cost


@dataclass(frozen=True)
class DecayViabilityAssessment:
    strike: Decimal
    right: OptionRight
    dte_days: Decimal | None
    holding_horizon_hours: Decimal

    premium_reference: Decimal | None  # mid-price used as the premium base, when available

    expected_move_over_horizon: Decimal | None  # underlying points, over holding_horizon_hours (NOT full DTE)
    expected_move_over_horizon_pct: Decimal | None  # percent of spot

    expected_premium_response: Decimal | None  # |delta|*move + 0.5*gamma*move^2 (2nd-order, matches decay_engine.py)
    theta_decay_per_trading_hour: Decimal | None
    theta_to_premium_pct_per_hour: Decimal | None  # theta_per_hour / premium_reference, as a percent

    decay_cost_over_horizon: Decimal | None
    spread_cost_round_trip: Decimal | None
    total_modeled_cost: Decimal | None

    # Master Product Grooming / Final Correctness Pass -- renamed from
    # `breakeven_underlying_move(_pct)`. This is NOT the contractual
    # option-expiry breakeven (strike +/- premium) -- it is a model-
    # estimated "how many underlying points of favorable delta-driven
    # premium movement are needed to fully offset this leg's modeled
    # decay+spread cost over `holding_horizon_hours`" figure:
    # total_modeled_cost / |delta| (first-order only). Calling this
    # "breakeven" was a real, confirmed naming defect -- the formula
    # itself is unchanged, only the name and its explanatory text.
    required_underlying_move: Decimal | None
    required_underlying_move_pct: Decimal | None

    # The GENUINE contractual expiry breakeven -- pure arithmetic, no
    # model assumption: strike + premium for a call, strike - premium
    # for a put (using the same `premium_reference` mid/LTP this
    # assessment already uses). Kept explicitly separate from
    # `required_underlying_move` above so the two concepts (a real
    # contract fact vs. a model-estimated cost-coverage figure) can
    # never be confused for one another.
    contractual_expiry_breakeven: Decimal | None

    iv_up_scenario_premium_effect: Decimal | None  # vega * (+iv_scenario_points)
    iv_down_scenario_premium_effect: Decimal | None  # vega * (-iv_scenario_points)

    viability_ratio: Decimal | None  # expected_premium_response / total_modeled_cost
    scenarios: list[DecayScenario] = field(default_factory=list)

    verdict: DecayViabilityVerdict = DecayViabilityVerdict.INSUFFICIENT_DATA
    detail: str = ""
    scope_note: str = SCOPE_NOTE


def _decimal_sqrt(value: Decimal) -> Decimal:
    if value <= 0:
        return Decimal(0)
    return value.sqrt()


def _insufficient(
    *, strike: Decimal, right: OptionRight, dte_days: Decimal, holding_horizon_hours: Decimal, detail: str
) -> DecayViabilityAssessment:
    return DecayViabilityAssessment(
        strike=strike, right=right, dte_days=dte_days, holding_horizon_hours=holding_horizon_hours,
        premium_reference=None, expected_move_over_horizon=None, expected_move_over_horizon_pct=None,
        expected_premium_response=None, theta_decay_per_trading_hour=None, theta_to_premium_pct_per_hour=None,
        decay_cost_over_horizon=None, spread_cost_round_trip=None, total_modeled_cost=None,
        required_underlying_move=None, required_underlying_move_pct=None, contractual_expiry_breakeven=None,
        iv_up_scenario_premium_effect=None,
        iv_down_scenario_premium_effect=None, viability_ratio=None, verdict=DecayViabilityVerdict.INSUFFICIENT_DATA,
        detail=detail,
    )


def assess_decay_viability(
    *,
    strike: Decimal,
    right: OptionRight,
    spot: Decimal | None,
    expiry: date,
    as_of: datetime,
    implied_volatility: Decimal | None,
    delta: Decimal | None,
    gamma: Decimal | None,
    vega: Decimal | None,
    theta: Decimal | None,
    ltp: Decimal | None,
    bid: Decimal | None,
    ask: Decimal | None,
    holding_horizon_hours: Decimal,
    iv_scenario_points: Decimal,
    favorable_min_ratio: Decimal,
    acceptable_min_ratio: Decimal,
    headwind_min_ratio: Decimal,
) -> DecayViabilityAssessment:
    """`holding_horizon_hours` (THRESHOLD, required): the real-hours
    holding period BOTH the expected move and the cost are measured over
    — see the module docstring's "correctness history" for why this must
    be one consistent horizon, not two different ones.

    `iv_scenario_points` (THRESHOLD, required): the vol-point step
    (e.g. `Decimal("1.0")` for a +/-1 IV point scenario) used for the
    vega-based IV-up/IV-down scenario fields.

    `favorable_min_ratio` >= `acceptable_min_ratio` >= `headwind_min_ratio`
    (THRESHOLD, required): descending viability-ratio cutoffs; below
    `headwind_min_ratio` is `DECAY_UNFAVORABLE`.

    Gamma/vega are optional (`None` is tolerated) — the assessment still
    computes with delta/theta alone if they're missing, exactly like
    `decay_engine.py`'s own attribution does, but a leg with no Greeks at
    all, no IV, no spot, or no usable bid/ask still returns
    `INSUFFICIENT_DATA`, never a guess.
    """
    dte_days_int = (expiry - as_of.date()).days
    dte_days = Decimal(max(dte_days_int, 0))

    if (
        spot is None or spot <= 0 or implied_volatility is None or implied_volatility <= 0
        or delta is None or theta is None or bid is None or ask is None or ask <= 0
    ):
        return _insufficient(
            strike=strike, right=right, dte_days=dte_days, holding_horizon_hours=holding_horizon_hours,
            detail="missing one or more of spot/IV/delta/theta/bid/ask -- cannot assess decay viability",
        )

    premium_reference = ltp if ltp is not None and ltp > 0 else (bid + ask) / 2

    # The expected move is now computed over the SAME horizon as the cost
    # (the fix -- see module docstring). One NSE trading day (6.25h) is
    # treated as ~1 calendar day for this conversion (HEURISTIC,
    # documented above).
    horizon_trading_days = holding_horizon_hours / NSE_TRADING_HOURS_PER_DAY
    horizon_years = horizon_trading_days / Decimal(365)
    expected_move_fraction = (implied_volatility / Decimal(100)) * _decimal_sqrt(horizon_years)
    expected_move = spot * expected_move_fraction

    gamma_term = (gamma * expected_move * expected_move) / 2 if gamma is not None else Decimal(0)
    expected_premium_response = abs(delta) * expected_move + gamma_term

    theta_per_hour = abs(theta) / NSE_TRADING_HOURS_PER_DAY
    theta_to_premium_pct_per_hour = (theta_per_hour / premium_reference * Decimal(100)) if premium_reference > 0 else None

    decay_cost = theta_per_hour * holding_horizon_hours
    spread_cost = ask - bid
    total_cost = decay_cost + spread_cost

    required_move = (total_cost / abs(delta)) if delta != 0 else None
    required_move_pct = (required_move / spot * Decimal(100)) if required_move is not None else None

    # Genuine contractual expiry breakeven -- pure arithmetic, the
    # standard textbook definition, computed independently of the
    # decay/cost model above (never derived FROM `required_move`).
    contractual_breakeven = strike + premium_reference if right == OptionRight.CE else strike - premium_reference

    iv_up_effect = vega * iv_scenario_points if vega is not None else None
    iv_down_effect = vega * (-iv_scenario_points) if vega is not None else None

    scenarios = [
        _scenario("Strong favorable move (+2x expected)", Decimal(2), expected_move, delta, gamma, total_cost),
        _scenario("Moderate favorable move (+1x expected)", Decimal(1), expected_move, delta, gamma, total_cost),
        _scenario("Flat", Decimal(0), expected_move, delta, gamma, total_cost),
        _scenario("Moderate adverse move (-1x expected)", Decimal(-1), expected_move, delta, gamma, total_cost),
        _scenario("Strong adverse move (-2x expected)", Decimal(-2), expected_move, delta, gamma, total_cost),
    ]

    if total_cost <= 0:
        return DecayViabilityAssessment(
            strike=strike, right=right, dte_days=dte_days, holding_horizon_hours=holding_horizon_hours,
            premium_reference=premium_reference, expected_move_over_horizon=expected_move,
            expected_move_over_horizon_pct=expected_move_fraction * Decimal(100),
            expected_premium_response=expected_premium_response, theta_decay_per_trading_hour=theta_per_hour,
            theta_to_premium_pct_per_hour=theta_to_premium_pct_per_hour, decay_cost_over_horizon=decay_cost,
            spread_cost_round_trip=spread_cost, total_modeled_cost=total_cost, required_underlying_move=required_move,
            required_underlying_move_pct=required_move_pct, contractual_expiry_breakeven=contractual_breakeven,
            iv_up_scenario_premium_effect=iv_up_effect,
            iv_down_scenario_premium_effect=iv_down_effect, viability_ratio=None, scenarios=scenarios,
            verdict=DecayViabilityVerdict.INSUFFICIENT_DATA, detail="total modeled decay+spread cost is zero or negative -- ratio undefined",
        )

    ratio = expected_premium_response / total_cost
    if ratio >= favorable_min_ratio:
        verdict = DecayViabilityVerdict.DECAY_FAVORABLE
    elif ratio >= acceptable_min_ratio:
        verdict = DecayViabilityVerdict.DECAY_ACCEPTABLE
    elif ratio >= headwind_min_ratio:
        verdict = DecayViabilityVerdict.DECAY_HEADWIND
    else:
        verdict = DecayViabilityVerdict.DECAY_UNFAVORABLE

    detail = (
        f"expected move over the {holding_horizon_hours}h horizon is {expected_move:.2f} pts "
        f"({expected_move_fraction * 100:.2f}%); estimated premium response ~{expected_premium_response:.2f} "
        f"(delta{'+gamma' if gamma is not None else ' only, gamma unavailable'}); modeled cost over the same "
        f"horizon is {total_cost:.2f} (decay {decay_cost:.2f} + round-trip spread {spread_cost:.2f}); "
        f"ratio {ratio:.2f}x -- first-order/second-order approximation only, not a profitability guarantee"
    )

    return DecayViabilityAssessment(
        strike=strike, right=right, dte_days=dte_days, holding_horizon_hours=holding_horizon_hours,
        premium_reference=premium_reference, expected_move_over_horizon=expected_move,
        expected_move_over_horizon_pct=expected_move_fraction * Decimal(100),
        expected_premium_response=expected_premium_response, theta_decay_per_trading_hour=theta_per_hour,
        theta_to_premium_pct_per_hour=theta_to_premium_pct_per_hour, decay_cost_over_horizon=decay_cost,
        spread_cost_round_trip=spread_cost, total_modeled_cost=total_cost, required_underlying_move=required_move,
        required_underlying_move_pct=required_move_pct, contractual_expiry_breakeven=contractual_breakeven,
        iv_up_scenario_premium_effect=iv_up_effect,
        iv_down_scenario_premium_effect=iv_down_effect, viability_ratio=ratio, scenarios=scenarios,
        verdict=verdict, detail=detail,
    )


def _scenario(
    label: str, multiple: Decimal, expected_move: Decimal, delta: Decimal, gamma: Decimal | None, total_cost: Decimal
) -> DecayScenario:
    # `move` already carries the correct sign via `multiple` (expected_move
    # itself is always >= 0); `abs(delta) * move` therefore correctly comes
    # out negative for an adverse (multiple < 0) scenario without a branch.
    # `gamma_term` is always >= 0 regardless of direction -- positive gamma
    # genuinely benefits a long option holder on a large move either way,
    # a real, correct property of long options, not an approximation error.
    move = multiple * expected_move
    gamma_term = (gamma * move * move) / 2 if gamma is not None else Decimal(0)
    response = abs(delta) * move + gamma_term
    return DecayScenario(
        label=label, move_multiple=multiple, underlying_move=move, estimated_premium_response=response,
        net_after_modeled_costs=response - total_cost,
    )
