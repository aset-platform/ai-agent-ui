"""Backend secret_loader resolution order."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from backend.secret_loader import load_secret, reset_cache


def test_returns_default_when_neither_file_nor_env(monkeypatch):
    reset_cache()
    monkeypatch.delenv("ALGO_KITE_API_SECRET", raising=False)
    with patch(
        "backend.secret_loader._SECRETS_DIR",
        Path("/nonexistent-test-path"),
    ):
        assert load_secret(
            "algo_kite_api_secret", default="fallback",
        ) == "fallback"


def test_env_fallback_when_no_file(monkeypatch):
    reset_cache()
    monkeypatch.setenv("ALGO_KITE_API_SECRET", "from-env")
    with patch(
        "backend.secret_loader._SECRETS_DIR",
        Path("/nonexistent-test-path"),
    ):
        assert load_secret("algo_kite_api_secret") == "from-env"


def test_file_wins_over_env(monkeypatch, tmp_path):
    reset_cache()
    monkeypatch.setenv("ALGO_KITE_API_SECRET", "from-env")
    secret_file = tmp_path / "algo_kite_api_secret"
    secret_file.write_text("from-file\n", encoding="utf-8")
    with patch(
        "backend.secret_loader._SECRETS_DIR", tmp_path,
    ):
        assert load_secret("algo_kite_api_secret") == "from-file"


def test_strips_trailing_whitespace(monkeypatch, tmp_path):
    reset_cache()
    monkeypatch.delenv("ALGO_KITE_API_SECRET", raising=False)
    secret_file = tmp_path / "algo_kite_api_secret"
    # Trailing newlines + spaces from `security` CLI output.
    secret_file.write_text("  abc-def  \n\n", encoding="utf-8")
    with patch(
        "backend.secret_loader._SECRETS_DIR", tmp_path,
    ):
        assert load_secret("algo_kite_api_secret") == "abc-def"


def test_empty_file_falls_through_to_env(monkeypatch, tmp_path):
    reset_cache()
    monkeypatch.setenv("ALGO_KITE_API_SECRET", "from-env")
    secret_file = tmp_path / "algo_kite_api_secret"
    secret_file.write_text("\n  \n", encoding="utf-8")
    with patch(
        "backend.secret_loader._SECRETS_DIR", tmp_path,
    ):
        assert load_secret("algo_kite_api_secret") == "from-env"


def test_returns_none_when_default_omitted(monkeypatch):
    reset_cache()
    monkeypatch.delenv("ALGO_KITE_API_SECRET", raising=False)
    with patch(
        "backend.secret_loader._SECRETS_DIR",
        Path("/nonexistent-test-path"),
    ):
        assert load_secret("algo_kite_api_secret") is None
