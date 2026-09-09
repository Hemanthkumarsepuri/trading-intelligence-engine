"""FII/DII cash vs index F&O flow — informational, never a vote.

No verified parseable static producer is wired. Values are either
caller-supplied (source + as-of date required) or UNKNOWN. News mentioning
FII/FPI is not a flow series.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class FlowAvailability(str, Enum):
    CALLER_SUPPLIED = "CALLER_SUPPLIED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class InstitutionalFlowContext:
    availability: FlowAvailability
    fii_cash_net: Decimal | None
    dii_cash_net: Decimal | None
    index_fo_net: Decimal | None
    as_of_date: date | None
    source: str | None
    retrieved_at: datetime
    detail: str


def unknown_institutional_flows(*, retrieved_at: datetime) -> InstitutionalFlowContext:
    return InstitutionalFlowContext(
        availability=FlowAvailability.UNKNOWN,
        fii_cash_net=None, dii_cash_net=None, index_fo_net=None, as_of_date=None, source=None,
        retrieved_at=retrieved_at,
        detail=(
            "FII/DII CASH FLOW: UNKNOWN -- no verified parseable authorized static producer in this "
            "dependency set (legacy .xls is not parsed). INDEX F&O FLOW: UNKNOWN (not inferred from cash)."
        ),
    )


def caller_supplied_institutional_flows(
    *,
    fii_cash_net: Decimal | None,
    dii_cash_net: Decimal | None,
    index_fo_net: Decimal | None,
    as_of_date: date,
    source: str,
    retrieved_at: datetime,
) -> InstitutionalFlowContext:
    return InstitutionalFlowContext(
        availability=FlowAvailability.CALLER_SUPPLIED,
        fii_cash_net=fii_cash_net, dii_cash_net=dii_cash_net, index_fo_net=index_fo_net,
        as_of_date=as_of_date, source=source, retrieved_at=retrieved_at,
        detail=(
            f"FII/DII figures are CALLER-SUPPLIED from {source} as of {as_of_date.isoformat()} -- "
            "not an exchange feed parsed by this system. INDEX F&O FLOW is independent and is UNKNOWN "
            "unless a separate index-F&O figure was supplied."
        ),
    )
