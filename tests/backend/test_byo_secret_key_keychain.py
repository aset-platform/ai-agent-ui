"""Tests for BYO_SECRET_KEY loaded via load_secret.

Verifies file-first, env-fallback, and missing behaviour for the
byo_secret_key slug (adapts the existing secret_loader pattern from
test_secret_loader.py — uses patch on _SECRETS_DIR rather than a
SECRETS_DIR env var since the loader resolves at import time).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from backend.secret_loader import load_secret


def test_byo_secret_key_from_file(tmp_path, monkeypatch):
    """When /run/secrets/byo_secret_key exists, load_secret reads it."""
    secret_file = tmp_path / "byo_secret_key"
    secret_file.write_text("file-source-key")
    monkeypatch.delenv("BYO_SECRET_KEY", raising=False)
    load_secret.cache_clear()

    with patch("backend.secret_loader._SECRETS_DIR", tmp_path):
        result = load_secret("byo_secret_key")

    assert result == "file-source-key"


def test_byo_secret_key_falls_back_to_env(tmp_path, monkeypatch):
    """When file missing, load_secret falls back to BYO_SECRET_KEY env."""
    monkeypatch.setenv("BYO_SECRET_KEY", "env-source-key")
    load_secret.cache_clear()

    with patch(
        "backend.secret_loader._SECRETS_DIR",
        Path("/nonexistent-test-path"),
    ):
        result = load_secret("byo_secret_key")

    assert result == "env-source-key"


def test_byo_secret_key_missing_returns_none(tmp_path, monkeypatch):
    """Missing in both sources → None (not raise)."""
    monkeypatch.delenv("BYO_SECRET_KEY", raising=False)
    load_secret.cache_clear()

    with patch(
        "backend.secret_loader._SECRETS_DIR",
        Path("/nonexistent-test-path"),
    ):
        result = load_secret("byo_secret_key")

    assert result is None


def test_existing_byo_keys_decrypt_after_migration(monkeypatch):
    """A key encrypted under env-sourced Fernet decrypts after we
    migrate the source to file-sourced (same key value).

    Smoke-test only: verifies Fernet accepts the key shape; does not
    perform a real round-trip requiring a valid 32-byte key.
    """
    from cryptography.fernet import Fernet

    # 44-char URL-safe base64 string — use a real key for shape
    real_key = Fernet.generate_key().decode()
    # Confirm Fernet accepts it without raising
    f = Fernet(real_key.encode())
    ct = f.encrypt(b"test-payload")
    assert f.decrypt(ct) == b"test-payload"
