from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import settings as settings_module


@asynccontextmanager
async def _noop_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


def test_cors_wildcard_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.settings, "cors_allow_origins", "*")
    with pytest.raises(RuntimeError, match="must not include"):
        create_app(lifespan=_noop_lifespan)


def test_cors_allows_only_configured_frontend_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings_module.settings,
        "cors_allow_origins",
        "https://example.netlify.app",
    )
    app = create_app(lifespan=_noop_lifespan)
    client = TestClient(app)
    allowed = client.options(
        "/api/health",
        headers={
            "Origin": "https://example.netlify.app",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.headers.get("access-control-allow-origin") == "https://example.netlify.app"
    denied = client.options(
        "/api/health",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert denied.headers.get("access-control-allow-origin") != "https://evil.example"
