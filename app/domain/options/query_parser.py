"""Parses a free-text instrument query — e.g. "KAYNES", "KAYNES 4000 CE",
"KAYNES 4000 CE SEP" — into a structured `ParsedQuery`.

Pure tokenization only: no instrument-master lookup, no expiry
resolution, no network call. Per this project's dependency-direction rule
(`domain/` never imports `data/`/`persistence/`/`orchestration/`), the
symbol/strike/right/expiry-hint recognized here are handed to
`orchestration` to actually resolve against the real instrument master
and real expiries — this module only recognizes what the user TYPED.

This is deliberately consistent with the master directive's rule #5,
"USER INPUT IS NOT A DECISION": a parsed strike+right is the REQUESTED
CONTRACT, nothing more — this module makes no judgement about whether it
is a good trade, and `ParsedQuery` carries no directional vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.domain.market.models import OptionRight

_MONTH_ABBREVIATIONS = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"}
_RIGHT_TOKENS = {"CE": OptionRight.CE, "PE": OptionRight.PE}


@dataclass(frozen=True)
class ParsedQuery:
    raw_text: str
    symbol: str | None
    strike: Decimal | None
    right: OptionRight | None
    expiry_hint: str | None  # a month abbreviation the user typed, e.g. "SEP" -- never a resolved date
    errors: list[str] = field(default_factory=list)

    @property
    def has_specific_contract(self) -> bool:
        """True only when BOTH a strike and a right were recognized --
        anything less is not enough to identify one contract (Sprint 2's
        REQUESTED CONTRACT analysis gate)."""
        return self.strike is not None and self.right is not None


def parse_instrument_query(text: str) -> ParsedQuery:
    tokens = text.strip().upper().split()
    if not tokens:
        return ParsedQuery(raw_text=text, symbol=None, strike=None, right=None, expiry_hint=None, errors=["empty query"])

    symbol = tokens[0]
    strike: Decimal | None = None
    right: OptionRight | None = None
    expiry_hint: str | None = None
    errors: list[str] = []

    for tok in tokens[1:]:
        if tok in _RIGHT_TOKENS:
            if right is not None:
                errors.append(f"multiple option-right tokens found ('{right.value}' and '{tok}')")
            else:
                right = _RIGHT_TOKENS[tok]
        elif tok in _MONTH_ABBREVIATIONS:
            if expiry_hint is not None:
                errors.append(f"multiple expiry hints found ('{expiry_hint}' and '{tok}')")
            else:
                expiry_hint = tok
        else:
            try:
                value = Decimal(tok)
            except InvalidOperation:
                errors.append(f"unrecognized token '{tok}'")
                continue
            if value <= 0:
                errors.append(f"strike must be positive, got '{tok}'")
            elif strike is not None:
                errors.append(f"multiple strike-like tokens found ({strike} and {value})")
            else:
                strike = value

    if strike is not None and right is None:
        errors.append("a strike was given without CE/PE -- cannot determine the option right")
    if right is not None and strike is None:
        errors.append("CE/PE was given without a strike -- cannot determine which contract")

    return ParsedQuery(raw_text=text, symbol=symbol, strike=strike, right=right, expiry_hint=expiry_hint, errors=errors)
