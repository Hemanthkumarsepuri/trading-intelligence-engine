from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.persistence.jsonl_file import JsonlWatchRecordRepository


@asynccontextmanager
async def _noop_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


def _app(tmp_path: Path) -> FastAPI:
    app = create_app(lifespan=_noop_lifespan)
    app.state.watch_repository = JsonlWatchRecordRepository(tmp_path)
    return app


def _observation() -> dict[str, object]:
    return {
        "audit_id": "audit-reliance",
        "symbol": "RELIANCE",
        "query": "RELIANCE 1270 PE",
        "research_state": "EARLY_SETUP",
        "timing_stage": "EARLY",
        "market_observed_at": "2026-09-19T06:00:00+00:00",
        "generated_at": "2026-09-19T06:00:00+00:00",
        "visual": {
            "development": {
                "pattern": "FAILED_BREAKDOWN_RECLAIM",
                "what_is_missing": "hold",
                "confirm_if": "confirm",
                "invalidate_if": "invalidate",
            },
            "quality": {"data_quality": "LIVE"},
            "requested_contract": {
                "found": True,
                "assessment": {"strike": "1270", "right": "PE", "liquidity_grade": "ACCEPTABLE"},
            },
        },
    }


def test_watch_crud_and_duplicate(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    created = client.post(
        "/api/research/watches",
        json={"symbol": "RELIANCE", "query": "RELIANCE 1270 PE", "snapshot_kind": "analyze", "observation": _observation()},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["symbol"] == "RELIANCE"
    assert body["t0"]["research_state"] == "EARLY_SETUP"
    assert body["t0"]["pattern"] == "FAILED_BREAKDOWN_RECLAIM"
    watch_id = body["watch_id"]
    dup = client.post(
        "/api/research/watches",
        json={"symbol": "RELIANCE", "snapshot_kind": "analyze", "observation": _observation()},
    )
    assert dup.status_code == 409
    listed = client.get("/api/research/watches")
    assert listed.status_code == 200
    assert len(listed.json()["watches"]) == 1
    one = client.get(f"/api/research/watches/{watch_id}")
    assert one.status_code == 200
    later = dict(_observation())
    later["research_state"] = "CONFLICT"
    later["audit_id"] = "audit-2"
    later["market_observed_at"] = "2026-09-19T06:30:00+00:00"
    refreshed = client.post(
        f"/api/research/watches/{watch_id}/latest",
        json={"snapshot_kind": "analyze", "observation": later},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["t0"]["research_state"] == "EARLY_SETUP"
    assert refreshed.json()["latest"]["research_state"] == "CONFLICT"
    removed = client.delete(f"/api/research/watches/{watch_id}")
    assert removed.status_code == 200
    assert client.get("/api/research/watches").json()["watches"] == []


def test_invalid_symbol_without_observation(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    resp = client.post("/api/research/watches", json={"symbol": "ZZZXNOTREAL", "snapshot_kind": "analyze"})
    assert resp.status_code == 400
    assert "UNAVAILABLE" in resp.json()["detail"]


def test_migrate_legacy(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))
    resp = client.post(
        "/api/research/watches/migrate",
        json={
            "symbols": ["KAYNES", "BEL"],
            "snapshot_kind": "screener",
            "observations": {
                "KAYNES": {
                    "symbol": "KAYNES",
                    "research_state": "EARLY_SETUP",
                    "developing_pattern": "FAILED_BREAKDOWN_RECLAIM",
                    "observed_at": "2026-09-19T06:00:00+00:00",
                }
            },
        },
    )
    assert resp.status_code == 200
    watches = {w["symbol"]: w for w in resp.json()["watches"]}
    assert watches["KAYNES"]["t0_unavailable"] is False
    assert watches["BEL"]["t0_unavailable"] is True
