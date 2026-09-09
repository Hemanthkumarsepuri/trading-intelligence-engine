"""Sprint 7B, Objective 1 -- Indian MACRO market-regime intelligence: a
deterministic RISK_ON/RISK_OFF/MIXED/TRANSITION/INSUFFICIENT_DATA read
over the SAME real macro inputs `global_context.py` already fetches
(NIFTY, Nifty Bank, India VIX, MCX Crude Oil, USD/INR) -- zero new
fetch, zero new provider.

Named `MacroRegime`/`classify_macro_regime` (not `MarketRegime`) to stay
clearly distinct from `app.domain.options.market_regime`'s own
`MarketRegime`/`classify_market_regime` -- that module classifies ONE
STOCK's own technical regime (TRENDING/RANGE/HIGH_VOLATILITY from its
own ATR/EMA/VWAP); this module classifies the BROAD MACRO environment.
Two genuinely different concepts that happen to share a similar name in
plain English, kept structurally separate here.

Deliberately a SEPARATE function from `assess_global_context()`, not a
relabeling of its verdict: that function answers "is today's broad
market a tailwind or headwind for A STOCK" (explicitly, by its own
documented rule, USD/INR never votes there -- "no stock-specific
sector-relevance basis"). This module answers a genuinely different
question -- "what is the BROAD MARKET's own risk regime today" -- where
a weakening rupee legitimately IS a real risk-off signal at the market
level, so USD/INR votes here (inverted: INR weakening is risk-off).
Never lets one input be decisive -- requires a majority of the real
CONTRIBUTING inputs to agree before calling a regime, exactly like
`global_context.py`'s own voting discipline.

Sector movement and non-NSE/MCX global index context (GIFT NIFTY, S&P
500, etc.) are honestly NOT wired in here -- no authorized source exists
for either (see `global_context.py`'s own module docstring and
`docs/data-sources/PROVIDER_DECISION.md`) -- never approximated.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class MacroRegime(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    MIXED = "MIXED"
    TRANSITION = "TRANSITION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class MacroRegimeInput:
    label: str
    day_change_pct: Decimal | None
    contributes: bool
    detail: str


@dataclass(frozen=True)
class MacroRegimeResult:
    regime: MacroRegime
    detail: str
    inputs: list[MacroRegimeInput]


def _vote(
    inputs: list[MacroRegimeInput], votes: list[int], *, label: str, change: Decimal | None, invert: bool,
    note: str, meaningful_move_pct: Decimal,
) -> None:
    if change is None:
        inputs.append(MacroRegimeInput(label=label, day_change_pct=None, contributes=False, detail=f"{label}: unavailable"))
        return
    if abs(change) < meaningful_move_pct:
        inputs.append(MacroRegimeInput(label=label, day_change_pct=change, contributes=False, detail=f"{label}: {change:+.2f}% (below the {meaningful_move_pct}% meaningful-move floor)"))
        return
    direction = -1 if (change > 0) == invert else 1
    votes.append(direction)
    inputs.append(MacroRegimeInput(label=label, day_change_pct=change, contributes=True, detail=f"{label}: {change:+.2f}% -- {note}"))


def classify_macro_regime(
    *, nifty_day_change_pct: Decimal | None, bank_nifty_day_change_pct: Decimal | None,
    india_vix_day_change_pct: Decimal | None, crude_oil_day_change_pct: Decimal | None,
    usdinr_day_change_pct: Decimal | None, meaningful_move_pct: Decimal,
) -> MacroRegimeResult:
    """`meaningful_move_pct` (THRESHOLD, required, undefaulted) -- same
    discipline as `global_context.py`'s own parameter of the same name,
    a deliberately separate value (not silently shared) since this
    module's voting rule (5 real inputs, USD/INR included) genuinely
    differs from that module's (4 real inputs, USD/INR excluded).
    """
    inputs: list[MacroRegimeInput] = []
    votes: list[int] = []

    _vote(inputs, votes, label="NIFTY 50", change=nifty_day_change_pct, invert=False, note="broad market direction", meaningful_move_pct=meaningful_move_pct)
    _vote(inputs, votes, label="Nifty Bank", change=bank_nifty_day_change_pct, invert=False, note="broad market direction (financials-weighted)", meaningful_move_pct=meaningful_move_pct)
    _vote(inputs, votes, label="India VIX", change=india_vix_day_change_pct, invert=True, note="rising VIX conventionally read as risk-off", meaningful_move_pct=meaningful_move_pct)
    _vote(inputs, votes, label="MCX Crude Oil", change=crude_oil_day_change_pct, invert=True, note="rising crude conventionally a headwind for a net oil-importing economy", meaningful_move_pct=meaningful_move_pct)
    _vote(inputs, votes, label="USD/INR", change=usdinr_day_change_pct, invert=True, note="a weakening rupee conventionally read as broad risk-off, unconfirmed", meaningful_move_pct=meaningful_move_pct)

    if not votes:
        known_any = any(i.day_change_pct is not None for i in inputs)
        if not known_any:
            return MacroRegimeResult(regime=MacroRegime.INSUFFICIENT_DATA, detail="no real macro inputs were available this run", inputs=inputs)
        return MacroRegimeResult(
            regime=MacroRegime.TRANSITION,
            detail="real macro inputs are available but none moved meaningfully today -- a quiet/transitional session, not a real regime read",
            inputs=inputs,
        )
    if all(v > 0 for v in votes):
        return MacroRegimeResult(regime=MacroRegime.RISK_ON, detail=f"{len(votes)} contributing real input(s) all read risk-on", inputs=inputs)
    if all(v < 0 for v in votes):
        return MacroRegimeResult(regime=MacroRegime.RISK_OFF, detail=f"{len(votes)} contributing real input(s) all read risk-off", inputs=inputs)
    risk_on_n = sum(1 for v in votes if v > 0)
    risk_off_n = sum(1 for v in votes if v < 0)
    return MacroRegimeResult(regime=MacroRegime.MIXED, detail=f"contributing real inputs disagree ({risk_on_n} risk-on vs {risk_off_n} risk-off)", inputs=inputs)
