"""Adversarial Analysis Engine (Phase 9) — before any final decision is
rendered, explicitly constructs the BULL CASE and BEAR CASE an analyst
would need to weigh, surfaces CONTRADICTIONS, lists MISSING DATA, and
derives KEY RISKS — all synthesized from the SAME `EvidenceMatrix` already
computed elsewhere in the pipeline. No new data source, no new
computation of any underlying fact; this module only reorganizes and
cross-checks facts that already exist.

This runs independently of whether a directional candidate was ever
generated — `candidate_engine.py` only ever builds evidence for whichever
side already won the bias gate; this module builds BOTH cases regardless,
specifically to answer Phase 9's explicit question: "what evidence would
make the OPPOSITE trade correct?" If that opposite case turns out to be
about as well-supported as the case that "won," `opposite_case_is_equally_supported`
says so plainly — that is itself a reason to prefer NO_TRADE, not a
detail to bury.

Contradictions are derived from the EXACT SAME per-group anti-gaming
mechanism `EvidenceMatrix` already enforces (`group_verdict() ==
CONFLICTING`) — a group whose own rows disagree internally is a real,
structural contradiction traced back to one data source, never invented
by comparing two independently-fabricated readings.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.options.evidence_matrix import (
    EvidenceDirection,
    EvidenceMatrix,
    GroupVerdict,
    OverallConvergence,
)


@dataclass(frozen=True)
class AdversarialAnalysis:
    bull_case: list[str]
    bear_case: list[str]
    contradictions: list[str]
    missing_data: list[str]
    key_risks: list[str]
    opposite_case_is_equally_supported: bool


def build_adversarial_analysis(matrix: EvidenceMatrix) -> AdversarialAnalysis:
    bull_case = [f"{r.name}: {r.detail}" for r in matrix.voting_rows(EvidenceDirection.BULLISH)]
    bear_case = [f"{r.name}: {r.detail}" for r in matrix.voting_rows(EvidenceDirection.BEARISH)]
    missing_data = [f"{r.name}: {r.detail}" for r in matrix.rows if r.direction == EvidenceDirection.UNKNOWN]

    contradictions: list[str] = []
    for group, verdict in matrix.group_verdicts().items():
        if verdict != GroupVerdict.CONFLICTING:
            continue
        group_rows = [r for r in matrix.rows if r.group == group]
        bullish_names = [r.name for r in group_rows if r.direction == EvidenceDirection.BULLISH]
        bearish_names = [r.name for r in group_rows if r.direction == EvidenceDirection.BEARISH]
        contradictions.append(
            f"{group.value}: {', '.join(bullish_names)} (bullish) vs {', '.join(bearish_names)} (bearish) "
            f"-- the same evidence source disagrees internally, not two independent confirmations"
        )

    convergence = matrix.overall_convergence()
    opposite_equally_supported = convergence == OverallConvergence.CONFLICT
    if opposite_equally_supported:
        contradictions.append(
            "overall evidence convergence is CONFLICT -- independent evidence groups disagree; "
            "neither the bull nor the bear case is genuinely stronger"
        )

    key_risks = _derive_key_risks(matrix, bull_case=bull_case, bear_case=bear_case)

    return AdversarialAnalysis(
        bull_case=bull_case, bear_case=bear_case, contradictions=contradictions, missing_data=missing_data,
        key_risks=key_risks, opposite_case_is_equally_supported=opposite_equally_supported,
    )


def _derive_key_risks(matrix: EvidenceMatrix, *, bull_case: list[str], bear_case: list[str]) -> list[str]:
    risks: list[str] = []
    if bull_case and bear_case:
        risks.append(
            "both directions have real supporting evidence -- a directional trade here fights genuine "
            "opposing evidence, not merely noise"
        )
    if not bull_case and not bear_case:
        risks.append(
            "neither direction has real supporting evidence -- any directional trade here would be "
            "unsupported speculation, not evidence-based"
        )

    liquidity_row = next((r for r in matrix.rows if r.name == "Liquidity"), None)
    if liquidity_row is not None and "untradeable" in liquidity_row.detail.lower():
        risks.append("liquidity is untradeable for the assessed candidate(s) -- execution risk independent of directional correctness")

    freshness_row = next((r for r in matrix.rows if r.name == "Data freshness"), None)
    if freshness_row is not None and freshness_row.direction == EvidenceDirection.UNKNOWN:
        risks.append(f"data freshness is degraded ({freshness_row.detail}) -- this analysis may not reflect the current market")

    return risks
