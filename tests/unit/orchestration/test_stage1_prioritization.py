"""Final release gate (Sections 4/5/6) -- Stage-1 prioritisation of
expensive Stage-2 capacity.

Two real defects were measured on a live 210-symbol scan (15 Sep 2026,
scan `8fe77965e9cc`) and fixed in `screen_universe()`. These tests pin
the corrected behaviour so neither can silently return:

1. Ordering counted bucket LABELS while calling itself "corroboration",
   even though one structural finding emits several labels (compression
   emits 3, reversal 2).
2. The final tie-break was alphabetical, so a tie decided by spelling
   excluded the SAME names on every run, forever.

Nothing here asserts a score, a probability, or any ordering by the SIZE
of a move -- those remain absent by construction.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.data.providers.base import RawOHLC, RawQuote
from app.data.providers.upstox_provider import ExchangeStatus
from app.domain.options.stage1_discovery import (
    DEVELOPING_MOMENTUM,
    EARLY_REVERSAL,
    FAILED_BREAKDOWN_RECLAIM_CANDIDATE,
    INTRADAY_COMPRESSION,
    NEAR_SESSION_BOUNDARY,
    ORDER_FLOW_PARTICIPATION,
    PRE_BREAKOUT_COMPRESSION_CANDIDATE,
    RELATIVE_STRENGTH_VS_INDEX,
    independent_dimensions,
)
from app.orchestration.daily_research import ScreeningConfig, screen_universe

AS_OF = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)


# ============================================================
# independent_dimensions -- the honest corroboration measure
# ============================================================


def test_one_compression_finding_is_one_dimension_not_three() -> None:
    """The exact inversion that made a single compression finding
    outrank two genuinely independent observations."""
    compression = [INTRADAY_COMPRESSION, NEAR_SESSION_BOUNDARY, PRE_BREAKOUT_COMPRESSION_CANDIDATE]
    two_real = [DEVELOPING_MOMENTUM, ORDER_FLOW_PARTICIPATION]

    assert len(compression) > len(two_real)  # label count says compression "wins"
    assert len(independent_dimensions(compression)) == 1
    assert len(independent_dimensions(two_real)) == 2  # corroboration says otherwise


def test_one_reversal_finding_is_one_dimension_not_two() -> None:
    assert len(independent_dimensions([EARLY_REVERSAL, FAILED_BREAKDOWN_RECLAIM_CANDIDATE])) == 1


def test_genuinely_independent_observations_each_count_once() -> None:
    observations = [
        DEVELOPING_MOMENTUM, ORDER_FLOW_PARTICIPATION, RELATIVE_STRENGTH_VS_INDEX,
        EARLY_REVERSAL, FAILED_BREAKDOWN_RECLAIM_CANDIDATE, INTRADAY_COMPRESSION,
    ]
    assert independent_dimensions(observations) == frozenset(
        {"MOMENTUM", "ORDER_FLOW", "RELATIVE_STRENGTH", "REVERSAL", "COMPRESSION"}
    )


def test_an_unrecognised_bucket_still_counts_as_its_own_dimension() -> None:
    """A newly added bucket must never silently stop counting as
    evidence just because this map has not been updated."""
    assert independent_dimensions(["SOME_FUTURE_BUCKET"]) == frozenset({"SOME_FUTURE_BUCKET"})


def test_no_dimension_outweighs_another() -> None:
    """Every dimension counts exactly 1 -- there is no weighting, so this
    can never become a score."""
    for bucket in (DEVELOPING_MOMENTUM, ORDER_FLOW_PARTICIPATION, RELATIVE_STRENGTH_VS_INDEX):
        assert len(independent_dimensions([bucket])) == 1


# ============================================================
# screen_universe ordering -- the fair, reproducible tie-break
# ============================================================


_EXPIRY_MS = 1790676000000  # 2026-09-29, a real upcoming monthly expiry relative to AS_OF


def _master(symbols: list[str]) -> list[dict[str, object]]:
    """Each symbol needs real F&O rows or Stage 1 rejects it outright for
    having no valid upcoming expiry (which is correct behaviour, just not
    what these tests are about)."""
    rows: list[dict[str, object]] = []
    for s in symbols:
        rows.append({"segment": "NSE_EQ", "name": s, "exchange": "NSE", "instrument_type": "EQ",
                     "instrument_key": f"NSE_EQ|{s}", "trading_symbol": s})
        for kind in ("CE", "PE", "FUT"):
            row: dict[str, object] = {
                "segment": "NSE_FO", "underlying_symbol": s, "instrument_type": kind,
                "expiry": _EXPIRY_MS, "weekly": False, "lot_size": 500,
                "instrument_key": f"NSE_FO|{s}{kind}",
            }
            if kind != "FUT":
                row["strike_price"] = 100.0
            rows.append(row)
    return rows


class _FakeQuotes:
    """Every symbol gets the IDENTICAL quote, so every one matches the
    identical buckets and lands in one big tie -- isolating the
    tie-break itself, which is what is under test."""

    name = "fake"

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols

    async def get_quotes(self, security_ids: list[str]) -> dict[str, RawQuote]:
        return {
            key: RawQuote(
                security_id=key, last_price=100.0, previous_close=99.0, volume=1_000_000,
                average_price=100.0, exchange_timestamp=AS_OF,
                ohlc=RawOHLC(open=99.0, high=101.0, low=99.0, close=100.0),
                total_buy_quantity=3_000_000, total_sell_quantity=1_000_000,
            )
            for key in security_ids
        }


def _survivors(symbols: list[str], *, as_of: datetime, cap: int) -> list[str]:
    result = asyncio.run(screen_universe(
        symbols, provider=_FakeQuotes(symbols), instrument_master=_master(symbols),
        as_of=as_of, exchange_status=ExchangeStatus.NORMAL_CLOSE,
        config=ScreeningConfig(survivor_cap=cap),
    ))
    return result.survivors


_TIED = [f"SYM{i:03d}" for i in range(60)]


def test_the_tie_break_is_not_alphabetical() -> None:
    """The measured defect: on a real scan every truncated name with
    identical evidence sorted after the promoted ones, so names late in
    the alphabet could never be deep-analyzed."""
    survivors = _survivors(_TIED, as_of=AS_OF, cap=20)

    assert len(survivors) == 20
    assert survivors != sorted(_TIED)[:20], "tie-break fell back to alphabetical order"


def test_the_same_session_is_fully_reproducible() -> None:
    """Rotation must never cost reproducibility -- re-running the same
    session must yield the identical selection."""
    first = _survivors(_TIED, as_of=AS_OF, cap=20)
    again = _survivors(_TIED, as_of=AS_OF.replace(hour=9, minute=45), cap=20)

    assert first == again


def test_a_different_session_rotates_which_tied_names_get_capacity() -> None:
    """So that no equally-qualified name is permanently unreachable."""
    day_one = set(_survivors(_TIED, as_of=AS_OF, cap=20))
    day_two = set(_survivors(_TIED, as_of=AS_OF.replace(day=16), cap=20))

    assert day_one != day_two


def test_real_corroboration_always_outranks_the_rotation() -> None:
    """The tie-break may only order candidates already equal on real
    evidence -- it must never move a weaker candidate past a stronger
    one. Checked across many sessions, so this proves the ordering, not
    one lucky date."""
    strong, weak = "ZZZ_STRONG", "AAA_WEAK"
    symbols = [strong, weak]

    class _Mixed:
        name = "fake"

        def __init__(self, stamp: datetime) -> None:
            self._stamp = stamp

        async def get_quotes(self, security_ids: list[str]) -> dict[str, RawQuote]:
            out: dict[str, RawQuote] = {}
            for key in security_ids:
                if key.endswith(strong):
                    # a real move, still near its own day VWAP, closing at
                    # the session high with a real buy/sell imbalance
                    out[key] = RawQuote(
                        security_id=key, last_price=100.9, previous_close=100.0, volume=1_000_000,
                        average_price=100.85, exchange_timestamp=self._stamp,
                        ohlc=RawOHLC(open=100.0, high=100.9, low=100.0, close=100.9),
                        total_buy_quantity=3_000_000, total_sell_quantity=1_000_000,
                    )
                else:
                    # order-flow imbalance ALONE on a completely flat tape
                    out[key] = RawQuote(
                        security_id=key, last_price=100.0, previous_close=100.0, volume=1_000_000,
                        average_price=100.0, exchange_timestamp=self._stamp,
                        ohlc=RawOHLC(open=100.0, high=100.0, low=100.0, close=100.0),
                        total_buy_quantity=3_000_000, total_sell_quantity=1_000_000,
                    )
            return out

    checked = 0
    for day in range(1, 26):
        as_of = AS_OF.replace(day=day)
        result = asyncio.run(screen_universe(
            symbols, provider=_Mixed(as_of), instrument_master=_master(symbols),
            as_of=as_of, exchange_status=ExchangeStatus.NORMAL_CLOSE,
            config=ScreeningConfig(survivor_cap=1),
        ))
        if not result.survivors:
            continue  # non-trading day for this fixture -- nothing to assert
        checked += 1
        assert result.survivors == [strong], f"rotation overrode real corroboration on day {day}"
    assert checked >= 10, f"expected many sessions to exercise the ordering, got {checked}"


def test_deferred_candidates_are_reported_as_capacity_not_as_rejection() -> None:
    """Section 5/12 -- a deferred candidate must never read as a finding
    that the name was uninteresting."""
    symbols = _TIED[:40]
    result = asyncio.run(screen_universe(
        symbols, provider=_FakeQuotes(symbols), instrument_master=_master(symbols),
        as_of=AS_OF, exchange_status=ExchangeStatus.NORMAL_CLOSE, config=ScreeningConfig(survivor_cap=10),
    ))

    deferred = [r for r in result.rejected if "STAGE_2_SKIPPED_CAPACITY" in r.reason]
    assert len(deferred) == 30
    assert result.truncated_count == 30
    assert result.survivor_cap_applied is True
    reason = deferred[0].reason
    assert "independent evidence dimension" in reason
    assert "NOT a finding that this name is uninteresting" in reason


# ============================================================
# Deferred is not a coverage FAILURE (final release gate, Section 5/12)
# ============================================================


def test_a_deferred_candidate_is_never_counted_as_a_coverage_failure() -> None:
    """Regression: when the deferral wording changed, these records fell
    through `categorize_rejection()` to `OTHER` -- which coverage counts
    as a reliability failure -- and a real 210/210 Stage-1 screen was
    reported as `stage1_successful: 35` with coverage `POOR`. A capacity
    deferral means the symbol WAS reliably screened; it must never make
    coverage look worse than the run actually was."""
    from app.orchestration.daily_research import (
        RejectionRecord,
        _reliability_failed_symbols,
        categorize_rejection,
    )

    deferred = RejectionRecord(
        symbol="SBIN",
        reason=(
            "STAGE_2_SKIPPED_CAPACITY: matched DEVELOPING_MOMENTUM, ORDER_FLOW_PARTICIPATION "
            "(2 independent evidence dimension(s)) and remains a real Stage-1 candidate, but Stage-2 "
            "capacity is 30 analyses per run. Deferred for capacity only -- NOT a finding that this "
            "name is uninteresting, and not a rejection of its evidence."
        ),
    )

    assert categorize_rejection(deferred.reason) != "OTHER"
    assert categorize_rejection(deferred.reason).startswith("STAGE_2_DEFERRED_CAPACITY")
    assert _reliability_failed_symbols([deferred]) == set()
