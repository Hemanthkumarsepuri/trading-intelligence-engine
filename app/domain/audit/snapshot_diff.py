""""What changed?" (Master Product Grooming Sprint, Part 7/Section 9) —
a deterministic comparison between the CURRENT `AnalysisSnapshot` and the
most recent PRIOR `AnalysisSnapshot` for the same symbol, both already
persisted in the audit journal.

This is deliberately distinct from `app.orchestration.analysis_change.
detect_changes()`, which compares two `InstrumentSnapshot`s (the
watchlist's own lighter-weight, continuously-refreshed type) and reports
only a small set of NAMED structural transitions to avoid tick-to-tick
noise. Here, the two inputs are separate, user-INITIATED analyses of the
same symbol — often hours or days apart — so reporting the actual
before/after values (spot, IV, OI, CE/PE prices, decision) is the useful
answer, not noise. Both modules share the same non-negotiable rule: never
fabricate a change, never interpolate a missing value, never infer
causality — a value that was `None` before or after is reported as
`None`, not silently skipped or guessed.

No new analysis is computed here — every value compared already exists on
the two snapshots as persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.domain.audit.models import AnalysisSnapshot


@dataclass(frozen=True)
class FieldChange:
    """One before/after pair. `changed` is `None` when either side is
    unavailable — an unavailable comparison is not the same as "no
    change", and must never be reported as one."""

    label: str
    before: str | None
    after: str | None
    changed: bool | None


@dataclass(frozen=True)
class SnapshotChangeReport:
    has_prior_snapshot: bool
    previous_audit_id: str | None = None
    previous_generated_at: datetime | None = None
    time_since_previous_seconds: float | None = None
    underlying: list[FieldChange] = field(default_factory=list)
    options_structure: list[FieldChange] = field(default_factory=list)
    ce_requested: list[FieldChange] = field(default_factory=list)
    pe_requested: list[FieldChange] = field(default_factory=list)
    decision: list[FieldChange] = field(default_factory=list)
    newly_triggered_risks: list[str] = field(default_factory=list)
    newly_resolved_risks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _dec(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _pair(label: str, before: object | None, after: object | None) -> FieldChange:
    b = str(before) if before is not None else None
    a = str(after) if after is not None else None
    changed = None if (b is None or a is None) else (b != a)
    return FieldChange(label=label, before=b, after=a, changed=changed)


def build_snapshot_change_report(*, previous: AnalysisSnapshot | None, current: AnalysisSnapshot) -> SnapshotChangeReport:
    """Never raises. `previous=None` (no valid prior snapshot for this
    symbol) yields `has_prior_snapshot=False` and nothing else — the
    caller's job is to render that as "Not enough prior observations to
    describe a change," never to synthesize one.
    """
    if previous is None:
        return SnapshotChangeReport(has_prior_snapshot=False)

    if previous.identity.symbol != current.identity.symbol:
        # Defensive only -- callers are expected to look up `previous` via
        # `query_by_symbol(current.identity.symbol)`, so this should never
        # actually happen. Never silently compare across symbols.
        return SnapshotChangeReport(
            has_prior_snapshot=False,
            notes=[f"prior snapshot symbol {previous.identity.symbol!r} does not match current {current.identity.symbol!r} -- refusing to compare"],
        )

    delta_seconds = (current.identity.generated_at - previous.identity.generated_at).total_seconds()

    underlying = [
        _pair("Market state", previous.identity.market_state, current.identity.market_state),
        _pair("Spot", _dec(previous.underlying.spot), _dec(current.underlying.spot)),
        _pair("Day change %", _dec(previous.underlying.day_change_pct), _dec(current.underlying.day_change_pct)),
        _pair("Trend", previous.technical.trend, current.technical.trend),
        _pair("EMA alignment", previous.technical.ema_alignment, current.technical.ema_alignment),
        _pair("Price vs VWAP", previous.technical.vwap_position, current.technical.vwap_position),
        _pair("RSI", _dec(previous.technical.rsi_value), _dec(current.technical.rsi_value)),
    ]

    options_structure = [
        _pair("ATM strike", _dec(previous.options.atm_strike), _dec(current.options.atm_strike)),
        _pair("Chain IV", _dec(previous.options.chain_iv), _dec(current.options.chain_iv)),
        _pair("IV trend", previous.options.iv_trend, current.options.iv_trend),
        _pair("Total CE OI", previous.options.total_call_oi, current.options.total_call_oi),
        _pair("Total PE OI", previous.options.total_put_oi, current.options.total_put_oi),
        _pair("PCR (OI)", _dec(previous.options.pcr_oi), _dec(current.options.pcr_oi)),
    ]

    notes: list[str] = []
    ce_requested: list[FieldChange] = []
    pe_requested: list[FieldChange] = []
    if previous.contracts.reference_strike != current.contracts.reference_strike:
        notes.append(
            f"reference strike changed ({previous.contracts.reference_strike} -> "
            f"{current.contracts.reference_strike}) -- CE/PE prices below are not a like-for-like comparison"
        )
    prev_ce, curr_ce = previous.contracts.ce_requested, current.contracts.ce_requested
    if prev_ce is not None or curr_ce is not None:
        ce_requested = [
            _pair("CE LTP", _dec(prev_ce.ltp) if prev_ce else None, _dec(curr_ce.ltp) if curr_ce else None),
            _pair("CE IV", _dec(prev_ce.implied_volatility) if prev_ce else None, _dec(curr_ce.implied_volatility) if curr_ce else None),
            _pair("CE OI", prev_ce.open_interest if prev_ce else None, curr_ce.open_interest if curr_ce else None),
        ]
    prev_pe, curr_pe = previous.contracts.pe_requested, current.contracts.pe_requested
    if prev_pe is not None or curr_pe is not None:
        pe_requested = [
            _pair("PE LTP", _dec(prev_pe.ltp) if prev_pe else None, _dec(curr_pe.ltp) if curr_pe else None),
            _pair("PE IV", _dec(prev_pe.implied_volatility) if prev_pe else None, _dec(curr_pe.implied_volatility) if curr_pe else None),
            _pair("PE OI", prev_pe.open_interest if prev_pe else None, curr_pe.open_interest if curr_pe else None),
        ]

    decision = [
        _pair("Final bias", previous.decision.final_bias, current.decision.final_bias),
        _pair("Decision", previous.decision.decision, current.decision.decision),
        _pair("Invalidation level", _dec(previous.decision.invalidation_level), _dec(current.decision.invalidation_level)),
    ]

    prev_risks = set(previous.adversarial.key_risks)
    curr_risks = set(current.adversarial.key_risks)
    newly_triggered = sorted(curr_risks - prev_risks)
    newly_resolved = sorted(prev_risks - curr_risks)

    return SnapshotChangeReport(
        has_prior_snapshot=True,
        previous_audit_id=previous.identity.audit_id,
        previous_generated_at=previous.identity.generated_at,
        time_since_previous_seconds=delta_seconds,
        underlying=underlying,
        options_structure=options_structure,
        ce_requested=ce_requested,
        pe_requested=pe_requested,
        decision=decision,
        newly_triggered_risks=newly_triggered,
        newly_resolved_risks=newly_resolved,
        notes=notes,
    )
