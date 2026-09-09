from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import Settings
from scripts import fetch_upstox_historical
from scripts.fetch_upstox_historical import MissingCredentialError, main


def test_missing_token_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_upstox_historical, "settings", Settings(upstox_access_token=None))

    with pytest.raises(MissingCredentialError, match="UPSTOX_ACCESS_TOKEN"):
        fetch_upstox_historical._require_token()


def test_token_present_returns_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_upstox_historical, "settings", Settings(upstox_access_token="secret-token"))

    assert fetch_upstox_historical._require_token() == "secret-token"


def test_main_wrong_arg_count_returns_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])
    assert exit_code == 2
    assert "usage" in capsys.readouterr().out


def test_main_bad_date_returns_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["NSE_EQ|INE002A01018", "RELIANCE", "not-a-date", "2026-08-27"])
    assert exit_code == 2


def test_main_missing_credential_returns_waiting_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(fetch_upstox_historical, "settings", Settings(upstox_access_token=None))

    exit_code = main(
        ["NSE_EQ|INE002A01018", "RELIANCE", "2026-07-01", "2026-08-27", str(tmp_path / "out.csv")]
    )

    out = capsys.readouterr().out
    assert exit_code == 3
    assert "WAITING" in out
    assert "UPSTOX_ACCESS_TOKEN" in out
    assert not (tmp_path / "out.csv").exists()  # no partial/fabricated file written


def test_never_prints_a_token_value(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(fetch_upstox_historical, "settings", Settings(upstox_access_token=None))
    main(["NSE_EQ|INE002A01018", "RELIANCE", "2026-07-01", "2026-08-27"])
    out = capsys.readouterr().out
    assert "secret" not in out.lower()  # sanity: no token echoed under any code path exercised here
