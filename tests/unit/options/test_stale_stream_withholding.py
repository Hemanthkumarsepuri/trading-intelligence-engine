"""Adversarial freshness: a stale stream must not vote; other streams may."""

from __future__ import annotations

from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceGroup,
    EvidenceRow,
    withhold_stale_stream_rows,
)


def _row(name: str, group: EvidenceGroup, direction: EvidenceDirection = EvidenceDirection.BULLISH) -> EvidenceRow:
    return EvidenceRow(name, group, direction, "live reading")


def test_stale_chain_withholds_oi_iv_liquidity_but_not_m15() -> None:
    rows = [
        _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE),
        _row("Volume", EvidenceGroup.OPTIONS_OI),
        _row("IV", EvidenceGroup.OPTIONS_IV),
        _row("Liquidity", EvidenceGroup.LIQUIDITY),
        _row("Spot/Futures basis", EvidenceGroup.FUTURES),
    ]
    gated = withhold_stale_stream_rows(
        rows, chain_is_current=False, futures_are_current=True, quote_is_current=True,
    )
    by_name = {r.name: r for r in gated}
    assert by_name["M15 trend"].direction == EvidenceDirection.BULLISH
    assert by_name["Volume"].direction == EvidenceDirection.UNKNOWN
    assert by_name["IV"].direction == EvidenceDirection.UNKNOWN
    assert by_name["Liquidity"].direction == EvidenceDirection.UNKNOWN
    assert by_name["Spot/Futures basis"].direction == EvidenceDirection.BULLISH
    assert "withheld" in by_name["Volume"].detail


def test_stale_futures_withholds_only_futures_group() -> None:
    rows = [
        _row("Volume", EvidenceGroup.OPTIONS_OI),
        _row("Spot/Futures basis", EvidenceGroup.FUTURES),
        _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE),
    ]
    gated = withhold_stale_stream_rows(
        rows, chain_is_current=True, futures_are_current=False, quote_is_current=True,
    )
    by_name = {r.name: r for r in gated}
    assert by_name["Volume"].direction == EvidenceDirection.BULLISH
    assert by_name["Spot/Futures basis"].direction == EvidenceDirection.UNKNOWN
    assert by_name["M15 trend"].direction == EvidenceDirection.BULLISH


def test_stale_quote_withholds_relative_strength_only() -> None:
    rows = [
        _row("Relative strength", EvidenceGroup.RELATIVE_STRENGTH),
        _row("Volume", EvidenceGroup.OPTIONS_OI),
        _row("M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE),
    ]
    gated = withhold_stale_stream_rows(
        rows, chain_is_current=True, futures_are_current=True, quote_is_current=False,
    )
    by_name = {r.name: r for r in gated}
    assert by_name["Relative strength"].direction == EvidenceDirection.UNKNOWN
    assert by_name["Volume"].direction == EvidenceDirection.BULLISH
    assert by_name["M15 trend"].direction == EvidenceDirection.BULLISH
