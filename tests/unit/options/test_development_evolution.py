from __future__ import annotations

from app.domain.options.development_evolution import (
    DevelopmentEvolution,
    classify_development_evolution,
)


def test_first_snapshot_is_insufficient_history() -> None:
    assert classify_development_evolution(previous_state=None, current_state="WATCH") == DevelopmentEvolution.INSUFFICIENT_HISTORY


def test_watch_to_early_setup_is_emerging() -> None:
    assert classify_development_evolution(previous_state="WATCH", current_state="EARLY_SETUP") == DevelopmentEvolution.EMERGING


def test_early_to_confirmed_is_confirming() -> None:
    assert classify_development_evolution(previous_state="EARLY_SETUP", current_state="CONFIRMED_SETUP") == DevelopmentEvolution.CONFIRMING


def test_any_to_conflict_is_invalidated() -> None:
    assert classify_development_evolution(previous_state="EARLY_SETUP", current_state="CONFLICT") == DevelopmentEvolution.INVALIDATED


def test_becoming_extended() -> None:
    assert classify_development_evolution(previous_state="CONFIRMED_SETUP", current_state="EXTENDED") == DevelopmentEvolution.BECOMING_EXTENDED


def test_unchanged() -> None:
    assert classify_development_evolution(previous_state="WATCH", current_state="WATCH") == DevelopmentEvolution.UNCHANGED


def test_weakening() -> None:
    assert classify_development_evolution(previous_state="CONFIRMED_SETUP", current_state="WATCH") == DevelopmentEvolution.WEAKENING
