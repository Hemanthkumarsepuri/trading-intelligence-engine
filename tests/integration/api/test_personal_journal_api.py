from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.journal.personal_journal import (
    MistakeClass,
    PersonalJournalEntry,
    PersonalJournalOutcome,
)
from app.persistence.jsonl_file import JsonlPersonalJournalRepository
from tests.integration.api.test_dashboard_api import _configured_app


def test_journal_round_trip_and_does_not_infer_emotion(tmp_path: Path) -> None:
    repo = JsonlPersonalJournalRepository(tmp_path)
    entry = PersonalJournalEntry(
        recorded_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        symbol="RELIANCE",
        user_reasoning="Watching relative-strength rotation; not entering yet.",
        research_bucket="DEVELOPING",
        direction_hypothesis="BULLISH",
    )
    asyncio.run(repo.save_entry(entry))
    loaded = asyncio.run(repo.query_entries(symbol="reliance"))
    assert len(loaded) == 1
    assert loaded[0].journal_id == entry.journal_id
    assert loaded[0].user_reasoning == entry.user_reasoning
    # Emotion is never inferred -- an entry without an outcome has no mistake_class.
    outcome = PersonalJournalOutcome(
        journal_id=entry.journal_id,
        captured_at=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
        what_i_thought="Developing bullish evidence, waiting for breakout.",
        what_actually_happened="Broke out then reversed into the range.",
        mistake_class=MistakeClass.NO_CONFIRMATION,
    )
    asyncio.run(repo.save_outcome(outcome))
    outcomes = asyncio.run(repo.query_outcomes(entry.journal_id))
    assert len(outcomes) == 1
    assert outcomes[0].mistake_class == MistakeClass.NO_CONFIRMATION
    assert outcomes[0].mistake_class != MistakeClass.EMOTIONAL_DECISION


def test_personal_journal_http_round_trip(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=None, instrument_master=None)
    app.state.personal_journal = JsonlPersonalJournalRepository(tmp_path / "personal")
    client = TestClient(app)
    resp = client.post("/api/journal/personal", json={"symbol": "kaynes", "user_reasoning": "Need confirmation of reclaim."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "KAYNES"
    assert "journal_id" in body
    listed = client.get("/api/journal/personal", params={"symbol": "KAYNES"})
    assert listed.status_code == 200
    assert listed.json()["entries"][0]["user_reasoning"] == "Need confirmation of reclaim."
    out = client.post(
        f"/api/journal/personal/{body['journal_id']}/outcome",
        json={
            "what_i_thought": "Reclaim developing",
            "what_actually_happened": "Lost the level again",
            "mistake_class": "NO_CONFIRMATION",
        },
    )
    assert out.status_code == 200
    assert out.json()["mistake_class"] == "NO_CONFIRMATION"


def test_personal_journal_rejects_unknown_mistake_class(tmp_path: Path) -> None:
    app = _configured_app(tmp_path, provider=None, instrument_master=None)
    app.state.personal_journal = JsonlPersonalJournalRepository(tmp_path / "personal")
    client = TestClient(app)
    saved = client.post("/api/journal/personal", json={"symbol": "NIFTY", "user_reasoning": "journal first"})
    journal_id = saved.json()["journal_id"]
    bad = client.post(
        f"/api/journal/personal/{journal_id}/outcome",
        json={"what_i_thought": "x", "what_actually_happened": "y", "mistake_class": "WILL_GO_UP"},
    )
    assert bad.status_code == 400
