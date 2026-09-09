"""SAFETY_LOCK.txt invariant test.

`Settings.assert_broker_execution_disabled()` is invoked from
`app.api.main.real_lifespan` at application startup. A true
`BROKER_ORDER_EXECUTION_ENABLED` must fail closed. This system has no
order-placement endpoint and remains read-only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import create_app, real_lifespan
from app.config.settings import Settings


def test_default_settings_leave_broker_execution_disabled() -> None:
    settings = Settings()
    assert settings.broker_order_execution_enabled is False
    settings.assert_broker_execution_disabled()  # must not raise


def test_assert_raises_when_broker_execution_enabled() -> None:
    settings = Settings(broker_order_execution_enabled=True)
    with pytest.raises(RuntimeError, match="BROKER_ORDER_EXECUTION_ENABLED"):
        settings.assert_broker_execution_disabled()


def test_assert_raises_regardless_of_other_field_values() -> None:
    # A tampered safety flag must be caught even if surrounded by otherwise
    # ordinary-looking config — no combination of other settings suppresses it.
    settings = Settings(
        broker_order_execution_enabled=True,
        market_data_provider="dhan",
        llm_provider="anthropic",
        max_risk_per_trade=500.0,
    )
    with pytest.raises(RuntimeError):
        settings.assert_broker_execution_disabled()


def test_real_lifespan_fails_closed_when_execution_flag_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.main as main_mod
    import app.config.settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "broker_order_execution_enabled", True)
    monkeypatch.setattr(main_mod.settings, "broker_order_execution_enabled", True)

    app = create_app(lifespan=real_lifespan)
    with pytest.raises(RuntimeError, match="BROKER_ORDER_EXECUTION_ENABLED"), TestClient(app):
        pass


def test_no_order_placement_routes_exist() -> None:
    @asynccontextmanager
    async def _noop(_app: FastAPI) -> AsyncIterator[None]:
        yield

    app = create_app(lifespan=_noop)
    paths = [getattr(route, "path", "") for route in app.routes]
    for path in paths:
        lowered = path.lower()
        assert "/orders" not in lowered
        assert "place-order" not in lowered
        assert "cancel-order" not in lowered
