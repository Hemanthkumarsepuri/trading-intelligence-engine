from __future__ import annotations

from app.domain.system.status import StreamHealthState, classify_stream_status
from app.orchestration.system_status import build_system_status


def test_green_requires_configured_reachable_authenticated_and_valid_response() -> None:
    assert classify_stream_status(
        configured=True, reachable=True, authenticated=True, valid_response=True,
    ) == StreamHealthState.GREEN
    assert classify_stream_status(
        configured=True, reachable=True, authenticated=True, valid_response=False,
    ) == StreamHealthState.YELLOW
    assert classify_stream_status(
        configured=True, reachable=False, authenticated=True, valid_response=False,
    ) == StreamHealthState.RED
    assert classify_stream_status(
        configured=True, reachable=False, authenticated=True, valid_response=False, optional=True,
    ) == StreamHealthState.YELLOW
    assert classify_stream_status(
        configured=False, reachable=False, authenticated=False, valid_response=False, optional=True,
    ) == StreamHealthState.UNKNOWN


def test_option_chain_is_not_green_just_because_the_provider_exists() -> None:
    view = build_system_status(
        token_configured=True,
        instrument_master_loaded=True,
        sector_map_loaded=False,
        qwen_enabled=True,
        qwen_model="qwen2.5-coder-7b-instruct",
        qwen_verified_ok=None,
    )
    by_name = {s.name: s for s in view.streams}
    assert by_name["Market Data"].state == "GREEN"
    assert by_name["Option Chain"].state != "GREEN"
    assert by_name["News"].state != "GREEN"
    assert by_name["AI Explanation"].state != "GREEN"
    assert by_name["5paisa"].state == "UNKNOWN"
    assert by_name["5paisa"].actually_used is False
    assert view.broker_execution == "impossible"


def test_qwen_green_only_after_successful_probe() -> None:
    view = build_system_status(
        token_configured=True,
        instrument_master_loaded=True,
        sector_map_loaded=True,
        qwen_enabled=True,
        qwen_model="qwen2.5-coder-7b-instruct",
        qwen_verified_ok=True,
    )
    ai = next(s for s in view.streams if s.name == "AI Explanation")
    assert ai.state == "GREEN"
