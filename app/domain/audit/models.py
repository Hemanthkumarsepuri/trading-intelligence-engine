"""Post-Analysis Audit Journal — pure, immutable record models (Milestone G).

This is NOT an ML system, NOT a prediction-training system, and NOT a
scoring system. It is an empirical measurement system: it records exactly
what this system believed at analysis time, based only on evidence
available at that moment, and later records what actually happened,
without ever going back and changing the first record.

=== IMMUTABILITY RULE (the single most important property here) ===
Once an `AnalysisSnapshot` is created, it is NEVER mutated. No field is
ever recalculated from a later API call, no missing value is ever
back-filled with later data, no decision is ever overwritten. If a real
error is later discovered in a snapshot, the fix is a NEW record with
`corrects_audit_id` pointing at the original — the original stays exactly
as it was written. This is enforced structurally, not just by convention:
every repository in `app.persistence` that stores these records exposes
only an append-only `save()` — there is no update or delete method for
any of these types anywhere in this codebase.

=== NO DUPLICATION OF EXISTING DATA ===
The full option-chain snapshot for a given underlying/expiry/instant is
already durably persisted by the pre-existing `OptionChainRepository`
(`app.persistence.interfaces`). `AnalysisSnapshot` does not duplicate it —
`OptionsSection.chain_reference` records only the `(underlying, expiry,
data_timestamp)` needed to look the real snapshot back up.

=== CHECKPOINT-SAMPLED RESOLUTION ===
Outcome checkpoints are captured at real wall-clock instants an operator
(or a scheduler) actually requests them — never backfilled or
interpolated. Consequently every excursion/level/invalidation calculation
derived from checkpoints (`app.domain.audit.reconciliation`) is only as
precise as the checkpoints that were actually captured, not tick-level —
this is documented on every field that depends on it, not hidden.

=== KNOWN, DELIBERATE GAPS (documented, not silently missing) ===
- No "rejected candidate with rationale" list exists: `candidate_engine.py`
  EXCLUDES non-qualifying legs rather than retaining them with a reason,
  so there is nothing to capture here without inventing data.
- News/event data: this system has no legitimate free news source (see
  docs/data-sources/PROVIDER_DECISION.md) — `NewsSection` is always the
  honest unavailable state, never fabricated.

=== THRESHOLD DISCIPLINE ===
Every enum in this module is a DESCRIPTIVE classification of what was
observed, never a probability, a score, or a win/loss verdict. Numeric
cutoffs used to derive these classifications (e.g. how close price must
come to a level to count as "tested") are required, undefaulted
parameters at the point of use in `reconciliation.py`, exactly like every
other threshold in this codebase.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domain.market.models import OptionRight

# Bump this constant on ANY material change to the decision/evidence/
# candidate-generation rules (decision_engine.py, evidence_matrix.py,
# candidate_engine.py, decay_viability.py, adversarial_analysis.py, ...).
# Every `AnalysisSnapshot` records the version that produced it so future
# outcome statistics are never silently contaminated by mixing analyses
# produced under different rules (Phase 14's explicit requirement).
CURRENT_ANALYSIS_VERSION = "1.0.0"


def new_audit_id() -> str:
    """The only sanctioned way to mint an `audit_id` — a random, globally
    unique identifier, never derived from mutable state (a timestamp+symbol
    composite could collide if a caller ever re-runs at the exact same
    `as_of`, which a replay/backtest legitimately might).
    """
    return uuid4().hex


class CheckpointLabel(str, Enum):
    FIVE_MIN = "5m"
    FIFTEEN_MIN = "15m"
    THIRTY_MIN = "30m"
    SIXTY_MIN = "60m"
    EOD = "EOD"


# Fixed offsets from `generated_at`. EOD deliberately has none here — it is
# anchored to NSE's real market-close wall-clock time, not a fixed duration
# from generation time (see `app.orchestration.audit_journal.eod_deadline`).
CHECKPOINT_OFFSETS: dict[CheckpointLabel, timedelta] = {
    CheckpointLabel.FIVE_MIN: timedelta(minutes=5),
    CheckpointLabel.FIFTEEN_MIN: timedelta(minutes=15),
    CheckpointLabel.THIRTY_MIN: timedelta(minutes=30),
    CheckpointLabel.SIXTY_MIN: timedelta(minutes=60),
}

CHECKPOINT_ORDER: dict[CheckpointLabel, int] = {
    CheckpointLabel.FIVE_MIN: 0,
    CheckpointLabel.FIFTEEN_MIN: 1,
    CheckpointLabel.THIRTY_MIN: 2,
    CheckpointLabel.SIXTY_MIN: 3,
    CheckpointLabel.EOD: 4,
}


class CheckpointStatus(str, Enum):
    RECORDED = "RECORDED"
    MARKET_CLOSED = "MARKET_CLOSED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class _FrozenModel(BaseModel):
    """Every model in this module inherits from this rather than bare
    `BaseModel` — the IMMUTABILITY RULE (module docstring) applies to
    every nested section, not just the top-level `AnalysisSnapshot`/
    `OutcomeCheckpoint`/`ReconciliationResult`. Setting `frozen=True`
    only on the outer model would NOT have prevented `snapshot.underlying.
    spot = ...` (mutating the nested object in place) — every level of
    nesting needs it.
    """

    model_config = {"frozen": True}


# ============================================================
# Phase 1 — the immutable analysis snapshot, section by section
# ============================================================


class IdentitySection(_FrozenModel):
    audit_id: str
    symbol: str
    instrument_key: str | None
    generated_at: datetime
    timezone: str = "Asia/Kolkata"
    market_state: str
    analysis_version: str
    corrects_audit_id: str | None = None


class UnderlyingSection(_FrozenModel):
    spot: Decimal | None
    day_change: Decimal | None
    day_change_pct: Decimal | None
    data_age_seconds: float | None
    freshness_label: str | None


class TechnicalSection(_FrozenModel):
    trend: str
    ema_alignment: str | None
    vwap_position: str | None
    rsi_value: Decimal | None
    atr_pct_of_price: Decimal | None
    regime_detail: str


class FuturesSection(_FrozenModel):
    futures_instrument_key: str | None
    futures_ltp: Decimal | None
    futures_oi: Decimal | None
    futures_basis_pct: Decimal | None
    futures_interpretation: str | None


class OptionChainReference(_FrozenModel):
    """Pointer to the already-persisted `OptionChainSnapshot` — see module
    docstring's "NO DUPLICATION" section. Not the chain itself.
    """

    underlying: str
    expiry: date
    data_timestamp: datetime | None


class OptionsSection(_FrozenModel):
    expiry: date | None
    atm_strike: Decimal | None
    total_call_oi: int | None
    total_put_oi: int | None
    pcr_oi: Decimal | None
    atm_ce_iv: Decimal | None
    atm_pe_iv: Decimal | None
    chain_iv: Decimal | None
    ce_pe_skew: Decimal | None
    iv_trend: str | None
    oi_structure_detail: str | None
    chain_reference: OptionChainReference | None


class TemporalObservationSnapshot(_FrozenModel):
    strike: Decimal
    right: OptionRight
    interval_seconds: float
    price_change_pct: Decimal | None
    oi_change_pct: Decimal | None
    volume_change: int | None
    iv_change: Decimal | None
    conventional_reading: str
    quality: str


class AnomalySnapshot(_FrozenModel):
    metric_name: str
    current_value: Decimal | None
    deviation_ratio: Decimal | None
    verdict: str
    detail: str


class TemporalSection(_FrozenModel):
    observations: list[TemporalObservationSnapshot] = Field(default_factory=list)
    anomalies: list[AnomalySnapshot] = Field(default_factory=list)


class LevelSnapshot(_FrozenModel):
    kind: str  # "support" | "resistance"
    strike: Decimal
    strength: str
    distance_from_spot_pct: Decimal | None
    stability_state: str
    evidence: str


class LevelsSection(_FrozenModel):
    levels: list[LevelSnapshot] = Field(default_factory=list)


class GlobalContextInputSnapshot(_FrozenModel):
    label: str
    day_change_pct: Decimal | None
    contributes_to_verdict: bool
    detail: str


class GlobalSection(_FrozenModel):
    inputs: list[GlobalContextInputSnapshot] = Field(default_factory=list)
    verdict: str | None
    detail: str | None


class NewsItemSnapshot(_FrozenModel):
    """Sprint 4 -- a real, legitimate, first-party news item (Upstox's own
    `/v2/news`, same already-authorized account, see docs/data-sources/
    PROVIDER_DECISION.md). `direction`/`relevance` mirror `app.domain.news.
    models.NewsItem` exactly (`direction` is always `"UNKNOWN"` -- no
    fabricated sentiment)."""

    source: str
    published_at: datetime
    title: str
    summary: str | None
    url: str | None
    relevance: str
    direction: str
    evidence_quality: str


class NewsSection(_FrozenModel):
    """Additive since Sprint 4 (Part T: audit-journal compatibility) --
    `items`/`fetch_error` both default so an OLDER persisted record
    (written before this field existed) still deserializes cleanly with
    `items=[]`. `unavailable_reason` keeps its original default text for
    the same backward-compatibility reason, but a Sprint-4-or-later
    snapshot only ever sets it when news was genuinely unavailable this
    run (fetch failure or no legitimate source) -- never merely because
    zero items happened to exist (a real, successful, empty result is
    `items=[]` with `unavailable_reason=None`, not the STOP message).
    """

    provider: str | None = None
    items: list[NewsItemSnapshot] = Field(default_factory=list)
    fetch_error: str | None = None
    unavailable_reason: str | None = "NOT AVAILABLE FROM CURRENT AUTHORIZED DATA SOURCES"


class EvidenceRowSnapshot(_FrozenModel):
    name: str
    group: str
    direction: str
    detail: str


class EvidenceSection(_FrozenModel):
    rows: list[EvidenceRowSnapshot] = Field(default_factory=list)
    convergence: str


class AdversarialSection(_FrozenModel):
    bull_case: list[str] = Field(default_factory=list)
    bear_case: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    opposite_case_is_equally_supported: bool


class QualitySection(_FrozenModel):
    data_quality: str
    evidence_quality: str
    decision_quality: str
    setup_quality: str
    option_quality: str
    liquidity_quality: str
    risk_quality: str
    supporting_evidence_count: int
    conflicting_evidence_count: int


class CandidateSnapshot(_FrozenModel):
    strike: Decimal
    right: OptionRight
    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    spread_pct: Decimal | None
    open_interest: int | None
    implied_volatility: Decimal | None
    delta: Decimal | None
    theta: Decimal | None
    gamma: Decimal | None
    vega: Decimal | None
    liquidity_grade: str
    is_atm: bool
    strikes_from_atm: int
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    invalidation_condition: str
    risks: list[str] = Field(default_factory=list)
    decay_verdict: str | None = None
    decay_viability_ratio: Decimal | None = None
    # Renamed from `breakeven_underlying_move_pct` -- this is a model-
    # estimated cost-coverage figure, NOT the contractual expiry
    # breakeven; see decay_viability.py's field docstrings. Additive
    # rename: a pre-existing persisted record simply has neither the old
    # nor new field populated on re-read (pydantic ignores unknown
    # fields by default), which is honest -- it never silently relabels
    # old data under the new name.
    required_underlying_move_pct: Decimal | None = None
    contractual_expiry_breakeven: Decimal | None = None
    expected_move_over_horizon_pct: Decimal | None = None


class DecisionSection(_FrozenModel):
    final_bias: str
    decision: str
    reasoning: str
    candidate_selected: CandidateSnapshot | None
    invalidation_level: Decimal | None
    invalidation_description: str | None


class ContractAssessmentSnapshot(_FrozenModel):
    """Sprint 5, Phase 8 — a serializable copy of one `contract_analysis.
    ContractAssessment`'s already-computed fields for ONE specific
    strike/right. Reused for the requested contract, the opposite-right
    contract, and every nearby alternative — same shape regardless of
    role, and no new proprietary score is computed here (Phase 8's
    explicit requirement)."""

    strike: Decimal
    right: OptionRight
    moneyness: str | None
    ltp: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    spread_pct: Decimal | None
    open_interest: int | None
    change_in_open_interest: int | None
    volume: int | None
    implied_volatility: Decimal | None
    delta: Decimal | None
    theta: Decimal | None
    gamma: Decimal | None
    vega: Decimal | None
    liquidity_grade: str
    decay_verdict: str | None
    decay_viability_ratio: Decimal | None
    distance_to_support_pct: Decimal | None
    distance_to_resistance_pct: Decimal | None


class ContractObservationSection(_FrozenModel):
    """Additive (Sprint 5, Phase 8). Persists the SAME symmetric CE/PE
    `ContractComparison` data `direction_analysis.build_direction_
    comparison()` already computes around one reference strike (the
    user's requested strike if they asked for a specific contract, ATM
    otherwise — see `options_intelligence_pipeline.py`). This closes a
    real gap found in Sprint 5's Phase 0 audit: `DecisionSection.
    candidate_selected` tracks the BIAS-GATED candidate (Milestone G,
    predates Sprint 2), which is not necessarily the contract the user
    actually asked about. `requested_right` records which right (if any)
    the user actually typed — `None` means a bare underlying query
    ("KAYNES"), never silently defaulted to a side.

    Defaults to an empty section so a pre-Sprint-5 persisted
    `AnalysisSnapshot` (which never had this field) still deserializes
    cleanly — same backward-compatibility pattern as Sprint 4's
    `NewsSection` additions.
    """

    reference_strike: Decimal | None = None
    requested_right: OptionRight | None = None
    ce_requested: ContractAssessmentSnapshot | None = None
    ce_alternatives: list[ContractAssessmentSnapshot] = Field(default_factory=list)
    pe_requested: ContractAssessmentSnapshot | None = None
    pe_alternatives: list[ContractAssessmentSnapshot] = Field(default_factory=list)


class AnalysisSnapshot(_FrozenModel):
    """The IMMUTABLE audit record for one completed analysis — see module
    docstring's IMMUTABILITY RULE before touching anything that reads or
    writes one of these.
    """

    identity: IdentitySection
    underlying: UnderlyingSection
    technical: TechnicalSection
    futures: FuturesSection
    options: OptionsSection
    temporal: TemporalSection
    levels: LevelsSection
    global_context: GlobalSection
    news: NewsSection
    evidence: EvidenceSection
    adversarial: AdversarialSection
    quality: QualitySection
    candidates: list[CandidateSnapshot] = Field(default_factory=list)
    decision: DecisionSection
    contracts: ContractObservationSection = Field(default_factory=ContractObservationSection)


# ============================================================
# Phase 2/3 — outcome checkpoints
# ============================================================


class DecayAttributionSnapshot(_FrozenModel):
    """Direct reuse of `app.domain.options.decay_engine.DecayAttribution`
    — no new attribution math, just a serializable copy of its fields.
    """

    observed_price_change: Decimal | None
    underlying_price_change: Decimal | None
    iv_change: Decimal | None
    delta_effect: Decimal | None
    gamma_effect: Decimal | None
    vega_effect: Decimal | None
    theta_effect: Decimal | None
    residual: Decimal | None
    confidence: str
    detail: str


class UnderlyingOutcome(_FrozenModel):
    """`pct_change` doubles as "distance from original spot" (they are the
    same number by construction — the original spot IS the 100% baseline).
    The running high/low "reached since analysis" is NOT duplicated here
    per-checkpoint (a single checkpoint cannot know the full history) — it
    is computed once, correctly, across the FULL checkpoint sequence by
    `app.domain.audit.reconciliation.compute_excursion()` and reported as
    `ReconciliationResult.excursion`.
    """

    spot: Decimal | None
    absolute_change: Decimal | None
    pct_change: Decimal | None


class OptionOutcome(_FrozenModel):
    option_ltp: Decimal | None
    option_pct_change: Decimal | None
    implied_volatility: Decimal | None
    iv_change: Decimal | None
    spread_pct: Decimal | None
    liquidity_grade: str | None
    intrinsic_value: Decimal | None
    intrinsic_value_change: Decimal | None
    time_remaining_days: int | None
    decay_attribution: DecayAttributionSnapshot | None = None


class OutcomeCheckpoint(_FrozenModel):
    """One append-only observation of what actually happened after an
    analysis, captured at a real wall-clock instant. `scheduled_offset_seconds`
    is `None` only for `EOD` (anchored to market close, not a fixed
    duration); `actual_elapsed_seconds` is always the REAL elapsed time —
    a late capture is recorded as late, never silently relabeled as
    exactly on schedule.
    """

    audit_id: str
    checkpoint_label: CheckpointLabel
    scheduled_offset_seconds: float | None
    captured_at: datetime
    actual_elapsed_seconds: float
    status: CheckpointStatus

    underlying: UnderlyingOutcome | None = None
    option: OptionOutcome | None = None

    # Sprint 5, Phase 8 — the SAME outcome shape as `option`, but keyed to
    # the reference-strike CE/PE legs tracked in `AnalysisSnapshot.
    # contracts` (requested + opposite side), independent of whichever
    # candidate the bias-gated `option` field above tracks. `None` when
    # `contracts.reference_strike` was `None` (no chain reference) or the
    # leg was not found in the chain at checkpoint time -- never fabricated.
    ce_option: OptionOutcome | None = None
    pe_option: OptionOutcome | None = None

    # A fresh re-run of the evidence matrix / adversarial analysis at this
    # checkpoint's own `as_of` — used only by `reconciliation.py` to
    # compare against the ORIGINAL snapshot. Never fed back into, and
    # never mutates, the original `AnalysisSnapshot`.
    evidence: EvidenceSection | None = None
    adversarial: AdversarialSection | None = None

    detail: str = ""


# ============================================================
# Phase 4-12 — reconciliation
# ============================================================


class ThesisStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    MIXED = "MIXED"
    UNRESOLVED = "UNRESOLVED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class LevelOutcomeState(str, Enum):
    HELD = "HELD"
    FAILED = "FAILED"
    BROKEN = "BROKEN"
    TESTED = "TESTED"
    NOT_TESTED = "NOT_TESTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class DecayReconciliationVerdict(str, Enum):
    CONSISTENT = "CONSISTENT"
    PARTIALLY_CONSISTENT = "PARTIALLY_CONSISTENT"
    INCONSISTENT = "INCONSISTENT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class EvidenceRowOutcome(str, Enum):
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    UNCHANGED = "UNCHANGED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ExcursionSample(_FrozenModel):
    """Objective, unlabeled running excursion as of one checkpoint —
    computed IDENTICALLY regardless of decision type (Phase 12: NO_TRADE
    gets the same numbers a directional thesis gets). MFE/MAE for a
    directional thesis is purely a relabeling of `up_move_pct`/
    `down_move_pct` done in `reconciliation.render`, never a different
    calculation.
    """

    checkpoint_label: str
    up_move_pct: Decimal
    down_move_pct: Decimal  # positive number = magnitude of the downside move


class InvalidationTracking(_FrozenModel):
    invalidation_level: Decimal | None
    reached: bool | None  # None = no directional level to track, or no checkpoints yet
    reached_at_checkpoint: str | None
    reached_at: datetime | None


class LevelOutcomeEntry(_FrozenModel):
    kind: str
    strike: Decimal
    state: LevelOutcomeState
    first_test_checkpoint: str | None
    detail: str


class EvidenceRowReconciliation(_FrozenModel):
    name: str
    group: str
    original_direction: str
    latest_direction: str | None
    outcome: EvidenceRowOutcome


class AdversarialReconciliation(_FrozenModel):
    bull_case_count_original: int
    bull_case_count_latest: int | None
    bear_case_count_original: int
    bear_case_count_latest: int | None
    contradiction_resolved: bool | None
    latest_checkpoint_used: str | None


class DecayReconciliationEntry(_FrozenModel):
    checkpoint_label: str
    verdict: DecayReconciliationVerdict
    detail: str


class CePeCheckpointObservation(_FrozenModel):
    """Sprint 6, Phase 8 — one checkpoint's SIDE-BY-SIDE CE and PE
    observation for the reference strike, purely descriptive (no
    preference, no score). `status` mirrors the checkpoint's own
    `CheckpointStatus` (plus `"PENDING"` for a label not yet captured at
    all) so the dashboard never has to re-derive it."""

    checkpoint_label: str
    status: str
    underlying_spot: Decimal | None
    underlying_pct_change: Decimal | None
    ce_price: Decimal | None
    ce_pct_change: Decimal | None
    pe_price: Decimal | None
    pe_pct_change: Decimal | None


class CePeOutcomeComparison(_FrozenModel):
    """Sprint 6, Phase 8 — THE empirical CE-vs-PE comparison this sprint
    exists to produce: what happened to the requested CE and the
    corresponding PE at the same reference strike, checkpoint by
    checkpoint. `ce_thesis_status`/`pe_thesis_status` reuse the EXACT SAME
    pct-change-sign classification `evaluate_thesis()` already applies to
    `option_thesis_status` (CONFIRMED = gained value, INVALIDATED = lost
    value, MIXED = unchanged, INSUFFICIENT_DATA = never recorded) — no new
    scoring, no preference, no recommendation. `None` on both `AnalysisSnapshot.
    contracts.ce_requested`/`pe_requested` absent means there is genuinely
    nothing to compare (e.g. a bare underlying query) -- see
    `build_ce_pe_comparison()`."""

    reference_strike: Decimal | None
    ce_initial_price: Decimal | None
    pe_initial_price: Decimal | None
    underlying_initial_spot: Decimal | None
    ce_thesis_status: ThesisStatus | None
    pe_thesis_status: ThesisStatus | None
    observations: list[CePeCheckpointObservation] = Field(default_factory=list)


class ReconciliationResult(_FrozenModel):
    """A derived, re-computable VIEW over one `AnalysisSnapshot` plus
    however many `OutcomeCheckpoint`s have been captured so far. Each call
    to `build_reconciliation()` produces a NEW record (append-only, like
    everything else here) rather than mutating a previous one — `is_final`
    tells a reader whether this is the terminal (EOD-inclusive) view or an
    interim one computed with only some checkpoints available so far.
    """

    audit_id: str
    computed_at: datetime
    checkpoints_used: list[str] = Field(default_factory=list)
    is_final: bool

    underlying_thesis_status: ThesisStatus
    option_thesis_status: ThesisStatus | None
    option_vs_underlying_divergence_note: str | None

    invalidation: InvalidationTracking
    excursion: list[ExcursionSample] = Field(default_factory=list)
    level_outcomes: list[LevelOutcomeEntry] = Field(default_factory=list)
    evidence_reconciliation: list[EvidenceRowReconciliation] = Field(default_factory=list)
    adversarial_reconciliation: AdversarialReconciliation | None
    decay_reconciliation: list[DecayReconciliationEntry] = Field(default_factory=list)
    # Sprint 6, Phase 8 -- additive; `None` for a pre-Sprint-6 persisted
    # record (never existed) or a snapshot with no reference strike to
    # compare (a bare underlying query).
    ce_pe_comparison: CePeOutcomeComparison | None = None

    detail: str


class JournalCounters(_FrozenModel):
    """OBSERVED COUNTS ONLY — see Phase 18. Never a performance metric,
    never a rate, never a percentage, never "accuracy". This milestone
    computes no win rate at all, regardless of sample size.
    """

    analyses_count: int
    outcomes_count: int
    unresolved_count: int
    invalidated_count: int
    confirmed_count: int
