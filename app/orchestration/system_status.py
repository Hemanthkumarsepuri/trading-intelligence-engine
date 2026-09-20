"""Assemble a factual system-status snapshot from already-known state.

Does not probe external APIs. A stream that has not produced a valid
response this process is YELLOW or UNKNOWN, never GREEN.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.system.status import StreamStatus, classify_stream_status


class StreamStatusView(BaseModel):
    name: str
    state: str
    configured: bool
    reachable: bool
    authenticated: bool
    valid_response: bool
    actually_used: bool
    last_successful_call: datetime | None = None
    detail: str


class SystemStatusView(BaseModel):
    token_configured: bool
    primary_provider: str
    secondary_provider: str
    broker_execution: str
    qwen_enabled: bool
    qwen_model: str
    streams: list[StreamStatusView] = Field(default_factory=list)


def _view(status: StreamStatus) -> StreamStatusView:
    return StreamStatusView(
        name=status.name,
        state=status.state.value,
        configured=status.configured,
        reachable=status.reachable,
        authenticated=status.authenticated,
        valid_response=status.valid_response,
        actually_used=status.actually_used,
        last_successful_call=status.last_successful_call,
        detail=status.detail,
    )


def _stream(
    *,
    name: str,
    configured: bool,
    reachable: bool,
    authenticated: bool,
    valid_response: bool,
    actually_used: bool,
    detail: str,
    optional: bool = False,
    last_successful_call: datetime | None = None,
) -> StreamStatus:
    state = classify_stream_status(
        configured=configured,
        reachable=reachable,
        authenticated=authenticated,
        valid_response=valid_response,
        optional=optional,
    )
    return StreamStatus(
        name=name,
        state=state,
        configured=configured,
        reachable=reachable,
        authenticated=authenticated,
        valid_response=valid_response,
        actually_used=actually_used,
        last_successful_call=last_successful_call,
        detail=detail,
    )


def build_system_status(
    *,
    token_configured: bool,
    instrument_master_loaded: bool,
    sector_map_loaded: bool,
    qwen_enabled: bool,
    qwen_model: str,
    qwen_verified_ok: bool | None,
    primary_provider: str = "upstox",
    dhan_configured: bool = False,
    fivepaisa_configured: bool = False,
) -> SystemStatusView:
    """`qwen_verified_ok` is True only after a successful probe, False after
    a failed probe, None if this process has not probed."""
    market_ok = token_configured and instrument_master_loaded
    streams = [
        _stream(
            name="Market Data",
            configured=token_configured,
            reachable=token_configured,
            authenticated=token_configured,
            valid_response=instrument_master_loaded,
            actually_used=True,
            detail=(
                "Instrument master loaded at startup -- quotes are verified per analysis."
                if market_ok
                else "UPSTOX_ACCESS_TOKEN missing or instrument master not loaded."
            ),
        ),
        _stream(
            name="Option Chain",
            configured=token_configured,
            reachable=token_configured,
            authenticated=token_configured,
            valid_response=False,
            actually_used=True,
            detail="Same Upstox account; GREEN only after a successful chain fetch this session. Not verified at health.",
        ),
        _stream(
            name="Futures",
            configured=token_configured,
            reachable=token_configured,
            authenticated=token_configured,
            valid_response=False,
            actually_used=True,
            detail="Same Upstox account; not verified until a futures quote succeeds this session.",
        ),
        _stream(
            name="News",
            configured=token_configured,
            reachable=token_configured,
            authenticated=token_configured,
            valid_response=False,
            actually_used=True,
            detail="Upstox news is fetched per symbol. A failed fetch is NEWS_DATA_UNAVAILABLE, not NO_NEWS.",
        ),
        _stream(
            name="Sector Data",
            configured=True,
            reachable=sector_map_loaded,
            authenticated=True,
            valid_response=sector_map_loaded,
            actually_used=sector_map_loaded,
            detail=(
                "Official NSE Nifty 500 industry map loaded."
                if sector_map_loaded
                else "Sector classification unavailable this session -- not inferred from company names."
            ),
            optional=True,
        ),
        _stream(
            name="Historical Data",
            configured=token_configured,
            reachable=token_configured,
            authenticated=token_configured,
            valid_response=False,
            actually_used=True,
            detail="Candles are fetched per analysis. No local multi-year OHLCV warehouse is implemented.",
        ),
        _stream(
            name="AI Explanation",
            configured=qwen_enabled,
            reachable=qwen_verified_ok is True,
            authenticated=qwen_enabled,
            valid_response=qwen_verified_ok is True,
            actually_used=False,
            optional=True,
            detail=(
                "Qwen probe succeeded -- explanations only, never research-state."
                if qwen_verified_ok is True
                else (
                    "Qwen enabled but not verified on /api/health. Use /api/qwen/health. Fail-open."
                    if qwen_enabled and qwen_verified_ok is None
                    else "Qwen unavailable or disabled -- deterministic research continues."
                )
            ),
        ),
        _stream(
            name="5paisa",
            configured=fivepaisa_configured,
            reachable=False,
            authenticated=False,
            valid_response=False,
            actually_used=False,
            optional=True,
            detail="Adapter not implemented. Documented only -- not production-ready in TIRE.",
        ),
        _stream(
            name="Dhan",
            configured=dhan_configured,
            reachable=False,
            authenticated=dhan_configured,
            valid_response=False,
            actually_used=False,
            optional=True,
            detail="Adapter exists in-repo; dashboard does not construct or call it.",
        ),
    ]
    return SystemStatusView(
        token_configured=token_configured,
        primary_provider=primary_provider,
        secondary_provider="not_wired",
        broker_execution="impossible",
        qwen_enabled=qwen_enabled,
        qwen_model=qwen_model,
        streams=[_view(s) for s in streams],
    )


def as_health_payload(
    view: SystemStatusView,
    *,
    server_time_utc: str,
    session_window: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "token_configured": view.token_configured,
        "server_time_utc": server_time_utc,
        "qwen_enabled": view.qwen_enabled,
        "qwen_model": view.qwen_model,
        "primary_provider": view.primary_provider,
        "secondary_provider": view.secondary_provider,
        "broker_execution": view.broker_execution,
        "streams": [s.model_dump(mode="json") for s in view.streams],
    }
    if session_window is not None:
        payload["session_window"] = session_window
    return payload
