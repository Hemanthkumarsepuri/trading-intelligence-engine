"""Sprint 8, Objective P1 -- official sector classification and 3-level
relative strength (STOCK vs SECTOR vs MARKET).

Pure domain logic: every input here is an already-fetched real value (the
`{symbol: industry}` mapping from `nse_sector_index.py`, real day-change
percentages already computed elsewhere) -- this module performs no I/O
and never infers a sector from a company's name (the exact defect this
was built to avoid, per repeated explicit product instruction).

Deliberately reuses `evidence_matrix.row_relative_strength()`'s own real
STOCK-vs-NIFTY comparison rather than reimplementing it -- this module
only ADDS the sector dimension that comparison never had, and never
recomputes STOCK-vs-NIFTY itself.

Structural context, not independent evidence: per this sprint's own
explicit rule ("sector classification is structural context, not
independent confirmation by itself"), nothing in this module produces an
`EvidenceRow` or participates in `EvidenceMatrix`/`decide()` -- it is
informational only, exactly like OI migration, basis-change, and macro
regime context before it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class SectorClassification(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"  # symbol not found in the real NSE constituent file -- never guessed


@dataclass(frozen=True)
class SectorInfo:
    classification: SectorClassification
    industry: str | None  # the real NSE-published industry label, or None when UNKNOWN
    source: str  # e.g. "NSE Nifty 500 constituent list" -- always attributed, never bare


# Real NSE-industry-label -> real NSE sectoral INDEX trading_symbol,
# confirmed present with a real `instrument_key` in the live Upstox
# instrument master 2026-09-03 (same master already used for NIFTY/
# BANKNIFTY/VIX context). Deliberately NOT a complete mapping: the real
# NSE Nifty 500 constituent file carries 20 distinct industry labels, and
# only the ones below have a clean, official, single sectoral index this
# system can point to without stretching the correspondence (e.g. "Power"
# has no dedicated index distinct from the broader, oil/gas-heavy "Nifty
# Energy" -- left unmapped rather than forcing a loose match). Every
# industry NOT in this table stays honestly `sector_day_change_pct=None`
# -- never guessed, never approximated by a nearby index.
INDUSTRY_TO_SECTOR_INDEX_SYMBOL: dict[str, str] = {
    "Automobile and Auto Components": "NIFTY AUTO",
    "Financial Services": "FINNIFTY",  # real trading_symbol for the "Nifty Fin Service" index -- broader than banks alone, matching this industry label better than BANKNIFTY would
    "Fast Moving Consumer Goods": "NIFTY FMCG",
    "Healthcare": "NIFTY HEALTHCARE",
    "Information Technology": "NIFTY IT",
    "Media Entertainment & Publication": "NIFTY MEDIA",
    "Metals & Mining": "NIFTY METAL",
    "Oil Gas & Consumable Fuels": "NIFTY OIL AND GAS",
    "Realty": "NIFTY REALTY",
}


def sector_index_trading_symbol(industry: str | None) -> str | None:
    """Real, documented, partial lookup -- see `INDUSTRY_TO_SECTOR_INDEX_SYMBOL`'s
    own docstring for exactly which industries are (and are not) mapped
    and why."""
    if industry is None:
        return None
    return INDUSTRY_TO_SECTOR_INDEX_SYMBOL.get(industry)


def classify_sector(symbol: str, sector_map: dict[str, str]) -> SectorInfo:
    """`sector_map` is the real `{trading_symbol: industry}` dict from
    `nse_sector_index.fetch_sector_index()`. A symbol absent from it
    (outside the Nifty 500, or the fetch never succeeded this run) is
    honestly UNKNOWN -- never inferred from the symbol's own name."""
    industry = sector_map.get(symbol)
    if industry is None:
        return SectorInfo(classification=SectorClassification.UNKNOWN, industry=None, source="NSE Nifty 500 constituent list")
    return SectorInfo(classification=SectorClassification.KNOWN, industry=industry, source="NSE Nifty 500 constituent list")


class RelativeStrengthTier(str, Enum):
    """STOCK vs SECTOR vs MARKET, as a real, ordered, three-way
    comparison of already-computed day-change percentages -- never a
    synthesized score. `UNKNOWN` whenever any one of the three real
    values is unavailable (never backfilled)."""

    STOCK_LEADING = "STOCK_LEADING"  # stock > sector > market, or stock is the clear top performer of the three
    STOCK_LAGGING = "STOCK_LAGGING"  # stock < sector < market, or stock is the clear bottom performer
    SECTOR_LEADING_STOCK_INLINE = "SECTOR_LEADING_STOCK_INLINE"  # sector is the standout mover; stock merely tracks it
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SectorRelativeStrength:
    sector_info: SectorInfo
    stock_day_change_pct: Decimal | None
    sector_day_change_pct: Decimal | None
    market_day_change_pct: Decimal | None
    tier: RelativeStrengthTier
    detail: str


def _rank(values: dict[str, Decimal]) -> list[str]:
    return sorted(values, key=lambda k: values[k], reverse=True)


def classify_sector_relative_strength(
    *,
    sector_info: SectorInfo,
    stock_day_change_pct: Decimal | None,
    sector_day_change_pct: Decimal | None,
    market_day_change_pct: Decimal | None,
    meaningful_gap_pct: Decimal,
) -> SectorRelativeStrength:
    """Real, deterministic 3-way ranking -- never a score. Requires the
    sector itself to be KNOWN (never ranks against an UNKNOWN sector) and
    all three real day-change values to be present."""
    if sector_info.classification == SectorClassification.UNKNOWN:
        return SectorRelativeStrength(
            sector_info=sector_info, stock_day_change_pct=stock_day_change_pct, sector_day_change_pct=None,
            market_day_change_pct=market_day_change_pct, tier=RelativeStrengthTier.UNKNOWN,
            detail="sector is UNKNOWN for this symbol (not in the real NSE Nifty 500 constituent list) -- no sector comparison possible",
        )
    if stock_day_change_pct is None or sector_day_change_pct is None or market_day_change_pct is None:
        return SectorRelativeStrength(
            sector_info=sector_info, stock_day_change_pct=stock_day_change_pct, sector_day_change_pct=sector_day_change_pct,
            market_day_change_pct=market_day_change_pct, tier=RelativeStrengthTier.UNKNOWN,
            detail="one or more real day-change values (stock/sector/market) unavailable this run",
        )

    values = {"stock": stock_day_change_pct, "sector": sector_day_change_pct, "market": market_day_change_pct}
    order = _rank(values)
    stock_vs_sector = stock_day_change_pct - sector_day_change_pct
    sector_vs_market = sector_day_change_pct - market_day_change_pct

    if order[0] == "stock" and abs(stock_vs_sector) >= meaningful_gap_pct:
        tier = RelativeStrengthTier.STOCK_LEADING
        detail = f"stock {stock_day_change_pct:+.2f}% leads sector {sector_day_change_pct:+.2f}% and market {market_day_change_pct:+.2f}%"
    elif order[-1] == "stock" and abs(stock_vs_sector) >= meaningful_gap_pct:
        tier = RelativeStrengthTier.STOCK_LAGGING
        detail = f"stock {stock_day_change_pct:+.2f}% lags sector {sector_day_change_pct:+.2f}% and market {market_day_change_pct:+.2f}%"
    elif abs(sector_vs_market) >= meaningful_gap_pct and abs(stock_vs_sector) < meaningful_gap_pct:
        tier = RelativeStrengthTier.SECTOR_LEADING_STOCK_INLINE
        detail = f"sector {sector_day_change_pct:+.2f}% is the standout mover vs market {market_day_change_pct:+.2f}%; stock {stock_day_change_pct:+.2f}% merely tracks its own sector"
    else:
        tier = RelativeStrengthTier.MIXED
        detail = f"stock {stock_day_change_pct:+.2f}% / sector {sector_day_change_pct:+.2f}% / market {market_day_change_pct:+.2f}% -- no clear leader/laggard by {meaningful_gap_pct}pt threshold"

    return SectorRelativeStrength(
        sector_info=sector_info, stock_day_change_pct=stock_day_change_pct, sector_day_change_pct=sector_day_change_pct,
        market_day_change_pct=market_day_change_pct, tier=tier, detail=detail,
    )
