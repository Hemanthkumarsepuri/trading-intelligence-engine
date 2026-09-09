from __future__ import annotations

from app.domain.options.plain_language import (
    PCR_CONTEXT,
    pattern_plain_english,
    pattern_technical_label,
)


def test_oi_migration_plain_english_does_not_require_technical_jargon() -> None:
    assert "Open interest is shifting" in pattern_plain_english("OI_MIGRATION")
    assert "OI_MIGRATION" in pattern_technical_label("OI_MIGRATION")


def test_pcr_context_is_explicitly_non_directional() -> None:
    assert "not directional confirmation" in PCR_CONTEXT
