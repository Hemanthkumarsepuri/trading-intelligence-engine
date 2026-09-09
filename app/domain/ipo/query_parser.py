"""IPO query intent detection (Part 17) — a SEPARATE parser from
`app.domain.options.query_parser`, never modifying it. `is_ipo_query()`
is the routing gate a caller (orchestration layer) should check FIRST;
only when it returns `False` does a query fall through to the existing
options parser, so "KAYNES 4000 CE" is completely unaffected (it
contains no "IPO" token).

=== KNOWN LIMITATION, STATED HONESTLY ===
This parser is stateless -- it sees only the current query text, not
conversation history. A bare follow-up like "How much chance do I have?"
(no company name, no "IPO" token) CANNOT be identified as an IPO query in
isolation and is deliberately NOT special-cased here; a caller wanting
that needs actual conversation-context tracking, which is out of this
module's scope. Pretending to handle it with a guess would be worse than
returning GENERAL/UNRECOGNIZED honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

_IPO_TOKEN = "IPO"

_COMMAND_WORDS = {
    "IPO", "IPOS", "TODAY", "TODAY'S", "TODAYS", "UPCOMING", "OPEN", "CLOSED", "RECENT", "RECENTLY",
    "BEST", "TOP", "WHICH", "SHOULD", "I", "APPLY", "FOR", "HAS", "HIGHEST", "HIDDEN", "POTENTIAL",
    "GMP", "CAN", "GET", "QUOTA", "DO", "SHAREHOLDERS", "CHANCE", "HOW", "MUCH", "AM", "THE", "A", "AN",
    "OF", "TO", "AND", "LISTING", "ALLOTMENT",
}


class IPOIntentKind(str, Enum):
    COMPANY_LOOKUP = "COMPANY_LOOKUP"
    TODAY_OPEN = "TODAY_OPEN"
    UPCOMING = "UPCOMING"
    RECENTLY_CLOSED = "RECENTLY_CLOSED"
    BEST_LISTING = "BEST_LISTING"
    BEST_ALLOTMENT = "BEST_ALLOTMENT"
    HIDDEN_OPPORTUNITY = "HIDDEN_OPPORTUNITY"
    GMP_LOOKUP = "GMP_LOOKUP"
    SHAREHOLDER_QUOTA_LOOKUP = "SHAREHOLDER_QUOTA_LOOKUP"
    GENERAL = "GENERAL"


@dataclass(frozen=True)
class IPOQueryIntent:
    raw_text: str
    company_hint: str | None
    intent: IPOIntentKind
    mentions: list[str] = field(default_factory=list)  # every non-command token found, for a caller wanting more than the single `company_hint`


def is_ipo_query(text: str) -> bool:
    """The routing gate. Deliberately simple and explicit: the literal
    whole-word token "IPO" or its plural "IPOS" (case-insensitive)
    anywhere in the query. Every example in Part 17 except the context-
    dependent follow-up ("How much chance do I have?" -- see module
    docstring) contains one of these tokens."""
    tokens = text.upper().replace("?", " ").replace(",", " ").replace("'S", "").split()
    return _IPO_TOKEN in tokens or "IPOS" in tokens


def parse_ipo_query(text: str) -> IPOQueryIntent:
    cleaned = text.upper().replace("?", " ").replace(",", " ").replace("'S", "").split()
    mentions = [tok for tok in cleaned if tok not in _COMMAND_WORDS]

    ipo_token = _IPO_TOKEN if _IPO_TOKEN in cleaned else ("IPOS" if "IPOS" in cleaned else None)
    company_hint: str | None = None
    if ipo_token is not None:
        idx = cleaned.index(ipo_token)
        if idx > 0 and cleaned[idx - 1] not in _COMMAND_WORDS:
            company_hint = cleaned[idx - 1]
    if company_hint is None and mentions:
        company_hint = mentions[0]

    if "GMP" in cleaned:
        intent = IPOIntentKind.GMP_LOOKUP
    elif "QUOTA" in cleaned or "SHAREHOLDERS" in cleaned:
        intent = IPOIntentKind.SHAREHOLDER_QUOTA_LOOKUP
    elif "HIDDEN" in cleaned or "POTENTIAL" in cleaned:
        intent = IPOIntentKind.HIDDEN_OPPORTUNITY
    elif "BEST" in cleaned and "LISTING" in cleaned:
        intent = IPOIntentKind.BEST_LISTING
    elif ("BEST" in cleaned or "HIGHEST" in cleaned) and ("ALLOTMENT" in cleaned or "CHANCE" in cleaned):
        intent = IPOIntentKind.BEST_ALLOTMENT
    elif "UPCOMING" in cleaned:
        intent = IPOIntentKind.UPCOMING
    elif "RECENT" in cleaned or "RECENTLY" in cleaned or "CLOSED" in cleaned:
        intent = IPOIntentKind.RECENTLY_CLOSED
    elif "TODAY" in cleaned or "TODAYS" in cleaned or "OPEN" in cleaned:
        intent = IPOIntentKind.TODAY_OPEN
    elif company_hint is not None:
        intent = IPOIntentKind.COMPANY_LOOKUP
    else:
        intent = IPOIntentKind.GENERAL

    return IPOQueryIntent(raw_text=text, company_hint=company_hint, intent=intent, mentions=mentions)
