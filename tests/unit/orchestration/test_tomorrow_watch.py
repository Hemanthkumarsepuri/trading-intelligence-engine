"""Tomorrow Watch — closed/pre-market checklist. Not a forecast."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.domain.market.trading_calendar import classify_session_window, session_window_payload
from app.domain.research.watch_record import WatchChange, WatchChangeCategory
from app.orchestration.tomorrow_watch import (
    TomorrowWatchView,
    build_tomorrow_watch,
    factual_since_t0_lines,
    one_line_research_summary,
)
from app.orchestration.visual_data import (
    DevelopmentNarrativeView,
    EvidenceRowView,
    FreshnessVisual,
    OptionChainVisual,
    RequestedContractVisual,
    StreamFreshnessView,
    TermStructureExpiryView,
    TermStructureVisual,
    VisualData,
)

_SUNDAY = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)  # 15:30 IST Sunday
_PRE_OPEN = datetime(2026, 9, 21, 3, 40, tzinfo=UTC)  # 09:10 IST Monday
_BEFORE_PRE_OPEN = datetime(2026, 9, 21, 2, 0, tzinfo=UTC)  # 07:30 IST Monday
_OPEN = datetime(2026, 9, 21, 6, 30, tzinfo=UTC)  # 12:00 IST Monday
_POST = datetime(2026, 9, 21, 11, 0, tzinfo=UTC)  # 16:30 IST Monday


def _payload(as_of: datetime) -> dict[str, object]:
    return session_window_payload(classify_session_window(as_of))


def _visual(**kwargs: object) -> VisualData:
    return VisualData.model_construct(**kwargs)  # type: ignore[arg-type]  # partial fixtures: **dict[str, object] vs each typed field


def _build(
    as_of: datetime,
    *,
    research_state: str | None = "CONFLICT",
    visual: VisualData | None = None,
    has_specific_contract: bool = True,
    invalidation_condition: str | None = None,
    market_observed_at: datetime | None = None,
) -> TomorrowWatchView | None:
    payload = _payload(as_of)
    return build_tomorrow_watch(
        session_window=str(payload["session_window"]),
        observation_kind=str(payload["observation_kind"]),
        research_session_mode=str(payload["research_session_mode"]),
        next_session_open_ist_label=str(payload["next_session_open_ist_label"]),
        research_state=research_state,
        visual=visual,
        has_specific_contract=has_specific_contract,
        invalidation_condition=invalidation_condition,
        market_observed_at=market_observed_at,
    )


def test_closed_sunday_is_last_observed_not_live() -> None:
    payload = _payload(_SUNDAY)
    assert payload["session_window"] == "CLOSED"
    assert payload["research_session_mode"] == "CLOSED"
    assert payload["observation_kind"] == "LAST_OBSERVED"
    assert payload["live_discover_available"] is False
    view = _build(_SUNDAY)
    assert view is not None
    assert view.observation_kind == "LAST_OBSERVED"
    assert "21 Sep 2026" in (view.next_session_open_ist_label or "")
    assert "09:15" in (view.next_session_open_ist_label or "")


def test_pre_open_trading_day_and_pre_market_before_call_auction() -> None:
    pre = _payload(_PRE_OPEN)
    assert pre["session_window"] == "PRE_OPEN"
    assert pre["research_session_mode"] == "PRE_MARKET"
    assert pre["observation_kind"] == "LAST_OBSERVED"
    assert _build(_PRE_OPEN) is not None

    morning = _payload(_BEFORE_PRE_OPEN)
    assert morning["session_window"] == "CLOSED"
    assert morning["research_session_mode"] == "PRE_MARKET"
    assert _build(_BEFORE_PRE_OPEN) is not None


def test_open_transition_omits_tomorrow_watch() -> None:
    live = _payload(_OPEN)
    assert live["session_window"] == "OPEN"
    assert live["research_session_mode"] == "LIVE"
    assert live["observation_kind"] == "LIVE"
    assert live["live_discover_available"] is True
    assert _build(_OPEN) is None


def test_post_market_keeps_last_observed() -> None:
    post = _payload(_POST)
    assert post["session_window"] == "CLOSED"
    assert post["research_session_mode"] == "POST_MARKET"
    assert post["observation_kind"] == "LAST_OBSERVED"
    view = _build(_POST)
    assert view is not None
    assert view.research_session_mode == "POST_MARKET"


def test_at_open_check_is_refresh_language_not_a_prediction() -> None:
    view = _build(_SUNDAY, has_specific_contract=True)
    assert view is not None
    joined = " ".join(view.at_open_check).lower()
    assert "refresh underlying price observation" in joined
    assert "refresh option-chain observation" in joined
    assert "verify requested expiry" in joined
    assert "re-evaluate research state" in joined
    assert "sell" not in joined
    assert "buy" not in joined
    assert "goes up" not in joined
    assert "bullish" not in joined


def test_conflict_state_is_not_upgraded_when_market_is_closed() -> None:
    view = _build(_SUNDAY, research_state="CONFLICT")
    assert view is not None
    assert "conflicting evidence" in view.one_line_summary.lower()
    assert "confirmed setup" not in view.one_line_summary.lower()
    assert "will fall" not in view.one_line_summary.lower()


def test_confirmation_and_invalidation_are_not_manufactured() -> None:
    empty = _build(_SUNDAY, visual=None, invalidation_condition=None)
    assert empty is not None
    assert empty.confirm_if is None
    assert empty.invalidate_if is None

    visual = _visual(
        development=DevelopmentNarrativeView(
            pattern="NONE",
            what_is_developing="",
            why_it_matters="price and options disagree",
            what_is_missing="hold above reclaim",
            confirm_if="reclaim holds on the next live print",
            invalidate_if="lose reclaim on the next live print",
            freshness_note="",
        )
    )
    filled = _build(_SUNDAY, visual=visual)
    assert filled is not None
    assert filled.confirm_if == "reclaim holds on the next live print"
    assert filled.invalidate_if == "lose reclaim on the next live print"
    assert filled.missing == "hold above reclaim"
    assert any("confirmation condition" in line.lower() for line in filled.at_open_check)


def test_verified_expiry_dte_matches_term_structure_row_not_first_row() -> None:
    visual = _visual(
        option_chain=OptionChainVisual.model_construct(expiry=date(2026, 9, 29)),
        requested_contract=RequestedContractVisual.model_construct(
            requested_strike=Decimal("1270"),
            requested_right="PE",
            found=True,
            expiry=date(2026, 9, 29),
        ),
        term_structure=TermStructureVisual(
            expiries=[
                TermStructureExpiryView(
                    expiry=date(2026, 10, 27), is_weekly=False, days_remaining=37,
                    atm_strike=None, chain_iv=None, ce_pe_skew=None, pcr_oi=None,
                ),
                TermStructureExpiryView(
                    expiry=date(2026, 9, 29), is_weekly=True, days_remaining=9,
                    atm_strike=None, chain_iv=None, ce_pe_skew=None, pcr_oi=None,
                ),
            ],
            iv_slope=None,
            notes=[],
        ),
    )
    view = _build(_SUNDAY, visual=visual)
    assert view is not None
    assert view.contract_dte == 9


def test_invalid_or_missing_expiry_does_not_invent_dte() -> None:
    missing = _build(_SUNDAY, visual=_visual(option_chain=OptionChainVisual.model_construct(expiry=None)))
    assert missing is not None
    assert missing.contract_dte is None

    unmatched = _visual(
        option_chain=OptionChainVisual.model_construct(expiry=date(2026, 11, 1)),
        term_structure=TermStructureVisual(
            expiries=[
                TermStructureExpiryView(
                    expiry=date(2026, 9, 29), is_weekly=True, days_remaining=9,
                    atm_strike=None, chain_iv=None, ce_pe_skew=None, pcr_oi=None,
                ),
            ],
            iv_slope=None,
            notes=[],
        ),
    )
    view = _build(_SUNDAY, visual=unmatched)
    assert view is not None
    assert view.contract_dte is None


def test_unverified_contract_identity_is_preserved_in_at_open_check() -> None:
    visual = _visual(
        requested_contract=RequestedContractVisual.model_construct(
            requested_strike=Decimal("1270"),
            requested_right="PE",
            found=False,
            expiry=None,
        )
    )
    view = _build(_SUNDAY, visual=visual, has_specific_contract=True)
    assert view is not None
    assert any("unverified" in line.lower() for line in view.at_open_check)


def test_evidence_groups_are_not_double_counted_or_scored() -> None:
    visual = _visual(
        evidence=[
            EvidenceRowView(name="EMA", group="underlying_price_structure", direction="BULLISH", detail="up"),
            EvidenceRowView(name="VWAP", group="underlying_price_structure", direction="BULLISH", detail="above"),
            EvidenceRowView(name="Call OI", group="options_oi", direction="BEARISH", detail="call build"),
            EvidenceRowView(name="Put OI", group="options_oi", direction="BEARISH", detail="put unwind"),
            EvidenceRowView(name="Change in OI", group="options_oi", direction="BEARISH", detail="dOI"),
            EvidenceRowView(name="Volume", group="options_oi", direction="BEARISH", detail="vol"),
            EvidenceRowView(name="IV", group="options_iv", direction="NEUTRAL", detail="iv"),
        ]
    )
    view = _build(_SUNDAY, visual=visual, research_state="CONFLICT")
    assert view is not None
    labels = [g.label for g in view.evidence_groups]
    assert labels.count("OPTION STRUCTURE") == 1
    assert labels.count("PRICE STRUCTURE") == 1
    option = next(g for g in view.evidence_groups if g.label == "OPTION STRUCTURE")
    assert option.status == "SUPPORTED"
    assert len(option.evidence) == 4
    dumped = view.model_dump()
    assert "score" not in dumped
    assert "probability" not in dumped
    assert all("score" not in g.model_dump() for g in view.evidence_groups)
    assert TomorrowWatchView.model_fields["one_line_summary"] is not None


def test_intra_group_conflict_and_stale_isolation() -> None:
    visual = _visual(
        evidence=[
            EvidenceRowView(name="EMA", group="underlying_price_structure", direction="BULLISH", detail="up"),
            EvidenceRowView(name="VWAP", group="underlying_price_structure", direction="BEARISH", detail="below"),
            EvidenceRowView(name="Call OI", group="options_oi", direction="BEARISH", detail="x"),
        ],
        freshness=FreshnessVisual.model_construct(
            streams=[
                StreamFreshnessView(stream="candles_m15", label="STALE", usable_for_vote=False),
                StreamFreshnessView(stream="option_chain", label="MARKET_CLOSED", usable_for_vote=True),
            ]
        ),
    )
    view = _build(_SUNDAY, visual=visual, research_state="CONFLICT")
    assert view is not None
    by_label = {g.label: g for g in view.evidence_groups}
    assert by_label["PRICE STRUCTURE"].status == "STALE"
    assert by_label["OPTION STRUCTURE"].status == "SUPPORTED"
    assert "conflicting evidence" in view.one_line_summary.lower()


def test_market_closed_freshness_does_not_force_stale_status() -> None:
    visual = _visual(
        evidence=[
            EvidenceRowView(name="EMA", group="underlying_price_structure", direction="BULLISH", detail="up"),
        ],
        freshness=FreshnessVisual.model_construct(
            streams=[StreamFreshnessView(stream="candles_m15", label="MARKET_CLOSED", usable_for_vote=False)]
        ),
    )
    view = _build(_SUNDAY, visual=visual)
    assert view is not None
    assert view.evidence_groups[0].status == "SUPPORTED"
    assert view.evidence_groups[0].freshness == "MARKET_CLOSED"


def test_factual_since_t0_skips_missing_fields_and_no_material_change() -> None:
    changes = [
        WatchChange(category=WatchChangeCategory.NO_MATERIAL_CHANGE, field="observation", before="a", after="b"),
        WatchChange(category=WatchChangeCategory.EVIDENCE_CHANGED, field="ltp", before=None, after="35.00"),
        WatchChange(category=WatchChangeCategory.EVIDENCE_CHANGED, field="ltp", before="32.50", after="35.00"),
        WatchChange(category=WatchChangeCategory.EVIDENCE_CHANGED, field="oi", before="1221500", after="1250000"),
        WatchChange(
            category=WatchChangeCategory.OBSERVATION_SCOPE_CHANGED,
            field="instrument_type",
            before="UNDERLYING",
            after="OPTION_CONTRACT",
        ),
    ]
    lines = factual_since_t0_lines(changes)
    assert "Option LTP changed from 32.50 to 35.00." in lines
    assert "Option open interest changed from 1221500 to 1250000." in lines
    assert any("Observation scope changed" in line for line in lines)
    assert all("32.50 → 35.00" not in line or "from" in line for line in lines)
    assert not any("momentum" in line.lower() for line in lines)
    assert "Option LTP changed from None" not in " ".join(lines)


def test_one_line_summary_never_predicts_direction() -> None:
    for state in (
        "DATA_INSUFFICIENT",
        "CONFLICT",
        "CONFIRMATION_PENDING",
        "NO_TRADE",
        "EXTENDED",
        "CONFIRMED_SETUP",
        "EARLY_SETUP",
        "WATCH",
        "UNKNOWN",
    ):
        text = one_line_research_summary(state).lower()
        assert "will" not in text
        assert "tomorrow" not in text
        assert "%" not in text
        assert "buy" not in text or "not a buy" in text
        assert "sell" not in text
