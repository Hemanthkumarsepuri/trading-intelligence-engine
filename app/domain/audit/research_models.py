"""Daily Market Researcher — the append-only research-run audit record.

This is deliberately a SEPARATE, additive record from `AnalysisSnapshot`
(`app.domain.audit.models`), not a duplicate of it. Every symbol the
researcher deep-analyzes already gets its own normal, immutable
`AnalysisSnapshot` via the exact same `run_analysis()` path a manual
single-symbol query uses (see `app.orchestration.daily_research`) — this
module only records the RESEARCH-SPECIFIC information that snapshot
doesn't carry: which symbols were screened, which were rejected and why,
which were shortlisted and at what rank, and the ranking rationale
(the real tie-break values, never a new score) -- each shortlisted entry
cross-references its own real `audit_id` rather than duplicating any
analytical content.

Same immutability discipline as `app.domain.audit.models`: no update or
delete method exists anywhere for this record (see
`app.persistence.jsonl_file.JsonlResearchRunRepository`). Following the
same per-module `_FrozenModel` convention already used by
`app.domain.ipo.audit_models` (a fresh local copy, not a cross-module
import of the options-domain one).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


def new_run_id() -> str:
    """Mirrors `app.domain.audit.models.new_audit_id()` -- a random,
    globally unique identifier for one research run, never derived from
    mutable state."""
    return uuid4().hex


class _FrozenModel(BaseModel):
    model_config = {"frozen": True}


class RejectionRecord(_FrozenModel):
    """One screened-out or gated symbol, with the real reason it did not
    reach the shortlist -- never silently dropped."""

    symbol: str
    reason: str


class RankingRationale(_FrozenModel):
    """The real, already-computed tie-break values `rank_candidates()`
    used for one shortlisted candidate -- exactly what a reader would
    need to verify "why did this rank #1 today" without re-running
    anything. Never a blended/opaque score."""

    decision_tier: str
    directional_verdict: str
    supporting_groups: int
    liquidity_grade: str
    decay_verdict: str | None
    required_underlying_move_pct: str | None
    breakeven_distance_pct: str | None
    headroom_pct: str | None


class ShortlistRecord(_FrozenModel):
    rank: int
    symbol: str
    audit_id: str | None  # cross-reference into the normal AnalysisSnapshot journal
    selected_right: str
    selected_strike: str
    rationale: RankingRationale
    # Research-deepening phase -- additive, defaulted so an OLDER
    # persisted record (before this field existed) still deserializes.
    research_confidence: str | None = None
    # Early-stage discovery phase -- additive, defaulted for the same
    # reason. `None` means "not computed this run" (an older record),
    # never a real classification.
    early_stage_state: str | None = None
    # Early-move discovery phase -- additive, same backward-compat-default
    # pattern. `participation_note` is the real, honest Stage-1 buy/sell
    # quantity observation (never called "accumulation" -- see
    # `_participation_note()`); `market_context` is SUPPORTIVE/OPPOSING/
    # NEUTRAL/UNKNOWN, reported only, never a ranking input (see
    # `_market_context()`).
    participation_note: str | None = None
    market_context: str | None = None


class ResearchCoverage(_FrozenModel):
    """Sprint 1 -- how completely THIS run actually evaluated the real
    universe. This is metadata about the RUN's reliability, never a stock-
    quality signal: it is computed strictly AFTER ranking/gating already
    produced the shortlist, from counts `rank_candidates()`/`_gate()`
    never see, and is never read back into either -- a `POOR`-coverage run
    still applies the exact same gates to whatever it did reliably
    evaluate. Distinguishes a real, evidence-based rejection or Stage-1
    screen-out (the symbol WAS reliably evaluated -- see
    `compute_research_coverage()`) from a genuine infrastructure/data
    failure (the symbol could NOT be reliably evaluated this run), reusing
    the SAME rejection-reason categories `categorize_rejection()` already
    computes -- never a second judgment about a symbol."""

    universe_count: int
    # `None` for all three exactly when Stage 1 was skipped (an explicit
    # `?symbols=` override) -- same "None means not run, never zero"
    # convention as `stage_one_survivor_count` elsewhere in this module.
    stage1_attempted: int | None
    stage1_successful: int | None
    stage1_failed: int | None
    stage2_attempted: int
    stage2_successful: int
    stage2_failed: int
    # Real sub-tallies within the failures above, string-matched from the
    # SAME already-generated rejection-reason text (never a new fetch,
    # never a new judgment) -- surfaced separately because they answer two
    # different real questions: "was the provider slow/unreachable" vs
    # "did a real timestamp-ordering defect occur."
    timeout_failures: int
    timestamp_quality_failures: int
    # HIGH / GOOD / DEGRADED / POOR -- see `compute_research_coverage()`
    # for the real, evidence-derived ratio and its round, documented
    # thresholds (never a fitted/arbitrary weight).
    classification: str


class SymbolFreshnessRecord(_FrozenModel):
    """95%+ Reliability & Performance Gate -- one symbol's real,
    already-computed retrieval facts (`AnalyzeResponse.generated_at`/
    `.latency_seconds`, already produced by `run_analysis()` for every
    symbol regardless of this record's existence). Exists so a whole-
    market scan can honestly answer "how old is EACH symbol's data",
    never collapsing 210 real, different retrieval times into one
    single scan-level timestamp (see `ResearchScanSnapshot`'s own
    docstring for why that collapsing would be dishonest)."""

    symbol: str
    generated_at: datetime | None
    latency_seconds: float | None
    market_state: str | None
    succeeded: bool


class ResearchScanSnapshot(_FrozenModel):
    """95%+ Reliability & Performance Gate -- a whole-market scan is
    itself a temporal object: the first symbol analyzed and the last
    symbol analyzed were not observed at the same instant, even though
    they appear in the same run. This snapshot makes that honest instead
    of letting the UI imply one single "LIVE" moment for the entire
    scan. Every field here is copied from data `run_daily_research()`
    already computed (`ResearchCoverage`, each `AnalyzeResponse`) --
    never a new fetch, never a new judgment about data quality."""

    scan_id: str = Field(default_factory=new_run_id)
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    # A cheap, honest version marker for "which universe definition
    # produced this run" -- the count of F&O-eligible equity underlyings
    # `list_fo_eligible_equity_underlyings()` returned this run, not a
    # hash or a manually incremented number nobody would keep updated.
    universe_version: str
    requested_symbols: int
    successful_symbols: int
    failed_symbols: int
    provider: str
    coverage: ResearchCoverage
    per_symbol_freshness: list[SymbolFreshnessRecord] = Field(default_factory=list)
    generated_at: datetime
    universe_source: str = "upstox_instrument_master.NSE_FO_equity_underlyings"
    fno_ban_status: str = "FNO_BAN_STATUS_UNKNOWN"
    universe_discovery_seconds: float = 0.0
    stage1_seconds: float = 0.0
    stage2_seconds: float = 0.0
    assembly_seconds: float = 0.0
    avg_stage2_latency_seconds: float = 0.0
    survivor_cap_applied: bool = False
    truncated_at_stage1: int = 0
    index_relative_strength_available: bool = False
    news_timing_note: str = "News fetch is inside per-symbol Stage 2; not a separate timed stage."


class ResearchRunRecord(_FrozenModel):
    """One daily-researcher run."""

    run_id: str = Field(default_factory=new_run_id)
    generated_at: datetime
    market_state: str
    universe: list[str]
    screened_count: int
    deep_analyzed_count: int
    rejected: list[RejectionRecord]
    shortlist: list[ShortlistRecord]
    no_high_conviction: bool
    # Research-deepening phase -- additive, both defaulted so an OLDER
    # persisted record (before Stage 1 / the rejection tally existed)
    # still deserializes without a migration. `None` means "Stage 1 did
    # not run this call" (an explicit `symbols` override was given),
    # never "zero survivors" (that case is a real `0`).
    stage_one_survivor_count: int | None = None
    rejection_summary: dict[str, int] = Field(default_factory=dict)
    # Early-stage discovery phase -- additive, real counts derived from
    # `rejection_summary`'s own categories (never a duplicate judgment).
    rejected_as_extended_count: int = 0
    rejected_insufficient_evidence_count: int = 0
    # Sprint 1 -- additive, same backward-compat-default pattern. `None`
    # means "not computed this run" (an older record), never a real
    # coverage classification.
    coverage: ResearchCoverage | None = None


# ============================================================
# Sprint 3 -- RESEARCH OUTCOME TRACKING: what actually happened to a
# shortlisted candidate afterward. Two separate, append-only record
# types, following the EXACT SAME architectural split
# `app.domain.audit.models` already established for the single-symbol
# live-operation checkpoint system (`AnalysisSnapshot` immutable at
# capture time; `OutcomeCheckpoint` a later, separate observation) --
# never a second design. A `ResearchObservation` is NEVER modified once
# written, regardless of what a later `ResearchOutcomeCheckpoint` reveals
# -- no look-ahead can ever leak backward into the original research
# record. This is research OUTCOME TRACKING, never trade execution and
# never a claim of "success" -- see `ResearchProgression`'s own docstring
# for the deliberately factual vocabulary.
# ============================================================


class ResearchObservation(_FrozenModel):
    """One shortlisted research candidate at the exact moment it was
    identified -- every field here is copied VERBATIM from fields
    `ResearchThesisView`/`RankedCandidate` already computed for that run's
    real shortlist entry (see `_build_research_observation()`); nothing
    here is a new calculation, and nothing here is ever recomputed or
    edited after the fact."""

    observation_id: str = Field(default_factory=new_run_id)
    run_id: str  # cross-references the ResearchRunRecord this came from
    audit_id: str | None  # cross-references the underlying AnalysisSnapshot
    generated_at: datetime
    symbol: str
    direction: str  # BULLISH or BEARISH, verbatim from the candidate's own preferred_direction
    # Phase 3 gap-closure -- `None` for a price-only observation (no
    # historical option-chain evidence to select a contract from, see
    # `app.orchestration.historical_replay`'s price-only gate). Every
    # LIVE observation before and after this phase always supplies a real
    # value here; `None` is exclusively a replay/no-derivatives fact,
    # never a live gap.
    selected_right: str | None = None
    selected_strike: str | None = None
    early_stage_state: str
    research_confidence: str
    actionability: str  # the real, unmodified response.decision (or DATA_INSUFFICIENT)
    spot_at_observation: str | None
    contractual_expiry_breakeven: str | None
    # The real nearest opposing S/R level standing in this thesis's way,
    # if one existed this run -- kept as separate kind/value fields (not
    # a formatted string) so a later checkpoint can compare against the
    # real numeric value without parsing display text.
    nearest_level_kind: str | None
    nearest_level_value: str | None
    market_context: str | None
    participation_note: str | None
    coverage_classification: str | None  # the run's own ResearchCoverage.classification at observation time
    thesis: str
    # Sprint 4 -- additive, same backward-compat-default pattern as every
    # earlier field on this record: `None` means "not computed this run"
    # (an older persisted record), never a real classification. Verbatim
    # copies of `ResearchThesisView`'s own Sprint 4 fields -- see
    # `classify_structural_context()`/`classify_participation_depth()`/
    # `classify_relative_strength()`/`_pre_breakout_signal()` in
    # `app.orchestration.daily_research` for what each real value means.
    # Persisted for future analysis only -- no statistics are computed yet.
    structural_context: str | None = None
    participation_depth: str | None = None
    relative_strength: str | None = None
    pre_breakout_signal: bool | None = None
    # Phase 3 -- additive, same backward-compat-default pattern: an OLDER
    # persisted record (every one written before this phase) had exactly
    # one source, so it defaults to "LIVE" rather than leaving this
    # ambiguous. "REPLAY" marks an observation produced by
    # `app.orchestration.historical_replay` against locally persisted
    # historical candles instead of a live scan -- see that module's own
    # docstring. Replay observations are written to their OWN, separate
    # repository/files (never the live `research_observations.jsonl`), so
    # this field is redundant-but-explicit defense-in-depth, not the only
    # thing keeping the two apart.
    source: str = "LIVE"
    # Phase 3 gap-closure -- `False` means this observation was built from
    # PRICE/TECHNICAL evidence alone because the provider had no
    # historical option-chain data for this instant (see
    # `app.orchestration.historical_replay`'s price-only gate). Every
    # observation before this phase, and every LIVE observation since,
    # defaults `True` (unchanged meaning: a real contract was selected).
    # This is the one authoritative "was a real contract ever evaluated"
    # signal -- `selected_right`/`selected_strike` being `None` already
    # implies it, but this field makes the fact explicit and queryable
    # without inferring it from another field's absence.
    derivatives_evidence_available: bool = True
    # Phase 3 gap-closure -- the real, already-computed "what's missing"
    # text for this observation (e.g. "historical option-chain evidence
    # unavailable for this instant" for a price-only replay observation,
    # or a live observation's own real confirmation gap). `None` when
    # nothing is missing beyond ordinary pending confirmation. Never a
    # second evidence computation -- always copied verbatim from a field
    # `daily_research.py`'s own thesis-building already produced.
    missing_evidence: str | None = None
    # 95% sprint, Sprint 2 -- the real, already-computed named development
    # pattern (`DevelopmentPattern` value, e.g. "PRE_BREAKOUT_COMPRESSION",
    # or "NONE") this observation was built from -- verbatim from
    # `ResearchThesisView.developing_pattern` (live) or
    # `DevelopmentNarrativeView.pattern` (price-only replay). This is the
    # ONE field `app.orchestration.pattern_aggregation` groups by; adding
    # it here (rather than parsing `thesis` free text) is what makes
    # deterministic historical pattern aggregation possible without a
    # second classification. `None` only for an older persisted record
    # written before this field existed.
    pattern: str | None = None


class ResearchCheckpointLabel(str, Enum):
    """Trading-SESSION-based, deliberately never calendar days -- a
    weekend/holiday must never silently count as elapsed research time.
    See `most_recent_trading_day_at_or_before()`'s sibling,
    `next_trading_day()` (`app.domain.market.trading_calendar`), which
    `_target_trading_session_date()` walks forward with."""

    PLUS_1_SESSION = "PLUS_1_SESSION"
    PLUS_3_SESSIONS = "PLUS_3_SESSIONS"
    PLUS_5_SESSIONS = "PLUS_5_SESSIONS"


RESEARCH_CHECKPOINT_SESSIONS_AHEAD: dict[ResearchCheckpointLabel, int] = {
    ResearchCheckpointLabel.PLUS_1_SESSION: 1,
    ResearchCheckpointLabel.PLUS_3_SESSIONS: 3,
    ResearchCheckpointLabel.PLUS_5_SESSIONS: 5,
}


class ResearchProgression(str, Enum):
    """Deliberately factual, never a trading-outcome claim -- this system
    has no execution capability, so nothing here may ever read as
    "profitable"/"successful trade". `FOLLOW_THROUGH_OBSERVED` means the
    early-stage classification genuinely progressed toward confirmation
    by the checkpoint; `NO_FOLLOW_THROUGH` means it stayed early-stage or
    regressed to quieter; `FAILED_SETUP` means the real evidence at the
    checkpoint specifically shows a false-breakout signal; `UNKNOWN` means
    the checkpoint's own data was insufficient to classify progression at
    all (never guessed)."""

    FOLLOW_THROUGH_OBSERVED = "FOLLOW_THROUGH_OBSERVED"
    NO_FOLLOW_THROUGH = "NO_FOLLOW_THROUGH"
    FAILED_SETUP = "FAILED_SETUP"
    UNKNOWN = "UNKNOWN"


class ResearchOutcomeCheckpoint(_FrozenModel):
    """One append-only, factual later observation of a `ResearchObservation`
    -- a SEPARATE record; the original observation is never touched.
    Every `| None` field is honestly `None` when the real data needed to
    compute it was not reliably available at capture time -- never a
    guessed or interpolated value."""

    observation_id: str
    checkpoint_label: ResearchCheckpointLabel
    target_trading_session_date: date
    captured_at: datetime
    # The real MarketDataState (as a string) the fresh re-analysis read at
    # capture time -- e.g. a checkpoint captured while the market happened
    # to be closed is honestly labeled as such, never presented as live.
    data_state: str | None
    spot_at_checkpoint: str | None
    move_pct_from_observation: str | None
    # "if reliably available" (Sprint 3 spec) -- computed from the real
    # M15 candles the checkpoint's own fresh re-analysis already fetched,
    # ONLY when that history genuinely covers the full observation-to-
    # checkpoint window; `None`, honestly, otherwise (never a partial or
    # guessed excursion).
    max_favorable_move_pct: str | None
    max_adverse_move_pct: str | None
    swing_level_broken: bool | None
    breakeven_reached: bool | None
    became_extended: bool | None
    next_observed_early_stage_state: str | None
    progression: ResearchProgression
    note: str | None = None


# ============================================================
# Sprint 5 -- OUTCOME INTELLIGENCE: a deterministic, read-only SUMMARY
# across all of one observation's own already-persisted checkpoints (see
# `app.orchestration.research_outcome.summarize_research_outcome()`).
# This is research VALIDATION, never a trade result -- it answers "did
# this early-stage classification objectively progress, in hindsight",
# reusing ONLY each checkpoint's own already-computed `progression` field
# (never recomputed, never a new probability/score). Never persisted on
# its own -- always derived fresh from the real checkpoints that exist as
# of the moment it's requested, so it can never go stale or disagree with
# what's actually in the journal.
# ============================================================


class ResearchOutcomeStatus(str, Enum):
    """Deliberately factual, never a trading-outcome claim -- see
    `ResearchProgression`'s own docstring for the same discipline, which
    this status is directly derived from (never independently computed).
    `PENDING` means no checkpoint is due yet (or a due checkpoint has not
    yet been captured by a sweep); `INSUFFICIENT_OUTCOME_DATA` means
    checkpoints exist but none of them could determine progression
    (every one is `ResearchProgression.UNKNOWN`)."""

    PENDING = "PENDING"
    FOLLOW_THROUGH_OBSERVED = "FOLLOW_THROUGH_OBSERVED"
    NO_FOLLOW_THROUGH = "NO_FOLLOW_THROUGH"
    FAILED_SETUP = "FAILED_SETUP"
    INSUFFICIENT_OUTCOME_DATA = "INSUFFICIENT_OUTCOME_DATA"


class ResearchOutcomeSummary(_FrozenModel):
    """One observation's outcome, summarized across every checkpoint
    captured for it SO FAR. Never a probability, expected return, win
    rate, or confidence score -- `explanation` is a deterministic,
    factual sentence built only from already-persisted checkpoint facts
    (see `summarize_research_outcome()`)."""

    observation_id: str
    outcome_status: ResearchOutcomeStatus
    # "+1"/"+3"/"+5" -- the earliest checkpoint session, if any, whose own
    # already-persisted `progression` was FOLLOW_THROUGH_OBSERVED. `None`
    # when no checkpoint has qualified (including every non-FOLLOW_THROUGH
    # status).
    earliest_follow_through_session: str | None
    # The most recently CAPTURED checkpoint's own
    # `next_observed_early_stage_state` -- `None` when no checkpoint has
    # been captured yet. Never a prediction of what state comes next.
    latest_checkpoint_state: str | None
    explanation: str
