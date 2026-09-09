"""Pure, deterministic option-chain aggregation: ATM/strike-window
resolution, moneyness classification, chain-wide OI/PCR totals, and
top-OI-strike candidates. Nothing here trades, scores, or ranks a "best"
option — it only computes facts directly derivable from one
`OptionChainSnapshot`, evidence for a later interpretive layer to consume,
never fabricated depth on top of what the chain actually contains.

Support/resistance-from-OI is a well-established, standard options-market
reading convention (heaviest Put OI below spot = candidate support; heaviest
Call OI above spot = candidate resistance) — `top_oi_strikes()` surfaces the
raw ranking; it does not itself assert "this level will hold."
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.market.models import OptionChainSnapshot, OptionQuote, OptionRight
from app.domain.options.models import ChainTotals, Moneyness, StrikeOI


def distinct_strikes(snapshot: OptionChainSnapshot) -> list[Decimal]:
    return sorted({leg.strike for leg in snapshot.legs})


def atm_strike(snapshot: OptionChainSnapshot) -> Decimal | None:
    """The chain's own strike nearest to `underlying_last_price` — `None`
    if the snapshot has no spot price or no strikes at all (never guessed).
    """
    if snapshot.underlying_last_price is None:
        return None
    strikes = distinct_strikes(snapshot)
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - snapshot.underlying_last_price))  # type: ignore[operator]


def strike_window(snapshot: OptionChainSnapshot, *, strikes_each_side: int) -> list[Decimal]:
    """Strikes within `strikes_each_side` positions of ATM. Thin wrapper
    over `strike_window_around()` — kept as its own function (rather than
    inlining `atm_strike(snapshot)` at every call site) since "window
    around ATM" is by far the most common case in this codebase.
    """
    atm = atm_strike(snapshot)
    if atm is None:
        return []
    return strike_window_around(snapshot, center=atm, strikes_each_side=strikes_each_side)


def strike_window_around(snapshot: OptionChainSnapshot, *, center: Decimal, strikes_each_side: int) -> list[Decimal]:
    """Strikes within `strikes_each_side` positions of an ARBITRARY center
    strike (not necessarily ATM) — the requested-contract comparison
    (Sprint 2) needs alternatives centered on what the user actually
    asked about, which may be several strikes away from ATM. Based on the
    chain's own actual distinct strike list — not an assumed fixed
    interval, so a chain with irregular spacing (or a gap) still windows
    correctly. Returns `[]` if `center` is not itself one of the chain's
    real strikes (never guesses the nearest one silently).
    """
    strikes = distinct_strikes(snapshot)
    if center not in strikes:
        return []
    idx = strikes.index(center)
    lo = max(0, idx - strikes_each_side)
    hi = min(len(strikes), idx + strikes_each_side + 1)
    return strikes[lo:hi]


def classify_moneyness(*, strike: Decimal, right: OptionRight, atm: Decimal, spot: Decimal) -> Moneyness:
    """Classifies one strike/right against the chain's ATM strike and spot.
    A strike exactly at ATM is always `ATM` regardless of side; otherwise a
    call is ITM below spot / OTM above, and a put is the mirror image —
    standard, textbook option moneyness, not a project-specific convention.
    """
    if strike == atm:
        return Moneyness.ATM
    if right == OptionRight.CE:
        return Moneyness.ITM if strike < spot else Moneyness.OTM
    return Moneyness.ITM if strike > spot else Moneyness.OTM


def chain_totals(snapshot: OptionChainSnapshot) -> ChainTotals:
    """Chain-wide OI/volume sums and put/call ratios. A leg with `None` OI
    or volume is excluded from the corresponding sum (not treated as zero)
    and counted in `legs_with_missing_oi` — a snapshot with lots of missing
    OI should be treated with more caution than one with a genuine `0` OI
    reported by the exchange, and this keeps that distinction visible to
    the caller rather than silently blending the two.
    """
    call_oi = put_oi = call_volume = put_volume = 0
    known_oi = missing_oi = 0
    for leg in snapshot.legs:
        if leg.open_interest is None:
            missing_oi += 1
        else:
            known_oi += 1
            if leg.right == OptionRight.CE:
                call_oi += leg.open_interest
            else:
                put_oi += leg.open_interest
        if leg.volume is not None:
            if leg.right == OptionRight.CE:
                call_volume += leg.volume
            else:
                put_volume += leg.volume

    pcr_oi = (Decimal(put_oi) / Decimal(call_oi)) if call_oi else None
    pcr_volume = (Decimal(put_volume) / Decimal(call_volume)) if call_volume else None

    return ChainTotals(
        total_call_oi=call_oi,
        total_put_oi=put_oi,
        total_call_volume=call_volume,
        total_put_volume=put_volume,
        put_call_ratio_oi=pcr_oi,
        put_call_ratio_volume=pcr_volume,
        legs_with_known_oi=known_oi,
        legs_with_missing_oi=missing_oi,
    )


def top_oi_strikes(snapshot: OptionChainSnapshot, *, right: OptionRight, limit: int) -> list[StrikeOI]:
    """The `limit` strikes with the highest open interest for one side,
    descending — the raw candidate list a support/resistance reading would
    draw on. Legs with unknown OI are excluded, never treated as `0` (a
    real `0`-OI leg would otherwise be indistinguishable from a
    missing-data one, and the latter must never rank as "no interest here").
    """
    entries = [
        StrikeOI(strike=leg.strike, open_interest=leg.open_interest)
        for leg in snapshot.legs
        if leg.right == right and leg.open_interest is not None
    ]
    entries.sort(key=lambda e: e.open_interest, reverse=True)
    return entries[:limit]


@dataclass(frozen=True)
class ChainRow:
    """One strike's CALL | STRIKE | PUT row — pure regrouping of the
    snapshot's own legs (a flat list with CE and PE as separate entries)
    for a visual chain table (Sprint 3). Computes nothing new; `call`/`put`
    are `None` only when that side genuinely has no leg at this strike in
    the real chain (never fabricated)."""

    strike: Decimal
    call: OptionQuote | None
    put: OptionQuote | None


def build_chain_rows(snapshot: OptionChainSnapshot) -> list[ChainRow]:
    """Every distinct strike in the chain, ascending, each carrying its
    real CE and PE legs (or `None` for a side that doesn't exist at that
    strike)."""
    by_strike: dict[Decimal, dict[OptionRight, OptionQuote]] = {}
    for leg in snapshot.legs:
        by_strike.setdefault(leg.strike, {})[leg.right] = leg
    return [
        ChainRow(strike=strike, call=legs.get(OptionRight.CE), put=legs.get(OptionRight.PE))
        for strike, legs in sorted(by_strike.items())
    ]
