"""Sprint 5, Phase 2/16 — tests for the `scripts/run_live_analysis.py` CLI:
argument parsing (default queries, `--repeat`) and per-symbol error
isolation (`_run_one_query` must never let one query's unexpected failure
abort the batch)."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

import pytest

import scripts.run_live_analysis as cli


def test_main_uses_default_queries_when_none_given(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], float | None, bool]] = []

    async def spy_run(queries: list[str], *, repeat_seconds: float | None, auto_atm: bool = False) -> None:
        calls.append((queries, repeat_seconds, auto_atm))

    monkeypatch.setattr(cli, "run", spy_run)
    monkeypatch.setattr(sys, "argv", ["run_live_analysis.py"])

    cli.main()

    assert calls == [(cli.DEFAULT_QUERIES, None, False)]


def test_main_parses_repeat_flag_and_explicit_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], float | None, bool]] = []

    async def spy_run(queries: list[str], *, repeat_seconds: float | None, auto_atm: bool = False) -> None:
        calls.append((queries, repeat_seconds, auto_atm))

    monkeypatch.setattr(cli, "run", spy_run)
    monkeypatch.setattr(sys, "argv", ["run_live_analysis.py", "--repeat", "300", "KAYNES", "KAYNES 4000 CE"])

    cli.main()

    assert calls == [(["KAYNES", "KAYNES 4000 CE"], 300.0, False)]


def test_main_parses_auto_atm_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], float | None, bool]] = []

    async def spy_run(queries: list[str], *, repeat_seconds: float | None, auto_atm: bool = False) -> None:
        calls.append((queries, repeat_seconds, auto_atm))

    monkeypatch.setattr(cli, "run", spy_run)
    monkeypatch.setattr(sys, "argv", ["run_live_analysis.py", "--auto-atm", "KAYNES", "JIOFIN"])

    cli.main()

    assert calls == [(["KAYNES", "JIOFIN"], None, True)]


def test_main_auto_atm_and_repeat_combine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], float | None, bool]] = []

    async def spy_run(queries: list[str], *, repeat_seconds: float | None, auto_atm: bool = False) -> None:
        calls.append((queries, repeat_seconds, auto_atm))

    monkeypatch.setattr(cli, "run", spy_run)
    monkeypatch.setattr(sys, "argv", ["run_live_analysis.py", "--auto-atm", "--repeat", "120", "KAYNES"])

    cli.main()

    assert calls == [(["KAYNES"], 120.0, True)]


def test_main_missing_repeat_value_prints_usage_and_does_not_crash(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["run_live_analysis.py", "--repeat"])
    cli.main()  # must not raise
    assert "usage" in capsys.readouterr().out.lower()


def test_run_one_query_isolates_an_unexpected_error(capsys: pytest.CaptureFixture[str]) -> None:
    """Phase 2/18 -- one query's completely unexpected failure (not a
    ProviderError, not a parse error -- something this CLI never
    anticipated) must be caught and reported, never allowed to propagate
    and abort the rest of the batch."""

    class _ExplodingContext:
        def _lock_for(self, key: str) -> object:
            raise RuntimeError("boom -- simulated unexpected failure")

    async def go() -> None:
        await cli._run_one_query("KAYNES", ctx=_ExplodingContext(), now=datetime(2026, 8, 29, 10, 0, tzinfo=UTC))  # type: ignore[arg-type]

    asyncio.run(go())  # must not raise
    out = capsys.readouterr().out
    assert "ERROR (unexpected)" in out
    assert "boom" in out
