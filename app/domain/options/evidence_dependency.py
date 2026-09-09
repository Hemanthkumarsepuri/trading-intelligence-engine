"""Formal evidence-dependency map.

Independent confirmation is counted per `EvidenceGroup`, never per row.
Cash-context streams are not evidence groups and must not vote.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.options.evidence_matrix import EvidenceGroup


@dataclass(frozen=True)
class EvidenceDependency:
    evidence_group: EvidenceGroup | None
    source: str
    transformation: str
    correlated_with: tuple[str, ...]
    independent_of: tuple[str, ...]
    directional: bool
    freshness_requirement: str
    historical_requirement: str
    may_vote: bool


DEPENDENCIES: tuple[EvidenceDependency, ...] = (
    EvidenceDependency(
        evidence_group=EvidenceGroup.UNDERLYING_PRICE_STRUCTURE,
        source="M15 candles",
        transformation="EMA alignment, rolling or session VWAP position, regime synthesis",
        correlated_with=("M15 trend", "VWAP", "Market regime"),
        independent_of=("options_oi", "futures", "relative_strength"),
        directional=True,
        freshness_requirement="current-session M15",
        historical_requirement="enough bars for EMA/VWAP",
        may_vote=True,
    ),
    EvidenceDependency(
        evidence_group=EvidenceGroup.OPTIONS_OI,
        source="one option-chain snapshot",
        transformation="PCR level/change, OI, volume, OI S/R",
        correlated_with=("Call/Put OI structure", "PCR change", "Change in OI", "Volume", "Support", "Resistance"),
        independent_of=("underlying_price_structure", "futures", "relative_strength"),
        directional=True,
        freshness_requirement="current chain (HTTP receipt freshness, not exchange matching time)",
        historical_requirement="prior snapshot required for PCR change and OI migration",
        may_vote=True,
    ),
    EvidenceDependency(
        evidence_group=EvidenceGroup.OPTIONS_IV,
        source="same option-chain snapshot (IV fields)",
        transformation="ATM IV and CE/PE skew",
        correlated_with=("IV", "IV skew", "options_oi"),
        independent_of=("underlying_price_structure",),
        directional=False,
        freshness_requirement="current chain",
        historical_requirement="history required for IV rank; a single print is context",
        may_vote=False,
    ),
    EvidenceDependency(
        evidence_group=EvidenceGroup.FUTURES,
        source="futures quote",
        transformation="current basis vs basis change vs OI quadrant",
        correlated_with=("Spot/Futures", "Futures OI"),
        independent_of=("options_oi", "relative_strength"),
        directional=True,
        freshness_requirement="current futures LTP",
        historical_requirement="prior basis required for BASIS_CHANGE (not CURRENT_BASIS)",
        may_vote=True,
    ),
    EvidenceDependency(
        evidence_group=EvidenceGroup.RELATIVE_STRENGTH,
        source="underlying day-change vs Nifty day-change",
        transformation="gap vs meaningful threshold",
        correlated_with=("GLOBAL Nifty input uses the same Nifty day-change",),
        independent_of=("options_oi", "futures"),
        directional=True,
        freshness_requirement="current underlying quote and current Nifty quote",
        historical_requirement="none beyond today's quote window",
        may_vote=True,
    ),
    EvidenceDependency(
        evidence_group=EvidenceGroup.GLOBAL,
        source="index/macro quotes",
        transformation="market-wide context verdict",
        correlated_with=("Nifty day-change also used in relative strength",),
        independent_of=("options_oi",),
        directional=True,
        freshness_requirement="current index quotes",
        historical_requirement="none",
        may_vote=True,
    ),
    EvidenceDependency(
        evidence_group=EvidenceGroup.NEWS_EVENT,
        source="instrument-scoped headlines",
        transformation="presence of headlines; never a directional vote",
        correlated_with=(),
        independent_of=("options_oi", "futures"),
        directional=False,
        freshness_requirement="publication vs retrieval times on the item",
        historical_requirement="no look-ahead past as_of",
        may_vote=False,
    ),
    EvidenceDependency(
        evidence_group=EvidenceGroup.LIQUIDITY,
        source="selected option bid/ask/OI/volume",
        transformation="contract quality, not direction",
        correlated_with=("option chain",),
        independent_of=("underlying_price_structure",),
        directional=False,
        freshness_requirement="current chain",
        historical_requirement="none",
        may_vote=False,
    ),
    EvidenceDependency(
        evidence_group=EvidenceGroup.DATA_QUALITY,
        source="freshness classifier",
        transformation="usable_for_vote / withheld rows",
        correlated_with=(),
        independent_of=(),
        directional=False,
        freshness_requirement="n/a (is the freshness record)",
        historical_requirement="none",
        may_vote=False,
    ),
    EvidenceDependency(
        evidence_group=None,
        source="sample Nifty 50 breadth / delivery / FII-DII",
        transformation="cash context display only",
        correlated_with=("not an EvidenceGroup",),
        independent_of=("all voting groups",),
        directional=False,
        freshness_requirement="shown but never a vote",
        historical_requirement="delivery/FII are previous-session or caller-supplied",
        may_vote=False,
    ),
)


def cash_context_may_vote() -> bool:
    return False


def options_oi_row_names_are_one_group() -> tuple[str, ...]:
    return ("Call/Put OI structure", "PCR change", "Change in OI", "Volume", "Support", "Resistance")
