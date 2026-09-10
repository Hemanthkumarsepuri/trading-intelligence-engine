from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.orchestration.daily_research import DailyResearchResult
from tests.integration.api.test_dashboard_api import _MASTER, _configured_app, _provider, _router


def test_latest_job_is_none_before_scan(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    client = TestClient(app)
    resp = client.get("/api/research/jobs/latest")
    assert resp.status_code == 200
    assert resp.json()["status"] == "NONE"


def test_discover_job_does_not_block_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()

    async def fake_run(*args: Any, **kwargs: Any) -> DailyResearchResult:
        on_progress = kwargs.get("on_progress")
        if on_progress is not None:
            await on_progress({"stage": "stage1", "processed": 1, "total": 10, "message": "fast discovery"})
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.02)
        return DailyResearchResult(
            generated_at=datetime(2026, 9, 9, tzinfo=UTC),
            market_state="MARKET_CLOSED",
            universe=["RELIANCE"],
            screened_count=1,
            deep_analyzed_count=0,
            no_high_conviction=True,
        )

    monkeypatch.setattr("app.api.main.run_daily_research", fake_run)
    app = _configured_app(tmp_path, provider=_provider(_router()), instrument_master=_MASTER)
    with TestClient(app) as client:
        posted = client.post("/api/research/jobs/discover")
        assert posted.status_code == 200
        assert posted.json()["status"] in ("QUEUED", "RUNNING")
        assert started.wait(timeout=2)
        health = client.get("/api/health")
        assert health.status_code == 200
        latest = client.get("/api/research/jobs/latest").json()
        assert latest["status"] in ("QUEUED", "RUNNING")
        assert latest["processed"] >= 1
        release.set()
        deadline = time.time() + 3
        while time.time() < deadline:
            if client.get("/api/research/jobs/latest").json()["status"] == "COMPLETE":
                break
            time.sleep(0.05)
        assert client.get("/api/research/jobs/latest").json()["status"] == "COMPLETE"


def test_qwen_health_timeout_status(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=None, instrument_master=None)

    class _Adapter:
        model = "qwen2.5-coder-7b-instruct"
        models_endpoint = "http://127.0.0.1:1234/v1/models"

        async def connectivity_probe(self, client: object) -> object:
            from app.llm.qwen_narrative import QwenResult
            return QwenResult(
                ok=False, status="QWEN_TIMEOUT", explanation=None, detail="Qwen health timed out",
                latency_ms=12, model=self.model, endpoint=self.models_endpoint,
            )

    app.state.qwen = _Adapter()
    client = TestClient(app)
    resp = client.get("/api/qwen/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "QWEN_TIMEOUT"
    assert resp.json()["ok"] is False


def test_research_explain_timeout_returns_deterministic_fallback(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=None, instrument_master=None)

    class _Adapter:
        model = "qwen2.5-coder-7b-instruct"

        async def explain(self, client: object, **kwargs: Any) -> object:
            from app.llm.qwen_narrative import QwenResult
            return QwenResult(
                ok=False, status="TIMEOUT", explanation=None, detail="Qwen request timed out",
                latency_ms=8, model=self.model, endpoint="http://127.0.0.1:1234/v1/chat/completions",
            )

    app.state.qwen = _Adapter()
    client = TestClient(app)
    started = time.perf_counter()
    resp = client.post(
        "/api/research/explain",
        json={"facts": {"symbol": "RELIANCE", "state": "CONFLICT"}, "source_evidence_ids": ["E1"]},
    )
    elapsed = time.perf_counter() - started
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "TIMEOUT"
    assert body["fallback"] == "deterministic"
    assert elapsed < 2.0
