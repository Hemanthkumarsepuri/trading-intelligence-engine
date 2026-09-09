"""Global/market-wide context (Phase 12) — built entirely from data
Upstox already legitimately provides through the same account already in
use: NIFTY 50, Nifty Bank, India VIX (all `NSE_INDEX`, already resolved
elsewhere in this codebase — no new instrument master or API surface
needed for these three).

Two further real, confirmed-available (2026-08-29) inputs — USD/INR
(`NCD_FO` currency derivatives, in the same NSE instrument master) and
MCX Crude Oil (a separate, real Upstox instrument master file,
`.../exchange/MCX.json.gz`, confirmed live: 15,552 `MCX_FO` entries
including a real `CRUDE OIL` contract) — are accepted as optional inputs
here so this module is ready for them, but are NOT YET wired into the
pipeline this round (documented gap, not silently dropped — wiring them
needs a second instrument-master fetch and NCD_FO/MCX_FO-specific futures
resolution, deferred to keep this slice reviewable).

Explicitly NOT available through Upstox at all (confirmed 2026-08-29 — no
matching entry in either the NSE or MCX instrument master): GIFT NIFTY,
S&P 500, Nasdaq, Dow, other global indices. These stay permanently out of
this module rather than being approximated from a proxy.

This module reports MARKET-WIDE context, never stock-specific SECTOR
relevance — Upstox's instrument master carries no reliable sector field
for individual equities, so this codebase has no legitimate basis to
claim "USD/INR matters more to this stock than that one." USD/INR is
therefore always informational only, never folded into the tailwind/
headwind verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class GlobalContextVerdict(str, Enum):
    TAILWIND = "GLOBAL_TAILWIND"
    HEADWIND = "GLOBAL_HEADWIND"
    MIXED = "MIXED"
    LOW_RELEVANCE = "LOW_RELEVANCE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class MarketContextInput:
    label: str
    day_change_pct: Decimal | None
    contributes_to_verdict: bool
    detail: str


@dataclass(frozen=True)
class GlobalContextAssessment:
    inputs: list[MarketContextInput]
    verdict: GlobalContextVerdict
    detail: str


def _vote(
    inputs: list[MarketContextInput], votes: list[int], *, label: str, change: Decimal | None, invert: bool,
    note: str, meaningful_move_pct: Decimal, implausible_move_pct: Decimal,
) -> None:
    """Sprint 7A, Objective 2 -- `implausible_move_pct` quarantines an
    objectively impossible provider value (e.g. a real-world-observed
    "-100% USD/INR" glitch) to `UNKNOWN`/excluded rather than letting it
    silently vote. This is NOT a second "meaningful move" threshold in
    the other direction -- `meaningful_move_pct` filters out NOISE (too
    small to matter); `implausible_move_pct` filters out CORRUPTION (too
    large to be a real day-change for any of these instruments). Never
    fabricates a replacement value -- the real, impossible number is
    still shown, just excluded from the verdict.
    """
    if change is None:
        inputs.append(MarketContextInput(label=label, day_change_pct=None, contributes_to_verdict=False, detail=f"{label}: unavailable"))
        return
    if abs(change) >= implausible_move_pct:
        inputs.append(
            MarketContextInput(
                label=label, day_change_pct=change, contributes_to_verdict=False,
                detail=f"{label}: {change:+.2f}% -- INVALID PROVIDER VALUE (exceeds a {implausible_move_pct}% plausible-move ceiling), excluded from evidence",
            )
        )
        return
    if abs(change) < meaningful_move_pct:
        inputs.append(
            MarketContextInput(
                label=label, day_change_pct=change, contributes_to_verdict=False,
                detail=f"{label}: {change:+.2f}% (below the {meaningful_move_pct}% meaningful-move floor)",
            )
        )
        return
    direction = -1 if (change > 0) == invert else 1
    votes.append(direction)
    inputs.append(MarketContextInput(label=label, day_change_pct=change, contributes_to_verdict=True, detail=f"{label}: {change:+.2f}% -- {note}"))


def assess_global_context(
    *,
    nifty_day_change_pct: Decimal | None,
    bank_nifty_day_change_pct: Decimal | None,
    india_vix_day_change_pct: Decimal | None,
    usdinr_day_change_pct: Decimal | None = None,
    crude_oil_day_change_pct: Decimal | None = None,
    meaningful_move_pct: Decimal,
    implausible_move_pct: Decimal = Decimal("25"),
) -> GlobalContextAssessment:
    """`meaningful_move_pct` (THRESHOLD, required, undefaulted): the
    day-change magnitude (percent) below which a move is treated as noise
    rather than a real directional contribution — no single correct value
    independent of each instrument's own typical daily range.

    `implausible_move_pct` (THRESHOLD, defaulted 25% -- an ENGINEERING
    CEILING, not a real historical record for any of these instruments,
    all of which move at most a few percent on an extreme real day):
    Sprint 7A's invalid-value quarantine (Objective 2) -- a magnitude at
    or beyond this is treated as a corrupted/impossible provider value
    (e.g. the real-world "-100% USD/INR" case), excluded from the
    verdict, never silently averaged in or replaced with a guess.

    `invert` semantics (HEURISTIC, documented conventions, not statistically
    validated): NIFTY/Nifty Bank vote in their own direction (they ARE the
    broad market); India VIX votes inverted (rising VIX conventionally
    read as risk-off/headwind); MCX Crude Oil votes inverted (rising crude
    conventionally a headwind for India as a net oil importer). USD/INR
    never votes — see module docstring.
    """
    inputs: list[MarketContextInput] = []
    votes: list[int] = []

    _vote(inputs, votes, label="NIFTY 50", change=nifty_day_change_pct, invert=False, note="broad market direction", meaningful_move_pct=meaningful_move_pct, implausible_move_pct=implausible_move_pct)
    _vote(inputs, votes, label="Nifty Bank", change=bank_nifty_day_change_pct, invert=False, note="broad market direction (financials-weighted)", meaningful_move_pct=meaningful_move_pct, implausible_move_pct=implausible_move_pct)
    _vote(inputs, votes, label="India VIX", change=india_vix_day_change_pct, invert=True, note="rising VIX conventionally read as risk-off, unconfirmed", meaningful_move_pct=meaningful_move_pct, implausible_move_pct=implausible_move_pct)
    _vote(inputs, votes, label="MCX Crude Oil", change=crude_oil_day_change_pct, invert=True, note="rising crude conventionally a headwind for a net oil-importing economy, unconfirmed", meaningful_move_pct=meaningful_move_pct, implausible_move_pct=implausible_move_pct)

    if usdinr_day_change_pct is None:
        inputs.append(MarketContextInput(label="USD/INR", day_change_pct=None, contributes_to_verdict=False, detail="USD/INR: unavailable"))
    elif abs(usdinr_day_change_pct) >= implausible_move_pct:
        # Objective 2's own named example: USD/INR never votes anyway,
        # but an impossible value must still be labeled as such, never
        # displayed as an ordinary informational reading.
        inputs.append(
            MarketContextInput(
                label="USD/INR", day_change_pct=usdinr_day_change_pct, contributes_to_verdict=False,
                detail=f"USD/INR: {usdinr_day_change_pct:+.2f}% -- INVALID PROVIDER VALUE (exceeds a {implausible_move_pct}% plausible-move ceiling)",
            )
        )
    else:
        inputs.append(
            MarketContextInput(
                label="USD/INR", day_change_pct=usdinr_day_change_pct, contributes_to_verdict=False,
                detail=f"USD/INR: {usdinr_day_change_pct:+.2f}% -- informational only, no stock-specific sector-relevance basis",
            )
        )

    if not votes:
        # Sprint 7A -- a quarantined (INVALID PROVIDER VALUE) input is
        # neither "known and below the noise floor" nor "genuinely
        # unavailable" -- distinguished here so the verdict detail never
        # misdescribes an impossible value as merely "a quiet day."
        below_floor_any = any(
            i.day_change_pct is not None and i.label != "USD/INR" and "INVALID PROVIDER VALUE" not in i.detail
            for i in inputs
        )
        quarantined_any = any("INVALID PROVIDER VALUE" in i.detail for i in inputs)
        if below_floor_any:
            verdict = GlobalContextVerdict.LOW_RELEVANCE
            detail = "available inputs were all below the meaningful-move floor -- no material global context today"
        elif quarantined_any:
            verdict = GlobalContextVerdict.INSUFFICIENT_DATA
            detail = "the only real inputs available were quarantined as INVALID PROVIDER VALUE -- no reliable global context today"
        else:
            verdict = GlobalContextVerdict.INSUFFICIENT_DATA
            detail = "no real global/market-wide inputs were available"
    elif all(v > 0 for v in votes):
        verdict = GlobalContextVerdict.TAILWIND
        detail = f"{len(votes)} contributing input(s) all read tailwind"
    elif all(v < 0 for v in votes):
        verdict = GlobalContextVerdict.HEADWIND
        detail = f"{len(votes)} contributing input(s) all read headwind"
    else:
        tailwind_n = sum(1 for v in votes if v > 0)
        headwind_n = sum(1 for v in votes if v < 0)
        verdict = GlobalContextVerdict.MIXED
        detail = f"contributing inputs disagree ({tailwind_n} tailwind vs {headwind_n} headwind)"

    return GlobalContextAssessment(inputs=inputs, verdict=verdict, detail=detail)
