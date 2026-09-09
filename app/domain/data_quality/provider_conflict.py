"""PROVIDER_CONFLICT -- two current providers disagree; never average them.

`PROVIDER_DISAGREEMENT` is the historical name used in older docs and
config (`PROVIDER_DISAGREEMENT_TOLERANCE`). Both names refer to the same
data-quality event. Neither print may vote until reconciled.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

PROVIDER_CONFLICT = "PROVIDER_CONFLICT"
PROVIDER_DISAGREEMENT = PROVIDER_CONFLICT  # historical alias -- same event


@dataclass(frozen=True)
class ProviderConflict:
    field: str
    provider_a: str
    value_a: str
    provider_b: str
    value_b: str
    event: str = PROVIDER_CONFLICT


def classify_provider_conflict(
    *,
    field: str,
    provider_a: str,
    value_a: Decimal,
    provider_b: str,
    value_b: Decimal,
    tolerance: Decimal,
) -> ProviderConflict | None:
    """Return a conflict event when both values are present and differ
    by more than `tolerance` as a fraction of the first value. Never
    picks the 'better' number."""
    if value_a == 0:
        disagreed = value_b != 0
    else:
        disagreed = abs(value_a - value_b) / abs(value_a) > tolerance
    if not disagreed:
        return None
    return ProviderConflict(
        field=field,
        provider_a=provider_a,
        value_a=str(value_a),
        provider_b=provider_b,
        value_b=str(value_b),
    )
