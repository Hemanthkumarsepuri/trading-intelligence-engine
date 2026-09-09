from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.market.models import OptionRight
from app.orchestration.query_context import (
    ContextState,
    FollowupKind,
    IPOQueryContext,
    OptionsQueryContext,
    resolve_ipo_followup,
    resolve_options_followup,
)

NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


def _options_ctx(*, strike: Decimal | None = Decimal("4000"), established_at: datetime = NOW) -> OptionsQueryContext:
    return OptionsQueryContext(query="KAYNES 4000 CE", symbol="KAYNES", strike=strike, right=OptionRight.CE, audit_id="audit-1", established_at=established_at)


def _ipo_ctx(*, ipo_id: str | None = "tempsens-ipo", established_at: datetime = NOW) -> IPOQueryContext:
    return IPOQueryContext(query="Tempsens IPO", company_name="Tempsens Instruments (India) IPO", ipo_id=ipo_id, audit_id="ipo-audit-1", established_at=established_at)


# -- valid follow-up ----------------------------------------------------


def test_what_about_pe_resolves_to_swapped_query() -> None:
    resolution = resolve_options_followup("What about PE?", context=_options_ctx(), now=NOW + timedelta(minutes=1))
    assert resolution.kind == FollowupKind.RESOLVED_QUERY
    assert resolution.resolved_query == "KAYNES 4000 PE"


def test_which_expiry_answers_from_existing_context_without_a_new_query() -> None:
    resolution = resolve_options_followup("Which expiry?", context=_options_ctx(), now=NOW + timedelta(minutes=1))
    assert resolution.kind == FollowupKind.ANSWERED_FROM_CONTEXT
    assert resolution.resolved_query is None
    assert "TERM STRUCTURE" in resolution.message


def test_ipo_why_answers_from_existing_context() -> None:
    resolution = resolve_ipo_followup("Why?", context=_ipo_ctx(), now=NOW + timedelta(minutes=1))
    assert resolution.kind == FollowupKind.ANSWERED_FROM_CONTEXT
    assert "Tempsens" in resolution.message


def test_ipo_audit_history_resolves_to_the_real_ipo_id() -> None:
    resolution = resolve_ipo_followup("audit history", context=_ipo_ctx(), now=NOW + timedelta(minutes=1))
    assert resolution.kind == FollowupKind.RESOLVED_QUERY
    assert resolution.resolved_query == "tempsens-ipo"


# -- stale context --------------------------------------------------------


def test_stale_options_context_reports_no_context() -> None:
    resolution = resolve_options_followup("what about pe", context=_options_ctx(established_at=NOW), now=NOW + timedelta(minutes=31))
    assert resolution.kind == FollowupKind.NO_CONTEXT


def test_fresh_context_just_under_ttl_still_resolves() -> None:
    resolution = resolve_options_followup("what about pe", context=_options_ctx(established_at=NOW), now=NOW + timedelta(minutes=29))
    assert resolution.kind == FollowupKind.RESOLVED_QUERY


def test_stale_ipo_context_reports_no_context() -> None:
    resolution = resolve_ipo_followup("why", context=_ipo_ctx(established_at=NOW), now=NOW + timedelta(minutes=31))
    assert resolution.kind == FollowupKind.NO_CONTEXT


# -- ambiguous follow-up ---------------------------------------------------


def test_unrecognized_options_phrase_needs_clarification() -> None:
    resolution = resolve_options_followup("what about it?", context=_options_ctx(), now=NOW)
    assert resolution.kind == FollowupKind.NEEDS_CLARIFICATION


def test_unrecognized_ipo_phrase_needs_clarification() -> None:
    resolution = resolve_ipo_followup("tell me more", context=_ipo_ctx(), now=NOW)
    assert resolution.kind == FollowupKind.NEEDS_CLARIFICATION


def test_pe_followup_without_a_specific_strike_needs_clarification_not_a_guess() -> None:
    bare_ctx = OptionsQueryContext(query="KAYNES", symbol="KAYNES", strike=None, right=None, audit_id="audit-1", established_at=NOW)
    resolution = resolve_options_followup("what about pe", context=bare_ctx, now=NOW)
    assert resolution.kind == FollowupKind.NEEDS_CLARIFICATION


# -- missing context --------------------------------------------------------


def test_no_options_context_at_all_reports_no_context() -> None:
    resolution = resolve_options_followup("what about pe", context=None, now=NOW)
    assert resolution.kind == FollowupKind.NO_CONTEXT


def test_no_ipo_context_at_all_reports_no_context() -> None:
    resolution = resolve_ipo_followup("why", context=None, now=NOW)
    assert resolution.kind == FollowupKind.NO_CONTEXT


# -- cross-domain isolation -------------------------------------------------


def test_options_and_ipo_context_are_independent_fields_never_mixed() -> None:
    state = ContextState(options=_options_ctx(), ipo=None)
    # An IPO follow-up must never see the options context, even though
    # the same ContextState instance holds both fields.
    resolution = resolve_ipo_followup("why", context=state.ipo, now=NOW)
    assert resolution.kind == FollowupKind.NO_CONTEXT


def test_setting_ipo_context_does_not_affect_options_context() -> None:
    state = ContextState()
    state.options = _options_ctx()
    state.ipo = _ipo_ctx()
    assert state.options.symbol == "KAYNES"
    assert state.ipo.company_name != "KAYNES"
    # Resolving an options follow-up must use only `state.options`.
    resolution = resolve_options_followup("what about pe", context=state.options, now=NOW)
    assert resolution.resolved_query == "KAYNES 4000 PE"


# -- restart behavior (in-memory only, documented) --------------------------


def test_a_fresh_context_state_after_restart_has_no_context() -> None:
    """`ContextState` is held only in server process memory (see module
    docstring rule 7) -- a restart naturally produces a fresh, empty
    instance, which must behave exactly like "no context yet", never an
    error and never a stale leftover from before the restart."""
    fresh_state = ContextState()
    assert fresh_state.options is None
    assert fresh_state.ipo is None
    assert resolve_options_followup("what about pe", context=fresh_state.options, now=NOW).kind == FollowupKind.NO_CONTEXT
    assert resolve_ipo_followup("why", context=fresh_state.ipo, now=NOW).kind == FollowupKind.NO_CONTEXT
