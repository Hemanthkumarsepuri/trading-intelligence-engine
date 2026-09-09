"""StrategyDefinition / Setup — the strategy-interpretation contract.

Per `docs/strategies/STRATEGY_FRAMEWORK.md` (as reconciled with the
`STRATEGY_INPUT_CONTRACT_DECISION.md` gate sequence): a strategy receives
raw, already-`as_of`-bounded candles plus `MarketState` and `as_of`, and owns
all technical interpretation and direction assignment itself. Nothing
upstream of `detect_setup()` computes a directional read, an MTF alignment,
or any confidence/score — `domain/technical` remains fact-only.

**Deliberately covers only the setup-detection subset of the originally
documented five-method Protocol.** `evidence_requirements()`,
`invalidation_rule()`, and `risk_reward_policy()` are NOT included here:
their referenced types (`EvidenceCategory`, `InvalidationCondition`,
`RiskRewardPolicy`) remain genuinely unresolved
(`STRATEGY_INPUT_CONTRACT_DECISION.md` §K — no field shape given anywhere in
the repository). Fabricating placeholder shapes for them would be exactly
the invented-contract risk this project's evidence discipline forbids.
Extending this Protocol to add them is a future, separately-scoped decision,
once an owner supplies their shapes — not solved in advance here.

**`Setup.mtf_alignment` from the original `STRATEGY_FRAMEWORK.md` draft is
dropped, not merely deferred.** `DIRECTIONAL_VOTE_FINAL_GATE.md` §15/§19
(item O-7) found `MTFAlignment` as a generic type was never resolved and,
per that document's own conclusion, "may not belong as a required field at
all if a given strategy never computes one." A strategy wanting multi-
timeframe reasoning computes and combines it entirely internally and may
describe what it did in `structural_basis`; no generic field is required to
carry it, and no generic type is introduced to fill the gap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel

from app.domain.market.market_state import MarketState
from app.domain.market.models import Candle, Timeframe
from app.domain.market.timeframe_requirement import TimeframeRequirement


class Setup(BaseModel):
    """A strategy's typed conclusion that a candidate trade setup exists.

    Direction assignment happens exclusively inside the strategy that
    produces this object — nothing upstream computes or supplies it.
    """

    strategy_name: str
    strategy_version: str
    direction: Literal["BULLISH", "BEARISH"]
    origin_timestamp: datetime
    trigger_level: Decimal
    structural_basis: str


class StrategyDefinition(Protocol):
    """Structural interface a concrete strategy implements. See module
    docstring for what is intentionally not part of this Protocol yet.
    """

    name: str
    version: str

    def required_timeframes(self) -> Sequence[TimeframeRequirement]:
        """Which timeframes (and, via `TimeframeRequirement.minimum_candles`,
        how much history) this strategy needs. Static and parameterless —
        must not depend on market data or `as_of`.
        """
        ...

    def detect_setup(
        self,
        *,
        market_state: MarketState,
        candles: Mapping[Timeframe, Sequence[Candle]],
        as_of: datetime,
    ) -> Setup | None:
        """Pure interpretation step. Receives already-fetched, already-
        normalized, already-`as_of`-bounded inputs; never fetches data
        itself, never reads the clock, never calls the LLM. May call any
        `domain/technical` function directly with whatever periods it
        chooses — those choices are this strategy's own, not declared
        anywhere generic.
        """
        ...
