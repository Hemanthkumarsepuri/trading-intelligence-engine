"""Release gate (Sections 9-12) -- the reusable HISTORICAL RESEARCH DATASET.

Turns raw per-bar replay observations into research records that keep
two things strictly apart:

    KNEW_THEN       -- copied verbatim from the observation and the
                       unmodified analysis response at that bar's own
                       `as_of`. Nothing in it can depend on a later candle.
    HAPPENED_AFTER  -- the outcome horizons (`outcome_horizons`), each an
                       independent CONFIRMATION and INVALIDATION fact, plus
                       the +5-session reference status the pattern report
                       counts.

Episodes, not bars. The replay walks every M15 bar and records an
observation whenever the gate clears, so one setup that persists for eight
bars yields eight near-identical observations. Counting those as eight
independent observations would inflate every pattern count with
autocorrelated duplicates -- exactly the "statistics from nothing" this
product must not produce. `group_into_episodes()` collapses CONTIGUOUS bars
of the same session, pattern and direction into one episode represented by
its FIRST bar (the earliest moment TIRE could have known), and keeps the
episode's bar count as provenance.

Market context is a FACT, not a regime model: the NIFTY 50 move from the
previous session's last close to the last index bar at or before the
observation instant, bucketed at a fixed +/-0.25% into NIFTY_UP / NIFTY_DOWN
/ NIFTY_FLAT, or UNKNOWN when the index series does not cover that instant.
Sector context is the static NSE industry classification of the symbol
(the same reference file the live pipeline uses), labelled as such.

Nothing here is a probability, a rate, a score or a prediction.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.domain.audit.research_models import ResearchObservation, ResearchOutcomeStatus
from app.domain.market.models import Candle
from app.orchestration.dashboard_service import AnalyzeResponse
from app.orchestration.outcome_horizons import compute_all_horizons
from app.orchestration.pattern_aggregation import outcome_for_replay_observation
from app.utils.time import to_ist

DATASET_VERSION = "1"
BAR_INTERVAL = timedelta(minutes=15)
MARKET_FLAT_BAND_PCT = Decimal("0.25")

MARKET_UP = "NIFTY_UP"
MARKET_DOWN = "NIFTY_DOWN"
MARKET_FLAT = "NIFTY_FLAT"
MARKET_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CapturedObservation:
    observation: ResearchObservation
    knew_then: dict[str, Any]


@dataclass(frozen=True)
class Episode:
    first: CapturedObservation
    bars: int
    last_bar_at: datetime


def capture_knew_then(observation: ResearchObservation, response: AnalyzeResponse) -> dict[str, Any]:
    """Everything recorded here is read from `observation`/`response`,
    both produced at the bar's own `as_of`. Evidence is split by the
    thesis direction exactly as the evidence matrix labelled it; NEUTRAL
    and UNKNOWN rows are kept apart rather than forced to a side."""
    visual = response.visual
    rows = list(visual.evidence) if visual is not None else []
    opposite = "BEARISH" if observation.direction == "BULLISH" else "BULLISH"
    development = visual.development if visual is not None else None

    def _rows(direction: set[str]) -> list[str]:
        return [f"{r.name}: {r.detail}" for r in rows if r.direction in direction]

    return {
        "symbol": observation.symbol,
        "timestamp": observation.generated_at.isoformat(),
        "pattern": observation.pattern,
        "direction": observation.direction,
        "timing_stage": observation.early_stage_state,
        "research_state": response.research_state,
        "decision": observation.actionability,
        "supporting_evidence": _rows({observation.direction}),
        "conflicting_evidence": _rows({opposite}),
        "neutral_or_unknown_evidence": _rows({"NEUTRAL", "UNKNOWN"}),
        "missing_evidence": observation.missing_evidence,
        "confirmation": {
            "rule": development.confirm_if if development is not None else None,
            "level_kind": observation.nearest_level_kind,
            "level_value": observation.nearest_level_value,
        },
        "invalidation": {
            "rule": development.invalidate_if if development is not None else None,
            "level_kind": observation.invalidation_level_kind,
            "level_value": observation.invalidation_level_value,
            # Two patterns carry a deterministic level today:
            # PRE_BREAKOUT_COMPRESSION (its own supporting structure) and
            # FAILED_BREAKDOWN_RECLAIM (the reclaimed level itself). Every
            # other pattern's invalidation stays UNKNOWN downstream, which
            # is what this flag reports -- read from the observation, never
            # from a hardcoded pattern list.
            "deterministic": observation.invalidation_level_value is not None,
        },
        "spot_at_observation": observation.spot_at_observation,
        "derivatives_evidence_available": observation.derivatives_evidence_available,
    }


def group_into_episodes(captured: Sequence[CapturedObservation]) -> list[Episode]:
    """Contiguous same-session, same-pattern, same-direction bars of ONE
    symbol become one episode. Input need not be sorted."""
    ordered = sorted(captured, key=lambda c: (c.observation.symbol, c.observation.generated_at))
    episodes: list[Episode] = []
    current: CapturedObservation | None = None
    bars = 0
    last: datetime | None = None
    for item in ordered:
        obs = item.observation
        continues = (
            current is not None and last is not None
            and obs.symbol == current.observation.symbol
            and obs.pattern == current.observation.pattern
            and obs.direction == current.observation.direction
            and to_ist(obs.generated_at).date() == to_ist(last).date()
            and obs.generated_at - last <= BAR_INTERVAL
        )
        if continues:
            bars += 1
            last = obs.generated_at
            continue
        if current is not None and last is not None:
            episodes.append(Episode(first=current, bars=bars, last_bar_at=last))
        current, bars, last = item, 1, obs.generated_at
    if current is not None and last is not None:
        episodes.append(Episode(first=current, bars=bars, last_bar_at=last))
    return episodes


class MarketContextSeries:
    """As-of-bounded NIFTY 50 lookups over a real local M15 series."""

    def __init__(self, candles: Sequence[Candle]) -> None:
        self._candles = sorted(candles, key=lambda c: c.freshness.data_timestamp)
        self._times = [c.freshness.data_timestamp for c in self._candles]

    def classify(self, at: datetime) -> str:
        index = bisect.bisect_right(self._times, at) - 1
        if index < 0:
            return MARKET_UNKNOWN
        current = self._candles[index]
        session = to_ist(current.freshness.data_timestamp).date()
        previous_close: Decimal | None = None
        cursor = index
        while cursor >= 0:
            candle = self._candles[cursor]
            if to_ist(candle.freshness.data_timestamp).date() < session:
                previous_close = candle.close
                break
            cursor -= 1
        # Stale index data (the last index bar is not from the observation's
        # own session) is not a context fact about that session.
        if previous_close is None or previous_close == 0 or session != to_ist(at).date():
            return MARKET_UNKNOWN
        change_pct = (current.close - previous_close) / previous_close * 100
        if change_pct >= MARKET_FLAT_BAND_PCT:
            return MARKET_UP
        if change_pct <= -MARKET_FLAT_BAND_PCT:
            return MARKET_DOWN
        return MARKET_FLAT


def build_dataset_row(
    episode: Episode, *, candles: list[Candle], dataset_as_of: datetime, market: MarketContextSeries | None,
    sector: str | None, provenance: dict[str, Any],
) -> tuple[ResearchObservation, ResearchOutcomeStatus, dict[str, Any]]:
    """The representative observation (market context stamped on its
    existing `market_context` field), its +5-session reference status, and
    the dataset row with KNEW_THEN and HAPPENED_AFTER kept separate."""
    first = episode.first
    market_context = market.classify(first.observation.generated_at) if market is not None else MARKET_UNKNOWN
    observation = first.observation.model_copy(update={"market_context": market_context})
    status = outcome_for_replay_observation(observation, candles, as_of=dataset_as_of)
    horizons = {
        outcome.horizon.value: {
            "target_timestamp": outcome.target_timestamp.isoformat(),
            "data_sufficient": outcome.data_sufficient,
            "confirmation": outcome.confirmation_outcome.value,
            "invalidation": outcome.invalidation_outcome.value,
            "first_confirmation_at": outcome.first_confirmation_at.isoformat() if outcome.first_confirmation_at else None,
            "first_invalidation_at": outcome.first_invalidation_at.isoformat() if outcome.first_invalidation_at else None,
            "max_favorable_move_pct": str(outcome.max_favorable_move_pct) if outcome.max_favorable_move_pct is not None else None,
            "max_adverse_move_pct": str(outcome.max_adverse_move_pct) if outcome.max_adverse_move_pct is not None else None,
            "note": outcome.note,
        }
        for outcome in compute_all_horizons(observation, candles, as_of=dataset_as_of)
    }
    row = {
        "dataset_version": DATASET_VERSION,
        "observation_id": observation.observation_id,
        "knew_then": {
            **first.knew_then,
            "market_context": market_context,
            "sector_context": sector or "UNKNOWN",
        },
        "happened_after": {
            "outcome_30m": horizons.get("PLUS_30M"),
            "outcome_1h": horizons.get("PLUS_1H"),
            "outcome_1d": horizons.get("PLUS_1D"),
            "outcome_3d": horizons.get("PLUS_3D"),
            "outcome_5d": horizons.get("PLUS_5D"),
            "reference_status_plus_5d": status.value,
        },
        "provenance": {
            **provenance,
            "source": observation.source,
            "run_id": observation.run_id,
            "episode_bars": episode.bars,
            "episode_first_bar": observation.generated_at.isoformat(),
            "episode_last_bar": episode.last_bar_at.isoformat(),
            "dataset_as_of": dataset_as_of.isoformat(),
            "derivatives_evidence_available": observation.derivatives_evidence_available,
        },
    }
    return observation, status, row
