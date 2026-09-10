"""Default user-facing wording for named patterns and common metrics.

Technical identifiers stay available under "Technical details". This
module never changes research state.
"""

from __future__ import annotations

_STATE_PLAIN: dict[str, str] = {
    "EARLY_SETUP": "Developing opportunity",
    "CONFIRMED_SETUP": "Confirmed setup — not a buy instruction",
    "CONFIRMATION_PENDING": "Waiting for confirmation",
    "DATA_INSUFFICIENT": "Not enough reliable data",
    "CONFLICT": "Independent evidence groups disagree",
    "NO_TRADE": "Nothing compelling is developing",
    "EXTENDED": "The move is already extended",
    "WATCH": "Watch — confirmation is incomplete",
    "UNKNOWN": "Unknown",
}

_BLOCKER_PLAIN: dict[str, str] = {
    "DATA_INSUFFICIENT": "Not enough reliable data.",
    "INSUFFICIENT_HISTORY": "Insufficient historical data to validate the developing pattern.",
    "CONFLICT": "Independent evidence groups disagree.",
    "CONTRACT_UNUSABLE": "Contract is not usable for reliable options research.",
    "CORE_STREAM_STALE": "A required data stream is stale.",
    "NO_DIRECTIONAL_BIAS": "Evidence did not establish a directional bias.",
    "EXTENDED": "The move is already extended.",
    "REQUIRED_CONFIRMATION_MISSING": "Waiting for confirmation.",
    "NONE": "No blocking condition identified.",
    "PROVIDER_CONFLICT": "Data providers disagree.",
}

_PATTERN_PLAIN: dict[str, str] = {
    "OI_MIGRATION": "Open interest is shifting across strikes.",
    "RELATIVE_STRENGTH": "This stock is moving differently from the broader index.",
    "RELATIVE_STRENGTH_ROTATION": "The stock is leading versus both the index and its official sector.",
    "FUTURES_STRUCTURE": "The futures-versus-spot relationship has changed.",
    "PRE_BREAKOUT_COMPRESSION": "Price is tightening near an important level, without a confirmed break yet.",
    "FAILED_BREAKDOWN_RECLAIM": "A recent breakdown failed to hold and price has come back.",
    "NONE": "No named developing pattern is present.",
}

_PATTERN_TECHNICAL: dict[str, str] = {
    "OI_MIGRATION": "OI_MIGRATION -- CE/PE OI-weighted strike migration on a current chain.",
    "RELATIVE_STRENGTH": "RELATIVE_STRENGTH -- stock vs Nifty day-change with non-opposing M15.",
    "RELATIVE_STRENGTH_ROTATION": "RELATIVE_STRENGTH_ROTATION -- stock vs Nifty plus official sector RS.",
    "FUTURES_STRUCTURE": "FUTURES_STRUCTURE -- comparable basis change, not a single basis print.",
    "PRE_BREAKOUT_COMPRESSION": "PRE_BREAKOUT_COMPRESSION -- range compression + proximity to a real opposing level.",
    "FAILED_BREAKDOWN_RECLAIM": "FAILED_BREAKDOWN_RECLAIM -- failed breakdown that has been reclaimed.",
    "NONE": "DevelopmentPattern.NONE",
}


def research_state_plain_english(state: str | None) -> str:
    key = (state or "UNKNOWN").strip() or "UNKNOWN"
    return _STATE_PLAIN.get(key, key.replace("_", " ").title())


def blocker_plain_english(blocker_class: str | None) -> str:
    key = (blocker_class or "NONE").strip() or "NONE"
    return _BLOCKER_PLAIN.get(key, key.replace("_", " ").title())


def pattern_plain_english(pattern: str | None) -> str:
    key = (pattern or "NONE").strip() or "NONE"
    return _PATTERN_PLAIN.get(key, f"Named pattern {key} is present.")


def pattern_technical_label(pattern: str | None) -> str:
    key = (pattern or "NONE").strip() or "NONE"
    return _PATTERN_TECHNICAL.get(key, key)


def happening_plain_english(*, pattern: str | None, bucket: str, event_risk: str) -> str:
    """Primary-card 'what is happening' -- never BUY/SELL."""
    _ = event_risk
    if bucket == "EVENT_DRIVEN":
        return "A material event was detected. No named developing setup is present."
    if bucket == "ALREADY_MOVED":
        return "Price has already broken recent structure. Fresh entry research should be cautious."
    if bucket == "EXTENDED":
        return "The move is already extended relative to this system's own tests."
    if bucket == "DATA_INSUFFICIENT":
        return "Not enough trustworthy data to describe a developing situation."
    if bucket == "CONFLICT":
        return "Independent evidence groups disagree, so no developing setup is claimed."
    if bucket == "CONFIRMATION_PENDING" or bucket == "CONFIRMED":
        return pattern_plain_english(pattern)
    return pattern_plain_english(pattern)


def contract_usability_plain_english(liquidity_grade: str | None) -> str:
    grade = (liquidity_grade or "").strip().lower()
    if grade in {"excellent", "good"}:
        return "Options look liquid enough to research."
    if grade == "moderate":
        return "Options liquidity is usable but not excellent."
    if grade in {"poor", "untradeable"}:
        return "Options liquidity is weak -- treat contract conclusions as unconfirmed."
    return "Contract liquidity confirmation pending."


PCR_CONTEXT = (
    "Context only -- this describes current option positioning. "
    "PCR by itself is not directional confirmation."
)
