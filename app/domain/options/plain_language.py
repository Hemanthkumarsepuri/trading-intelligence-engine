"""Default user-facing wording for named patterns and common metrics.

Technical identifiers stay available under "Technical details". This
module never changes research state.
"""

from __future__ import annotations

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


def pattern_plain_english(pattern: str | None) -> str:
    key = (pattern or "NONE").strip() or "NONE"
    return _PATTERN_PLAIN.get(key, f"Named pattern {key} is present.")


def pattern_technical_label(pattern: str | None) -> str:
    key = (pattern or "NONE").strip() or "NONE"
    return _PATTERN_TECHNICAL.get(key, key)


PCR_CONTEXT = (
    "Context only -- this describes current option positioning. "
    "PCR by itself is not directional confirmation."
)
