"""The cross-evidence engine — the most important module in this package.

Builds an explicit evidence matrix: one row per observable dimension
(M15 trend, VWAP, spot/futures basis, futures OI, call/put OI structure,
change in OI, volume, IV, IV skew, support, resistance, liquidity, market
regime, data freshness), each tagged with a `EvidenceDirection`
(BULLISH/BEARISH/NEUTRAL/UNKNOWN) and an `EvidenceGroup`.

The `EvidenceGroup` tag is the anti-gaming mechanism this milestone
explicitly requires: several rows legitimately trace back to the SAME
underlying data (e.g. "M15 trend" and "VWAP" both come from the same M15
candle series; "Call OI structure", "Put OI structure", "Change in OI",
"Volume", "Support", and "Resistance" all come from the same option-chain
fetch). Convergence is computed PER GROUP first, and only a group's single
resulting verdict counts toward the overall convergence/conflict decision
— five rows from one data source can never masquerade as five independent
confirmations.

Some rows are deliberately never directional (`IV`, `Liquidity`,
`Data freshness` always return NEUTRAL or UNKNOWN) — they are real
evidence for option/liquidity/data quality, not for market bias, and
forcing them into a bullish/bearish label would be exactly the kind of
fabricated interpretation this project forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.domain.news.models import NewsItem
from app.domain.options.global_context import GlobalContextAssessment, GlobalContextVerdict
from app.domain.options.iv_context import AtmIvSummary
from app.domain.options.liquidity import LiquidityGrade
from app.domain.options.market_regime import MarketRegime, MarketRegimeResult
from app.domain.options.price_oi_interpretation import PriceOIObservation, PriceOIQuadrant
from app.domain.options.support_resistance import Level
from app.domain.technical.ema_alignment import EMAAlignmentState
from app.domain.technical.series import IndicatorStatus
from app.domain.technical.vwap_position import VWAPPositionState


class EvidenceDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class EvidenceGroup(str, Enum):
    UNDERLYING_PRICE_STRUCTURE = "underlying_price_structure"  # M15 candles: EMA, VWAP, market regime
    FUTURES = "futures"  # futures LTP/OI
    OPTIONS_OI = "options_oi"  # one option-chain fetch: call/put OI, change-in-OI, volume, support, resistance
    OPTIONS_IV = "options_iv"  # same chain fetch, but IV is a distinct measurement (volatility, not positioning)
    LIQUIDITY = "liquidity"  # never directional
    DATA_QUALITY = "data_quality"  # never directional
    GLOBAL = "global"  # NIFTY/Nifty Bank/India VIX/crude -- market-wide, not stock-specific
    NEWS_EVENT = "news_event"  # real per-company headlines -- never directional (see row_news())
    # Sprint 7A, Objective 6 -- the underlying's OWN real day-change vs
    # NIFTY's, a genuinely independent evidence group: it reads the SAME
    # underlying-quote data GLOBAL/UNDERLYING_PRICE_STRUCTURE already use,
    # but compares it against a peer rather than describing it in
    # isolation -- a real, different question ("is THIS stock behaving
    # differently from the market today"), never a re-scoring of an
    # existing row.
    RELATIVE_STRENGTH = "relative_strength"


@dataclass(frozen=True)
class EvidenceRow:
    name: str
    group: EvidenceGroup
    direction: EvidenceDirection
    detail: str


def withhold_stale_stream_rows(
    rows: list[EvidenceRow],
    *,
    chain_is_current: bool,
    futures_are_current: bool,
    quote_is_current: bool,
) -> list[EvidenceRow]:
    """Staleness of one stream must not vote. Chain-derived groups, futures
    rows, and quote-derived relative strength are independently gated --
    matching M15 `data_is_current` on price-structure rows.
    """
    chain_groups = {EvidenceGroup.OPTIONS_OI, EvidenceGroup.OPTIONS_IV, EvidenceGroup.LIQUIDITY}
    gated: list[EvidenceRow] = []
    for row in rows:
        if row.group in chain_groups and not chain_is_current:
            gated.append(EvidenceRow(
                row.name, row.group, EvidenceDirection.UNKNOWN,
                "option-chain snapshot is not current -- withheld, not used as a current-session vote",
            ))
        elif row.group == EvidenceGroup.FUTURES and not futures_are_current:
            gated.append(EvidenceRow(
                row.name, row.group, EvidenceDirection.UNKNOWN,
                "futures quote is not current -- withheld, not used as a current-session vote",
            ))
        elif row.group == EvidenceGroup.RELATIVE_STRENGTH and not quote_is_current:
            gated.append(EvidenceRow(
                row.name, row.group, EvidenceDirection.UNKNOWN,
                "underlying quote is not current -- relative strength withheld",
            ))
        else:
            gated.append(row)
    return gated


class GroupVerdict(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    CONFLICTING = "CONFLICTING"  # this one group's own rows disagree with each other
    NON_DIRECTIONAL = "NON_DIRECTIONAL"  # every row in the group is NEUTRAL/UNKNOWN


class OverallConvergence(str, Enum):
    CONVERGENCE_BULLISH = "CONVERGENCE_BULLISH"
    CONVERGENCE_BEARISH = "CONVERGENCE_BEARISH"
    CONFLICT = "CONFLICT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class EvidenceMatrix:
    rows: list[EvidenceRow]

    def group_verdict(self, group: EvidenceGroup) -> GroupVerdict:
        directions = {r.direction for r in self.rows if r.group == group}
        directional = directions & {EvidenceDirection.BULLISH, EvidenceDirection.BEARISH}
        if not directional:
            return GroupVerdict.NON_DIRECTIONAL
        if directional == {EvidenceDirection.BULLISH}:
            return GroupVerdict.BULLISH
        if directional == {EvidenceDirection.BEARISH}:
            return GroupVerdict.BEARISH
        return GroupVerdict.CONFLICTING

    def group_verdicts(self) -> dict[EvidenceGroup, GroupVerdict]:
        groups = {r.group for r in self.rows}
        return {g: self.group_verdict(g) for g in groups}

    def overall_convergence(self) -> OverallConvergence:
        """Computed from PER-GROUP verdicts, never raw row counts — see
        module docstring. A group whose own rows conflict contributes
        neither a bullish nor a bearish vote (it is evidence of
        disagreement, not of a direction).
        """
        verdicts = self.group_verdicts().values()
        bullish_groups = sum(1 for v in verdicts if v == GroupVerdict.BULLISH)
        bearish_groups = sum(1 for v in verdicts if v == GroupVerdict.BEARISH)
        conflicting_groups = sum(1 for v in verdicts if v == GroupVerdict.CONFLICTING)

        if bullish_groups > 0 and bearish_groups == 0 and conflicting_groups == 0:
            return OverallConvergence.CONVERGENCE_BULLISH
        if bearish_groups > 0 and bullish_groups == 0 and conflicting_groups == 0:
            return OverallConvergence.CONVERGENCE_BEARISH
        if bullish_groups == 0 and bearish_groups == 0 and conflicting_groups == 0:
            return OverallConvergence.INSUFFICIENT_EVIDENCE
        return OverallConvergence.CONFLICT

    def supporting_count(self, direction: EvidenceDirection) -> int:
        return sum(1 for r in self.rows if r.direction == direction)

    def supporting_group_count(self, direction: EvidenceDirection) -> int:
        """Final Hardening Pass, Phase 20 (evidence double-counting audit)
        -- the group-aware counterpart to `supporting_count()`. Real,
        demonstrated defect: several rows within ONE `EvidenceGroup` (e.g.
        `OPTIONS_OI`'s "PCR change"/"Change in OI (ATM CE)"/"Change in OI
        (ATM PE)") can all read BULLISH from what is genuinely the SAME
        underlying option-chain OI data sliced three ways -- `supporting_
        count()` would report 3 "supporting rows" even though only ONE
        independent evidence dimension actually agrees, exactly the kind
        of artificial thesis-strengthening `overall_convergence()` already
        guards against for the bullish/bearish DECISION but which nothing
        previously guarded for the EVIDENCE-QUALITY magnitude reported to
        the user. Counts DISTINCT groups whose own `group_verdict()`
        equals `direction` -- never raw rows. Only BULLISH/BEARISH are
        meaningful directions here (a group's verdict is never NEUTRAL/
        UNKNOWN); any other `direction` argument correctly returns 0.
        """
        if direction not in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH):
            return 0
        target = GroupVerdict.BULLISH if direction == EvidenceDirection.BULLISH else GroupVerdict.BEARISH
        return sum(1 for v in self.group_verdicts().values() if v == target)


# -- individual row builders --------------------------------------------
# Each function takes only already-computed results (never re-fetches, never
# reads the clock) and returns exactly one row. Conventions used below that
# are not plain observable facts are explicitly labeled as conventions in
# the row's `detail` text -- never presented as confirmed fact.


def row_m15_trend(*, ema_alignment: EMAAlignmentState | None, ema_status: IndicatorStatus, data_is_current: bool = True) -> EvidenceRow:
    """Sprint 7A -- `data_is_current` (defaulted `True`, so every existing
    caller is unaffected) is the market-data-synchronization gate: `False`
    means the real M15 candle series this was computed from is STALE
    relative to the current live session (see
    `_candle_series_is_current()` in the pipeline) -- a current-session
    quote must never silently be interpreted alongside previous-session
    candle-derived trend/VWAP evidence. This is deliberately a SEPARATE
    reason from `ema_status != OK` (genuinely insufficient history) --
    the history here is real and sufficient, it's just not current.
    """
    if not data_is_current:
        return EvidenceRow(
            "M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.UNKNOWN,
            "the latest real M15 candle predates the current live trading session -- not used as current-session "
            "evidence (see Data Integrity)",
        )
    if ema_status != IndicatorStatus.OK or ema_alignment is None:
        return EvidenceRow(
            "M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.UNKNOWN,
            "EMA alignment unavailable (insufficient M15 history)",
        )
    # `EMAAlignmentState` names the NUMERIC SEQUENCE in period order
    # (9, 21, 50), not a trend: `ASCENDING` means EMA9 < EMA21 < EMA50 --
    # the FASTER average sitting BELOW the slower ones, which is what a
    # FALLING tape looks like. `ema_alignment.py`'s own docstring says so
    # explicitly and warns callers not to read its label as a direction.
    #
    # This row used to do exactly that, and had the mapping backwards: it
    # reported a falling series as BULLISH evidence and a rising one as
    # BEARISH. Measured over 1,404 real M15 samples from the local
    # 45-symbol candle store, price had FALLEN over the trailing 50 bars
    # in 90.2% of `ASCENDING` samples (mean -1.68%) and RISEN in 89.3% of
    # `DESCENDING` samples (mean +1.96%). The detail text below now
    # states the geometry in words rather than repeating the ambiguous
    # sequence label, so the row cannot be misread the same way again.
    if ema_alignment == EMAAlignmentState.DESCENDING:
        return EvidenceRow(
            "M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH,
            "EMA(9,21,50): each faster average is above the slower one (EMA9 > EMA21 > EMA50) -- "
            "the ordering a rising M15 series produces",
        )
    if ema_alignment == EMAAlignmentState.ASCENDING:
        return EvidenceRow(
            "M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BEARISH,
            "EMA(9,21,50): each faster average is below the slower one (EMA9 < EMA21 < EMA50) -- "
            "the ordering a falling M15 series produces",
        )
    return EvidenceRow(
        "M15 trend", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.NEUTRAL, "EMA(9,21,50) sequence is mixed"
    )


def row_vwap(
    *,
    vwap_state: VWAPPositionState | None,
    vwap_status: IndicatorStatus,
    data_is_current: bool = True,
    session_vwap: bool = False,
) -> EvidenceRow:
    """Sprint 7A -- see `row_m15_trend()`'s own docstring for `data_is_current`.
    When `session_vwap` is True this is NSE cash-session VWAP (IST 09:15-15:30).
    Rolling multi-day VWAP must never be labeled session VWAP.
    """
    kind = "NSE cash-session VWAP (IST 09:15-15:30, session reset)" if session_vwap else (
        "rolling VWAP computed over the supplied M15 candle series (not a session VWAP)"
    )
    if not data_is_current:
        return EvidenceRow(
            "VWAP", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.UNKNOWN,
            "the latest real M15 candle predates the current live trading session -- not used as current-session "
            "evidence (see Data Integrity)",
        )
    if vwap_status != IndicatorStatus.OK or vwap_state is None:
        return EvidenceRow(
            "VWAP", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.UNKNOWN,
            "session VWAP unavailable (no current-session M15 bars)" if session_vwap
            else "VWAP position unavailable (insufficient M15 history)",
        )
    if vwap_state == VWAPPositionState.ABOVE:
        return EvidenceRow(
            "VWAP", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BULLISH,
            f"price above {kind}",
        )
    if vwap_state == VWAPPositionState.BELOW:
        return EvidenceRow(
            "VWAP", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.BEARISH,
            f"price below {kind}",
        )
    return EvidenceRow(
        "VWAP", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.NEUTRAL,
        f"price exactly at {kind}",
    )


def row_market_regime(regime: MarketRegimeResult, *, data_is_current: bool = True) -> EvidenceRow:
    """Sprint 7A -- see `row_m15_trend()`'s own docstring for
    `data_is_current`; regime is derived from the SAME real M15/ATR
    series, so it carries the same synchronization risk."""
    if not data_is_current:
        return EvidenceRow(
            "Market regime", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, EvidenceDirection.UNKNOWN,
            "the latest real M15 candle predates the current live trading session -- not used as current-session "
            "evidence (see Data Integrity)",
        )
    mapping = {
        MarketRegime.TRENDING_BULLISH: EvidenceDirection.BULLISH,
        MarketRegime.TRENDING_BEARISH: EvidenceDirection.BEARISH,
        MarketRegime.RANGE: EvidenceDirection.NEUTRAL,
        MarketRegime.HIGH_VOLATILITY: EvidenceDirection.NEUTRAL,
        MarketRegime.LOW_VOLATILITY: EvidenceDirection.NEUTRAL,
        MarketRegime.MIXED: EvidenceDirection.NEUTRAL,
        MarketRegime.DATA_INSUFFICIENT: EvidenceDirection.UNKNOWN,
    }
    return EvidenceRow(
        "Market regime", EvidenceGroup.UNDERLYING_PRICE_STRUCTURE, mapping[regime.regime],
        f"{regime.regime.value}: {regime.detail}",
    )


def row_spot_futures_basis(*, spot: Decimal | None, futures_ltp: Decimal | None) -> EvidenceRow:
    """Futures trading at a premium to spot is the NORMAL state (cost of
    carry) and is NOT treated as bullish here -- only backwardation
    (futures below spot), which is unusual for equity index/stock futures
    and conventionally read as a bearish anomaly, is treated as directional.
    This is a documented convention, not a statistically validated signal.
    """
    if spot is None or futures_ltp is None or spot <= 0:
        return EvidenceRow("Spot/Futures", EvidenceGroup.FUTURES, EvidenceDirection.UNKNOWN, "spot or futures price unavailable")
    basis_pct = (futures_ltp - spot) / spot * Decimal(100)
    if futures_ltp < spot:
        return EvidenceRow(
            "Spot/Futures", EvidenceGroup.FUTURES, EvidenceDirection.BEARISH,
            f"futures at a {basis_pct:.2f}% discount to spot (backwardation) -- unusual, conventionally bearish, unconfirmed",
        )
    return EvidenceRow(
        "Spot/Futures", EvidenceGroup.FUTURES, EvidenceDirection.NEUTRAL,
        f"futures at a {basis_pct:.2f}% premium to spot -- normal cost-of-carry state, not directional",
    )


def row_futures_oi(observation: PriceOIObservation) -> EvidenceRow:
    if observation.quadrant == PriceOIQuadrant.INSUFFICIENT_DATA:
        return EvidenceRow("Futures OI", EvidenceGroup.FUTURES, EvidenceDirection.UNKNOWN, observation.conventional_reading)
    mapping = {
        PriceOIQuadrant.PRICE_UP_OI_UP: EvidenceDirection.BULLISH,
        PriceOIQuadrant.PRICE_UP_OI_DOWN: EvidenceDirection.NEUTRAL,
        PriceOIQuadrant.PRICE_DOWN_OI_UP: EvidenceDirection.BEARISH,
        PriceOIQuadrant.PRICE_DOWN_OI_DOWN: EvidenceDirection.NEUTRAL,
        PriceOIQuadrant.PRICE_FLAT: EvidenceDirection.NEUTRAL,
    }
    return EvidenceRow("Futures OI", EvidenceGroup.FUTURES, mapping[observation.quadrant], observation.conventional_reading)


# PCR bands below are a commonly-cited RETAIL heuristic (PCR > ~1.3 read as
# oversold/contrarian-bullish, PCR < ~0.7 read as overbought/contrarian-
# bearish) -- NOT statistically validated by this system, and explicitly
# labeled as such in every row that uses them.
_PCR_BULLISH_ABOVE = Decimal("1.3")
_PCR_BEARISH_BELOW = Decimal("0.7")


def row_put_call_oi_structure(pcr_oi: Decimal | None) -> EvidenceRow:
    """Sprint 7A, Objective 4 -- PCR LEVEL ALONE is now NEVER directional
    (the prior bare-threshold BULLISH/BEARISH rule is removed). The real
    PCR value and where it sits relative to the commonly-cited bands is
    still reported as CONTEXT, but only `row_pcr_change()` (which
    additionally requires a real prior snapshot AND corroborating real
    CE/PE OI-balance movement) can ever vote a direction from PCR.
    """
    if pcr_oi is None:
        return EvidenceRow(
            "Call/Put OI structure", EvidenceGroup.OPTIONS_OI, EvidenceDirection.UNKNOWN,
            "put/call OI ratio undefined (call OI is zero or unknown)",
        )
    if pcr_oi >= _PCR_BULLISH_ABOVE:
        band = f"at/above the commonly-cited {_PCR_BULLISH_ABOVE} contrarian-bullish band"
    elif pcr_oi <= _PCR_BEARISH_BELOW:
        band = f"at/below the commonly-cited {_PCR_BEARISH_BELOW} contrarian-bearish band"
    else:
        band = "within the neutral band"
    return EvidenceRow(
        "Call/Put OI structure", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL,
        f"PCR: {pcr_oi:.2f}. Context only -- this describes current option positioning. PCR by itself is not directional confirmation. {band} (see PCR change). context only; not directional by itself",
    )


def row_pcr_change(
    *, current_pcr: Decimal | None, previous_pcr: Decimal | None,
    current_call_oi: int | None, previous_call_oi: int | None,
    current_put_oi: int | None, previous_put_oi: int | None,
    meaningful_pcr_change: Decimal, meaningful_oi_change_fraction: Decimal,
) -> EvidenceRow:
    """Sprint 7A, Objective 4 -- PCR CHANGE + OI BALANCE, corroborated.
    Directional ONLY when a real prior option-chain snapshot exists, the
    PCR's own real change is meaningful, AND the real CE/PE OI change
    corroborates which side actually drove it (same contrarian
    convention `row_put_call_oi_structure()` already documents: relative
    put-OI growth reads contrarian-bullish, relative call-OI growth reads
    contrarian-bearish) -- otherwise NEUTRAL/UNKNOWN, always. This is the
    ONLY row in this module that can turn a PCR observation into a vote.
    """
    if previous_pcr is None or current_pcr is None:
        return EvidenceRow(
            "PCR change", EvidenceGroup.OPTIONS_OI, EvidenceDirection.UNKNOWN,
            "INSUFFICIENT_HISTORY -- no real prior option-chain snapshot available yet to compare PCR against",
        )
    pcr_change = current_pcr - previous_pcr
    if abs(pcr_change) < meaningful_pcr_change:
        return EvidenceRow(
            "PCR change", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL,
            f"PCR(OI) {previous_pcr:.2f} -> {current_pcr:.2f} ({pcr_change:+.2f}) -- not a meaningful real change",
        )

    ce_change_frac = _fractional_change(current_call_oi, previous_call_oi)
    pe_change_frac = _fractional_change(current_put_oi, previous_put_oi)
    if ce_change_frac is None or pe_change_frac is None:
        return EvidenceRow(
            "PCR change", EvidenceGroup.OPTIONS_OI, EvidenceDirection.UNKNOWN,
            f"PCR(OI) {previous_pcr:.2f} -> {current_pcr:.2f} ({pcr_change:+.2f}) -- real prior CE/PE OI unavailable to corroborate",
        )

    oi_balance_favors_puts = pe_change_frac > ce_change_frac + meaningful_oi_change_fraction
    oi_balance_favors_calls = ce_change_frac > pe_change_frac + meaningful_oi_change_fraction
    oi_balance_detail = f"CE OI {ce_change_frac:+.1%} vs PE OI {pe_change_frac:+.1%}"

    if pcr_change > 0 and oi_balance_favors_puts:
        return EvidenceRow(
            "PCR change", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BULLISH,
            f"PCR(OI) rose {pcr_change:+.2f} ({previous_pcr:.2f} -> {current_pcr:.2f}), corroborated by real put OI growing "
            f"faster than call OI ({oi_balance_detail}) -- commonly-cited contrarian-bullish convention, unconfirmed",
        )
    if pcr_change < 0 and oi_balance_favors_calls:
        return EvidenceRow(
            "PCR change", EvidenceGroup.OPTIONS_OI, EvidenceDirection.BEARISH,
            f"PCR(OI) fell {pcr_change:+.2f} ({previous_pcr:.2f} -> {current_pcr:.2f}), corroborated by real call OI growing "
            f"faster than put OI ({oi_balance_detail}) -- commonly-cited contrarian-bearish convention, unconfirmed",
        )
    return EvidenceRow(
        "PCR change", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL,
        f"PCR(OI) changed {pcr_change:+.2f} ({previous_pcr:.2f} -> {current_pcr:.2f}) but the real CE/PE OI balance does "
        f"not corroborate a clear direction ({oi_balance_detail})",
    )


def _fractional_change(current: int | None, previous: int | None) -> Decimal | None:
    if current is None or previous is None or previous == 0:
        return None
    return (Decimal(current) - Decimal(previous)) / Decimal(previous)


def row_change_in_oi(observation: PriceOIObservation, *, right_label: str) -> EvidenceRow:
    if observation.quadrant == PriceOIQuadrant.INSUFFICIENT_DATA:
        return EvidenceRow(
            f"Change in OI ({right_label})", EvidenceGroup.OPTIONS_OI, EvidenceDirection.UNKNOWN, observation.conventional_reading
        )
    mapping = {
        PriceOIQuadrant.PRICE_UP_OI_UP: EvidenceDirection.BULLISH,
        PriceOIQuadrant.PRICE_UP_OI_DOWN: EvidenceDirection.NEUTRAL,
        PriceOIQuadrant.PRICE_DOWN_OI_UP: EvidenceDirection.BEARISH,
        PriceOIQuadrant.PRICE_DOWN_OI_DOWN: EvidenceDirection.NEUTRAL,
        PriceOIQuadrant.PRICE_FLAT: EvidenceDirection.NEUTRAL,
    }
    return EvidenceRow(
        f"Change in OI ({right_label})", EvidenceGroup.OPTIONS_OI, mapping[observation.quadrant], observation.conventional_reading
    )


def row_volume(*, call_volume: int, put_volume: int, min_meaningful_volume: int) -> EvidenceRow:
    """`min_meaningful_volume` is required, undefaulted -- below it, a
    volume comparison is noise, and there's no single correct floor for
    every underlying's typical activity level.
    """
    total = call_volume + put_volume
    if total < min_meaningful_volume:
        return EvidenceRow(
            "Volume", EvidenceGroup.OPTIONS_OI, EvidenceDirection.UNKNOWN,
            f"total CE+PE volume {total:,} is below the {min_meaningful_volume:,} meaningful-activity floor",
        )
    if call_volume > put_volume * 1.5:
        return EvidenceRow(
            "Volume", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL,
            f"call-side traded volume ({call_volume:,}) was materially higher than put-side volume ({put_volume:,}) -- "
            "observable activity imbalance only, not a directional vote",
        )
    if put_volume > call_volume * 1.5:
        return EvidenceRow(
            "Volume", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL,
            f"put-side traded volume ({put_volume:,}) was materially higher than call-side volume ({call_volume:,}) -- "
            "observable activity imbalance only, not a directional vote",
        )
    return EvidenceRow(
        "Volume", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL,
        f"call volume {call_volume:,} vs put volume {put_volume:,} -- no notable imbalance",
    )


def row_support(levels: list[Level], *, near_pct_threshold: Decimal) -> EvidenceRow:
    """S/R Directional-Evidence Correctness Pass -- an OI-concentration
    support candidate's mere EXISTENCE (or proximity to spot) is
    STRUCTURAL/CONTEXTUAL information, never bullish evidence on its own.
    This codebase has no real price-reaction confirmation mechanism (no
    "price held/rejected this level" signal exists anywhere) -- so this
    row is always NEUTRAL when a candidate exists, and UNKNOWN when none
    does. `near_pct_threshold` still shapes the wording (a level worth
    mentioning as "nearby" vs. "distant"), but never the direction --
    inventing a directional read from proximity alone was the exact
    defect this fixes. If a genuine price-reaction confirmation signal is
    ever added to this codebase, THAT signal (not proximity) is what
    should drive a directional read here.
    """
    supports = [lv for lv in levels if lv.kind.value == "support" and lv.distance_from_spot_pct is not None]
    if not supports:
        return EvidenceRow("Support", EvidenceGroup.OPTIONS_OI, EvidenceDirection.UNKNOWN, "no support candidate available")
    nearest = min(supports, key=lambda lv: abs(lv.distance_from_spot_pct))  # type: ignore[arg-type]
    pct = nearest.distance_from_spot_pct
    assert pct is not None
    proximity = "nearby" if abs(pct) <= near_pct_threshold else "not close enough to be immediately relevant"
    return EvidenceRow(
        "Support", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL,
        f"nearest support candidate at strike {nearest.strike} is {abs(pct):.2f}% below spot ({nearest.strength.value}, {proximity}) "
        f"-- a structural OI concentration only, not confirmed directional evidence",
    )


def row_resistance(levels: list[Level], *, near_pct_threshold: Decimal) -> EvidenceRow:
    """Mirrors `row_support()` exactly -- see its docstring."""
    resistances = [lv for lv in levels if lv.kind.value == "resistance" and lv.distance_from_spot_pct is not None]
    if not resistances:
        return EvidenceRow("Resistance", EvidenceGroup.OPTIONS_OI, EvidenceDirection.UNKNOWN, "no resistance candidate available")
    nearest = min(resistances, key=lambda lv: abs(lv.distance_from_spot_pct))  # type: ignore[arg-type]
    pct = nearest.distance_from_spot_pct
    assert pct is not None
    proximity = "nearby" if abs(pct) <= near_pct_threshold else "not close enough to be immediately relevant"
    return EvidenceRow(
        "Resistance", EvidenceGroup.OPTIONS_OI, EvidenceDirection.NEUTRAL,
        f"nearest resistance candidate at strike {nearest.strike} is {abs(pct):.2f}% above spot ({nearest.strength.value}, {proximity}) "
        f"-- a structural OI concentration only, not confirmed directional evidence",
    )


def row_iv_level(iv: AtmIvSummary) -> EvidenceRow:
    """IV level is NEVER directional on the underlying -- high IV means
    "a big move is priced in", not "up" or "down". Always NEUTRAL/UNKNOWN.
    """
    if iv.chain_iv is None:
        return EvidenceRow("IV", EvidenceGroup.OPTIONS_IV, EvidenceDirection.UNKNOWN, "ATM IV unavailable")
    return EvidenceRow("IV", EvidenceGroup.OPTIONS_IV, EvidenceDirection.NEUTRAL, f"ATM IV {iv.chain_iv:.2f}% -- not directional by itself")


_UNTRADEABLE_SKEW_LIQUIDITY = (LiquidityGrade.POOR, LiquidityGrade.UNTRADEABLE)


def row_iv_skew(
    iv: AtmIvSummary, *, meaningful_skew: Decimal,
    ce_liquidity_grade: LiquidityGrade | None, pe_liquidity_grade: LiquidityGrade | None,
) -> EvidenceRow:
    """A meaningfully richer PUT side (higher PE IV) is conventionally
    read as more hedging/fear demand (bearish lean); a richer CALL side as
    more speculative call demand (bullish lean). Both are contested,
    unvalidated conventions -- labeled as such.

    Sprint 6 hardening: a bare point-difference crossing `meaningful_skew`
    is NOT, by itself, enough to promote this row to a directional vote.
    Both ATM legs' own already-computed `LiquidityAssessment.grade`
    (`assess_liquidity()` -- the same real bid/ask/OI/volume/staleness
    rubric `row_liquidity()` already reuses, never a new calculation) must
    be at least POOR-excluded (not POOR/UNTRADEABLE) for the skew to be
    trusted as genuinely observed on two tradeable sides -- an IV
    "skew" computed from one or two barely-quoted legs is not a real
    market signal, just noise in a thin quote. When either side isn't
    genuinely tradable, this degrades to NEUTRAL/UNCONFIRMED regardless of
    the raw point gap.
    """
    if iv.ce_pe_skew is None:
        return EvidenceRow("IV skew", EvidenceGroup.OPTIONS_IV, EvidenceDirection.UNKNOWN, "CE/PE IV skew unavailable (one or both sides missing)")
    skew = iv.ce_pe_skew
    if abs(skew) < meaningful_skew:
        return EvidenceRow("IV skew", EvidenceGroup.OPTIONS_IV, EvidenceDirection.NEUTRAL, f"CE/PE IV skew is {skew:.2f} pts -- not meaningful")
    both_sides_tradeable = (
        ce_liquidity_grade is not None and ce_liquidity_grade not in _UNTRADEABLE_SKEW_LIQUIDITY
        and pe_liquidity_grade is not None and pe_liquidity_grade not in _UNTRADEABLE_SKEW_LIQUIDITY
    )
    if not both_sides_tradeable:
        return EvidenceRow(
            "IV skew", EvidenceGroup.OPTIONS_IV, EvidenceDirection.NEUTRAL,
            f"CE/PE IV skew is {skew:.2f} pts, but at least one ATM leg's own liquidity grade is too thin "
            f"(CE={ce_liquidity_grade.value if ce_liquidity_grade else 'n/a'}, PE={pe_liquidity_grade.value if pe_liquidity_grade else 'n/a'}) "
            "to trust this as a genuinely observed two-sided skew -- UNCONFIRMED",
        )
    if skew < 0:
        return EvidenceRow(
            "IV skew", EvidenceGroup.OPTIONS_IV, EvidenceDirection.BEARISH,
            f"PE IV exceeds CE IV by {abs(skew):.2f} pts on two genuinely tradeable ATM legs -- conventionally read as put-side richer (fear/hedging), unconfirmed",
        )
    return EvidenceRow(
        "IV skew", EvidenceGroup.OPTIONS_IV, EvidenceDirection.BULLISH,
        f"CE IV exceeds PE IV by {skew:.2f} pts on two genuinely tradeable ATM legs -- conventionally read as call-side richer (speculative demand), unconfirmed",
    )


_NO_SECTOR_SOURCE = (
    "STOCK vs SECTOR is reported separately as informational context when the official Nifty 500 "
    "industry map resolved; this row votes only STOCK vs NIFTY (a residual vs the index, not a second "
    "copy of the GLOBAL Nifty day-change vote)"
)


def row_relative_strength(
    *, underlying_day_change_pct: Decimal | None, nifty_day_change_pct: Decimal | None, meaningful_gap_pct: Decimal,
) -> EvidenceRow:
    """Sprint 7A, Objective 6 -- a lightweight, deterministic relative-
    strength read: the underlying's own real day-change-so-far vs
    NIFTY 50's, the SAME real comparable "day change so far" window both
    values already use (no new fetch -- `underlying_day_change_pct` is
    `report.day_change_pct`, already computed from the real quote;
    `nifty_day_change_pct` is the SAME real value `global_context.py`
    already computes). `UNKNOWN` whenever either real value is missing --
    never approximated or backfilled. Never claims WHY a stock diverges
    from the index, only THAT it did, today, by this real margin.

    Sprint 7B, Objective 4 -- STOCK vs SECTOR is deliberately NOT
    fabricated here: no authorized sector-classification or sector-index
    data source exists anywhere in this system's data layer, so every
    detail string below says so explicitly rather than silently omitting
    it or inventing a sector proxy. STOCK vs NIFTY (this row's real,
    computed verdict) remains the only relative-strength comparison this
    system can honestly support today.
    """
    if underlying_day_change_pct is None or nifty_day_change_pct is None:
        return EvidenceRow(
            "Relative strength", EvidenceGroup.RELATIVE_STRENGTH, EvidenceDirection.UNKNOWN,
            f"real underlying day-change or real NIFTY 50 day-change unavailable this run; {_NO_SECTOR_SOURCE}",
        )
    gap = underlying_day_change_pct - nifty_day_change_pct
    if abs(gap) < meaningful_gap_pct:
        return EvidenceRow(
            "Relative strength", EvidenceGroup.RELATIVE_STRENGTH, EvidenceDirection.NEUTRAL,
            f"underlying {underlying_day_change_pct:+.2f}% vs NIFTY 50 {nifty_day_change_pct:+.2f}% -- gap of {gap:+.2f}pts is not meaningful; {_NO_SECTOR_SOURCE}",
        )
    if gap > 0:
        return EvidenceRow(
            "Relative strength", EvidenceGroup.RELATIVE_STRENGTH, EvidenceDirection.BULLISH,
            f"OUTPERFORMING: underlying {underlying_day_change_pct:+.2f}% vs NIFTY 50 {nifty_day_change_pct:+.2f}% (gap {gap:+.2f}pts), this session only; {_NO_SECTOR_SOURCE}",
        )
    return EvidenceRow(
        "Relative strength", EvidenceGroup.RELATIVE_STRENGTH, EvidenceDirection.BEARISH,
        f"UNDERPERFORMING: underlying {underlying_day_change_pct:+.2f}% vs NIFTY 50 {nifty_day_change_pct:+.2f}% (gap {gap:+.2f}pts), this session only; {_NO_SECTOR_SOURCE}",
    )


def row_liquidity(best_candidate_grade: LiquidityGrade | None) -> EvidenceRow:
    """Never directional -- liquidity is a tradability gate, not a market
    bias signal.
    """
    if best_candidate_grade is None:
        return EvidenceRow("Liquidity", EvidenceGroup.LIQUIDITY, EvidenceDirection.UNKNOWN, "no candidate leg was assessed")
    return EvidenceRow("Liquidity", EvidenceGroup.LIQUIDITY, EvidenceDirection.NEUTRAL, f"best candidate liquidity grade: {best_candidate_grade.value}")


def row_data_freshness(*, is_fresh: bool, detail: str) -> EvidenceRow:
    """Never directional -- a gate on whether the rest of the matrix should
    be trusted at all, not itself a bias signal.
    """
    return EvidenceRow("Data freshness", EvidenceGroup.DATA_QUALITY, EvidenceDirection.NEUTRAL if is_fresh else EvidenceDirection.UNKNOWN, detail)


_GLOBAL_DIRECTION = {
    GlobalContextVerdict.TAILWIND: EvidenceDirection.BULLISH,
    GlobalContextVerdict.HEADWIND: EvidenceDirection.BEARISH,
    GlobalContextVerdict.MIXED: EvidenceDirection.NEUTRAL,
    GlobalContextVerdict.LOW_RELEVANCE: EvidenceDirection.NEUTRAL,
    GlobalContextVerdict.INSUFFICIENT_DATA: EvidenceDirection.UNKNOWN,
}


def row_global_context(assessment: GlobalContextAssessment) -> EvidenceRow:
    """Market-wide, not stock-specific — see `global_context.py`'s module
    docstring for why this can never claim per-stock sector relevance.
    """
    return EvidenceRow("Global context", EvidenceGroup.GLOBAL, _GLOBAL_DIRECTION[assessment.verdict], assessment.detail)


def row_news(items: list[NewsItem], *, fetch_error: str | None) -> EvidenceRow:
    """Never directional (Sprint 4, Part C: "A news article must NOT
    directly override technical/options evidence") — every real item this
    system has already carries `direction=NewsDirection.UNKNOWN` by
    construction (`app.domain.news.models.build_news_item()`), so this row
    can never vote BULLISH/BEARISH in `overall_convergence()` regardless of
    how many items exist. It exists purely so the presence/freshness/
    absence of real news is visible as its own independent evidence group,
    exactly like `Liquidity`/`Data freshness` already are.
    """
    if fetch_error is not None:
        return EvidenceRow("News/Events", EvidenceGroup.NEWS_EVENT, EvidenceDirection.UNKNOWN, f"news fetch failed: {fetch_error}")
    if not items:
        return EvidenceRow(
            "News/Events", EvidenceGroup.NEWS_EVENT, EvidenceDirection.NEUTRAL,
            "no recent company news found via the authorized source",
        )
    most_recent = max(items, key=lambda i: i.published_at)
    return EvidenceRow(
        "News/Events", EvidenceGroup.NEWS_EVENT, EvidenceDirection.NEUTRAL,
        f"{len(items)} recent item(s); most recent: {most_recent.title!r} ({most_recent.published_at.isoformat()}) "
        "-- content not directionally classified (no legitimate sentiment source)",
    )
