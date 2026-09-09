"""Sprint 4, Part T — the additive `NewsSection` change must not break a
record persisted before this milestone (`items`/`fetch_error` did not
exist yet). A real, literal pre-Sprint-4-shaped JSON string is validated
directly here, rather than assumed, per the project's own "verify, don't
trust" discipline.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.audit.models import NewsItemSnapshot, NewsSection


def test_old_pre_sprint4_news_section_json_still_deserializes() -> None:
    old_json = '{"provider": null, "unavailable_reason": "NOT AVAILABLE FROM CURRENT AUTHORIZED DATA SOURCES"}'
    section = NewsSection.model_validate_json(old_json)
    assert section.provider is None
    assert section.unavailable_reason == "NOT AVAILABLE FROM CURRENT AUTHORIZED DATA SOURCES"
    # The two NEW fields default cleanly for a record that never had them.
    assert section.items == []
    assert section.fetch_error is None


def test_new_news_section_with_real_items_round_trips() -> None:
    section = NewsSection(
        provider="upstox",
        items=[
            NewsItemSnapshot(
                source="upstox", published_at=datetime(2026, 8, 26, 10, 31, 53, tzinfo=UTC), title="Reliance beats estimates",
                summary="x", url="https://x", relevance="HIGH", direction="UNKNOWN", evidence_quality="HIGH",
            )
        ],
        fetch_error=None, unavailable_reason=None,
    )
    round_tripped = NewsSection.model_validate_json(section.model_dump_json())
    assert round_tripped == section
