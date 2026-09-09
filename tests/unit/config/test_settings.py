"""Regression test for a real incident: a `.env` file saved with a leading
UTF-8 byte-order-mark (common from Windows editors/tools) silently corrupted
the *first* variable's key name, so its value never reached `Settings` and
silently fell back to the field's default — with no error, no warning.

These tests never construct, assert, or print a real secret value; every
token here is an obviously-fake literal chosen only to prove the loading
mechanism itself.
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import Settings

_BOM = b"\xef\xbb\xbf"
_FAKE_TOKEN = "test-token-not-a-real-credential"


def test_env_file_with_leading_bom_still_loads_the_first_variable(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes(_BOM + f"UPSTOX_ACCESS_TOKEN={_FAKE_TOKEN}\n".encode())

    settings = Settings(_env_file=str(env_file))

    assert settings.upstox_access_token == _FAKE_TOKEN


def test_env_file_without_bom_still_loads_correctly(tmp_path: Path) -> None:
    """Regression guard the other direction: the BOM fix must not break the
    already-working, far more common BOM-less case.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(f"UPSTOX_ACCESS_TOKEN={_FAKE_TOKEN}\n", encoding="utf-8")

    settings = Settings(_env_file=str(env_file))

    assert settings.upstox_access_token == _FAKE_TOKEN


def test_env_file_with_bom_does_not_corrupt_a_later_variable_either(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes(_BOM + f"APP_NAME=custom-app\nUPSTOX_ACCESS_TOKEN={_FAKE_TOKEN}\n".encode())

    settings = Settings(_env_file=str(env_file))

    assert settings.app_name == "custom-app"
    assert settings.upstox_access_token == _FAKE_TOKEN


def test_missing_env_file_leaves_token_at_default_none(tmp_path: Path) -> None:
    settings = Settings(_env_file=str(tmp_path / "does_not_exist.env"))

    assert settings.upstox_access_token is None
