"""Pure Phase 4-12 reconciliation logic — compares an already-created,
NEVER-mutated `AnalysisSnapshot` against however many `OutcomeCheckpoint`s
have since been captured, and produces a `ReconciliationResult`.

Nothing in this module performs I/O, reads a clock, or calls a provider —
every input is caller-supplied, exactly like every other pure `domain`
module in this codebase. This is deliberate: reconciliation is a
computation over already-recorded facts, not a new data-gathering step.

=== MOST IMPORTANT RULE (per the milestone's own instruction) ===
This module exists to discover whether the system is actually good, not
to make it look successful. It never converts an outcome into a
GOOD/BAD or WIN/LOSS label. `ThesisStatus`/`LevelOutcomeState`/
`DecayReconciliationVerdict`/`EvidenceRowOutcome` are all purely
DESCRIPTIVE of what was observed — see `app.domain.audit.models` for the
full rationale. If the honest answer is "insufficient data", that is what
gets returned, every time, without exception.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.domain.audit.models import (
    CHECKPOINT_ORDER,
    AdversarialReconciliation,
    AdversarialSection,
    AnalysisSnapshot,
    CePeCheckpointObservation,
    CePeOutcomeComparison,
    CheckpointLabel,
    CheckpointStatus,
    DecayReconciliationEntry,
    DecayReconciliationVerdict,
    EvidenceRowOutcome,
    EvidenceRowReconciliation,
    EvidenceSection,
    ExcursionSample,
    InvalidationTracking,
    JournalCounters,
    LevelOutcomeEntry,
    LevelOutcomeState,
    LevelSnapshot,
    OptionOutcome,
    OutcomeCheckpoint,
    ReconciliationResult,
    ThesisStatus,
)

_DECAY_CONFIDENCE_MAP: dict[str, DecayReconciliationVerdict] = {
    "ATTRIBUTED": DecayReconciliationVerdict.CONSISTENT,
    "PARTIAL": DecayReconciliationVerdict.PARTIALLY_CONSISTENT,
    "UNRELIABLE": DecayReconciliationVerdict.INCONSISTENT,
    "INSUFFICIENT_DATA": DecayReconciliationVerdict.INSUFFICIENT_DATA,
}


def _ordered(checkpoints: list[OutcomeCheckpoint]) -> list[OutcomeCheckpoint]:
    return sorted(checkpoints, key=lambda c: CHECKPOINT_ORDER[c.checkpoint_label])


def _recorded(checkpoints: list[OutcomeCheckpoint]) -> list[OutcomeCheckpoint]:
    return [c for c in checkpoints if c.status == CheckpointStatus.RECORDED]


def track_invalidation(
    *, bias: str, invalidation_level: Decimal | None, checkpoints: list[OutcomeCheckpoint]
) -> InvalidationTracking:
    """Phase 5. Once reached, a thesis is invalidated permanently — this
    scans checkpoints in chronological order and stops at the FIRST breach,
    never re-evaluated against later data (that would be exactly the kind
    of look-ahead-flavored rewriting this project forbids elsewhere).
    """
    if bias not in ("BULLISH", "BEARISH") or invalidation_level is None:
        return InvalidationTracking(invalidation_level=invalidation_level, reached=None, reached_at_checkpoint=None, reached_at=None)

    recorded = [c for c in _ordered(checkpoints) if c.status == CheckpointStatus.RECORDED and c.underlying is not None and c.underlying.spot is not None]
    if not recorded:
        return InvalidationTracking(invalidation_level=invalidation_level, reached=None, reached_at_checkpoint=None, reached_at=None)

    for c in recorded:
        assert c.underlying is not None and c.underlying.spot is not None
        spot = c.underlying.spot
        breached = spot < invalidation_level if bias == "BULLISH" else spot > invalidation_level
        if breached:
            return InvalidationTracking(
                invalidation_level=invalidation_level, reached=True,
                reached_at_checkpoint=c.checkpoint_label.value, reached_at=c.captured_at,
            )
    return InvalidationTracking(invalidation_level=invalidation_level, reached=False, reached_at_checkpoint=None, reached_at=None)


def compute_excursion(original_spot: Decimal | None, checkpoints: list[OutcomeCheckpoint]) -> list[ExcursionSample]:
    """Phase 6 (MFE/MAE) and Phase 12 (NO_TRADE) share this SAME
    computation — see module docstring. `up_move_pct`/`down_move_pct` are
    running maxima across all RECORDED checkpoints seen so far, sampled
    only at checkpoint instants (not tick-level — see
    `app.domain.audit.models` module docstring).
    """
    if original_spot is None or original_spot == 0:
        return []
    samples: list[ExcursionSample] = []
    running_up = Decimal("0")
    running_down = Decimal("0")
    for c in _ordered(checkpoints):
        if c.status != CheckpointStatus.RECORDED or c.underlying is None or c.underlying.spot is None:
            continue
        pct = (c.underlying.spot - original_spot) / original_spot * Decimal(100)
        running_up = max(running_up, pct)
        running_down = max(running_down, -pct)
        samples.append(ExcursionSample(checkpoint_label=c.checkpoint_label.value, up_move_pct=running_up, down_move_pct=running_down))
    return samples


def classify_level_outcome(
    level: LevelSnapshot, *, checkpoints: list[OutcomeCheckpoint], test_proximity_pct: Decimal
) -> LevelOutcomeEntry:
    """Phase 9. `test_proximity_pct` (THRESHOLD, required, undefaulted) —
    how close price must come to the level, as a percent of the strike, to
    count as "tested"; no single correct value exists independent of the
    underlying's own typical volatility (callers should reuse
    `PipelineConfig.near_level_pct_threshold`, the same threshold that
    already governs "is this level near enough to matter" everywhere
    else in this codebase, rather than inventing a second one).

    Vocabulary used here (documented, not a universal convention):
    NOT_TESTED — price never came within `test_proximity_pct` and never
        crossed the level.
    TESTED — price came within range (or touched) but, as of the latest
        checkpoint, is still on the original side and still within range
        — not yet resolved either way.
    HELD — price came within range, then moved back outside it while
        staying on the original side — a clear reaction away from the
        level.
    BROKEN — the latest checkpoint's spot is on the OTHER side of the
        level.
    FAILED — price crossed to the other side at some earlier checkpoint
        but has reclaimed back to the original side by the latest
        checkpoint — a break that did not hold.
    """
    recorded = [c for c in _ordered(checkpoints) if c.status == CheckpointStatus.RECORDED and c.underlying is not None and c.underlying.spot is not None]
    if not recorded:
        return LevelOutcomeEntry(kind=level.kind, strike=level.strike, state=LevelOutcomeState.INSUFFICIENT_DATA, first_test_checkpoint=None, detail="no recorded checkpoints yet")

    is_support = level.kind == "support"

    def beyond(spot: Decimal) -> bool:
        return spot < level.strike if is_support else spot > level.strike

    def near(spot: Decimal) -> bool:
        if level.strike == 0:
            return False
        return abs(spot - level.strike) / level.strike * Decimal(100) <= test_proximity_pct

    spots = [(c, c.underlying.spot) for c in recorded if c.underlying is not None and c.underlying.spot is not None]
    first_test = next((c for c, s in spots if near(s) or beyond(s)), None)
    ever_beyond = any(beyond(s) for _, s in spots)
    latest_checkpoint, latest_spot = spots[-1]

    if first_test is None:
        return LevelOutcomeEntry(
            kind=level.kind, strike=level.strike, state=LevelOutcomeState.NOT_TESTED, first_test_checkpoint=None,
            detail=f"spot never came within {test_proximity_pct}% of the {level.kind} strike {level.strike}",
        )

    if beyond(latest_spot):
        state = LevelOutcomeState.BROKEN
        detail = f"spot ({latest_spot}) is beyond the {level.kind} strike {level.strike} as of the {latest_checkpoint.checkpoint_label.value} checkpoint"
    elif ever_beyond:
        state = LevelOutcomeState.FAILED
        detail = f"spot crossed beyond {level.strike} at an earlier checkpoint but had reclaimed back to {latest_spot} by {latest_checkpoint.checkpoint_label.value} -- the break did not hold"
    elif near(latest_spot):
        state = LevelOutcomeState.TESTED
        detail = f"spot tested {level.strike} (currently {latest_spot}, within {test_proximity_pct}%) -- not yet resolved"
    else:
        state = LevelOutcomeState.HELD
        detail = f"spot tested {level.strike} at {first_test.checkpoint_label.value} and has since moved to {latest_spot}, outside the {test_proximity_pct}% test band"

    return LevelOutcomeEntry(kind=level.kind, strike=level.strike, state=state, first_test_checkpoint=first_test.checkpoint_label.value, detail=detail)


def reconcile_evidence(original: EvidenceSection, latest: EvidenceSection | None) -> list[EvidenceRowReconciliation]:
    """Phase 10. Compares by row NAME (the same evidence dimension), never
    by position — a row missing from the later matrix (e.g. a candidate
    stopped existing) is INSUFFICIENT_DATA, never silently dropped.
    """
    if latest is None:
        return [
            EvidenceRowReconciliation(name=r.name, group=r.group, original_direction=r.direction, latest_direction=None, outcome=EvidenceRowOutcome.INSUFFICIENT_DATA)
            for r in original.rows
        ]
    latest_by_name = {r.name: r for r in latest.rows}
    results: list[EvidenceRowReconciliation] = []
    for r in original.rows:
        lr = latest_by_name.get(r.name)
        if lr is None:
            results.append(EvidenceRowReconciliation(name=r.name, group=r.group, original_direction=r.direction, latest_direction=None, outcome=EvidenceRowOutcome.INSUFFICIENT_DATA))
            continue
        if r.direction == "UNKNOWN" or lr.direction == "UNKNOWN":
            outcome = EvidenceRowOutcome.INSUFFICIENT_DATA
        elif r.direction == lr.direction:
            outcome = EvidenceRowOutcome.CONFIRMED if r.direction in ("BULLISH", "BEARISH") else EvidenceRowOutcome.UNCHANGED
        elif {r.direction, lr.direction} <= {"BULLISH", "BEARISH"}:
            outcome = EvidenceRowOutcome.INVALIDATED
        else:
            outcome = EvidenceRowOutcome.UNCHANGED
        results.append(EvidenceRowReconciliation(name=r.name, group=r.group, original_direction=r.direction, latest_direction=lr.direction, outcome=outcome))
    return results


def reconcile_adversarial(original: AdversarialSection, latest_checkpoint: OutcomeCheckpoint | None) -> AdversarialReconciliation:
    """Phase 11. Reports objective COUNTS only — never "the bull case won"
    or similar. `contradiction_resolved` is a plain boolean fact (did the
    original CONFLICT stop being a conflict), not a reward signal.
    """
    if latest_checkpoint is None or latest_checkpoint.adversarial is None:
        return AdversarialReconciliation(
            bull_case_count_original=len(original.bull_case), bull_case_count_latest=None,
            bear_case_count_original=len(original.bear_case), bear_case_count_latest=None,
            contradiction_resolved=None, latest_checkpoint_used=None,
        )
    la = latest_checkpoint.adversarial
    contradiction_resolved = (not la.opposite_case_is_equally_supported) if original.opposite_case_is_equally_supported else None
    return AdversarialReconciliation(
        bull_case_count_original=len(original.bull_case), bull_case_count_latest=len(la.bull_case),
        bear_case_count_original=len(original.bear_case), bear_case_count_latest=len(la.bear_case),
        contradiction_resolved=contradiction_resolved, latest_checkpoint_used=latest_checkpoint.checkpoint_label.value,
    )


def reconcile_decay(checkpoints: list[OutcomeCheckpoint]) -> list[DecayReconciliationEntry]:
    """Phase 8. Reuses `decay_engine.attribute_decay()`'s own
    `AttributionConfidence` verdict directly — see the class mapping below
    — rather than inventing new decay-quality math. Explicitly FIRST_ORDER,
    matching that module's own documented scope.
    """
    entries: list[DecayReconciliationEntry] = []
    for c in _ordered(checkpoints):
        if c.option is None or c.option.decay_attribution is None:
            continue
        da = c.option.decay_attribution
        verdict = _DECAY_CONFIDENCE_MAP.get(da.confidence, DecayReconciliationVerdict.INSUFFICIENT_DATA)
        entries.append(DecayReconciliationEntry(checkpoint_label=c.checkpoint_label.value, verdict=verdict, detail=da.detail))
    return entries


def _explain_divergence(option: OptionOutcome) -> str:
    """Phase 7. Only ever called when direction was right but the option
    lost value — reuses the SAME decay-attribution decomposition Phase 8
    already computed, never a separate heuristic.
    """
    da = option.decay_attribution
    if da is None or da.confidence == "INSUFFICIENT_DATA":
        return "underlying direction was correct but the option lost value; insufficient data to decompose the cause"
    causes: list[str] = []
    if da.theta_effect is not None and da.theta_effect < 0:
        causes.append(f"time decay (theta effect {da.theta_effect:+.2f})")
    if da.vega_effect is not None and da.vega_effect < 0:
        causes.append(f"IV contraction (vega effect {da.vega_effect:+.2f})")
    if option.spread_pct is not None and option.spread_pct > Decimal("5"):
        causes.append(f"wide spread ({option.spread_pct:.2f}%)")
    if not causes:
        causes.append("insufficient underlying movement relative to strike distance/DTE, or an undecomposed residual")
    return "underlying direction was correct but the option lost value -- plausible contributing factor(s): " + "; ".join(causes)


def evaluate_thesis(
    snapshot: AnalysisSnapshot, checkpoints: list[OutcomeCheckpoint], invalidation: InvalidationTracking
) -> tuple[ThesisStatus, ThesisStatus | None, str | None]:
    """Phase 4 + Phase 7, kept separate on purpose: `underlying_thesis_status`
    answers "did the conditions that justified the original thesis remain
    valid", never merely "did price go up". `option_thesis_status` answers
    the P&L question literally, and can legitimately disagree with the
    underlying status (a correct direction call whose option still lost
    money) — see Phase 7's own example.

    For a NEUTRAL/UNKNOWN bias (a NO_TRADE decision), there is no
    directional thesis to confirm or invalidate — `UNRESOLVED` is returned
    deliberately rather than stretching CONFIRMED/INVALIDATED to cover a
    non-directional case. Whether the original CONFLICT/uncertainty
    resolved is instead exposed via `AdversarialReconciliation.
    contradiction_resolved` and the per-row `evidence_reconciliation` —
    dedicated, honest signals rather than an overloaded thesis label.
    """
    bias = snapshot.decision.final_bias
    recorded = _recorded(_ordered(checkpoints))

    if bias not in ("BULLISH", "BEARISH"):
        return (ThesisStatus.UNRESOLVED if recorded else ThesisStatus.INSUFFICIENT_DATA), None, None

    if not recorded:
        return ThesisStatus.INSUFFICIENT_DATA, None, None

    if invalidation.reached:
        underlying_status = ThesisStatus.INVALIDATED
    else:
        latest = recorded[-1]
        latest_convergence = latest.evidence.convergence if latest.evidence is not None else None
        matching = f"CONVERGENCE_{bias}"
        if latest_convergence == matching:
            underlying_status = ThesisStatus.CONFIRMED
        elif latest_convergence in ("CONFLICT", "INSUFFICIENT_EVIDENCE", None):
            underlying_status = ThesisStatus.MIXED
        elif latest_convergence in ("CONVERGENCE_BULLISH", "CONVERGENCE_BEARISH"):
            underlying_status = ThesisStatus.MIXED  # evidence has flipped to the opposite convergence
        else:
            underlying_status = ThesisStatus.UNRESOLVED

    candidate = snapshot.decision.candidate_selected
    if candidate is None:
        return underlying_status, None, None

    option_checkpoints = [c for c in recorded if c.option is not None and c.option.option_pct_change is not None]
    if not option_checkpoints:
        return underlying_status, ThesisStatus.INSUFFICIENT_DATA, None

    latest_option = option_checkpoints[-1]
    assert latest_option.option is not None and latest_option.option.option_pct_change is not None
    pct = latest_option.option.option_pct_change
    if pct > 0:
        option_status = ThesisStatus.CONFIRMED
    elif pct < 0:
        option_status = ThesisStatus.INVALIDATED
    else:
        option_status = ThesisStatus.MIXED

    divergence_note = None
    if underlying_status == ThesisStatus.CONFIRMED and option_status == ThesisStatus.INVALIDATED:
        divergence_note = _explain_divergence(latest_option.option)

    return underlying_status, option_status, divergence_note


_LABEL_ORDER: list[CheckpointLabel] = sorted(CheckpointLabel, key=lambda label: CHECKPOINT_ORDER[label])


def _pct_to_thesis_status(pct: Decimal | None) -> ThesisStatus:
    """The SAME three-way classification `evaluate_thesis()` already
    applies to `option_thesis_status` (Phase 4/7 of Milestone G) --
    reused verbatim here for CE/PE comparison rather than inventing a
    second rule for the same concept (Sprint 6's explicit "no new
    mathematics" instruction)."""
    if pct is None:
        return ThesisStatus.INSUFFICIENT_DATA
    if pct > 0:
        return ThesisStatus.CONFIRMED
    if pct < 0:
        return ThesisStatus.INVALIDATED
    return ThesisStatus.MIXED


def build_ce_pe_comparison(snapshot: AnalysisSnapshot, checkpoints: list[OutcomeCheckpoint]) -> CePeOutcomeComparison | None:
    """Sprint 6, Phase 8 -- the empirical CE-vs-PE comparison. Returns
    `None` when there is genuinely nothing to compare (no reference
    strike was resolved for this snapshot -- e.g. a bare underlying
    query with no CE/PE requested at all); never fabricates a comparison
    where none exists.

    Every value here is copied directly from already-persisted,
    already-captured `OutcomeCheckpoint`s (Sprint 5's `ce_option`/
    `pe_option` fields) -- no new fetch, no estimate, no interpolation.
    A checkpoint label with no captured `OutcomeCheckpoint` at all is
    `"PENDING"`; one captured but MARKET_CLOSED/INSUFFICIENT_DATA keeps
    that honest status with `None` values, never a fabricated number.
    """
    contracts = snapshot.contracts
    if contracts.reference_strike is None:
        return None

    by_label = {c.checkpoint_label: c for c in checkpoints}
    observations: list[CePeCheckpointObservation] = []
    for label in _LABEL_ORDER:
        checkpoint = by_label.get(label)
        if checkpoint is None:
            observations.append(CePeCheckpointObservation(
                checkpoint_label=label.value, status="PENDING", underlying_spot=None, underlying_pct_change=None,
                ce_price=None, ce_pct_change=None, pe_price=None, pe_pct_change=None,
            ))
            continue
        u = checkpoint.underlying
        ce = checkpoint.ce_option
        pe = checkpoint.pe_option
        observations.append(CePeCheckpointObservation(
            checkpoint_label=label.value, status=checkpoint.status.value,
            underlying_spot=u.spot if u is not None else None, underlying_pct_change=u.pct_change if u is not None else None,
            ce_price=ce.option_ltp if ce is not None else None, ce_pct_change=ce.option_pct_change if ce is not None else None,
            pe_price=pe.option_ltp if pe is not None else None, pe_pct_change=pe.option_pct_change if pe is not None else None,
        ))

    recorded = [c for c in _ordered(checkpoints) if c.status == CheckpointStatus.RECORDED]
    latest_ce = next((c.ce_option.option_pct_change for c in reversed(recorded) if c.ce_option is not None and c.ce_option.option_pct_change is not None), None)
    latest_pe = next((c.pe_option.option_pct_change for c in reversed(recorded) if c.pe_option is not None and c.pe_option.option_pct_change is not None), None)

    return CePeOutcomeComparison(
        reference_strike=contracts.reference_strike,
        ce_initial_price=contracts.ce_requested.ltp if contracts.ce_requested is not None else None,
        pe_initial_price=contracts.pe_requested.ltp if contracts.pe_requested is not None else None,
        underlying_initial_spot=snapshot.underlying.spot,
        ce_thesis_status=_pct_to_thesis_status(latest_ce) if contracts.ce_requested is not None else None,
        pe_thesis_status=_pct_to_thesis_status(latest_pe) if contracts.pe_requested is not None else None,
        observations=observations,
    )


def build_reconciliation(
    snapshot: AnalysisSnapshot, checkpoints: list[OutcomeCheckpoint], *, now: datetime, near_level_pct_threshold: Decimal
) -> ReconciliationResult:
    """The single entry point tying Phases 4-12 together. Produces a NEW
    record every time it is called with more checkpoints — never mutates a
    previously-persisted `ReconciliationResult` (see that model's own
    docstring)."""
    ordered = _ordered(checkpoints)

    bias = snapshot.decision.final_bias
    invalidation = track_invalidation(bias=bias, invalidation_level=snapshot.decision.invalidation_level, checkpoints=ordered)
    underlying_status, option_status, divergence_note = evaluate_thesis(snapshot, ordered, invalidation)
    excursion = compute_excursion(snapshot.underlying.spot, ordered)
    level_outcomes = [classify_level_outcome(lv, checkpoints=ordered, test_proximity_pct=near_level_pct_threshold) for lv in snapshot.levels.levels]

    recorded = _recorded(ordered)
    latest_recorded = recorded[-1] if recorded else None
    evidence_recon = reconcile_evidence(snapshot.evidence, latest_recorded.evidence if latest_recorded is not None else None)
    adversarial_recon = reconcile_adversarial(snapshot.adversarial, latest_recorded)
    decay_recon = reconcile_decay(ordered)

    is_final = any(c.checkpoint_label == CheckpointLabel.EOD for c in ordered)
    ce_pe_comparison = build_ce_pe_comparison(snapshot, ordered)

    return ReconciliationResult(
        audit_id=snapshot.identity.audit_id, computed_at=now,
        checkpoints_used=[c.checkpoint_label.value for c in ordered], is_final=is_final,
        underlying_thesis_status=underlying_status, option_thesis_status=option_status,
        option_vs_underlying_divergence_note=divergence_note, invalidation=invalidation, excursion=excursion,
        level_outcomes=level_outcomes, evidence_reconciliation=evidence_recon, adversarial_reconciliation=adversarial_recon,
        decay_reconciliation=decay_recon, ce_pe_comparison=ce_pe_comparison,
        detail=f"reconciled using {len(ordered)} checkpoint(s), {len(recorded)} recorded",
    )


def compute_journal_counters(analyses: list[AnalysisSnapshot], reconciliations: list[ReconciliationResult]) -> JournalCounters:
    """Phase 18. See `JournalCounters`'s own docstring — these are counts,
    never a rate, never "accuracy".
    """
    by_audit_id = {r.audit_id: r for r in reconciliations}
    unresolved = sum(1 for a in analyses if (r := by_audit_id.get(a.identity.audit_id)) is None or not r.is_final)
    invalidated = sum(1 for r in reconciliations if r.underlying_thesis_status == ThesisStatus.INVALIDATED)
    confirmed = sum(1 for r in reconciliations if r.underlying_thesis_status == ThesisStatus.CONFIRMED)
    return JournalCounters(
        analyses_count=len(analyses), outcomes_count=len(reconciliations),
        unresolved_count=unresolved, invalidated_count=invalidated, confirmed_count=confirmed,
    )
