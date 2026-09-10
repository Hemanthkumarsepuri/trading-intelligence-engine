"""Stage-1 cheap discovery observations -- quote-derived only.

This module never sees an option chain, futures history, or news.
Bucket names that look like Stage-2 patterns always carry `_CANDIDATE`
so they cannot be mistaken for confirmed OI/IV/structure evidence.
"""

from __future__ import annotations

from collections.abc import Sequence

# Substrings that would mean Stage 1 invented chain/futures evidence.
STAGE1_FORBIDDEN_EVIDENCE = (
    "OI_MIGRATION",
    "IV_SKEW",
    "OPTION_CHAIN",
    "DELTA_OI",
    "FUTURES_STRUCTURE",
    "PCR",
    "BASIS",
)

INTRADAY_COMPRESSION = "INTRADAY_COMPRESSION"
NEAR_SESSION_BOUNDARY = "NEAR_SESSION_BOUNDARY"
PRE_BREAKOUT_COMPRESSION_CANDIDATE = "PRE_BREAKOUT_COMPRESSION_CANDIDATE"
FAILED_BREAKDOWN_RECLAIM_CANDIDATE = "FAILED_BREAKDOWN_RECLAIM_CANDIDATE"
RELATIVE_STRENGTH_VS_INDEX = "RELATIVE_STRENGTH_VS_INDEX"
DEVELOPING_MOMENTUM = "DEVELOPING_MOMENTUM"
ORDER_FLOW_PARTICIPATION = "ORDER_FLOW_PARTICIPATION"
EARLY_REVERSAL = "EARLY_REVERSAL"


def assert_no_stage1_option_claims(observations: Sequence[str]) -> None:
    """Deterministic guard used by tests. Stage 1 observations must not
    claim chain/futures evidence that was never fetched."""
    for name in observations:
        upper = name.upper()
        for forbidden in STAGE1_FORBIDDEN_EVIDENCE:
            if forbidden in upper:
                raise AssertionError(f"Stage 1 observation {name!r} claims {forbidden} evidence")


_OBSERVATION_PLAIN: dict[str, str] = {
    PRE_BREAKOUT_COMPRESSION_CANDIDATE: "compression candidate (not multi-day proof)",
    FAILED_BREAKDOWN_RECLAIM_CANDIDATE: "reclaim candidate",
    INTRADAY_COMPRESSION: "intraday range tightening",
    NEAR_SESSION_BOUNDARY: "near session high or low",
    RELATIVE_STRENGTH_VS_INDEX: "relative strength versus the index",
    DEVELOPING_MOMENTUM: "developing momentum",
    ORDER_FLOW_PARTICIPATION: "unusual participation",
    EARLY_REVERSAL: "early reversal candidate",
}


def observation_plain_english(name: str) -> str:
    return _OBSERVATION_PLAIN.get(name, name.replace("_", " ").lower())


def promotion_reason(observations: Sequence[str]) -> str:
    """Explainable promotion text -- named observations, never a score."""
    if not observations:
        return "Promoted because: explicit Stage-2 request (Stage 1 skipped)."
    return "Promoted because: " + " + ".join(observation_plain_english(item) for item in observations)
