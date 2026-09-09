"""Structured "why isn't this confirmed/tradeable" explanation -- the
generic fix for the real KAYNES UAT defect (`docs/TIRE_OPERATOR_UAT.md`,
Defect 2): the top-line DEVELOPING OBSERVATION narrative talked about
price/chain confirmation while the actual, more fundamental reason the
analysis was NO_TRADE was contract illiquidity. Both facts were real and
already computed elsewhere in the pipeline (`DecisionResult.reasoning`,
`DevelopmentNarrative.what_is_missing`, per-stream freshness) -- they were
simply never reconciled into one explanation with an explicit priority
order, so whichever text happened to render first (the development
narrative) looked like the whole story.

This module does not compute anything new. It reads already-computed
`DecisionResult`, freshness booleans, and `DevelopmentNarrative` fields and
assigns them a deterministic priority so the strongest, most specific
blocking reason is always the one labeled PRIMARY -- never string-matching,
never a hidden score, never a new evidence rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from app.domain.options.decision_engine import (
    EXTENDED_MIN_DAY_CHANGE_PCT,
    DecisionResult,
    FinalDecision,
    QualityLevel,
)
from app.domain.options.development import DevelopmentNarrative
from app.domain.options.evidence_matrix import OverallConvergence


class BlockerClass(str, Enum):
    """Deterministic precedence, most fundamental first -- see
    `determine_blockers()`'s own docstring for why this exact order (it is
    NOT the same order as `derive_research_state()`'s research-STATE
    precedence, which is a different question: "how mature is the
    situation" vs. this module's "what is the single strongest reason
    nothing more decisive can be said yet")."""

    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    CONFLICT = "CONFLICT"
    CONTRACT_UNUSABLE = "CONTRACT_UNUSABLE"
    CORE_STREAM_STALE = "CORE_STREAM_STALE"
    NO_DIRECTIONAL_BIAS = "NO_DIRECTIONAL_BIAS"
    EXTENDED = "EXTENDED"
    REQUIRED_CONFIRMATION_MISSING = "REQUIRED_CONFIRMATION_MISSING"
    NONE = "NONE"


@dataclass(frozen=True)
class ResearchBlocker:
    blocker_class: BlockerClass
    explanation: str


@dataclass(frozen=True)
class BlockerAssessment:
    """`primary` is the single reason a UI must lead with -- never
    contradicted by, and never buried below, a different explanation
    elsewhere on the page (the exact failure mode the real KAYNES case
    demonstrated). `secondary` lists every OTHER blocker class that is
    also independently true right now (e.g. a stale M15 candle stream
    alongside an illiquid contract) -- both real, neither hidden.
    `missing_confirmation` is a separate, narrower question ("what would
    need to happen for CONFIRMED_SETUP") that may still apply even when
    `primary` is something else entirely (e.g. CONTRACT_UNUSABLE can
    still have outstanding price/chain confirmation pending on top)."""

    primary: ResearchBlocker
    secondary: tuple[ResearchBlocker, ...] = field(default_factory=tuple)
    missing_confirmation: str | None = None


def _stale_streams(*, candles_are_current: bool, chain_is_current: bool, quote_is_current: bool) -> list[str]:
    stale = []
    if not quote_is_current:
        stale.append("underlying quote")
    if not chain_is_current:
        stale.append("option chain")
    if not candles_are_current:
        stale.append("M15 candles")
    return stale


def determine_blockers(
    *,
    decision: DecisionResult,
    candles_are_current: bool,
    chain_is_current: bool,
    quote_is_current: bool,
    day_change_pct: Decimal | None,
    development: DevelopmentNarrative | None,
) -> BlockerAssessment:
    """Precedence (most fundamental / most specific first -- a case can be
    simultaneously true for several of these; only one is ever PRIMARY):

    1. DATA_INSUFFICIENT -- nothing downstream can be trusted enough to
       reason about at all (matches `decide()`'s own first-checked rule).
    2. CONFLICT -- independent evidence groups actively disagree; more
       fundamental than "a candidate is illiquid" because there is no
       coherent bias to even attach a candidate assessment to.
    3. CONTRACT_UNUSABLE -- a coherent bias exists, but no sufficiently
       liquid/well-formed option candidate exists to act on it. This is
       the real KAYNES case: the bias question is settled, but the
       *contract* question independently blocks everything downstream --
       reporting "confirmation is pending" instead would be misleading,
       since more confirmation would not fix an illiquid contract.
    4. CORE_STREAM_STALE -- a required stream (quote/chain/M15) is not
       current. Ranked below CONTRACT_UNUSABLE deliberately: if the
       contract itself is already unusable, that is the more actionable
       fact for the user (waiting for fresher data will not fix it),
       even though `derive_research_state()` separately (and correctly,
       for a different question) treats staleness as forcing
       CONFIRMATION_PENDING ahead of a NO_TRADE decision.
    5. NO_DIRECTIONAL_BIAS -- evidence converged to neither bullish nor
       bearish (distinct from CONFLICT: this is "nothing pointed either
       way", not "evidence actively disagreed").
    6. EXTENDED -- everything else is otherwise clear, but the underlying
       has already moved far enough that confirmation quality is
       conventionally read as deteriorating, not improving.
    7. REQUIRED_CONFIRMATION_MISSING -- the ordinary, honest middle case:
       real evidence exists, nothing is broken, but independent
       confirmation has not yet happened (`WATCH`/`EARLY_SETUP`).
    8. NONE -- `TRADEABLE`: none of the above blockers apply. (`TRADEABLE`
       remains a compatibility label only -- see
       `docs/research/RESEARCH_STATE.md` -- this module does not change
       that.)
    """
    stale = _stale_streams(candles_are_current=candles_are_current, chain_is_current=chain_is_current, quote_is_current=quote_is_current)
    assessment = decision.assessment
    large_day_move = day_change_pct is not None and abs(day_change_pct) >= EXTENDED_MIN_DAY_CHANGE_PCT

    candidates: list[ResearchBlocker] = []

    if decision.decision == FinalDecision.DATA_INSUFFICIENT:
        candidates.append(ResearchBlocker(BlockerClass.DATA_INSUFFICIENT, decision.reasoning))
    if assessment.convergence == OverallConvergence.CONFLICT:
        candidates.append(ResearchBlocker(BlockerClass.CONFLICT, decision.reasoning))
    if assessment.option_quality == QualityLevel.INSUFFICIENT or assessment.liquidity_quality == QualityLevel.INSUFFICIENT:
        # Deliberately NOT `decision.reasoning` here -- that string describes
        # whatever `decide()` actually keyed its decision off (which, when
        # CONFLICT or DATA_INSUFFICIENT also apply, is about THAT, not about
        # contract quality). Reusing it produced a real defect found in
        # browser UAT: a CONTRACT_UNUSABLE secondary blocker whose
        # explanation text was a verbatim copy of the CONFLICT primary
        # explanation, contradicting Section 4's "no contradictory
        # explanations" requirement. This blocker's own explanation must
        # always describe the contract-quality fact it is actually reporting.
        contract_reason = (
            "no sufficiently liquid option candidate exists for this bias"
            if assessment.liquidity_quality == QualityLevel.INSUFFICIENT
            else "no option candidate with sufficient contract quality (pricing/Greeks/strike structure) exists for this bias"
        )
        candidates.append(ResearchBlocker(BlockerClass.CONTRACT_UNUSABLE, contract_reason))
    if stale:
        candidates.append(ResearchBlocker(
            BlockerClass.CORE_STREAM_STALE,
            f"{', '.join(stale)} not current -- technical confirmation on this stream cannot be evaluated right now",
        ))
    if (
        assessment.convergence not in (OverallConvergence.CONFLICT, OverallConvergence.INSUFFICIENT_EVIDENCE)
        and decision.decision == FinalDecision.NO_TRADE
        and assessment.option_quality != QualityLevel.INSUFFICIENT
        and assessment.liquidity_quality != QualityLevel.INSUFFICIENT
        and "no directional market bias" in decision.reasoning
    ):
        candidates.append(ResearchBlocker(BlockerClass.NO_DIRECTIONAL_BIAS, decision.reasoning))
    if large_day_move and decision.decision in (FinalDecision.TRADEABLE, FinalDecision.WATCH):
        candidates.append(ResearchBlocker(
            BlockerClass.EXTENDED,
            f"underlying day move ({day_change_pct}%) is at/beyond the {EXTENDED_MIN_DAY_CHANGE_PCT}% "
            "threshold this system treats as already extended -- conventionally read as late/deteriorating, not confirmed as safer",
        ))
    if decision.decision == FinalDecision.WATCH and not large_day_move:
        missing = development.what_is_missing if development is not None and development.pattern.value != "NONE" else decision.reasoning
        candidates.append(ResearchBlocker(BlockerClass.REQUIRED_CONFIRMATION_MISSING, missing))

    if not candidates:
        primary = ResearchBlocker(BlockerClass.NONE, "no blocking condition identified -- see the decision/quality breakdown for the full picture")
    else:
        order = [
            BlockerClass.DATA_INSUFFICIENT, BlockerClass.CONFLICT, BlockerClass.CONTRACT_UNUSABLE,
            BlockerClass.CORE_STREAM_STALE, BlockerClass.NO_DIRECTIONAL_BIAS, BlockerClass.EXTENDED,
            BlockerClass.REQUIRED_CONFIRMATION_MISSING,
        ]
        candidates.sort(key=lambda b: order.index(b.blocker_class))
        primary = candidates[0]

    secondary = tuple(c for c in candidates if c.blocker_class != primary.blocker_class)

    missing_confirmation = None
    if development is not None and development.pattern.value != "NONE":
        missing_confirmation = development.what_is_missing
    elif decision.decision == FinalDecision.WATCH:
        missing_confirmation = decision.reasoning

    return BlockerAssessment(primary=primary, secondary=secondary, missing_confirmation=missing_confirmation)
