"""Deterministic data-quality gate.

Implements the checks in docs/architecture/ARCHITECTURE.md Addendum A3. Runs
after normalization and before `domain/market` assembles anything from the
data. Every check is independently named and independently reported — the
final trade gate's `DATA_VALID` condition (§18/A6) and the audit trail both
consume the full per-check result, not a single boolean. Any failing check
means the caller must resolve the cycle to NO_TRADE_DATA_INSUFFICIENT; the
LLM is never invoked on data that failed this gate.

Some checks below (OHLC consistency, non-negative quantities) are already
enforced structurally by the `domain.market.models` constructors — they are
re-asserted here as defense-in-depth so they appear in the audit trail like
every other check, and so the gate stays correct even if a future code path
ever constructs a model outside the normal normalizer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from itertools import pairwise

from pydantic import BaseModel

from app.data.providers.health import HealthStatus, ProviderHealth
from app.domain.market.freshness import DataFreshness, FreshnessClass
from app.domain.market.models import Candle, OptionChainSnapshot, Quote


class CheckName(str, Enum):
    TIMESTAMP_VALIDITY = "TIMESTAMP_VALIDITY"
    FRESHNESS = "FRESHNESS"
    MISSING_FIELDS = "MISSING_FIELDS"
    DUPLICATE_RECORDS = "DUPLICATE_RECORDS"
    IMPOSSIBLE_PRICES = "IMPOSSIBLE_PRICES"
    INVALID_QUANTITIES = "INVALID_QUANTITIES"
    OHLC_CONSISTENCY = "OHLC_CONSISTENCY"
    OPTION_CHAIN_COMPLETENESS = "OPTION_CHAIN_COMPLETENESS"
    PROVIDER_STATUS = "PROVIDER_STATUS"
    CLOCK_SYNCHRONIZATION = "CLOCK_SYNCHRONIZATION"


class CheckResult(BaseModel):
    check: CheckName
    passed: bool
    detail: str


class QualityGateResult(BaseModel):
    checks: list[CheckResult]

    @property
    def valid(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


@dataclass(frozen=True)
class QualityGateConfig:
    max_age_by_class: dict[FreshnessClass, timedelta] = field(
        default_factory=lambda: {
            FreshnessClass.REAL_TIME: timedelta(seconds=15),
            FreshnessClass.SHORT_INTERVAL: timedelta(seconds=60),
            FreshnessClass.ANALYSIS_INTERVAL: timedelta(seconds=360),
            FreshnessClass.SLOW_REFRESH: timedelta(days=1),
            FreshnessClass.EVENT_DRIVEN: timedelta(hours=24),
        }
    )
    max_future_skew: timedelta = timedelta(seconds=5)
    max_clock_skew: timedelta = timedelta(seconds=5)
    max_price_jump_multiple: Decimal = Decimal("3")
    min_strike_coverage_fraction: Decimal = Decimal("0.8")


class DataQualityGate:
    """Stateless evaluator — one instance can be reused across cycles/tests."""

    def __init__(self, config: QualityGateConfig | None = None) -> None:
        self._config = config or QualityGateConfig()

    # -- Candles --------------------------------------------------------

    def evaluate_candles(
        self,
        candles: list[Candle],
        *,
        freshness_class: FreshnessClass,
        as_of: datetime,
        provider_health: ProviderHealth | None = None,
        reference_price: Decimal | None = None,
    ) -> QualityGateResult:
        checks = [
            self._check_timestamp_validity([c.freshness.data_timestamp for c in candles], as_of=as_of),
            self._check_freshness([c.freshness for c in candles], freshness_class=freshness_class, as_of=as_of),
            self._check_duplicate_candles(candles),
            self._check_ohlc_consistency(candles),
            self._check_invalid_quantities_candles(candles),
            self._check_impossible_prices_candles(candles, reference_price=reference_price),
            self._check_provider_status(provider_health),
            self._check_clock_sync([c.freshness for c in candles], as_of=as_of),
        ]
        return QualityGateResult(checks=checks)

    # -- Quotes -----------------------------------------------------------

    def evaluate_quote(
        self,
        quote: Quote,
        *,
        freshness_class: FreshnessClass,
        as_of: datetime,
        provider_health: ProviderHealth | None = None,
        reference_price: Decimal | None = None,
    ) -> QualityGateResult:
        checks = [
            self._check_timestamp_validity([quote.freshness.data_timestamp], as_of=as_of),
            self._check_freshness([quote.freshness], freshness_class=freshness_class, as_of=as_of),
            self._check_missing_fields_quote(quote),
            self._check_impossible_price_single(quote.last_price, reference_price=reference_price),
            self._check_provider_status(provider_health),
            self._check_clock_sync([quote.freshness], as_of=as_of),
        ]
        return QualityGateResult(checks=checks)

    # -- Option chain -------------------------------------------------------

    def evaluate_option_chain(
        self,
        snapshot: OptionChainSnapshot,
        *,
        freshness_class: FreshnessClass,
        as_of: datetime,
        expected_strike_count: int | None = None,
        provider_health: ProviderHealth | None = None,
    ) -> QualityGateResult:
        checks = [
            self._check_timestamp_validity([snapshot.freshness.data_timestamp], as_of=as_of),
            self._check_freshness([snapshot.freshness], freshness_class=freshness_class, as_of=as_of),
            self._check_option_chain_completeness(snapshot, expected_strike_count=expected_strike_count),
            self._check_provider_status(provider_health),
            self._check_clock_sync([snapshot.freshness], as_of=as_of),
        ]
        return QualityGateResult(checks=checks)

    # -- Individual checks ----------------------------------------------

    def _check_timestamp_validity(self, timestamps: list[datetime], *, as_of: datetime) -> CheckResult:
        if not timestamps:
            return CheckResult(check=CheckName.TIMESTAMP_VALIDITY, passed=False, detail="no records to validate")
        future = [t for t in timestamps if t > as_of + self._config.max_future_skew]
        if future:
            return CheckResult(
                check=CheckName.TIMESTAMP_VALIDITY,
                passed=False,
                detail=f"{len(future)} record(s) timestamped beyond as_of+skew tolerance (future-dated)",
            )
        for earlier, later in pairwise(timestamps):
            if later < earlier:
                return CheckResult(
                    check=CheckName.TIMESTAMP_VALIDITY,
                    passed=False,
                    detail="timestamps are not monotonically non-decreasing",
                )
        return CheckResult(check=CheckName.TIMESTAMP_VALIDITY, passed=True, detail="all timestamps valid and ordered")

    def _check_freshness(
        self, freshness_list: list[DataFreshness], *, freshness_class: FreshnessClass, as_of: datetime
    ) -> CheckResult:
        if not freshness_list:
            return CheckResult(check=CheckName.FRESHNESS, passed=False, detail="no records to check")
        max_age = self._config.max_age_by_class[freshness_class]
        stale = [f for f in freshness_list if f.is_stale(max_age, as_of=as_of)]
        if stale:
            return CheckResult(
                check=CheckName.FRESHNESS,
                passed=False,
                detail=f"{len(stale)} record(s) exceed max age {max_age} for class {freshness_class.value}",
            )
        return CheckResult(
            check=CheckName.FRESHNESS, passed=True, detail=f"all records within max age {max_age} for {freshness_class.value}"
        )

    def _check_duplicate_candles(self, candles: list[Candle]) -> CheckResult:
        seen: set[tuple[str, str, datetime]] = set()
        duplicate_count = 0
        for candle in candles:
            key = (candle.instrument_id, candle.timeframe.value, candle.freshness.data_timestamp)
            if key in seen:
                duplicate_count += 1
            seen.add(key)
        if duplicate_count:
            return CheckResult(
                check=CheckName.DUPLICATE_RECORDS, passed=False, detail=f"{duplicate_count} duplicate candle(s) detected"
            )
        return CheckResult(check=CheckName.DUPLICATE_RECORDS, passed=True, detail="no duplicates detected")

    def _check_ohlc_consistency(self, candles: list[Candle]) -> CheckResult:
        bad = [c for c in candles if c.low > c.high or not (c.low <= c.open <= c.high) or not (c.low <= c.close <= c.high)]
        if bad:
            return CheckResult(
                check=CheckName.OHLC_CONSISTENCY, passed=False, detail=f"{len(bad)} candle(s) fail OHLC consistency"
            )
        return CheckResult(
            check=CheckName.OHLC_CONSISTENCY,
            passed=True,
            detail="all candles internally consistent (also enforced structurally at construction)",
        )

    def _check_invalid_quantities_candles(self, candles: list[Candle]) -> CheckResult:
        bad = [c for c in candles if c.volume < 0 or (c.open_interest is not None and c.open_interest < 0)]
        if bad:
            return CheckResult(
                check=CheckName.INVALID_QUANTITIES, passed=False, detail=f"{len(bad)} candle(s) have negative volume/OI"
            )
        return CheckResult(
            check=CheckName.INVALID_QUANTITIES,
            passed=True,
            detail="all quantities non-negative (also enforced structurally at construction)",
        )

    def _check_impossible_prices_candles(self, candles: list[Candle], *, reference_price: Decimal | None) -> CheckResult:
        if not candles:
            return CheckResult(check=CheckName.IMPOSSIBLE_PRICES, passed=False, detail="no records to check")
        non_positive = [c for c in candles if c.close <= 0 or c.open <= 0]
        if non_positive:
            return CheckResult(
                check=CheckName.IMPOSSIBLE_PRICES, passed=False, detail=f"{len(non_positive)} candle(s) have zero/negative price"
            )
        if reference_price is not None and reference_price > 0:
            multiple = self._config.max_price_jump_multiple
            bad = [c for c in candles if c.close > reference_price * multiple or c.close < reference_price / multiple]
            if bad:
                return CheckResult(
                    check=CheckName.IMPOSSIBLE_PRICES,
                    passed=False,
                    detail=f"{len(bad)} candle(s) deviate more than {multiple}x from reference price {reference_price}",
                )
        return CheckResult(check=CheckName.IMPOSSIBLE_PRICES, passed=True, detail="no impossible prices detected")

    def _check_impossible_price_single(self, price: Decimal, *, reference_price: Decimal | None) -> CheckResult:
        if price <= 0:
            return CheckResult(check=CheckName.IMPOSSIBLE_PRICES, passed=False, detail="price is zero or negative")
        if reference_price is not None and reference_price > 0:
            multiple = self._config.max_price_jump_multiple
            if price > reference_price * multiple or price < reference_price / multiple:
                return CheckResult(
                    check=CheckName.IMPOSSIBLE_PRICES,
                    passed=False,
                    detail=f"price {price} deviates more than {multiple}x from reference price {reference_price}",
                )
        return CheckResult(check=CheckName.IMPOSSIBLE_PRICES, passed=True, detail="no impossible price detected")

    def _check_missing_fields_quote(self, quote: Quote) -> CheckResult:
        # `last_price` is already required (and >0) at the model level; this
        # check covers fields that are Optional there but expected for a
        # usable quote.
        missing = [name for name, value in (("previous_close", quote.previous_close), ("volume", quote.volume)) if value is None]
        if missing:
            return CheckResult(
                check=CheckName.MISSING_FIELDS, passed=False, detail=f"quote missing expected field(s): {', '.join(missing)}"
            )
        return CheckResult(check=CheckName.MISSING_FIELDS, passed=True, detail="all expected quote fields present")

    def _check_option_chain_completeness(
        self, snapshot: OptionChainSnapshot, *, expected_strike_count: int | None
    ) -> CheckResult:
        if not snapshot.legs:
            return CheckResult(
                check=CheckName.OPTION_CHAIN_COMPLETENESS, passed=False, detail="option chain snapshot has no legs"
            )
        counts = Counter(leg.strike for leg in snapshot.legs)
        incomplete = [strike for strike, count in counts.items() if count < 2]
        if incomplete:
            return CheckResult(
                check=CheckName.OPTION_CHAIN_COMPLETENESS,
                passed=False,
                detail=f"{len(incomplete)} strike(s) missing a CE or PE leg",
            )
        if expected_strike_count is not None and expected_strike_count > 0:
            coverage = Decimal(len(counts)) / Decimal(expected_strike_count)
            if coverage < self._config.min_strike_coverage_fraction:
                return CheckResult(
                    check=CheckName.OPTION_CHAIN_COMPLETENESS,
                    passed=False,
                    detail=f"strike coverage {coverage:.0%} below minimum {self._config.min_strike_coverage_fraction:.0%}",
                )
        return CheckResult(
            check=CheckName.OPTION_CHAIN_COMPLETENESS,
            passed=True,
            detail=f"{len(counts)} strikes present, each with both CE and PE legs",
        )

    def _check_provider_status(self, provider_health: ProviderHealth | None) -> CheckResult:
        if provider_health is None:
            return CheckResult(
                check=CheckName.PROVIDER_STATUS, passed=True, detail="no provider health supplied; check skipped"
            )
        if provider_health.status == HealthStatus.DOWN:
            return CheckResult(
                check=CheckName.PROVIDER_STATUS, passed=False, detail=f"provider {provider_health.provider} reports DOWN"
            )
        if provider_health.status == HealthStatus.DEGRADED:
            return CheckResult(
                check=CheckName.PROVIDER_STATUS, passed=False, detail=f"provider {provider_health.provider} reports DEGRADED"
            )
        return CheckResult(
            check=CheckName.PROVIDER_STATUS, passed=True, detail=f"provider {provider_health.provider} reports UP"
        )

    def _check_clock_sync(self, freshness_list: list[DataFreshness], *, as_of: datetime) -> CheckResult:
        # Best-effort: DhanHQ's documented responses don't expose an
        # independent server-time field, so this approximates clock sync by
        # checking the *most recently received* record's receipt time against
        # the cycle's `as_of` — older records in a batch (e.g. historical
        # backfill candles) are expected to have earlier receipt times and
        # must not trip this check; only "is our clock sane right now" is
        # being asked. See ARCHITECTURE.md Addendum A3.
        if not freshness_list:
            return CheckResult(check=CheckName.CLOCK_SYNCHRONIZATION, passed=True, detail="no records to check")
        most_recent = max(freshness_list, key=lambda f: f.received_timestamp)
        skew_seconds = abs((most_recent.received_timestamp - as_of).total_seconds())
        if timedelta(seconds=skew_seconds) > self._config.max_clock_skew:
            return CheckResult(
                check=CheckName.CLOCK_SYNCHRONIZATION,
                passed=False,
                detail=(
                    f"most recent record's received_timestamp vs as_of skew {skew_seconds:.1f}s exceeds tolerance "
                    f"{self._config.max_clock_skew} (best-effort check; no independent server-time source configured)"
                ),
            )
        return CheckResult(check=CheckName.CLOCK_SYNCHRONIZATION, passed=True, detail="clock skew within tolerance")
