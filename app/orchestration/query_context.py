"""Deterministic query context (Master Product Grooming Sprint, Section
6) — the SMALLEST possible mechanism for resolving short follow-ups like
"What about PE?" or "Which expiry?" against the most recent relevant
analysis. NOT a chatbot, NOT an LLM, NOT a generic conversational
framework: a fixed, documented set of deterministic phrases, matched
against a small, explicit, per-domain context record.

Rules (all enforced structurally below, not by convention):
1. Context is explicit -- `OptionsQueryContext`/`IPOQueryContext` carry
   only the few fields a follow-up can actually need.
2. Context expires -- `_is_stale()` checked against a caller-supplied TTL
   on every resolution; a stale context resolves as NO_CONTEXT.
3. An unrecognized follow-up phrase resolves as NEEDS_CLARIFICATION,
   never a guess.
4. Never guesses a different company/symbol -- every RESOLVED_QUERY is
   built ONLY from fields already on the stored context, never invented.
5. IPO and options contexts are two separate types held in two separate
   fields of `ContextState` -- structurally impossible for one domain's
   resolver to read the other's context (see `resolve_options_followup()`/
   `resolve_ipo_followup()`, each typed to accept only its own context).
6. Nothing here performs any new analysis. RESOLVED_QUERY only ever
   produces a plain query string for the EXISTING `/api/analyze` or
   `/api/ipo/analyze` pipeline to run unchanged; ANSWERED_FROM_CONTEXT
   only ever points at a section of the analysis the caller already has.
7. This context is held in server process memory only (see
   `app.state.query_context` in `app.api.main`) -- deliberately no new
   persistence format is introduced for it (Part 29's "prefer the
   smallest change" discipline). A process restart clears it; a fresh
   analysis re-establishes it immediately. This is a documented,
   intentional scope limit, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from app.domain.market.models import OptionRight

DEFAULT_CONTEXT_TTL = timedelta(minutes=30)


@dataclass(frozen=True)
class OptionsQueryContext:
    query: str
    symbol: str
    strike: Decimal | None
    right: OptionRight | None
    audit_id: str | None
    established_at: datetime


@dataclass(frozen=True)
class IPOQueryContext:
    query: str
    company_name: str
    ipo_id: str | None
    audit_id: str | None
    established_at: datetime


@dataclass
class ContextState:
    """One instance lives at `app.state.query_context` for the life of the
    process. Two independent fields -- never one shared field -- is what
    makes cross-domain leakage structurally impossible (rule 5 above)."""

    options: OptionsQueryContext | None = None
    ipo: IPOQueryContext | None = None


class FollowupKind(str, Enum):
    RESOLVED_QUERY = "RESOLVED_QUERY"
    ANSWERED_FROM_CONTEXT = "ANSWERED_FROM_CONTEXT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    NO_CONTEXT = "NO_CONTEXT"


@dataclass(frozen=True)
class FollowupResolution:
    kind: FollowupKind
    message: str
    resolved_query: str | None = None
    # Only meaningful when `kind is RESOLVED_QUERY` -- disambiguates WHAT
    # `resolved_query` actually is, so a caller never has to re-parse
    # `message` text to decide what to do with it. Options: "reanalyze"
    # (a full symbol/contract query to run through the existing analyze
    # pipeline). IPO: "audit_history" (a real `ipo_id` to read the
    # journal for) or "compare_add" (a company name to add to a compare
    # call -- never executed here, since comparing needs OTHER IPOs this
    # context does not have; rule 4, never guess).
    action: str | None = None


def _is_stale(established_at: datetime, *, now: datetime, ttl: timedelta) -> bool:
    return (now - established_at) > ttl


_OPTIONS_HELP = (
    "Recognized follow-ups: 'what about pe', 'what about ce', 'which expiry', "
    "'show alternatives', 'what changed', 'why'."
)
_IPO_HELP = "Recognized follow-ups: 'why', 'compare', 'audit history'."


def resolve_options_followup(
    text: str, *, context: OptionsQueryContext | None, now: datetime, ttl: timedelta = DEFAULT_CONTEXT_TTL
) -> FollowupResolution:
    if context is None:
        return FollowupResolution(kind=FollowupKind.NO_CONTEXT, message="No prior options analysis in this session to follow up on -- ask a full query first (e.g. 'KAYNES 4000 CE').")
    if _is_stale(context.established_at, now=now, ttl=ttl):
        return FollowupResolution(kind=FollowupKind.NO_CONTEXT, message=f"The last options analysis ({context.query!r}) is more than {int(ttl.total_seconds() // 60)} minutes old -- ask a full query again.")

    phrase = text.strip().lower().rstrip("?")

    if phrase in ("what about pe", "pe", "what about the pe", "and pe"):
        if context.strike is None:
            return FollowupResolution(kind=FollowupKind.NEEDS_CLARIFICATION, message=f"The last analysis ({context.query!r}) was not for a specific strike, so there is no PE contract to compare -- name a strike explicitly.")
        return FollowupResolution(kind=FollowupKind.RESOLVED_QUERY, resolved_query=f"{context.symbol} {context.strike} PE", action="reanalyze", message=f"Resolved to '{context.symbol} {context.strike} PE' from the last analysis's symbol and strike.")

    if phrase in ("what about ce", "ce", "what about the ce", "and ce"):
        if context.strike is None:
            return FollowupResolution(kind=FollowupKind.NEEDS_CLARIFICATION, message=f"The last analysis ({context.query!r}) was not for a specific strike, so there is no CE contract to compare -- name a strike explicitly.")
        return FollowupResolution(kind=FollowupKind.RESOLVED_QUERY, resolved_query=f"{context.symbol} {context.strike} CE", action="reanalyze", message=f"Resolved to '{context.symbol} {context.strike} CE' from the last analysis's symbol and strike.")

    if phrase in ("which expiry", "what expiry", "which expiries"):
        return FollowupResolution(kind=FollowupKind.ANSWERED_FROM_CONTEXT, message=f"See the TERM STRUCTURE section of the last analysis of {context.symbol!r} -- it already compares the requested expiry against nearby alternatives.")

    if phrase in ("show alternatives", "alternatives", "what are the alternatives"):
        return FollowupResolution(kind=FollowupKind.ANSWERED_FROM_CONTEXT, message=f"See the CANDIDATES / CONTRACT ALTERNATIVES section of the last analysis of {context.symbol!r}.")

    if phrase in ("why", "why?", "explain"):
        return FollowupResolution(kind=FollowupKind.ANSWERED_FROM_CONTEXT, message=f"See the EVIDENCE / FINAL ASSESSMENT sections of the last analysis of {context.symbol!r} -- every conclusion there is already tied to its supporting evidence.")

    if phrase in ("what changed", "what's changed", "what changed?"):
        return FollowupResolution(kind=FollowupKind.RESOLVED_QUERY, resolved_query=context.query, action="reanalyze", message=f"Re-running '{context.query}' -- its response's `what_changed` section compares it against the last persisted analysis of {context.symbol!r}.")

    return FollowupResolution(kind=FollowupKind.NEEDS_CLARIFICATION, message=f"'{text}' was not recognized as a follow-up to the last analysis of {context.symbol!r}. {_OPTIONS_HELP}")


def resolve_ipo_followup(
    text: str, *, context: IPOQueryContext | None, now: datetime, ttl: timedelta = DEFAULT_CONTEXT_TTL
) -> FollowupResolution:
    if context is None:
        return FollowupResolution(kind=FollowupKind.NO_CONTEXT, message="No prior IPO analysis in this session to follow up on -- ask about a company first.")
    if _is_stale(context.established_at, now=now, ttl=ttl):
        return FollowupResolution(kind=FollowupKind.NO_CONTEXT, message=f"The last IPO analysis ({context.company_name!r}) is more than {int(ttl.total_seconds() // 60)} minutes old -- ask again.")

    phrase = text.strip().lower().rstrip("?")

    if phrase in ("why", "explain"):
        return FollowupResolution(kind=FollowupKind.ANSWERED_FROM_CONTEXT, message=f"See the hidden-opportunity / hype-vs-evidence classification for {context.company_name!r} -- it already names its exact supporting and missing evidence.")

    if phrase in ("audit history", "history", "show history"):
        if context.ipo_id is None:
            return FollowupResolution(kind=FollowupKind.NEEDS_CLARIFICATION, message=f"{context.company_name!r} has no real Upstox ipo_id on the last analysis, so there is no journal to look up.")
        return FollowupResolution(kind=FollowupKind.RESOLVED_QUERY, resolved_query=context.ipo_id, action="audit_history", message=f"Fetch GET /api/ipo/journal/{context.ipo_id} for {context.company_name!r}'s audit history.")

    if phrase in ("compare", "compare it", "compare this"):
        return FollowupResolution(kind=FollowupKind.RESOLVED_QUERY, resolved_query=context.company_name, action="compare_add", message=f"Add {context.company_name!r} to a POST /api/ipo/compare call to compare it against other IPOs.")

    return FollowupResolution(kind=FollowupKind.NEEDS_CLARIFICATION, message=f"'{text}' was not recognized as a follow-up to the last IPO analysis of {context.company_name!r}. {_IPO_HELP}")
