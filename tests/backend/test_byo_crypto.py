"""Unit tests for Fernet-backed BYO key encryption."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from backend.crypto import byo_secrets
from backend.secret_loader import load_secret


@pytest.fixture(autouse=True)
def _reset_and_seed(monkeypatch):
    """Reset singleton + inject a fresh master key per test.

    - Clears load_secret LRU cache (byo_secrets uses load_secret).
    - Patches _SECRETS_DIR to a nonexistent path so file-based
      resolution is bypassed; env var is the only source in tests.
    - Sets a valid BYO_SECRET_KEY in env so non-error tests pass.
    """
    load_secret.cache_clear()
    monkeypatch.setattr(byo_secrets, "_fernet", None)
    monkeypatch.setenv(
        "BYO_SECRET_KEY", Fernet.generate_key().decode(),
    )
    with patch(
        "backend.secret_loader._SECRETS_DIR",
        Path("/nonexistent-test-secrets"),
    ):
        yield
    monkeypatch.setattr(byo_secrets, "_fernet", None)
    load_secret.cache_clear()


def test_round_trip_recovers_plaintext():
    ct = byo_secrets.encrypt_key("gsk_hello_world_plaintext")
    assert byo_secrets.decrypt_key(ct) == "gsk_hello_world_plaintext"


def test_encrypt_empty_raises():
    with pytest.raises(ValueError):
        byo_secrets.encrypt_key("")


def test_mask_groq_key():
    assert (
        byo_secrets.mask_key("gsk_ABCDEFGHIJKLMNOPQRSTUVWXabcd")
        == "gsk_****abcd"
    )


def test_mask_anthropic_key():
    assert (
        byo_secrets.mask_key(
            "sk-ant-abcdefghijklmnop1234wxyz",
        )
        == "sk-ant-****wxyz"
    )


def test_mask_short_key():
    assert byo_secrets.mask_key("abc") == "***"


def test_mask_no_prefix_falls_back_to_last4():
    assert (
        byo_secrets.mask_key("nokeywithprefixwxyz")
        == "****wxyz"
    )


def test_missing_master_key_raises(monkeypatch):
    monkeypatch.setattr(byo_secrets, "_fernet", None)
    monkeypatch.delenv("BYO_SECRET_KEY", raising=False)
    load_secret.cache_clear()
    with pytest.raises(RuntimeError, match="BYO_SECRET_KEY"):
        byo_secrets.get_fernet()


def test_invalid_master_key_raises(monkeypatch):
    monkeypatch.setattr(byo_secrets, "_fernet", None)
    monkeypatch.setenv(
        "BYO_SECRET_KEY", "not-a-valid-fernet-key",
    )
    load_secret.cache_clear()
    with pytest.raises(RuntimeError, match="Fernet"):
        byo_secrets.get_fernet()


def test_tampered_ciphertext_raises():
    ct = byo_secrets.encrypt_key("gsk_realkey")
    tampered = ct[:-4] + b"XXXX"
    with pytest.raises(RuntimeError):
        byo_secrets.decrypt_key(tampered)
