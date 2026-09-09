"""Chain-level data-quality checks — deterministic, evidence-only signals
about whether an `OptionChainSnapshot` is trustworthy enough to reason
about, never a trading judgement. A caller that sees `DEAD_CHAIN` or
`STALE_SNAPSHOT` should treat the chain as insufficient evidence (this
project's NO_TRADE-on-ambiguity default), not silently proceed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.domain.market.models import OptionChainSnapshot, OptionRight
from app.domain.options.models import ChainQualityIssue, ChainQualityIssueKind


def check_chain_quality(
    snapshot: OptionChainSnapshot,
    *,
    as_of: datetime,
    max_age: timedelta,
    max_spread_fraction: Decimal,
) -> list[ChainQualityIssue]:
    """`max_age` and `max_spread_fraction` are required, not defaulted —
    there is no universally correct "stale" or "wide" threshold for every
    underlying/liquidity tier; the caller must supply one appropriate to
    what it's using this chain for rather than this module silently
    asserting a threshold nothing here has evidence for.
    """
    issues: list[ChainQualityIssue] = []

    if as_of.date() > snapshot.expiry:
        issues.append(
            ChainQualityIssue(
                kind=ChainQualityIssueKind.EXPIRED_CONTRACT,
                detail=f"chain expiry {snapshot.expiry.isoformat()} is before as_of date {as_of.date().isoformat()}",
            )
        )

    if not snapshot.legs:
        issues.append(
            ChainQualityIssue(kind=ChainQualityIssueKind.EMPTY_CHAIN, detail="option chain has no legs")
        )

    seen_legs: set[tuple[Decimal, OptionRight]] = set()
    duplicates: list[str] = []
    for leg in snapshot.legs:
        key = (leg.strike, leg.right)
        if key in seen_legs:
            duplicates.append(f"{leg.right.value} {leg.strike}")
        seen_legs.add(key)
    if duplicates:
        issues.append(
            ChainQualityIssue(
                kind=ChainQualityIssueKind.DUPLICATE_STRIKE_RIGHT,
                detail=f"duplicate strike+right rows: {', '.join(duplicates)}",
            )
        )

    age = as_of - snapshot.freshness.data_timestamp
    if age > max_age:
        issues.append(
            ChainQualityIssue(
                kind=ChainQualityIssueKind.STALE_SNAPSHOT, detail=f"chain data is {age} old (max allowed {max_age})"
            )
        )

    if snapshot.underlying_last_price is None:
        issues.append(
            ChainQualityIssue(
                kind=ChainQualityIssueKind.NO_UNDERLYING_PRICE, detail="snapshot carries no underlying spot price"
            )
        )

    if snapshot.legs and all((leg.open_interest or 0) == 0 and (leg.volume or 0) == 0 for leg in snapshot.legs):
        issues.append(
            ChainQualityIssue(kind=ChainQualityIssueKind.DEAD_CHAIN, detail="every leg has zero OI and zero volume")
        )

    strikes: dict[Decimal, set[OptionRight]] = {}
    for leg in snapshot.legs:
        strikes.setdefault(leg.strike, set()).add(leg.right)
    one_sided = sorted(strike for strike, sides in strikes.items() if len(sides) < 2)
    if one_sided:
        issues.append(
            ChainQualityIssue(
                kind=ChainQualityIssueKind.ONE_SIDED_STRIKE,
                detail=f"{len(one_sided)} strike(s) missing a CE or PE leg: {one_sided}",
            )
        )

    missing_greeks = [leg for leg in snapshot.legs if leg.delta is None or leg.implied_volatility is None]
    if missing_greeks:
        issues.append(
            ChainQualityIssue(
                kind=ChainQualityIssueKind.MISSING_GREEKS,
                detail=f"{len(missing_greeks)} of {len(snapshot.legs)} leg(s) missing delta and/or IV",
            )
        )

    # Sprint 6 -- a crossed market (bid > ask) is objectively impossible
    # on a real order book, never just "wide" -- checked BEFORE the
    # spread-fraction math below, since `(ask-bid)/ask` goes negative for
    # a crossed leg and would otherwise silently never trip the
    # WIDE_SPREAD check at all.
    crossed_legs = []
    for leg in snapshot.legs:
        if leg.bid_price is None or leg.ask_price is None:
            continue
        if leg.bid_price > leg.ask_price:
            crossed_legs.append(f"{leg.right.value} {leg.strike} (bid={leg.bid_price} > ask={leg.ask_price})")
    if crossed_legs:
        issues.append(
            ChainQualityIssue(
                kind=ChainQualityIssueKind.CROSSED_MARKET,
                detail=f"{len(crossed_legs)} leg(s) show bid > ask, an objectively suspicious quote state: {', '.join(crossed_legs)}",
            )
        )

    wide_spread_legs = []
    for leg in snapshot.legs:
        if leg.bid_price is None or leg.ask_price is None or leg.ask_price <= 0 or leg.bid_price > leg.ask_price:
            continue
        spread_fraction = (leg.ask_price - leg.bid_price) / leg.ask_price
        if spread_fraction > max_spread_fraction:
            wide_spread_legs.append(f"{leg.right.value} {leg.strike}")
    if wide_spread_legs:
        issues.append(
            ChainQualityIssue(
                kind=ChainQualityIssueKind.WIDE_SPREAD,
                detail=f"{len(wide_spread_legs)} leg(s) exceed {max_spread_fraction:.0%} bid/ask spread: "
                f"{', '.join(wide_spread_legs)}",
            )
        )

    return issues
