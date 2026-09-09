"""Unusual Activity / Anomaly Detection Engine (Section 13, continuing the
prior milestone's Phase 7). Compares a CURRENT observable value (volume,
OI change magnitude, premium-move magnitude, IV-move magnitude, spread)
against a REAL historical baseline built only from persisted, already-
fetched snapshots — never a fabricated or assumed "typical" value.

One generic comparator (`detect_anomaly`) rather than five near-duplicate
functions: every anomaly category the product spec lists (unusual volume,
unusual OI addition, rapid IV expansion/contraction, abnormal spread,
abnormal premium movement) reduces to the same shape — "is this one real
number unusually large relative to this underlying's own real recent
history" — so the metric-specific logic lives entirely in what the caller
feeds in as `historical_values`, not in a duplicated comparator per metric.

Threshold classification:
    FACT      — none (this module computes only, never fetches).
    METRIC    — mean/min/max of `historical_values`, and the ratio of
                `current_value` to that mean.
    HEURISTIC — using a ratio-of-mean rather than a standard-deviation
                z-score. This project's real accumulated sample sizes are
                currently far too small (single digits) for a stable
                standard deviation to mean anything; a ratio-of-mean is a
                simpler, more robust comparator given that real
                limitation — not a claim that it is statistically ideal
                once more history accumulates.
    THRESHOLD — `min_sample_size`, `elevated_ratio`, `unusual_ratio` — all
                required, undefaulted (see `detect_anomaly`'s docstring).

Below `min_sample_size` real observations, the verdict is ALWAYS
`INSUFFICIENT_HISTORY` — this module never synthesizes a baseline to
produce an answer it doesn't have real evidence for.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class AnomalyVerdict(str, Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    UNUSUAL = "UNUSUAL"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


@dataclass(frozen=True)
class BaselineStats:
    sample_count: int
    mean: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None


@dataclass(frozen=True)
class AnomalyResult:
    metric_name: str
    current_value: Decimal | None
    baseline: BaselineStats
    deviation_from_mean: Decimal | None
    deviation_ratio: Decimal | None  # current_value / baseline.mean, when mean != 0
    verdict: AnomalyVerdict
    detail: str


def compute_baseline(values: list[Decimal]) -> BaselineStats:
    if not values:
        return BaselineStats(sample_count=0, mean=None, minimum=None, maximum=None)
    return BaselineStats(sample_count=len(values), mean=sum(values) / Decimal(len(values)), minimum=min(values), maximum=max(values))


def detect_anomaly(
    *,
    metric_name: str,
    current_value: Decimal | None,
    historical_values: list[Decimal],
    min_sample_size: int,
    elevated_ratio: Decimal,
    unusual_ratio: Decimal,
) -> AnomalyResult:
    """`min_sample_size` (THRESHOLD): the minimum count of real historical
    observations required before any verdict other than
    `INSUFFICIENT_HISTORY` is possible. `elevated_ratio`/`unusual_ratio`
    (THRESHOLD): multiples of the historical mean current_value must reach
    to be called `ELEVATED`/`UNUSUAL`. All three are required, undefaulted
    — there is no single correct value independent of which metric and
    which underlying's real liquidity this is being applied to; the
    caller must supply and document its own (see the pipeline's call
    site).
    """
    baseline = compute_baseline(historical_values)

    if current_value is None:
        return AnomalyResult(
            metric_name=metric_name, current_value=None, baseline=baseline, deviation_from_mean=None,
            deviation_ratio=None, verdict=AnomalyVerdict.INSUFFICIENT_HISTORY,
            detail=f"{metric_name}: current value unavailable",
        )
    if baseline.sample_count < min_sample_size:
        return AnomalyResult(
            metric_name=metric_name, current_value=current_value, baseline=baseline, deviation_from_mean=None,
            deviation_ratio=None, verdict=AnomalyVerdict.INSUFFICIENT_HISTORY,
            detail=f"{metric_name}: only {baseline.sample_count} real historical observation(s), need >= {min_sample_size}",
        )

    assert baseline.mean is not None
    deviation = current_value - baseline.mean
    if baseline.mean == 0:
        return AnomalyResult(
            metric_name=metric_name, current_value=current_value, baseline=baseline, deviation_from_mean=deviation,
            deviation_ratio=None, verdict=AnomalyVerdict.INSUFFICIENT_HISTORY,
            detail=f"{metric_name}: baseline mean is zero over {baseline.sample_count} observation(s) -- ratio undefined",
        )

    ratio = current_value / baseline.mean
    if ratio >= unusual_ratio:
        verdict = AnomalyVerdict.UNUSUAL
    elif ratio >= elevated_ratio:
        verdict = AnomalyVerdict.ELEVATED
    else:
        verdict = AnomalyVerdict.NORMAL

    detail = (
        f"{metric_name}: current={current_value}, baseline mean={baseline.mean:.2f} over "
        f"{baseline.sample_count} real observation(s) (range {baseline.minimum}-{baseline.maximum}), "
        f"ratio={ratio:.2f}x mean"
    )
    return AnomalyResult(
        metric_name=metric_name, current_value=current_value, baseline=baseline, deviation_from_mean=deviation,
        deviation_ratio=ratio, verdict=verdict, detail=detail,
    )
